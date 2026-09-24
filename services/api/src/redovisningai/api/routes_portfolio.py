"""Portföljvy och bolagsadministration."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from redovisningai.accounting.metrics import calculate_metric
from redovisningai.accounting.periods import same_period_previous_year
from redovisningai.api.deps import (
    Principal,
    db,
    get_company,
    get_principal,
    load_analysis,
    require_admin,
    require_write,
)
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.portfolio.score import PortfolioInputs, score

router = APIRouter(prefix="/api", tags=["portfölj"])


@router.get("/me")
def me(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return principal.to_dict()


def _company_row(s: Session, principal: Principal, c: m.Company, today: date) -> dict[str, Any]:
    reviews = s.scalars(
        select(m.PeriodReview).where(m.PeriodReview.company_id == c.id).order_by(m.PeriodReview.period.desc())
    ).all()
    latest = reviews[0] if reviews else None
    counts = dict(
        s.execute(
            select(m.FindingRow.severity, func.count())
            .where(m.FindingRow.company_id == c.id, m.FindingRow.status.in_(["NEW", "IN_PROGRESS", "ASK_CLIENT"]))
            .group_by(m.FindingRow.severity)
        ).all()
    )
    conn = s.scalars(select(m.Connection).where(m.Connection.company_id == c.id)).all()
    connection_ok = all(x.status in ("OK", "PENDING") for x in conn) if conn else True
    unanswered = (
        s.scalar(
            select(func.count())
            .select_from(m.ClientQuestion)
            .where(
                m.ClientQuestion.company_id == c.id,
                m.ClientQuestion.status == "SENT",
                m.ClientQuestion.sent_at < datetime.now().astimezone() - timedelta(days=7),
            )
        )
        or 0
    )
    last_approved = max((r.approved_at for r in reviews if r.approved_at), default=None)
    changed = any(r.status == "CHANGED_AFTER_APPROVAL" for r in reviews)
    expected = date(today.year, today.month, 1) - timedelta(days=1)
    expected = date(expected.year, expected.month, 1)
    latest_data = None
    margin_change = None
    try:
        analysis = load_analysis(principal, c.id)
        lm = analysis.latest_month()
        if lm is not None:
            latest_data = lm.start
            r12 = analysis.period(f"R12:{lm.end:%Y-%m}")
            cur = calculate_metric("operating_margin", analysis.index, r12)
            prev = calculate_metric("operating_margin", analysis.index, same_period_previous_year(r12))
            if cur.value is not None and prev.value is not None:
                margin_change = cur.value - prev.value
    except (HTTPException, ValueError):
        pass
    prio = score(
        PortfolioInputs(
            open_high=counts.get("HIGH", 0),
            open_medium=counts.get("MEDIUM", 0),
            open_low=counts.get("LOW", 0),
            changed_after_approval=changed,
            margin_change_pp=margin_change,
            last_review=last_approved.date() if last_approved else None,
            latest_data_month=latest_data,
            expected_month=expected,
            connection_ok=connection_ok,
            unanswered_questions_over_7d=unanswered,
            preliminary=bool(latest and latest.status == "PRELIMINARY"),
        ),
        today,
    )
    return {
        "id": str(c.id),
        "name": c.name,
        "org_number": c.org_number,
        "source_system": c.source_system,
        "latest_period": latest.period if latest else None,
        "status": latest.status if latest else "NO_DATA",
        "open_findings": {
            "high": counts.get("HIGH", 0),
            "medium": counts.get("MEDIUM", 0),
            "low": counts.get("LOW", 0),
        },
        "changed_after_approval": changed,
        "connection_ok": connection_ok,
        "unanswered_questions": unanswered,
        "latest_data_month": latest_data.isoformat() if latest_data else None,
        "margin_change_pp": None if margin_change is None else str(margin_change),
        "priority": prio.to_dict(),
    }


@router.get("/portfolio")
def portfolio(principal: Principal = Depends(get_principal), s: Session = Depends(db)) -> dict[str, Any]:
    today = date.today()
    companies = s.scalars(select(m.Company).where(m.Company.archived_at.is_(None)).order_by(m.Company.name)).all()
    rows = [_company_row(s, principal, c, today) for c in companies]
    rows.sort(key=lambda r: (-r["priority"]["score"], r["name"]))
    todo = {
        "missing_data": sum(1 for r in rows if any(x["code"] == "missing_data" for x in r["priority"]["reasons"])),
        "high_findings": sum(1 for r in rows if r["open_findings"]["high"]),
        "changed_after_approval": sum(1 for r in rows if r["changed_after_approval"]),
        "connection_errors": sum(1 for r in rows if not r["connection_ok"]),
        "unanswered_questions": sum(r["unanswered_questions"] for r in rows),
        "margin_drops": sum(1 for r in rows if any(x["code"] == "margin_drop" for x in r["priority"]["reasons"])),
        "needs_review": sum(1 for r in rows if r["status"] in ("NEEDS_REVIEW", "CHANGED_AFTER_APPROVAL")),
    }
    return {"companies": rows, "todo": todo, "generated_at": datetime.now().isoformat()}


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    org_number: str | None = Field(default=None, max_length=20)
    legal_form: str = "AB"
    industry: str | None = None
    vat_period: str = Field(default="quarter", pattern="^(month|quarter|year)$")
    food_retail: bool = False
    materiality: Decimal = Decimal("5000")


@router.post("/companies")
def create_company(
    body: CompanyIn, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    c = m.Company(org_id=principal.org_id, **body.model_dump())
    s.add(c)
    s.flush()
    s.add(m.CompanyAssignment(org_id=principal.org_id, company_id=c.id, user_id=principal.user_id))
    repo.audit(s, principal.ctx, "company.created", c.id, name=c.name)
    return {"id": str(c.id)}


class CompanySettingsIn(BaseModel):
    legal_form: str | None = None
    industry: str | None = None
    vat_period: str | None = Field(default=None, pattern="^(month|quarter|year)$")
    food_retail: bool | None = None
    materiality: Decimal | None = None
    has_overdraft: bool | None = None
    accounting_method_override: str | None = Field(default=None, pattern="^(invoice|cash)?$")
    manual_series: list[str] | None = None
    suspense_accounts: list[int] | None = None
    person_names: list[str] | None = None


@router.get("/companies/{company_id}")
def company_detail(company_id: uuid.UUID, s: Session = Depends(db)) -> dict[str, Any]:
    c = get_company(s, company_id)
    return {
        "id": str(c.id),
        "name": c.name,
        "org_number": c.org_number,
        "legal_form": c.legal_form,
        "industry": c.industry,
        "vat_period": c.vat_period,
        "food_retail": c.food_retail,
        "materiality": str(c.materiality),
        "has_overdraft": c.has_overdraft,
        "accounting_method_override": c.accounting_method_override,
        "source_system": c.source_system,
        "settings": c.settings,
    }


@router.patch("/companies/{company_id}")
def update_company(
    company_id: uuid.UUID,
    body: CompanySettingsIn,
    principal: Principal = Depends(require_write),
    s: Session = Depends(db),
) -> dict[str, Any]:
    c = get_company(s, company_id)
    data = body.model_dump(exclude_none=True)
    settings = dict(c.settings or {})
    for key in ("manual_series", "suspense_accounts", "person_names"):
        if key in data:
            settings[key] = data.pop(key)
    c.settings = settings
    for k, v in data.items():
        setattr(c, k, (v or None) if k == "accounting_method_override" else v)
    repo.audit(s, principal.ctx, "company.updated", c.id, changes=body.model_dump(exclude_none=True))
    from redovisningai.api.deps import invalidate_cache

    invalidate_cache(company_id)
    return {"ok": True}


class AssignmentIn(BaseModel):
    user_id: uuid.UUID


@router.post("/companies/{company_id}/assignments")
def assign(
    company_id: uuid.UUID, body: AssignmentIn, principal: Principal = Depends(require_admin), s: Session = Depends(db)
) -> dict[str, Any]:
    get_company(s, company_id)
    s.add(m.CompanyAssignment(org_id=principal.org_id, company_id=company_id, user_id=body.user_id))
    repo.audit(s, principal.ctx, "company.assigned", company_id, user_id=str(body.user_id))
    return {"ok": True}


@router.get("/users")
def users(s: Session = Depends(db)) -> list[dict[str, Any]]:
    rows = s.execute(select(m.AppUser, m.Membership).join(m.Membership, m.Membership.user_id == m.AppUser.id)).all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "name": u.name,
            "role": mem.role,
            "payroll": mem.can_payroll,
            "aml": mem.can_aml,
        }
        for u, mem in rows
    ]


@router.get("/portfolio/brief")
def portfolio_brief(principal: Principal = Depends(get_principal), s: Session = Depends(db)) -> dict[str, Any]:
    from redovisningai.ai.factory import build_ai_service
    from redovisningai.facts.model import FactStore

    data = portfolio(principal, s)
    pkg = {
        "companies": [
            {"name": c["name"], "score": c["priority"]["score"], "reasons": c["priority"]["reasons"]}
            for c in data["companies"][:15]
        ],
        "allowed": [],
    }
    out = build_ai_service(principal.org_id).run(
        "A7", pkg, FactStore(), org_id=str(principal.org_id), allowed_identifiers={c["name"] for c in data["companies"]}
    )
    return out.to_dict()
