"""Persistens: import med verifikationsversionering, laddning av Ledger, fynd, minne m.m."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, insert, or_, select
from sqlalchemy.orm import Session

from redovisningai.accounting.categories import CategoryMapping
from redovisningai.accounting.periods import month_start
from redovisningai.accounting.statements import StatementMapping
from redovisningai.db import models as m
from redovisningai.db.session import TenantContext
from redovisningai.domain.ledger import (
    Account,
    AccountType,
    FiscalYear,
    Ledger,
    Row,
    RowStatus,
    Voucher,
    YearData,
)
from redovisningai.facts.model import Visibility
from redovisningai.findings.lifecycle import FindingRecord, FindingStatus, SuppressionRule
from redovisningai.maturity.assess import AccountingMethod
from redovisningai.memory.resolutions import Resolution
from redovisningai.review.analysis import CompanyContext
from redovisningai.rules.engine import CompanySettings, Severity
from redovisningai.sie.convert import document_years
from redovisningai.sie.parser import PARSER_VERSION, SieDocument

# ---------------------------------------------------------------------------- revisionslogg


def audit(
    session: Session, ctx: TenantContext, action: str, company_id: uuid.UUID | None = None, **details: Any
) -> None:
    # Core-insert utan RETURNING: revisionsloggen kan skrivas även i kontexter som inte får läsa den
    # (t.ex. kundens publika svarssida).
    session.execute(
        insert(m.AuditEvent).values(
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            user_email=ctx.user_email,
            company_id=company_id,
            action=action,
            details=_jsonable(details),
        )
    )


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, uuid.UUID | Decimal | date | datetime):
        return str(obj)
    return obj


# ---------------------------------------------------------------------------- bolag


def company_context(session: Session, company: m.Company) -> CompanyContext:
    stmt_over: dict[int, str] = {}
    cat_over: dict[int, str] = {}
    rows = session.scalars(
        select(m.MappingOverride).where(
            or_(m.MappingOverride.company_id == company.id, m.MappingOverride.company_id.is_(None))
        )
    ).all()
    # Byråns mappning först, kundens överstyrning sist (vinner).
    for r in sorted(rows, key=lambda r: r.company_id is not None):
        (stmt_over if r.kind == "statement" else cat_over)[r.account] = r.target
    params: dict[str, dict[str, Any]] = {}
    for p in sorted(
        session.scalars(
            select(m.RuleParamOverride).where(
                or_(m.RuleParamOverride.company_id == company.id, m.RuleParamOverride.company_id.is_(None))
            )
        ).all(),
        key=lambda r: r.company_id is not None,
    ):
        params.setdefault(p.rule_code, {}).update(p.params)
    aliases = {
        a.key: a.canonical_name
        for a in session.scalars(
            select(m.CounterpartyAlias).where(
                or_(m.CounterpartyAlias.company_id == company.id, m.CounterpartyAlias.company_id.is_(None))
            )
        ).all()
    }
    s = company.settings or {}
    return CompanyContext(
        company_id=str(company.id),
        org_id=str(company.org_id),
        name=company.name,
        settings=CompanySettings(
            legal_form=company.legal_form,
            vat_period=company.vat_period,
            industry=company.industry,
            food_retail=company.food_retail,
            materiality=company.materiality,
            has_overdraft=company.has_overdraft,
            manual_series=tuple(s["manual_series"]) if s.get("manual_series") else None,
            suspense_accounts=tuple(int(a) for a in s.get("suspense_accounts", [])),
        ),
        statement_mapping=StatementMapping(stmt_over, "company" if stmt_over else "system"),
        category_mapping=CategoryMapping(overrides=cat_over),
        method_override=AccountingMethod(company.accounting_method_override)
        if company.accounting_method_override
        else None,
        aliases=aliases,
        person_names=list(s.get("person_names", [])),
        param_overrides=params,
    )


# ---------------------------------------------------------------------------- import


@dataclass(slots=True)
class ImportOutcome:
    import_ids: list[uuid.UUID]
    fiscal_years: list[tuple[date, date]]
    added: int
    changed: int
    removed: int
    unchanged: int
    affected_months: set[date]


def _fiscal_year(session: Session, ctx: TenantContext, company_id: uuid.UUID, fy: FiscalYear) -> m.FiscalYearRow:
    row = session.scalar(
        select(m.FiscalYearRow).where(m.FiscalYearRow.company_id == company_id, m.FiscalYearRow.start_date == fy.start)
    )
    if row is None:
        row = m.FiscalYearRow(org_id=ctx.org_id, company_id=company_id, start_date=fy.start, end_date=fy.end)
        session.add(row)
        session.flush()
    elif row.end_date != fy.end:
        row.end_date = fy.end
    return row


def _next_seq(session: Session, company_id: uuid.UUID) -> int:
    cur = session.scalar(select(func.max(m.ImportRun.seq)).where(m.ImportRun.company_id == company_id))
    return (cur or 0) + 1


def _voucher_key(series: str, number: str, content_hash: str) -> tuple[str, str]:
    return (series, number or f"h:{content_hash[:16]}")


def persist_document(
    session: Session,
    ctx: TenantContext,
    company_id: uuid.UUID,
    doc: SieDocument,
    *,
    source: str,
    source_file_id: uuid.UUID | None,
) -> ImportOutcome:
    """Spara en tolkad SIE-fil. Verifikationer versioneras: bara nya/ändrade sparas."""
    outcome = ImportOutcome([], [], 0, 0, 0, 0, set())
    # Konton (senaste namn vinner)
    existing_accounts = {
        a.number: a for a in session.scalars(select(m.Account).where(m.Account.company_id == company_id)).all()
    }
    for acc in doc.accounts.values():
        row = existing_accounts.get(acc.number)
        if row is None:
            session.add(
                m.Account(
                    org_id=ctx.org_id,
                    company_id=company_id,
                    number=acc.number,
                    name=acc.name,
                    type=acc.type.value if acc.type else None,
                    sru=acc.sru,
                )
            )
        elif acc.name != f"Konto {acc.number}":
            row.name, row.type, row.sru = acc.name, (acc.type.value if acc.type else row.type), acc.sru or row.sru

    for yd in document_years(doc):
        fy_row = _fiscal_year(session, ctx, company_id, yd.fiscal_year)
        if not yd.has_vouchers:
            # Sammandragsår: spara bara om det saknas import med verifikationer för året.
            has_full = session.scalar(
                select(func.count())
                .select_from(m.ImportRun)
                .where(
                    m.ImportRun.fiscal_year_id == fy_row.id,
                    m.ImportRun.has_vouchers.is_(True),
                    m.ImportRun.status == "COMPLETE",
                )
            )
            if has_full:
                continue
        seq = _next_seq(session, company_id)
        run = m.ImportRun(
            org_id=ctx.org_id,
            company_id=company_id,
            fiscal_year_id=fy_row.id,
            source_file_id=source_file_id,
            source=source,
            seq=seq,
            status="PENDING",
            parser_version=PARSER_VERSION,
            has_vouchers=yd.has_vouchers,
            created_by=ctx.user_id,
        )
        session.add(run)
        session.flush()
        outcome.import_ids.append(run.id)
        outcome.fiscal_years.append((yd.fiscal_year.start, yd.fiscal_year.end))
        stats = {"vouchers": len(yd.vouchers), "added": 0, "changed": 0, "removed": 0, "unchanged": 0}
        if yd.has_vouchers:
            current = {
                (v.series, v.number): v
                for v in session.scalars(
                    select(m.VoucherVersion).where(
                        m.VoucherVersion.company_id == company_id,
                        m.VoucherVersion.fiscal_year_id == fy_row.id,
                        m.VoucherVersion.valid_to.is_(None),
                    )
                ).all()
            }
            incoming: dict[tuple[str, str], Voucher] = {}
            for v in yd.vouchers:
                incoming[_voucher_key(v.series, v.number, v.content_hash())] = v
            new_versions: list[tuple[m.VoucherVersion, Voucher]] = []
            for key, v in incoming.items():
                h = v.content_hash()
                cur = current.get(key)
                if cur is not None and cur.content_hash == h:
                    stats["unchanged"] += 1
                    continue
                if cur is not None:
                    cur.valid_to = seq
                    stats["changed"] += 1
                    outcome.affected_months.add(month_start(cur.date))
                else:
                    stats["added"] += 1
                outcome.affected_months.add(month_start(v.date))
                vv = m.VoucherVersion(
                    org_id=ctx.org_id,
                    company_id=company_id,
                    fiscal_year_id=fy_row.id,
                    series=key[0],
                    number=key[1],
                    date=v.date,
                    text=v.text,
                    reg_date=v.reg_date,
                    signature=v.signature,
                    content_hash=h,
                    valid_from=seq,
                    source_line=v.source_line,
                )
                new_versions.append((vv, v))
            for key, cur in current.items():
                if key not in incoming:
                    cur.valid_to = seq
                    stats["removed"] += 1
                    outcome.affected_months.add(month_start(cur.date))
            session.add_all([vv for vv, _ in new_versions])
            session.flush()
            session.add_all(
                [
                    m.TransactionRow(
                        org_id=ctx.org_id,
                        company_id=company_id,
                        voucher_version_id=vv.id,
                        row_no=i,
                        account=r.account,
                        amount=r.amount,
                        trans_date=r.trans_date,
                        text=r.text,
                        quantity=r.quantity,
                        objects=[list(o) for o in r.objects],
                        status=r.status.value,
                        source_line=r.source_line,
                    )
                    for vv, v in new_versions
                    for i, r in enumerate(v.rows)
                ]
            )
        for kind, values in (("IB", yd.opening), ("UB", yd.closing), ("RES", yd.result)):
            session.add_all(
                [
                    m.YearBalance(
                        org_id=ctx.org_id, company_id=company_id, import_id=run.id, kind=kind, account=a, amount=x
                    )
                    for a, x in values.items()
                ]
            )
        for kind, values2 in (("PSALDO", yd.period_balances), ("PBUDGET", yd.budget)):
            session.add_all(
                [
                    m.PeriodAmount(
                        org_id=ctx.org_id,
                        company_id=company_id,
                        import_id=run.id,
                        kind=kind,
                        period=p,
                        account=a,
                        amount=x,
                    )
                    for (p, a), x in values2.items()
                ]
            )
        session.flush()
        materialize_balances(session, ctx, company_id, run, fy_row)
        run.status = "COMPLETE"
        run.completed_at = datetime.now()
        run.stats = stats
        for k in ("added", "changed", "removed", "unchanged"):
            setattr(outcome, k, getattr(outcome, k) + stats[k])
    return outcome


def materialize_balances(
    session: Session, ctx: TenantContext, company_id: uuid.UUID, run: m.ImportRun, fy_row: m.FiscalYearRow
) -> None:
    """Månadsrörelser per konto för importens giltiga verifikationer (aggregat)."""
    seq = run.seq
    q = (
        select(
            func.date_trunc("month", m.VoucherVersion.date).label("period"),
            m.TransactionRow.account,
            func.sum(m.TransactionRow.amount),
        )
        .join(m.TransactionRow, m.TransactionRow.voucher_version_id == m.VoucherVersion.id)
        .where(
            m.VoucherVersion.company_id == company_id,
            m.VoucherVersion.fiscal_year_id == fy_row.id,
            m.VoucherVersion.valid_from <= seq,
            or_(m.VoucherVersion.valid_to.is_(None), m.VoucherVersion.valid_to > seq),
            m.TransactionRow.status != RowStatus.REMOVED.value,
        )
        .group_by("period", m.TransactionRow.account)
    )
    session.add_all(
        [
            m.AccountPeriodBalance(
                org_id=ctx.org_id,
                company_id=company_id,
                import_id=run.id,
                period=p.date() if isinstance(p, datetime) else p,
                account=a,
                amount=amt,
            )
            for p, a, amt in session.execute(q).all()
            if amt != 0
        ]
    )


# ---------------------------------------------------------------------------- laddning


def latest_imports(session: Session, company_id: uuid.UUID) -> dict[uuid.UUID, m.ImportRun]:
    """Senaste slutförda import per räkenskapsår (import med verifikationer går före sammandrag)."""
    runs = session.scalars(
        select(m.ImportRun)
        .where(m.ImportRun.company_id == company_id, m.ImportRun.status == "COMPLETE")
        .order_by(m.ImportRun.seq)
    ).all()
    out: dict[uuid.UUID, m.ImportRun] = {}
    for r in runs:
        prev = out.get(r.fiscal_year_id)
        if prev is None or (r.has_vouchers or not prev.has_vouchers):
            out[r.fiscal_year_id] = r
    return out


def load_ledger(
    session: Session,
    company: m.Company,
    *,
    as_of: dict[str, int] | None = None,
) -> tuple[Ledger, dict[str, int]]:
    """Ladda bolagets bokföring. `as_of` = {fiscal_year_id: import_seq} för reproducerbara snapshots."""
    fys = {
        f.id: f for f in session.scalars(select(m.FiscalYearRow).where(m.FiscalYearRow.company_id == company.id)).all()
    }
    runs = latest_imports(session, company.id)
    if as_of:
        for fy_id_str, seq in as_of.items():
            r = session.scalar(select(m.ImportRun).where(m.ImportRun.company_id == company.id, m.ImportRun.seq == seq))
            if r is not None:
                runs[uuid.UUID(fy_id_str)] = r
    ledger = Ledger(company_name=company.name, org_number=company.org_number)
    for a in session.scalars(select(m.Account).where(m.Account.company_id == company.id)).all():
        ledger.accounts[a.number] = Account(a.number, a.name, AccountType(a.type) if a.type else None, a.sru)
    seqs: dict[str, int] = {}
    for fy_id, run in runs.items():
        fy = fys[fy_id]
        seqs[str(fy_id)] = run.seq
        yd = YearData(
            fiscal_year=FiscalYear(fy.start_date, fy.end_date), source_ref=str(run.id), has_vouchers=run.has_vouchers
        )
        for b in session.scalars(select(m.YearBalance).where(m.YearBalance.import_id == run.id)).all():
            {"IB": yd.opening, "UB": yd.closing, "RES": yd.result}[b.kind][b.account] = b.amount
        for pa in session.scalars(select(m.PeriodAmount).where(m.PeriodAmount.import_id == run.id)).all():
            (yd.period_balances if pa.kind == "PSALDO" else yd.budget)[(pa.period, pa.account)] = pa.amount
        if run.has_vouchers:
            yd.vouchers = _load_vouchers(session, company.id, fy_id, run.seq)
        ledger.years.append(yd)
    ledger.sort()
    return ledger, seqs


def _load_vouchers(session: Session, company_id: uuid.UUID, fy_id: uuid.UUID, seq: int) -> list[Voucher]:
    vv_rows = session.scalars(
        select(m.VoucherVersion).where(
            m.VoucherVersion.company_id == company_id,
            m.VoucherVersion.fiscal_year_id == fy_id,
            m.VoucherVersion.valid_from <= seq,
            or_(m.VoucherVersion.valid_to.is_(None), m.VoucherVersion.valid_to > seq),
        )
    ).all()
    ids = [v.id for v in vv_rows]
    rows_by: dict[int, list[m.TransactionRow]] = defaultdict(list)
    for i in range(0, len(ids), 5000):
        for r in session.scalars(
            select(m.TransactionRow).where(m.TransactionRow.voucher_version_id.in_(ids[i : i + 5000]))
        ).all():
            rows_by[r.voucher_version_id].append(r)
    out = []
    for v in vv_rows:
        number = "" if v.number.startswith("h:") else v.number
        rows = tuple(
            Row(
                account=r.account,
                amount=r.amount,
                trans_date=r.trans_date,
                text=r.text,
                quantity=r.quantity,
                objects=tuple(tuple(o) for o in r.objects),
                status=RowStatus(r.status),  # type: ignore[misc]
                source_line=r.source_line,
            )
            for r in sorted(rows_by[v.id], key=lambda r: r.row_no)
        )
        out.append(
            Voucher(
                series=v.series,
                number=number,
                date=v.date,
                text=v.text,
                rows=rows,
                reg_date=v.reg_date,
                signature=v.signature,
                source_line=v.source_line,
            )
        )
    out.sort(key=lambda v: (v.date, v.series, int(v.number) if v.number.isdigit() else 0, v.number))
    return out


def voucher_changes(
    session: Session,
    company_id: uuid.UUID,
    fy_id: uuid.UUID,
    from_seq: int,
    to_seq: int,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Verifikationer i perioden som lagts till, ändrats eller tagits bort mellan två importer."""

    def valid_at(seq: int) -> Any:
        return and_(
            m.VoucherVersion.valid_from <= seq,
            or_(m.VoucherVersion.valid_to.is_(None), m.VoucherVersion.valid_to > seq),
        )

    base = select(m.VoucherVersion).where(
        m.VoucherVersion.company_id == company_id, m.VoucherVersion.fiscal_year_id == fy_id
    )
    before = {(v.series, v.number): v for v in session.scalars(base.where(valid_at(from_seq))).all()}
    after = {(v.series, v.number): v for v in session.scalars(base.where(valid_at(to_seq))).all()}
    changes = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        in_period = any(x is not None and start <= x.date <= end for x in (a, b))
        if not in_period:
            continue
        kind = None
        if b is None:
            kind = "ADDED"
        elif a is None:
            kind = "REMOVED"
        elif a.content_hash != b.content_hash:
            kind = "CHANGED"
        if kind:
            v = a or b
            assert v is not None
            changes.append(
                {
                    "voucher": f"{v.series}{v.number}",
                    "kind": kind,
                    "date": v.date.isoformat(),
                    "text": v.text,
                    "reg_date": v.reg_date.isoformat() if v.reg_date else None,
                }
            )
    return changes


# ---------------------------------------------------------------------------- fynd och minne


def load_findings(session: Session, company_id: uuid.UUID) -> list[FindingRecord]:
    out = []
    for r in session.scalars(select(m.FindingRow).where(m.FindingRow.company_id == company_id)).all():
        out.append(
            FindingRecord(
                id=str(r.id),
                fingerprint=r.fingerprint,
                rule_code=r.rule_code,
                rule_version=r.rule_version,
                severity=Severity(r.severity),
                title=r.title,
                description=r.description,
                period=r.period,
                visibility=Visibility(r.visibility),
                status=FindingStatus(r.status),
                vouchers=list(r.vouchers),
                accounts=[int(a) for a in r.accounts],
                amount=r.amount,
                facts=list(r.facts),
                details=dict(r.details),
                legal_basis=r.legal_basis or "",
                category=r.category or "",
                seen_in_reviews=set(r.seen_in_reviews),
                first_seen=r.first_seen,
                last_seen=r.last_seen,
                resolution_note=r.resolution_note,
                resolved_by=r.resolved_by,
                resolved_at=r.resolved_at,
                suppression_id=r.suppression_id,
                memory_suggestion=r.memory_suggestion,
                case_key=r.case_key,
            )
        )
    return out


def save_findings(session: Session, ctx: TenantContext, company_id: uuid.UUID, records: list[FindingRecord]) -> None:
    existing = {
        r.fingerprint: r
        for r in session.scalars(select(m.FindingRow).where(m.FindingRow.company_id == company_id)).all()
    }
    for rec in records:
        row = existing.get(rec.fingerprint)
        if row is None:
            row = m.FindingRow(
                id=uuid.UUID(rec.id), org_id=ctx.org_id, company_id=company_id, fingerprint=rec.fingerprint
            )
            session.add(row)
        row.rule_code, row.rule_version = rec.rule_code, rec.rule_version
        row.severity, row.title, row.description = rec.severity.value, rec.title, rec.description
        row.period, row.visibility, row.status = rec.period, rec.visibility.value, rec.status.value
        row.category, row.legal_basis = rec.category, rec.legal_basis
        row.vouchers, row.accounts, row.amount = rec.vouchers, rec.accounts, rec.amount
        row.facts, row.details = rec.facts, _jsonable(rec.details)
        row.seen_in_reviews = sorted(rec.seen_in_reviews)
        row.first_seen, row.last_seen = rec.first_seen, rec.last_seen
        row.resolution_note, row.resolved_by, row.resolved_at = rec.resolution_note, rec.resolved_by, rec.resolved_at
        row.suppression_id, row.memory_suggestion, row.case_key = (
            rec.suppression_id,
            rec.memory_suggestion,
            rec.case_key,
        )


def load_suppressions(session: Session, company_id: uuid.UUID) -> list[SuppressionRule]:
    return [
        SuppressionRule(
            id=str(s.id),
            rule_code=s.rule_code,
            reason=s.reason,
            created_by=s.created_by,
            expires_at=s.expires_at,
            company_id=str(s.company_id) if s.company_id else None,
            accounts=[int(a) for a in s.accounts],
            text_contains=s.text_contains,
            max_amount=s.max_amount,
        )
        for s in session.scalars(
            select(m.SuppressionRuleRow).where(
                or_(m.SuppressionRuleRow.company_id == company_id, m.SuppressionRuleRow.company_id.is_(None))
            )
        ).all()
    ]


def load_resolutions(session: Session, company_id: uuid.UUID) -> list[Resolution]:
    return [
        Resolution(
            id=str(r.id),
            company_id=str(r.company_id),
            signature_exact=r.signature_exact,
            signature_loose=r.signature_loose,
            description=r.description,
            rule_code=r.rule_code,
            decision=FindingStatus(r.decision),
            rationale=r.rationale,
            decided_by=r.decided_by,
            decided_at=r.decided_at,
            valid_until=r.valid_until,
            times_reused=r.times_reused,
            example_finding_id=r.example_finding_id,
            example_title=r.example_title,
        )
        for r in session.scalars(select(m.ResolutionRow).where(m.ResolutionRow.company_id == company_id)).all()
    ]


def save_resolution(session: Session, ctx: TenantContext, res: Resolution) -> None:
    session.add(
        m.ResolutionRow(
            id=uuid.UUID(res.id),
            org_id=ctx.org_id,
            company_id=uuid.UUID(res.company_id),
            signature_exact=res.signature_exact,
            signature_loose=res.signature_loose,
            description=res.description,
            rule_code=res.rule_code,
            decision=res.decision.value,
            rationale=res.rationale,
            decided_by=res.decided_by,
            decided_at=res.decided_at,
            valid_until=res.valid_until,
            example_finding_id=res.example_finding_id,
            example_title=res.example_title,
        )
    )
