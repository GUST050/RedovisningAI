"""Publik svarssida, rapporter, regler, revisionslogg och PTL."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from redovisningai.accounting.periods import same_period_previous_year
from redovisningai.api.deps import (
    Principal,
    db,
    get_company,
    get_principal,
    load_analysis,
    require_admin,
    require_aml,
    require_write,
)
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.session import TenantContext, anonymous_session, tenant_session
from redovisningai.findings.lifecycle import precision_by_rule
from redovisningai.reports.builders import client_report, internal_report, reko_documentation, statements_tables
from redovisningai.reports.document import Table, to_docx, to_pdf, to_xlsx
from redovisningai.review.workflow import (
    ALLOWED_ATTACHMENT_TYPES,
    MAX_ATTACHMENT_BYTES,
    WorkflowError,
    answer_question,
    public_view,
    token_hash,
)
from redovisningai.rules.engine import default_catalog
from redovisningai.rules.rates import default_rates

# ============================================================================ publik svarssida

public = APIRouter(prefix="/api/public", tags=["publik"])


def _public_ctx(token: str) -> TenantContext:
    if len(token) < 20:
        raise HTTPException(404, "Länken är ogiltig eller har gått ut")
    with anonymous_session() as s:
        row = s.execute(text("select * from question_lookup(:h)"), {"h": token_hash(token)}).first()
    if row is None:
        raise HTTPException(404, "Länken är ogiltig eller har gått ut")
    return TenantContext(row.org_id, None, "PUBLIC_QUESTION", question_id=row.id, user_email="kund (publik länk)")


@public.get("/questions/{token}")
def public_question(token: str) -> dict[str, Any]:
    ctx = _public_ctx(token)
    with tenant_session(ctx) as s:
        q = s.get(m.ClientQuestion, ctx.question_id)
        if q is None:
            raise HTTPException(404, "Länken är ogiltig eller har gått ut")
        return public_view(q)


@public.post("/questions/{token}")
async def public_answer(
    token: str, answer: str = Form(..., min_length=1, max_length=10000), files: list[UploadFile] = File(default=[])
) -> dict[str, Any]:
    from redovisningai.jobs.pipeline import _org_store

    ctx = _public_ctx(token)
    if len(files) > 5:
        raise HTTPException(422, "Högst 5 bilagor")
    stored = []
    with tenant_session(ctx) as s:
        q = s.get(m.ClientQuestion, ctx.question_id)
        if q is None:
            raise HTTPException(404, "Länken är ogiltig eller har gått ut")
        store = _org_store(s, ctx, None)
        for f in files:
            data = await f.read()
            ctype = (f.content_type or "").split(";")[0]
            if ctype not in ALLOWED_ATTACHMENT_TYPES:
                raise HTTPException(422, f"Filtypen {ctype or 'okänd'} är inte tillåten")
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise HTTPException(422, "Bilagan är för stor (max 10 MB)")
            key = f"{ctx.org_id}/{q.company_id}/attachments/{uuid.uuid4()}"
            store.put(key, data)
            stored.append(
                {"name": (f.filename or "bilaga")[:200], "size": len(data), "content_type": ctype, "key": key}
            )
        try:
            answer_question(s, ctx, q, answer, stored)
        except WorkflowError as exc:
            raise HTTPException(409, str(exc)) from exc
        return public_view(q)


# ============================================================================ rapporter

reports = APIRouter(prefix="/api/companies/{company_id}", tags=["rapporter"])

MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _file(data: bytes, fmt: str, name: str) -> Response:
    return Response(
        data, media_type=MEDIA[fmt], headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'}
    )


def _findings_dicts(s: Session, company_id: uuid.UUID, period: str) -> list[dict[str, Any]]:
    from redovisningai.api.routes_review import _finding_dict

    rows = s.scalars(select(m.FindingRow).where(m.FindingRow.company_id == company_id)).all()
    return [_finding_dict(r) for r in rows if period in r.seen_in_reviews or r.period == period]


@reports.get("/reports/client")
def report_client(
    company_id: uuid.UUID,
    period: str,
    format: str = "pdf",
    principal: Principal = Depends(get_principal),
    s: Session = Depends(db),
) -> Response:
    if format not in ("pdf", "docx"):
        raise HTTPException(422, "format: pdf eller docx")
    company = get_company(s, company_id)
    a = load_analysis(principal, company_id)
    p = a.period(period)
    pr = s.scalar(
        select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period)
    )
    # Bara ett av konsulten godkänt mötesunderlag får följa med till kunden.
    meeting = pr.client_report.get("data") if pr and pr.client_report and pr.client_report.get("approved") else None
    doc = client_report(
        a.overview(p), a.statements(p, same_period_previous_year(p, a.ledger)), meeting, principal.org_name
    )
    repo.audit(s, principal.ctx, "report.client_exported", company_id, period=period, format=format)
    data = to_pdf(doc) if format == "pdf" else to_docx(doc)
    return _file(data, format, f"{company.name} {period} kundrapport")


@reports.get("/reports/internal")
def report_internal(
    company_id: uuid.UUID,
    period: str,
    format: str = "pdf",
    principal: Principal = Depends(get_principal),
    s: Session = Depends(db),
) -> Response:
    company = get_company(s, company_id)
    a = load_analysis(principal, company_id)
    p = a.period(period)
    from redovisningai.api.routes_review import cases as list_cases

    pr = s.scalar(
        select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period)
    )
    doc = internal_report(
        a.overview(p),
        _findings_dicts(s, company_id, period),
        list_cases(company_id, period, True, s),
        (pr.commentary or {}).get("data") if pr else None,
        a.maturity(p).to_dict(),
    )
    repo.audit(s, principal.ctx, "report.internal_exported", company_id, period=period, format=format)
    return _file(
        to_pdf(doc) if format == "pdf" else to_docx(doc),
        "pdf" if format == "pdf" else "docx",
        f"{company.name} {period} intern",
    )


@reports.get("/reports/reko")
def report_reko(
    company_id: uuid.UUID, period: str, principal: Principal = Depends(get_principal), s: Session = Depends(db)
) -> Response:
    company = get_company(s, company_id)
    pr = s.scalar(
        select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period)
    )
    if pr is None:
        raise HTTPException(404, "Perioden har inte granskats")
    audit_rows = s.scalars(
        select(m.AuditEvent).where(m.AuditEvent.company_id == company_id).order_by(m.AuditEvent.at)
    ).all()
    doc = reko_documentation(
        {"name": company.name, "org_number": company.org_number},
        period,
        {
            "status": pr.status,
            "approved_by": pr.approved_by,
            "approved_at": pr.approved_at.isoformat() if pr.approved_at else None,
            "snapshot": pr.snapshot,
            "changes": pr.changes,
        },
        _findings_dicts(s, company_id, period),
        [{"at": e.at.isoformat(), "user_email": e.user_email, "action": e.action} for e in audit_rows],
        {c: r.to_dict() for c, r in default_catalog().items()},
    )
    repo.audit(s, principal.ctx, "report.reko_exported", company_id, period=period)
    return _file(to_pdf(doc), "pdf", f"{company.name} {period} granskningsdokumentation")


@reports.get("/export/{what}.xlsx")
def export_xlsx(
    company_id: uuid.UUID,
    what: str,
    period: str | None = None,
    principal: Principal = Depends(get_principal),
    s: Session = Depends(db),
) -> Response:
    company = get_company(s, company_id)
    a = load_analysis(principal, company_id)
    p = a.period(period)
    if what == "statements":
        sheets = statements_tables(a.statements(p, same_period_previous_year(p, a.ledger)))
    elif what == "findings":
        fs = _findings_dicts(s, company_id, p.spec)
        sheets = [
            (
                "Fynd",
                Table(
                    ["Regel", "Allvar", "Fynd", "Status", "Verifikationer", "Belopp", "Motivering"],
                    [
                        [
                            f["rule_code"],
                            f["severity"],
                            f["title"],
                            f["status"],
                            ", ".join(f["vouchers"]),
                            f["amount"],
                            f["resolution_note"],
                        ]
                        for f in fs
                    ],
                    numeric_cols={5},
                ),
            )
        ]
    elif what == "transactions":
        from redovisningai.api.routes_company import transactions

        tx = transactions(company_id, None, p.start, p.end, None, 1, 500, s)
        sheets = [
            (
                "Transaktioner",
                Table(
                    ["Verifikation", "Datum", "Text", "Konto", "Kontonamn", "Belopp"],
                    [
                        [
                            r["voucher"],
                            r["date"],
                            r["text"] or r["voucher_text"],
                            r["account"],
                            r["account_name"],
                            r["amount"],
                        ]
                        for r in tx["rows"]
                    ],
                    numeric_cols={5},
                ),
            )
        ]
    else:
        raise HTTPException(404, "Okänd export")
    repo.audit(s, principal.ctx, "export.xlsx", company_id, what=what, period=p.spec)
    return _file(to_xlsx(sheets), "xlsx", f"{company.name} {p.spec} {what}")


# ============================================================================ regler, logg, AI-status

admin = APIRouter(prefix="/api", tags=["regler"])


@admin.get("/rules")
def rules() -> dict[str, Any]:
    return {
        "rules": [r.to_dict() for r in default_catalog().values()],
        "rates": [
            {
                "code": r.code,
                "value": r.value,
                "valid_from": r.valid_from.isoformat(),
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                "source": r.source,
                "note": r.note,
            }
            for r in default_rates().rates
        ],
    }


@admin.get("/rules/health")
def rules_health(s: Session = Depends(db)) -> list[dict[str, Any]]:
    records = []
    for c in s.scalars(select(m.Company)).all():
        records += repo.load_findings(s, c.id)
    return [p.to_dict() for p in precision_by_rule(records)]


class ParamsIn(BaseModel):
    params: dict[str, Any]
    company_id: uuid.UUID | None = None


@admin.put("/rules/{code}/params")
def set_params(
    code: str, body: ParamsIn, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    rd = default_catalog().get(code)
    if rd is None:
        raise HTTPException(404, "Okänd regel")
    unknown = set(body.params) - set(rd.params)
    if unknown:
        raise HTTPException(422, f"Okända parametrar: {', '.join(sorted(unknown))}")
    if body.company_id is None and principal.role != "ADMIN":
        raise HTTPException(403, "Byråövergripande parametrar kräver admin")
    row = s.scalar(
        select(m.RuleParamOverride).where(
            m.RuleParamOverride.rule_code == code, m.RuleParamOverride.company_id == body.company_id
        )
    )
    if row is None:
        row = m.RuleParamOverride(
            org_id=principal.org_id,
            company_id=body.company_id,
            rule_code=code,
            params=body.params,
            created_by=principal.email,
        )
        s.add(row)
    else:
        row.params = body.params
    repo.audit(s, principal.ctx, "rule.params_changed", body.company_id, rule=code, params=body.params)
    return {"ok": True}


class SuppressionIn(BaseModel):
    rule_code: str
    reason: str = Field(min_length=3, max_length=1000)
    company_id: uuid.UUID | None = None
    expires_at: str | None = None
    accounts: list[int] = Field(default_factory=list)
    text_contains: str | None = None


@admin.get("/suppressions")
def suppressions(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [
        {
            "id": str(r.id),
            "rule_code": r.rule_code,
            "reason": r.reason,
            "company_id": str(r.company_id) if r.company_id else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "accounts": r.accounts,
            "text_contains": r.text_contains,
            "created_by": r.created_by,
        }
        for r in s.scalars(select(m.SuppressionRuleRow)).all()
    ]


@admin.post("/suppressions")
def add_suppression(
    body: SuppressionIn, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    from datetime import date

    rd = default_catalog().get(body.rule_code)
    if rd is None:
        raise HTTPException(404, "Okänd regel")
    if rd.category == "aml":
        raise HTTPException(409, "PTL-signaler kan inte undertryckas generellt")
    if not body.expires_at:
        raise HTTPException(422, "Undertryckning kräver ett slutdatum")
    row = m.SuppressionRuleRow(
        org_id=principal.org_id,
        company_id=body.company_id,
        rule_code=body.rule_code,
        reason=body.reason,
        created_by=principal.email,
        expires_at=date.fromisoformat(body.expires_at),
        accounts=body.accounts,
        text_contains=body.text_contains,
    )
    s.add(row)
    repo.audit(s, principal.ctx, "suppression.created", body.company_id, rule=body.rule_code, reason=body.reason)
    return {"ok": True}


@admin.get("/audit")
def audit_log(company_id: uuid.UUID | None = None, limit: int = 200, s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = select(m.AuditEvent).order_by(m.AuditEvent.at.desc()).limit(min(limit, 1000))
    if company_id:
        q = q.where(m.AuditEvent.company_id == company_id)
    return [
        {
            "at": e.at.isoformat(),
            "user": e.user_email,
            "company_id": str(e.company_id) if e.company_id else None,
            "action": e.action,
            "details": e.details,
        }
        for e in s.scalars(q).all()
    ]


@admin.get("/ai/status")
def ai_status(principal: Principal = Depends(get_principal), s: Session = Depends(db)) -> dict[str, Any]:
    from redovisningai.config import get_settings

    st = get_settings()
    month = datetime.now().strftime("%Y-%m")
    used = s.scalar(select(m.AIUsage.tokens).where(m.AIUsage.month == month)) or 0
    org = s.get(m.Organization, principal.org_id)
    return {
        "enabled": st.ai_enabled,
        "platform": st.ai_platform,
        "region": st.ai_region,
        "secondary": st.ai_secondary_platform,
        "models": {"strong": st.ai_model_strong, "medium": st.ai_model_medium, "small": st.ai_model_small},
        "tokens_used_this_month": used,
        "monthly_budget": org.ai_monthly_token_budget if org else None,
        "notice": "AI-genererade texter märks i gränssnittet (AI Act art. 50) och granskas av konsulten.",
    }


class WatchIn(BaseModel):
    source_url: str
    source_text: str = Field(min_length=20, max_length=50000)


@admin.post("/rules/watch")
def rule_watch(
    body: WatchIn, principal: Principal = Depends(require_admin), s: Session = Depends(db)
) -> dict[str, Any]:
    from redovisningai.ai.factory import build_ai_service
    from redovisningai.facts.model import FactStore

    pkg = {
        "source_url": body.source_url,
        "source_text": body.source_text,
        "current_rates": rules()["rates"],
        "current_rules": [
            {"code": r["code"], "title": r["title"], "valid_to": r["valid_to"]} for r in rules()["rules"]
        ],
    }
    out = build_ai_service(principal.org_id).run("A8", pkg, FactStore(), org_id=str(principal.org_id))
    s.add(m.RuleProposal(org_id=principal.org_id, source_url=body.source_url, proposals=out.data["proposals"]))
    repo.audit(s, principal.ctx, "rules.watch", None, source_url=body.source_url, proposals=len(out.data["proposals"]))
    return out.to_dict()


@admin.get("/rules/proposals")
def rule_proposals(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [
        {
            "id": str(p.id),
            "source_url": p.source_url,
            "proposals": p.proposals,
            "status": p.status,
            "created_at": p.created_at.isoformat(),
        }
        for p in s.scalars(select(m.RuleProposal)).all()
    ]


# ============================================================================ PTL (endast PTL-ansvarig)

aml = APIRouter(prefix="/api/companies/{company_id}/aml", tags=["ptl"])


@aml.get("")
def aml_view(
    company_id: uuid.UUID, principal: Principal = Depends(require_aml), s: Session = Depends(db)
) -> dict[str, Any]:
    from redovisningai.api.routes_review import _finding_dict

    get_company(s, company_id)
    signals = [
        _finding_dict(r)
        for r in s.scalars(
            select(m.FindingRow).where(
                m.FindingRow.company_id == company_id, m.FindingRow.visibility == "RESTRICTED_AML"
            )
        ).all()
    ]
    assessments = [
        {
            "risk_level": a.risk_level,
            "factors": a.factors,
            "notes": a.notes,
            "decided_by": a.decided_by,
            "decided_at": a.decided_at.isoformat(),
        }
        for a in s.scalars(
            select(m.AmlAssessment)
            .where(m.AmlAssessment.company_id == company_id)
            .order_by(m.AmlAssessment.decided_at.desc())
        ).all()
    ]
    repo.audit(s, principal.ctx, "aml.viewed", company_id)
    return {
        "signals": signals,
        "assessments": assessments,
        "notice": "Signalerna är underlag för byråns bedömning enligt PTL. Meddelandeförbud gäller – "
        "informationen får inte delas med kunden.",
    }


class AssessmentIn(BaseModel):
    risk_level: str = Field(pattern="^(LOW|NORMAL|HIGH)$")
    factors: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


@aml.post("/assessment")
def aml_assess(
    company_id: uuid.UUID, body: AssessmentIn, principal: Principal = Depends(require_aml), s: Session = Depends(db)
) -> dict[str, Any]:
    get_company(s, company_id)
    s.add(
        m.AmlAssessment(
            org_id=principal.org_id,
            company_id=company_id,
            risk_level=body.risk_level,
            factors=body.factors,
            notes=body.notes,
            decided_by=principal.email,
        )
    )
    repo.audit(s, principal.ctx, "aml.assessed", company_id, risk_level=body.risk_level)
    return {"ok": True}


@aml.get("/export.csv")
def aml_export(
    company_id: uuid.UUID, principal: Principal = Depends(require_aml), s: Session = Depends(db)
) -> Response:
    """Export till byråns KYC-verktyg (t.ex. Visma Advisor KYC, Lundify)."""
    company = get_company(s, company_id)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["org_nr", "kund", "signal", "period", "verifikationer", "belopp", "status"])
    for r in s.scalars(
        select(m.FindingRow).where(m.FindingRow.company_id == company_id, m.FindingRow.visibility == "RESTRICTED_AML")
    ).all():
        w.writerow(
            [company.org_number, company.name, r.title, r.period, " ".join(r.vouchers), r.amount or "", r.status]
        )
    repo.audit(s, principal.ctx, "aml.exported", company_id)
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ptl-signaler-{company.org_number}.csv"'},
    )
