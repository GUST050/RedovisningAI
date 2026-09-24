"""Kundens arbetsyta: import, kopplingar, analys, transaktioner, förklara, spend, budget."""

from __future__ import annotations

import hashlib
import hmac
import io
import uuid
import zipfile
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from redovisningai.accounting.periods import same_period_previous_year
from redovisningai.analytics.budget import budget_vs_actual
from redovisningai.analytics.spend import spend_report
from redovisningai.analytics.tax_account import parse_tax_account_csv, reconcile_tax_account
from redovisningai.api.deps import (
    Principal,
    db,
    get_company,
    get_principal,
    invalidate_cache,
    load_analysis,
    require_write,
)
from redovisningai.config import get_settings
from redovisningai.connectors.fortnox import FortnoxApp
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.jobs.pipeline import ImportError_, import_sie
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis, voucher_view
from redovisningai.sie.parser import parse_sie

router = APIRouter(prefix="/api/companies/{company_id}", tags=["kund"])


def _ai(principal: Principal):  # type: ignore[no-untyped-def]
    from redovisningai.ai.factory import build_ai_service

    return build_ai_service(principal.org_id)


def _mask_payroll(obj: Any, principal: Principal) -> Any:
    """Ta bort radnivå för lönekonton för användare utan behörigheten Lönedata."""
    if principal.can_payroll:
        return obj
    if isinstance(obj, dict):
        if (
            "account" in obj
            and isinstance(obj.get("account"), int)
            and obj["account"] in PAYROLL
            and ("voucher" in obj or "text" in obj)
        ):
            return None
        out = {}
        for k, v in obj.items():
            mv = _mask_payroll(v, principal)
            if mv is not None or v is None:
                out[k] = mv
        return out
    if isinstance(obj, list):
        return [x for x in (_mask_payroll(v, principal) for v in obj) if x is not None]
    return obj


# ---------------------------------------------------------------------------- import


@router.post("/imports")
async def upload(
    company_id: uuid.UUID, file: UploadFile = File(...), principal: Principal = Depends(require_write)
) -> dict[str, Any]:
    raw = await file.read()
    try:
        res = import_sie(principal.ctx, company_id, file.filename or "fil.se", raw, ai=_ai(principal))
    except ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc
    invalidate_cache(company_id)
    return {
        "source_file_id": str(res.source_file_id) if res.source_file_id else None,
        "imports": [str(i) for i in res.import_ids],
        "duplicate": res.skipped_duplicate,
        "stats": res.stats,
        "issues": res.issues,
        "reviewed_periods": res.reviewed_periods,
    }


@router.get("/imports")
def list_imports(company_id: uuid.UUID, s: Session = Depends(db)) -> list[dict[str, Any]]:
    get_company(s, company_id)
    runs = s.execute(
        select(m.ImportRun, m.FiscalYearRow, m.SourceFile)
        .join(m.FiscalYearRow, m.FiscalYearRow.id == m.ImportRun.fiscal_year_id)
        .outerjoin(m.SourceFile, m.SourceFile.id == m.ImportRun.source_file_id)
        .where(m.ImportRun.company_id == company_id)
        .order_by(m.ImportRun.seq.desc())
    ).all()
    return [
        {
            "id": str(r.id),
            "seq": r.seq,
            "source": r.source,
            "status": r.status,
            "has_vouchers": r.has_vouchers,
            "fiscal_year": {"start": fy.start_date.isoformat(), "end": fy.end_date.isoformat()},
            "stats": r.stats,
            "created_at": r.created_at.isoformat(),
            "file": None
            if sf is None
            else {
                "name": sf.filename,
                "sha256": sf.sha256,
                "size": sf.size_bytes,
                "encoding": sf.encoding,
                "issues": sf.parse_issues,
            },
        }
        for r, fy, sf in runs
    ]


bulk_router = APIRouter(prefix="/api", tags=["kund"])


@bulk_router.post("/imports/bulk")
async def bulk_upload(
    file: UploadFile = File(...), principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    """Zip med många SIE-filer: varje fil kopplas till kund via organisationsnumret i filen."""
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise HTTPException(422, "Filen är inte en zip-fil") from exc
    companies = {
        "".join(ch for ch in (c.org_number or "") if ch.isdigit()): c.id for c in s.scalars(select(m.Company)).all()
    }
    results = []
    for info in zf.infolist():
        if info.is_dir() or info.file_size > 200 * 1024 * 1024:
            continue
        content = zf.read(info)
        try:
            doc = parse_sie(content)
        except Exception as exc:
            results.append({"file": info.filename, "status": "error", "error": str(exc)})
            continue
        cid = companies.get("".join(ch for ch in (doc.org_number or "") if ch.isdigit()))
        if cid is None:
            results.append(
                {
                    "file": info.filename,
                    "status": "no_match",
                    "org_number": doc.org_number,
                    "company_name": doc.company_name,
                }
            )
            continue
        try:
            res = import_sie(principal.ctx, cid, info.filename, content)
            invalidate_cache(cid)
            results.append(
                {
                    "file": info.filename,
                    "status": "duplicate" if res.skipped_duplicate else "imported",
                    "company_id": str(cid),
                    "stats": res.stats,
                }
            )
        except ImportError_ as exc:
            results.append({"file": info.filename, "status": "error", "error": str(exc)})
    return {"results": results}


# ---------------------------------------------------------------------------- kopplingar


def _state(company_id: uuid.UUID, org_id: uuid.UUID) -> str:
    raw = f"{company_id}:{org_id}"
    sig = hmac.new(get_settings().master_key.encode(), raw.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{raw}:{sig}"


def _fortnox_app() -> FortnoxApp:
    s = get_settings()
    if not (s.fortnox_client_id and s.fortnox_client_secret):
        raise HTTPException(503, "Fortnox-integrationen är inte konfigurerad (RAI_FORTNOX_CLIENT_ID/SECRET).")
    return FortnoxApp(
        s.fortnox_client_id, s.fortnox_client_secret, s.fortnox_redirect_uri, s.fortnox_api_base, s.fortnox_auth_base
    )


@router.get("/connections")
def connections(company_id: uuid.UUID, s: Session = Depends(db)) -> list[dict[str, Any]]:
    get_company(s, company_id)
    return [
        {
            "id": str(c.id),
            "source": c.source,
            "status": c.status,
            "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
            "last_error": c.last_error,
            "authorized_at": c.authorized_at.isoformat() if c.authorized_at else None,
        }
        for c in s.scalars(select(m.Connection).where(m.Connection.company_id == company_id)).all()
    ]


@router.post("/connections/fortnox/authorize")
def fortnox_authorize(
    company_id: uuid.UUID, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, str]:
    get_company(s, company_id)
    return {"url": _fortnox_app().authorize_url(_state(company_id, principal.org_id))}


callback_router = APIRouter(prefix="/api/connections", tags=["kund"])


@callback_router.get("/fortnox/callback")
def fortnox_callback(code: str, state: str, principal: Principal = Depends(require_write)) -> RedirectResponse:
    try:
        company_s, org_s, _sig = state.split(":")
        company_id, org_id = uuid.UUID(company_s), uuid.UUID(org_s)
    except ValueError as exc:
        raise HTTPException(400, "Ogiltig state") from exc
    if not hmac.compare_digest(state, _state(company_id, org_id)) or org_id != principal.org_id:
        raise HTTPException(400, "Ogiltig state")
    token = _fortnox_app().exchange_code(code)
    from redovisningai.db.session import tenant_session

    with tenant_session(principal.ctx) as s:
        get_company(s, company_id)
        conn = s.scalar(
            select(m.Connection).where(m.Connection.company_id == company_id, m.Connection.source == "fortnox")
        )
        if conn is None:
            conn = m.Connection(org_id=org_id, company_id=company_id, source="fortnox")
            s.add(conn)
        conn.tenant_ref = token.get("tenant_id")
        conn.status = "OK" if conn.tenant_ref else "ERROR"
        conn.last_error = None if conn.tenant_ref else "Tenant-id saknas i token"
        conn.authorized_by, conn.authorized_at = principal.user_id, datetime.now().astimezone()
        conn.cost_model = "marketplace"
        repo.audit(s, principal.ctx, "connection.authorized", company_id, source="fortnox")
    return RedirectResponse(f"{get_settings().web_base_url}/clients/{company_id}?tab=data")


@router.post("/sync")
def sync_now(company_id: uuid.UUID, principal: Principal = Depends(require_write)) -> dict[str, Any]:
    from redovisningai.connectors.sync import sync_company

    res = sync_company(principal.org_id, company_id)
    invalidate_cache(company_id)
    return res


# ---------------------------------------------------------------------------- analys


def _period(analysis: CompanyAnalysis, spec: str | None):  # type: ignore[no-untyped-def]
    try:
        return analysis.period(spec)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/overview")
def overview(
    company_id: uuid.UUID, period: str | None = None, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    return a.overview(_period(a, period))


@router.get("/statements")
def statements(
    company_id: uuid.UUID,
    period: str | None = None,
    compare: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    p = _period(a, period)
    c = _period(a, compare) if compare else same_period_previous_year(p, a.ledger)
    return a.statements(p, c)


@router.get("/cost-tree")
def cost_tree(
    company_id: uuid.UUID,
    period: str | None = None,
    compare: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    p = _period(a, period)
    c = _period(a, compare) if compare else same_period_previous_year(p, a.ledger)
    return a.cost_tree(p, c)


@router.get("/trend")
def trend(
    company_id: uuid.UUID, months: int = Query(default=24, ge=3, le=60), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    return load_analysis(principal, company_id).trend(months)


@router.get("/explain")
def explain(
    company_id: uuid.UUID,
    target: str = "operating_result",
    period: str | None = None,
    compare: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    p = _period(a, period)
    c = _period(a, compare) if compare else None
    try:
        res = a.explain(target, p, c)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"Okänt förklaringsmål: {target}") from exc
    return _mask_payroll(res, principal)  # type: ignore[no-any-return]


@router.get("/maturity")
def maturity(
    company_id: uuid.UUID, period: str | None = None, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    return a.maturity(_period(a, period)).to_dict()


@router.get("/spend")
def spend(
    company_id: uuid.UUID,
    period: str | None = None,
    compare: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    p = _period(a, period or (f"YTD:{a.latest_month().end:%Y-%m}" if a.latest_month() else None))
    c = _period(a, compare) if compare else same_period_previous_year(p, a.ledger)
    return spend_report(a.index, p, c, aliases=a.ctx.aliases).to_dict()


@router.get("/budget")
def budget(
    company_id: uuid.UUID, period: str | None = None, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    p = _period(a, period)
    lines = budget_vs_actual(a.index, p, a.ctx.statement_mapping)
    return {"period": p.spec, "has_budget": bool(lines), "lines": [ln.to_dict() for ln in lines]}


# ---------------------------------------------------------------------------- transaktioner (RLS i användarkontext)


@router.get("/transactions")
def transactions(
    company_id: uuid.UUID,
    account: int | None = None,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    s: Session = Depends(db),
) -> dict[str, Any]:
    get_company(s, company_id)
    latest = repo.latest_imports(s, company_id)
    seq_by_fy = {fy: r.seq for fy, r in latest.items()}
    conds = [m.VoucherVersion.company_id == company_id]
    fy_conds = [
        (m.VoucherVersion.fiscal_year_id == fy)
        & (m.VoucherVersion.valid_from <= seq)
        & (or_(m.VoucherVersion.valid_to.is_(None), m.VoucherVersion.valid_to > seq))
        for fy, seq in seq_by_fy.items()
    ]
    if not fy_conds:
        return {"rows": [], "total": 0, "page": page, "page_size": page_size}
    conds.append(or_(*fy_conds))
    if account is not None:
        conds.append(m.TransactionRow.account == account)
    if date_from:
        conds.append(m.VoucherVersion.date >= date_from)
    if date_to:
        conds.append(m.VoucherVersion.date <= date_to)
    if q:
        like = f"%{q}%"
        conds.append(or_(m.VoucherVersion.text.ilike(like), m.TransactionRow.text.ilike(like)))
    base = (
        select(m.TransactionRow, m.VoucherVersion)
        .join(m.VoucherVersion, m.VoucherVersion.id == m.TransactionRow.voucher_version_id)
        .where(*conds, m.TransactionRow.status != "removed")
    )
    total = s.scalar(select(func.count()).select_from(base.subquery()))
    rows = s.execute(
        base.order_by(m.VoucherVersion.date.desc(), m.VoucherVersion.id.desc(), m.TransactionRow.row_no)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    names = {a.number: a.name for a in s.scalars(select(m.Account).where(m.Account.company_id == company_id)).all()}
    return {
        "rows": [
            {
                "voucher": f"{v.series}{v.number}",
                "date": v.date.isoformat(),
                "voucher_text": v.text,
                "account": r.account,
                "account_name": names.get(r.account, ""),
                "amount": str(r.amount),
                "text": r.text,
                "status": r.status,
                "source_line": r.source_line,
            }
            for r, v in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/vouchers/{key}")
def voucher(
    company_id: uuid.UUID,
    key: str,
    period: str | None = None,
    on: date | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Verifikationsnummer börjar om varje räkenskapsår. `on` (datum) eller `period` avgör året;
    utan dem används det senaste året där numret finns."""
    a = load_analysis(principal, company_id)
    anchor = on
    if anchor is None and period:
        anchor = _period(a, period).end
    years = list(reversed(a.ledger.years))
    if anchor is not None:
        fy = a.ledger.year_for(anchor)
        years = ([fy] if fy else []) + [y for y in years if y is not fy]
    for y in years:
        for v in y.vouchers:
            if str(v.key) == key:
                return voucher_view(v, a.ledger, include_payroll_rows=principal.can_payroll)
    raise HTTPException(404, "Verifikationen finns inte")


@router.get("/accounts")
def accounts(company_id: uuid.UUID, s: Session = Depends(db)) -> list[dict[str, Any]]:
    get_company(s, company_id)
    return [
        {"number": a.number, "name": a.name, "type": a.type}
        for a in s.scalars(select(m.Account).where(m.Account.company_id == company_id).order_by(m.Account.number)).all()
    ]


# ---------------------------------------------------------------------------- skattekonto (V1.5)


@router.post("/tax-account")
async def tax_account(
    company_id: uuid.UUID,
    file: UploadFile = File(...),
    opening_balance: Decimal = Decimal(0),
    principal: Principal = Depends(require_write),
) -> dict[str, Any]:
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        tx = parse_tax_account_csv(content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    a = load_analysis(principal, company_id)
    lm = a.latest_month()
    if lm is None:
        raise HTTPException(422, "Bokföring saknas")
    rec = reconcile_tax_account(a.index, tx, lm.end, opening_skv_balance=opening_balance)
    from redovisningai.db.session import tenant_session

    with tenant_session(principal.ctx) as s:
        s.add(
            m.TaxAccountImport(
                org_id=principal.org_id,
                company_id=company_id,
                source="file",
                transactions=[{"date": t.date.isoformat(), "text": t.text, "amount": str(t.amount)} for t in tx],
                opening_balance=opening_balance,
                imported_by=principal.email,
            )
        )
        repo.audit(s, principal.ctx, "tax_account.imported", company_id, transactions=len(tx))
    return rec.to_dict()


# ---------------------------------------------------------------------------- mappning (A1)


@router.get("/mapping/suggestions")
def mapping_suggestions(company_id: uuid.UUID, principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    from redovisningai.facts.model import FactStore

    a = load_analysis(principal, company_id)
    unknown = []
    for acc in sorted(a.index.accounts_used):
        name = a.ledger.account_name(acc)
        if name.startswith("Konto ") or acc not in a.ledger.accounts:
            examples = [v.text for v in a.ledger.all_vouchers() if any(r.account == acc for r in v.rows)][:3]
            unknown.append({"account": acc, "name": name, "examples": examples})
    if not unknown:
        return {"suggestions": [], "source": "rules"}
    out = _ai(principal).run(
        "A1",
        {"accounts": unknown},
        FactStore(),
        org_id=str(principal.org_id),
        company_id=str(company_id),
        names_to_mask=a.ctx.person_names,
    )
    return {**out.data, "source": out.source}


@router.post("/mapping")
def set_mapping(
    company_id: uuid.UUID, body: dict[str, Any], principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    get_company(s, company_id)
    kind, account, target = body.get("kind"), int(body.get("account", 0)), str(body.get("target", ""))
    if kind not in ("statement", "category") or not account or not target:
        raise HTTPException(422, "kind, account och target krävs")
    row = s.scalar(
        select(m.MappingOverride).where(
            m.MappingOverride.company_id == company_id,
            m.MappingOverride.kind == kind,
            m.MappingOverride.account == account,
        )
    )
    if row is None:
        row = m.MappingOverride(
            org_id=principal.org_id,
            company_id=company_id,
            kind=kind,
            account=account,
            target=target,
            created_by=principal.email,
            source=body.get("source", "manual"),
        )
        s.add(row)
    else:
        row.target = target
    repo.audit(s, principal.ctx, "mapping.changed", company_id, kind=kind, account=account, target=target)
    invalidate_cache(company_id)
    return {"ok": True}
