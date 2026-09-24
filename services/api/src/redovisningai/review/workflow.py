"""Konsultens arbetsflöde: beslut på fynd och ärenden, godkännande av period, kundfrågor."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from redovisningai.config import get_settings
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.session import TenantContext
from redovisningai.facts.model import CALC_VERSION
from redovisningai.findings.lifecycle import FindingStatus, decide
from redovisningai.memory.resolutions import remember
from redovisningai.rules.engine import default_catalog


class WorkflowError(Exception):
    pass


# ---------------------------------------------------------------------------- beslut


def decide_findings(
    s: Session,
    ctx: TenantContext,
    company: m.Company,
    finding_ids: list[uuid.UUID],
    status: FindingStatus,
    note: str | None,
    *,
    remember_decision: bool = True,
    remember_until: date | None = None,
) -> int:
    """Fatta beslut på ett eller flera fynd (t.ex. alla i ett ärende). Sparas i kundminnet."""
    if ctx.role == "VIEWER":
        raise WorkflowError("Läsbehörighet kan inte fatta beslut.")
    records = {r.id: r for r in repo.load_findings(s, company.id)}
    ledger = None
    changed = []
    for fid in finding_ids:
        rec = records.get(str(fid))
        if rec is None:
            raise WorkflowError(f"Fyndet {fid} finns inte.")
        decide(rec, status, ctx.user_email or str(ctx.user_id), note)
        changed.append(rec)
    repo.save_findings(s, ctx, company.id, changed)
    if remember_decision and status in (FindingStatus.ACCEPTED_OK, FindingStatus.RESOLVED):
        if ledger is None:
            ledger, _ = repo.load_ledger(s, company)
        for rec in changed:
            res = remember(rec, str(company.id), ledger, valid_until=remember_until)
            if res is not None:
                repo.save_resolution(s, ctx, res)
    repo.audit(
        s, ctx, "finding.decided", company.id, findings=[str(f) for f in finding_ids], status=status.value, note=note
    )
    return len(changed)


# ---------------------------------------------------------------------------- godkännande


def approve_period(
    s: Session,
    ctx: TenantContext,
    company: m.Company,
    period: str,
    *,
    override_note: str | None = None,
) -> m.PeriodReview:
    if ctx.role == "VIEWER":
        raise WorkflowError("Läsbehörighet kan inte godkänna perioder.")
    pr = s.scalar(
        select(m.PeriodReview).where(m.PeriodReview.company_id == company.id, m.PeriodReview.period == period)
    )
    if pr is None:
        raise WorkflowError("Perioden har inte granskats ännu.")
    records = [r for r in repo.load_findings(s, company.id) if period in r.seen_in_reviews or r.period == period]
    open_high = [r for r in records if r.status.is_open and r.severity.value == "HIGH"]
    if (open_high or pr.status == "PRELIMINARY") and not (override_note and override_note.strip()):
        reasons = []
        if open_high:
            reasons.append(f"{len(open_high)} öppna High-fynd")
        if pr.status == "PRELIMINARY":
            reasons.append("perioden är preliminär")
        raise WorkflowError("Kan inte godkänna: " + ", ".join(reasons) + ". Ange motivering för att överstyra.")
    _, seqs = repo.load_ledger(s, company)
    catalog = default_catalog()
    ctx_company = repo.company_context(s, company)
    pr.snapshot = {
        "fy_seqs": seqs,
        "rule_versions": {c: r.version for c, r in catalog.items()},
        "mapping_version": ctx_company.statement_mapping.version,
        "category_version": ctx_company.category_mapping.version,
        "calc_version": CALC_VERSION,
        "findings": [{"fingerprint": r.fingerprint, "status": r.status.value, "rule": r.rule_code} for r in records],
        "override_note": override_note,
    }
    pr.status = "APPROVED"
    pr.approved_by = ctx.user_email or str(ctx.user_id)
    pr.approved_at = datetime.now()
    pr.changes = []
    # Ändrad-efter-godkännande-fynd för perioden är nu hanterade.
    for r in records:
        if r.rule_code == "CHANGED_AFTER_APPROVAL" and r.status.is_open:
            decide(r, FindingStatus.RESOLVED, pr.approved_by, "Ändringarna granskade och perioden godkänd på nytt.")
    repo.save_findings(s, ctx, company.id, records)
    repo.audit(s, ctx, "period.approved", company.id, period=period, override_note=override_note, snapshot_seqs=seqs)
    return pr


# ---------------------------------------------------------------------------- kundfrågor

ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(slots=True)
class CreatedQuestion:
    question: m.ClientQuestion
    token: str
    link: str


def create_question(
    s: Session,
    ctx: TenantContext,
    company: m.Company,
    case_key: str,
    text: str,
    *,
    recipient_email: str | None = None,
) -> CreatedQuestion:
    if ctx.role == "VIEWER":
        raise WorkflowError("Läsbehörighet kan inte skicka frågor.")
    if not text.strip():
        raise WorkflowError("Frågan är tom.")
    case = s.scalar(select(m.CaseRow).where(m.CaseRow.company_id == company.id, m.CaseRow.case_key == case_key))
    if case is None:
        raise WorkflowError("Ärendet finns inte.")
    findings = [r for r in repo.load_findings(s, company.id) if r.case_key == case_key]
    # Meddelandeförbud (PTL): frågor om PTL-signaler får aldrig gå till kunden.
    if case.visibility == "RESTRICTED_AML" or any(r.visibility.value == "RESTRICTED_AML" for r in findings):
        raise WorkflowError("Frågor om PTL-signaler kan inte skickas till kunden.")
    token = secrets.token_urlsafe(32)
    org = s.get(m.Organization, ctx.org_id)
    q = m.ClientQuestion(
        org_id=ctx.org_id,
        company_id=company.id,
        case_key=case_key,
        finding_ids=[r.id for r in findings],
        text=text.strip(),
        token_hash=token_hash(token),
        status="SENT",
        recipient_email=recipient_email,
        display={"company_name": company.name, "firm_name": org.name if org else "", "case_title": case.title},
        sent_by=ctx.user_email or str(ctx.user_id),
        expires_at=datetime.now().astimezone() + timedelta(days=get_settings().question_link_days),
    )
    s.add(q)
    for r in findings:
        if r.status.is_open:
            decide(r, FindingStatus.ASK_CLIENT, q.sent_by, "Fråga skickad till kunden.")
    repo.save_findings(s, ctx, company.id, findings)
    s.flush()
    repo.audit(s, ctx, "question.sent", company.id, question_id=str(q.id), case_key=case_key, recipient=recipient_email)
    link = f"{get_settings().web_base_url}/q/{token}"
    return CreatedQuestion(q, token, link)


def public_view(q: m.ClientQuestion) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "text": q.text,
        "status": q.status,
        "company_name": q.display.get("company_name"),
        "firm_name": q.display.get("firm_name"),
        "sent_at": q.sent_at.isoformat(),
        "expires_at": q.expires_at.isoformat(),
        "answer_text": q.answer_text,
        "attachments": [{"name": a["name"], "size": a["size"]} for a in q.attachments],
    }


def answer_question(
    s: Session, ctx: TenantContext, q: m.ClientQuestion, answer: str, attachments: list[dict[str, Any]]
) -> None:
    if ctx.role != "PUBLIC_QUESTION":
        raise WorkflowError("Fel kontext")
    if q.status not in ("SENT", "ANSWERED"):
        raise WorkflowError("Frågan är stängd.")
    q.answer_text = answer.strip()[:10000]
    q.attachments = list(q.attachments) + attachments
    q.status = "ANSWERED"
    q.answered_at = datetime.now()
    repo.audit(
        s, ctx, "question.answered", q.company_id, question_id=str(q.id), attachments=[a["name"] for a in attachments]
    )
