"""Importpipeline och granskningsjobb.

Stegen är idempotenta: (import, steg, stegversion) registreras i pipeline_step och körs inte
om. Jobben körs i bakgrunden via Procrastinate (jobs/worker.py) eller direkt (tester, CLI).

AnalyzeDataset:
  validate_file → parse_sie → persist (versionering) → materialize → review_periods → ai_enrich
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from redovisningai.accounting.periods import month, month_start
from redovisningai.ai.service import AIService
from redovisningai.cases.builder import Case
from redovisningai.config import get_settings
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.facts.model import Visibility
from redovisningai.findings.lifecycle import reconcile
from redovisningai.maturity.assess import PeriodStatus
from redovisningai.review.analysis import CompanyAnalysis
from redovisningai.rules.engine import FindingCandidate, Severity, default_catalog
from redovisningai.sie.parser import SieFormatError, parse_sie
from redovisningai.storage.objects import EncryptedStore, ObjectStore, get_object_store

log = logging.getLogger(__name__)
MAX_FILE_BYTES = 200 * 1024 * 1024
STEP_VERSION = "1"


class ImportError_(Exception):
    pass


@dataclass(slots=True)
class ImportResult:
    source_file_id: uuid.UUID | None
    import_ids: list[uuid.UUID]
    skipped_duplicate: bool
    issues: list[dict[str, Any]]
    stats: dict[str, int]
    reviewed_periods: list[str] = field(default_factory=list)


def _normalize_orgnr(s: str | None) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _org_store(session: Session, ctx: TenantContext, store: ObjectStore | None) -> ObjectStore:
    org = session.get(m.Organization, ctx.org_id)
    base = store or get_object_store()
    if org is not None and org.encrypted_dek:
        return EncryptedStore(base, org.encrypted_dek)
    return base


def import_sie(
    ctx: TenantContext,
    company_id: uuid.UUID,
    filename: str,
    raw: bytes,
    *,
    source: str = "sie_file",
    store: ObjectStore | None = None,
    allow_orgnr_mismatch: bool = False,
    run_review: bool = True,
    ai: AIService | None = None,
) -> ImportResult:
    if len(raw) > MAX_FILE_BYTES:
        raise ImportError_("Filen är för stor.")
    if b"\x00" in raw[:4096]:
        raise ImportError_("Filen ser ut att vara binär – inte en SIE-fil.")
    sha = hashlib.sha256(raw).hexdigest()
    with tenant_session(ctx) as s:
        company = s.get(m.Company, company_id)
        if company is None:
            raise ImportError_("Bolaget finns inte eller saknar behörighet.")
        dup = s.scalar(select(m.SourceFile).where(m.SourceFile.company_id == company_id, m.SourceFile.sha256 == sha))
        if dup is not None:
            repo.audit(s, ctx, "import.duplicate_skipped", company_id, filename=filename, sha256=sha)
            return ImportResult(dup.id, [], True, [], {})
        try:
            doc = parse_sie(raw)
        except SieFormatError as exc:
            repo.audit(s, ctx, "import.failed", company_id, filename=filename, error=str(exc))
            raise ImportError_(str(exc)) from exc
        if (
            doc.org_number
            and company.org_number
            and not allow_orgnr_mismatch
            and _normalize_orgnr(doc.org_number) != _normalize_orgnr(company.org_number)
        ):
            raise ImportError_(
                f"Filens organisationsnummer {doc.org_number} matchar inte bolaget {company.org_number}."
            )
        object_key = f"{ctx.org_id}/{company_id}/source/{sha}"
        _org_store(s, ctx, store).put(object_key, raw)
        issues = [
            {"line": i.line, "code": i.code, "message": i.message, "severity": i.severity.value} for i in doc.issues
        ]
        sf = m.SourceFile(
            org_id=ctx.org_id,
            company_id=company_id,
            filename=filename[:300],
            object_key=object_key,
            sha256=sha,
            size_bytes=len(raw),
            detected_format=f"SIE{doc.sie_type or ''}",
            encoding=doc.encoding,
            uploaded_by=ctx.user_id,
            parse_issues=issues,
            delete_after=date.today() + timedelta(days=30 * get_settings().source_file_retention_months),
        )
        s.add(sf)
        s.flush()
        outcome = repo.persist_document(s, ctx, company_id, doc, source=source, source_file_id=sf.id)
        for imp_id in outcome.import_ids:
            for step in ("validate_file", "parse_sie", "persist", "materialize"):
                s.add(
                    m.PipelineStep(
                        org_id=ctx.org_id,
                        import_id=imp_id,
                        step=step,
                        step_version=STEP_VERSION,
                        status="DONE",
                        finished_at=datetime.now(),
                    )
                )
        if not company.source_system:
            company.source_system = source
        repo.audit(
            s,
            ctx,
            "import.completed",
            company_id,
            filename=filename,
            sha256=sha,
            imports=[str(i) for i in outcome.import_ids],
            added=outcome.added,
            changed=outcome.changed,
            removed=outcome.removed,
        )
        result = ImportResult(
            sf.id,
            outcome.import_ids,
            False,
            issues,
            {
                "added": outcome.added,
                "changed": outcome.changed,
                "removed": outcome.removed,
                "unchanged": outcome.unchanged,
            },
        )
        affected = sorted(outcome.affected_months)
    if run_review and result.import_ids:
        result.reviewed_periods = review_company(
            TenantContext.worker(ctx.org_id), company_id, changed_months=affected, ai=ai
        )
    return result


# ---------------------------------------------------------------------------- granskning


def _periods_to_review(analysis: CompanyAnalysis, approved: set[str], changed_months: list[date] | None) -> list[str]:
    latest = analysis.latest_month()
    if latest is None:
        return []
    out = []
    cur = month_start(latest.end)
    # De tre senaste månaderna som inte är godkända granskas om efter varje import.
    for _ in range(3):
        spec = f"{cur:%Y-%m}"
        if spec not in approved and analysis.index.coverage.get(cur) == "vouchers":
            out.append(spec)
        prev = cur - timedelta(days=1)
        cur = date(prev.year, prev.month, 1)
    for mo in changed_months or []:
        spec = f"{mo:%Y-%m}"
        if spec not in approved and spec not in out and analysis.index.coverage.get(mo) == "vouchers":
            out.append(spec)
    return sorted(set(out))


def review_company(
    ctx: TenantContext,
    company_id: uuid.UUID,
    *,
    periods: list[str] | None = None,
    changed_months: list[date] | None = None,
    ai: AIService | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Kör kontroller, uppdatera fynd, minne, ärenden och periodstatus. Returnerar granskade perioder."""
    now = now or datetime.now()
    with tenant_session(ctx) as s:
        company = s.get(m.Company, company_id)
        if company is None:
            return []
        ledger, seqs = repo.load_ledger(s, company)
        if not ledger.years:
            return []
        cctx = repo.company_context(s, company)
        analysis = CompanyAnalysis(ledger, cctx)
        reviews = {
            r.period: r for r in s.scalars(select(m.PeriodReview).where(m.PeriodReview.company_id == company_id)).all()
        }
        approved = {p for p, r in reviews.items() if r.approved_at is not None}
        change_candidates = _detect_changes_after_approval(s, company, reviews, seqs, now)
        todo = periods or _periods_to_review(analysis, approved, changed_months)
        records = repo.load_findings(s, company_id)
        suppressions = repo.load_suppressions(s, company_id)
        resolutions = repo.load_resolutions(s, company_id)
        for spec in todo:
            period = analysis.period(spec)
            extra = [c for c in change_candidates if c.period == spec]
            result = analysis.review(
                period, records, suppressions=suppressions, resolutions=resolutions, extra_candidates=extra, now=now
            )
            records = result.records
            pr = reviews.get(spec)
            if pr is None:
                pr = m.PeriodReview(org_id=ctx.org_id, company_id=company_id, period=spec)
                s.add(pr)
                reviews[spec] = pr
            pr.maturity = result.maturity.to_dict()
            if pr.approved_at is None:
                pr.status = (
                    "PRELIMINARY"
                    if result.maturity.status is PeriodStatus.PRELIMINARY
                    else "NEEDS_REVIEW"
                    if any(r.status.is_open for r in records if spec in r.seen_in_reviews)
                    else "REVIEWED"
                )
            pr.updated_at = now
            _save_cases(s, ctx, company_id, result.cases, analysis, result, ai)
        # Ändringar i godkända perioder som inte granskas om: spara fynden ändå.
        for c in change_candidates:
            if c.period not in todo:
                res = reconcile(records, [c], c.period, now=now)
                records = records + res.created
        repo.save_findings(s, ctx, company_id, records)
        repo.audit(s, ctx, "review.completed", company_id, periods=todo)
        return todo


def _detect_changes_after_approval(
    s: Session,
    company: m.Company,
    reviews: dict[str, m.PeriodReview],
    seqs: dict[str, int],
    now: datetime,
) -> list[FindingCandidate]:
    rd = default_catalog()["CHANGED_AFTER_APPROVAL"]
    out: list[FindingCandidate] = []
    for spec, pr in reviews.items():
        if pr.approved_at is None or not pr.snapshot:
            continue
        snap_seqs: dict[str, int] = pr.snapshot.get("fy_seqs", {})
        p = month(int(spec[:4]), int(spec[5:7]))
        changes: list[dict[str, Any]] = []
        for fy_id, snap_seq in snap_seqs.items():
            cur_seq = seqs.get(fy_id)
            if cur_seq is None or cur_seq == snap_seq:
                continue
            changes += repo.voucher_changes(s, company.id, uuid.UUID(fy_id), snap_seq, cur_seq, p.start, p.end)
        if not changes:
            continue
        if pr.changes != changes:
            pr.changes = changes
            pr.status = "CHANGED_AFTER_APPROVAL"
            pr.updated_at = now
        kinds = {"ADDED": "tillagd", "CHANGED": "ändrad", "REMOVED": "borttagen"}
        desc = "; ".join(f"{c['voucher']} {kinds[c['kind']]} ({c['text']})" for c in changes[:8])
        out.append(
            FindingCandidate(
                rule_code=rd.code,
                rule_version=rd.version,
                severity=Severity.HIGH,
                title=f"{spec} ändrades efter godkännande ({len(changes)} verifikationer)",
                description=f"Perioden godkändes {pr.approved_at:%Y-%m-%d} av {pr.approved_by}. Därefter: {desc}.",
                period=spec,
                key=(spec, tuple(sorted(c["voucher"] + c["kind"] for c in changes))),
                visibility=Visibility.INTERNAL,
                vouchers=[c["voucher"] for c in changes],
                details={"changes": changes},
                legal_basis=rd.legal_basis,
                category=rd.category,
            )
        )
    return out


def _save_cases(
    s: Session,
    ctx: TenantContext,
    company_id: uuid.UUID,
    cases: list[Case],
    analysis: CompanyAnalysis,
    result: Any,
    ai: AIService | None,
) -> None:
    existing = {c.case_key: c for c in s.scalars(select(m.CaseRow).where(m.CaseRow.company_id == company_id)).all()}
    ai_by_ids: dict[frozenset[str], dict[str, Any]] = {}
    if ai is not None and ai.enabled and cases:
        pkg = analysis.case_package(result)
        if pkg["cases"]:
            out = ai.run(
                "A2",
                pkg,
                result.store,
                org_id=str(ctx.org_id),
                company_id=str(company_id),
                names_to_mask=analysis.ctx.person_names,
                allowed_identifiers=analysis.allowed_identifiers(),
            )
            for c in out.data.get("cases", []):
                ai_by_ids[frozenset(c["finding_ids"])] = {**c, "source": out.source, "trace_id": out.trace.id}
    for c in cases:
        row = existing.get(c.key)
        if row is None:
            row = m.CaseRow(org_id=ctx.org_id, company_id=company_id, case_key=c.key)
            s.add(row)
            existing[c.key] = row
        row.period, row.title, row.root_cause = c.period, c.title, c.root_cause
        row.suggested_action, row.ask_client_suggested = c.suggested_action, c.ask_client_suggested
        row.severity, row.visibility, row.memory_hint = c.severity.value, c.visibility.value, c.memory_hint
        enrichment = ai_by_ids.get(frozenset(f.id for f in c.findings))
        if enrichment is not None:
            row.ai = enrichment
        row.updated_at = datetime.now()


def review_all(org_id: uuid.UUID, ai: AIService | None = None) -> dict[str, list[str]]:
    """Nattlig körning för en byrå: granska alla aktiva bolag."""
    ctx = TenantContext.worker(org_id)
    with tenant_session(ctx) as s:
        ids = [c.id for c in s.scalars(select(m.Company).where(m.Company.archived_at.is_(None))).all()]
    return {str(cid): review_company(ctx, cid, ai=ai) for cid in ids}
