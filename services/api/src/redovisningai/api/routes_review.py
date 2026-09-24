"""Granskning: fynd, ärenden, beslut, perioder, AI-texter, kundfrågor och AI-analytikern."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from redovisningai.ai.providers.base import ToolBudget
from redovisningai.ai.tools import analyst_tools
from redovisningai.api.deps import (
    Principal,
    db,
    get_company,
    get_principal,
    invalidate_cache,
    load_analysis,
    require_write,
)
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.findings.lifecycle import FindingStatus
from redovisningai.jobs.pipeline import review_company
from redovisningai.review.workflow import (
    WorkflowError,
    approve_period,
    create_question,
    decide_findings,
)

router = APIRouter(prefix="/api/companies/{company_id}", tags=["granskning"])


def _ai(principal: Principal):  # type: ignore[no-untyped-def]
    from redovisningai.ai.factory import build_ai_service

    return build_ai_service(principal.org_id)


def _finding_dict(r: m.FindingRow) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "fingerprint": r.fingerprint,
        "rule_code": r.rule_code,
        "rule_version": r.rule_version,
        "severity": r.severity,
        "title": r.title,
        "description": r.description,
        "period": r.period,
        "visibility": r.visibility,
        "status": r.status,
        "category": r.category,
        "legal_basis": r.legal_basis,
        "vouchers": r.vouchers,
        "accounts": r.accounts,
        "amount": None if r.amount is None else str(r.amount),
        "facts": r.facts,
        "details": r.details,
        "resolution_note": r.resolution_note,
        "resolved_by": r.resolved_by,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "memory_suggestion": r.memory_suggestion,
        "case_key": r.case_key,
        "seen_in_reviews": r.seen_in_reviews,
    }


def _rendered(text: str, facts: list[dict[str, Any]]) -> str:
    by_id = {f["id"]: f.get("display", "") for f in facts}
    import re

    return re.sub(r"\{f:([^}\s]+)\}", lambda mm: by_id.get(mm.group(1), "[saknas]"), text)


@router.get("/findings")
def findings(
    company_id: uuid.UUID, period: str | None = None, status: str | None = None, s: Session = Depends(db)
) -> list[dict[str, Any]]:
    get_company(s, company_id)
    q = select(m.FindingRow).where(m.FindingRow.company_id == company_id)
    if status == "open":
        q = q.where(m.FindingRow.status.in_(["NEW", "IN_PROGRESS", "ASK_CLIENT"]))
    elif status:
        q = q.where(m.FindingRow.status == status)
    out = []
    for r in s.scalars(q).all():
        if period and period not in r.seen_in_reviews and r.period != period:
            continue
        d = _finding_dict(r)
        d["description_rendered"] = _rendered(r.description, r.facts)
        out.append(d)
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(out, key=lambda f: (order.get(f["severity"], 3), f["period"], f["title"]))


@router.get("/cases")
def cases(
    company_id: uuid.UUID, period: str | None = None, include_closed: bool = False, s: Session = Depends(db)
) -> list[dict[str, Any]]:
    get_company(s, company_id)
    all_findings: dict[str | None, list[m.FindingRow]] = {}
    for r in s.scalars(select(m.FindingRow).where(m.FindingRow.company_id == company_id)).all():
        all_findings.setdefault(r.case_key, []).append(r)
    out = []
    for c in s.scalars(select(m.CaseRow).where(m.CaseRow.company_id == company_id)).all():
        fs = all_findings.get(c.case_key, [])
        if not fs:
            continue
        if period and not any(period in f.seen_in_reviews or f.period == period for f in fs):
            continue
        statuses = {f.status for f in fs}
        status = (
            "OPEN"
            if "NEW" in statuses
            else "IN_PROGRESS"
            if "IN_PROGRESS" in statuses
            else "WAITING_CLIENT"
            if "ASK_CLIENT" in statuses
            else "CLOSED"
        )
        if status == "CLOSED" and not include_closed:
            continue
        findings_out = []
        for f in sorted(fs, key=lambda x: x.severity):
            d = _finding_dict(f)
            d["description_rendered"] = _rendered(f.description, f.facts)
            findings_out.append(d)
        out.append(
            {
                "key": c.case_key,
                "title": c.title,
                "root_cause": c.root_cause,
                "suggested_action": c.suggested_action,
                "ask_client_suggested": c.ask_client_suggested,
                "severity": c.severity,
                "visibility": c.visibility,
                "period": c.period,
                "memory_hint": c.memory_hint,
                "status": status,
                "ai": c.ai,
                "findings": findings_out,
                "vouchers": sorted({v for f in fs for v in f.vouchers}),
            }
        )
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(out, key=lambda c: (c["status"] == "CLOSED", order.get(c["severity"], 3), c["period"]))


class DecisionIn(BaseModel):
    status: FindingStatus
    note: str | None = Field(default=None, max_length=4000)
    remember_until: date | None = None
    finding_ids: list[uuid.UUID] | None = None


@router.post("/cases/{case_key}/decision")
def decide_case(
    company_id: uuid.UUID,
    case_key: str,
    body: DecisionIn,
    principal: Principal = Depends(require_write),
    s: Session = Depends(db),
) -> dict[str, Any]:
    company = get_company(s, company_id)
    ids = body.finding_ids or [
        r.id
        for r in s.scalars(
            select(m.FindingRow).where(m.FindingRow.company_id == company_id, m.FindingRow.case_key == case_key)
        ).all()
    ]
    try:
        n = decide_findings(
            s, principal.ctx, company, list(ids), body.status, body.note, remember_until=body.remember_until
        )
    except (WorkflowError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"updated": n}


@router.post("/findings/decision")
def decide(
    company_id: uuid.UUID, body: DecisionIn, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    company = get_company(s, company_id)
    if not body.finding_ids:
        raise HTTPException(422, "finding_ids krävs")
    try:
        n = decide_findings(
            s, principal.ctx, company, body.finding_ids, body.status, body.note, remember_until=body.remember_until
        )
    except (WorkflowError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"updated": n}


class ReviewIn(BaseModel):
    periods: list[str] | None = None


@router.post("/review/run")
def run_review(
    company_id: uuid.UUID, body: ReviewIn, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    get_company(s, company_id)
    done = review_company(TenantContext.worker(principal.org_id), company_id, periods=body.periods, ai=_ai(principal))
    invalidate_cache(company_id)
    return {"reviewed": done}


@router.get("/periods")
def periods(company_id: uuid.UUID, s: Session = Depends(db)) -> list[dict[str, Any]]:
    get_company(s, company_id)
    return [
        {
            "period": r.period,
            "status": r.status,
            "maturity": r.maturity,
            "approved_by": r.approved_by,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "changes": r.changes,
            "has_commentary": bool(r.commentary),
            "has_client_report": bool(r.client_report),
        }
        for r in s.scalars(
            select(m.PeriodReview).where(m.PeriodReview.company_id == company_id).order_by(m.PeriodReview.period.desc())
        ).all()
    ]


@router.get("/periods/{period}")
def period_detail(company_id: uuid.UUID, period: str, s: Session = Depends(db)) -> dict[str, Any]:
    get_company(s, company_id)
    r = s.scalar(select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period))
    if r is None:
        raise HTTPException(404, "Perioden har inte granskats")
    return {
        "period": r.period,
        "status": r.status,
        "maturity": r.maturity,
        "approved_by": r.approved_by,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
        "reported_at": r.reported_at.isoformat() if r.reported_at else None,
        "override_note": (r.snapshot or {}).get("override_note"),
        "changes": r.changes,
        "commentary": r.commentary,
        "client_report": r.client_report,
    }


class ApproveIn(BaseModel):
    override_note: str | None = Field(default=None, max_length=2000)


@router.post("/periods/{period}/approve")
def approve(
    company_id: uuid.UUID,
    period: str,
    body: ApproveIn,
    principal: Principal = Depends(require_write),
    s: Session = Depends(db),
) -> dict[str, Any]:
    company = get_company(s, company_id)
    try:
        pr = approve_period(s, principal.ctx, company, period, override_note=body.override_note)
    except WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": pr.status, "approved_at": pr.approved_at.isoformat() if pr.approved_at else None}


def _review_result(principal: Principal, company_id: uuid.UUID, period: str):  # type: ignore[no-untyped-def]
    a = load_analysis(principal, company_id)
    with tenant_session(principal.ctx) as s:
        records = repo.load_findings(s, company_id)
    # Återskapa granskningen i minnet (inga ändringar sparas) för att få fakta och ärenden.
    return a, a.review(a.period(period), records)


@router.post("/periods/{period}/commentary")
def commentary(company_id: uuid.UUID, period: str, principal: Principal = Depends(require_write)) -> dict[str, Any]:
    a, rev = _review_result(principal, company_id, period)
    out = _ai(principal).run(
        "A3",
        a.commentary_package(rev),
        rev.store,
        org_id=str(principal.org_id),
        company_id=str(company_id),
        names_to_mask=a.ctx.person_names,
        allowed_identifiers=a.allowed_identifiers(),
    )
    with tenant_session(principal.ctx) as s:
        pr = s.scalar(
            select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period)
        )
        if pr is not None:
            pr.commentary = {**out.to_dict(), "created_at": datetime.now().isoformat(), "by": principal.email}
        repo.audit(
            s, principal.ctx, "ai.commentary", company_id, period=period, source=out.source, trace_id=out.trace.id
        )
    return out.to_dict()


@router.post("/periods/{period}/meeting")
def meeting(company_id: uuid.UUID, period: str, principal: Principal = Depends(require_write)) -> dict[str, Any]:
    a, rev = _review_result(principal, company_id, period)
    pkg = a.client_package(rev)
    out = _ai(principal).run(
        "A4",
        pkg,
        rev.store,
        org_id=str(principal.org_id),
        company_id=str(company_id),
        names_to_mask=a.ctx.person_names,
        allowed_identifiers=a.allowed_identifiers(),
    )
    with tenant_session(principal.ctx) as s:
        pr = s.scalar(
            select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period)
        )
        if pr is not None:
            pr.client_report = {
                **out.to_dict(),
                "created_at": datetime.now().isoformat(),
                "by": principal.email,
                "approved": False,
            }
        repo.audit(s, principal.ctx, "ai.meeting", company_id, period=period, source=out.source, trace_id=out.trace.id)
    return out.to_dict()


class MeetingEditIn(BaseModel):
    summary: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    approve: bool = False


@router.put("/periods/{period}/meeting")
def edit_meeting(
    company_id: uuid.UUID,
    period: str,
    body: MeetingEditIn,
    principal: Principal = Depends(require_write),
    s: Session = Depends(db),
) -> dict[str, Any]:
    """Konsulten redigerar och godkänner kundmötesunderlaget innan det används."""
    get_company(s, company_id)
    pr = s.scalar(
        select(m.PeriodReview).where(m.PeriodReview.company_id == company_id, m.PeriodReview.period == period)
    )
    if pr is None:
        raise HTTPException(404, "Perioden finns inte")
    if body.approve and not principal.can_approve_reports and principal.role != "ADMIN":
        raise HTTPException(403, "Kräver behörigheten att godkänna kundrapporter")
    data = dict(pr.client_report or {})
    content = dict(data.get("data") or {})
    content["summary"] = [
        {"type": "OBSERVATION", "text": t, "rendered": t, "fact_ids": [], "edited": True} for t in body.summary
    ]
    content["questions"] = [
        {"type": "QUESTION", "text": t, "rendered": t, "fact_ids": [], "edited": True} for t in body.questions
    ]
    data["data"] = content
    data["approved"] = body.approve
    data["edited_by"] = principal.email
    pr.client_report = data
    if body.approve:
        pr.reported_at = datetime.now().astimezone()
        if pr.status == "APPROVED":
            pr.status = "REPORTED"
    repo.audit(s, principal.ctx, "report.edited", company_id, period=period, approved=body.approve)
    return {"ok": True}


# ---------------------------------------------------------------------------- kundfrågor


class QuestionIn(BaseModel):
    case_key: str
    text: str = Field(min_length=1, max_length=4000)
    recipient_email: str | None = None


@router.get("/cases/{case_key}/question-draft")
def question_draft(
    company_id: uuid.UUID, case_key: str, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    get_company(s, company_id)
    case = s.scalar(select(m.CaseRow).where(m.CaseRow.company_id == company_id, m.CaseRow.case_key == case_key))
    if case is None:
        raise HTTPException(404, "Ärendet finns inte")
    if case.visibility == "RESTRICTED_AML":
        raise HTTPException(409, "Frågor om PTL-signaler kan inte skickas till kunden.")
    from redovisningai.facts.model import FactStore

    pkg = {
        "ask_client": [{"key": case.case_key, "title": case.title, "question_hint": None}],
        "bridge": {},
        "metrics": {},
        "facts": [],
    }
    out = _ai(principal).run("A4", pkg, FactStore(), org_id=str(principal.org_id), company_id=str(company_id))
    q = next((x["question"] for x in out.data.get("case_questions", []) if x["case_key"] == case_key), None)
    return {
        "text": q or f"Hej! Vi har en fråga om följande: {case.title.lower()}. Kan du berätta mer?",
        "source": out.source,
    }


@router.post("/questions")
def send_question(
    company_id: uuid.UUID, body: QuestionIn, principal: Principal = Depends(require_write), s: Session = Depends(db)
) -> dict[str, Any]:
    company = get_company(s, company_id)
    try:
        created = create_question(
            s, principal.ctx, company, body.case_key, body.text, recipient_email=body.recipient_email
        )
    except WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": str(created.question.id), "link": created.link}


@router.get("/questions")
def list_questions(company_id: uuid.UUID, s: Session = Depends(db)) -> list[dict[str, Any]]:
    get_company(s, company_id)
    return [
        {
            "id": str(q.id),
            "case_key": q.case_key,
            "text": q.text,
            "status": q.status,
            "sent_by": q.sent_by,
            "sent_at": q.sent_at.isoformat(),
            "expires_at": q.expires_at.isoformat(),
            "answered_at": q.answered_at.isoformat() if q.answered_at else None,
            "answer_text": q.answer_text,
            "attachments": [{"name": a["name"], "size": a["size"], "key": a["key"]} for a in q.attachments],
        }
        for q in s.scalars(
            select(m.ClientQuestion)
            .where(m.ClientQuestion.company_id == company_id)
            .order_by(m.ClientQuestion.sent_at.desc())
        ).all()
    ]


@router.post("/questions/{question_id}/close")
def close_question(
    company_id: uuid.UUID,
    question_id: uuid.UUID,
    principal: Principal = Depends(require_write),
    s: Session = Depends(db),
) -> dict[str, Any]:
    q = s.get(m.ClientQuestion, question_id)
    if q is None or q.company_id != company_id:
        raise HTTPException(404, "Frågan finns inte")
    q.status = "CLOSED"
    repo.audit(s, principal.ctx, "question.closed", company_id, question_id=str(question_id))
    return {"ok": True}


@router.get("/questions/{question_id}/attachments/{key:path}")
def attachment(
    company_id: uuid.UUID,
    question_id: uuid.UUID,
    key: str,
    principal: Principal = Depends(get_principal),
    s: Session = Depends(db),
):  # type: ignore[no-untyped-def]
    from fastapi.responses import Response

    from redovisningai.jobs.pipeline import _org_store

    q = s.get(m.ClientQuestion, question_id)
    if q is None or q.company_id != company_id:
        raise HTTPException(404, "Frågan finns inte")
    a = next((x for x in q.attachments if x["key"] == key), None)
    if a is None:
        raise HTTPException(404, "Bilagan finns inte")
    data = _org_store(s, principal.ctx, None).get(a["key"])
    repo.audit(s, principal.ctx, "attachment.downloaded", company_id, question_id=str(question_id), name=a["name"])
    return Response(
        data, media_type=a["content_type"], headers={"Content-Disposition": f'attachment; filename="{a["name"]}"'}
    )


# ---------------------------------------------------------------------------- AI-analytikern och minne


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    period: str | None = None


@router.post("/ask")
def ask(company_id: uuid.UUID, body: AskIn, principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    a = load_analysis(principal, company_id)
    period = a.period(body.period)
    from redovisningai.facts.model import FactStore

    with tenant_session(principal.ctx) as s:
        records = repo.load_findings(s, company_id)
    store = FactStore()
    tools = analyst_tools(a, store, records, period)
    pkg = {
        "question": body.question,
        "company": a.ctx.name,
        "default_period": period.spec,
        "months_with_data": [x.isoformat() for x in a.index.months_with_data()][-24:],
    }
    out = _ai(principal).run(
        "A5",
        pkg,
        store,
        org_id=str(principal.org_id),
        company_id=str(company_id),
        names_to_mask=a.ctx.person_names,
        allowed_identifiers=a.allowed_identifiers(),
        tools=tools,
        tool_budget=ToolBudget(max_tool_calls=8, max_iterations=6),
    )
    with tenant_session(principal.ctx) as s:
        repo.audit(
            s, principal.ctx, "ai.ask", company_id, question=body.question, source=out.source, trace_id=out.trace.id
        )
    return {**out.to_dict(), "tool_calls": out.trace.tool_calls}


@router.get("/memory")
def memory(company_id: uuid.UUID, s: Session = Depends(db)) -> list[dict[str, Any]]:
    get_company(s, company_id)
    return [r.to_dict() for r in repo.load_resolutions(s, company_id)]


@router.delete("/memory/{resolution_id}")
def forget(
    company_id: uuid.UUID,
    resolution_id: uuid.UUID,
    principal: Principal = Depends(require_write),
    s: Session = Depends(db),
) -> dict[str, Any]:
    r = s.get(m.ResolutionRow, resolution_id)
    if r is None or r.company_id != company_id:
        raise HTTPException(404, "Finns inte")
    s.delete(r)
    repo.audit(s, principal.ctx, "memory.deleted", company_id, resolution_id=str(resolution_id))
    return {"ok": True}
