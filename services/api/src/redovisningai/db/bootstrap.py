"""Administrativa uppgifter som körs med ägarrollen: skapa byrå, första admin, demodata.

Applikationsrollen kan inte skapa byråer (ingen RLS-policy tillåter det) – det görs här vid
onboarding av en ny byrå.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from redovisningai.config import get_settings
from redovisningai.db import models as m
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.storage.objects import new_encrypted_dek


@dataclass(slots=True)
class CreatedOrg:
    org_id: uuid.UUID
    admin_user_id: uuid.UUID


def create_organization(
    name: str,
    admin_email: str,
    admin_name: str,
    *,
    admin_subject: str | None = None,
    org_number: str | None = None,
    owner_url: str | None = None,
) -> CreatedOrg:
    engine = create_engine(owner_url or get_settings().database_url_owner)
    with Session(engine) as s, s.begin():
        org = m.Organization(name=name, org_number=org_number, encrypted_dek=new_encrypted_dek())
        s.add(org)
        user = s.scalar(select(m.AppUser).where(m.AppUser.email == admin_email))
        if user is None:
            user = m.AppUser(email=admin_email, name=admin_name, idp_subject=admin_subject or f"dev:{admin_email}")
            s.add(user)
        s.flush()
        s.add(
            m.Membership(
                org_id=org.id, user_id=user.id, role="ADMIN", can_payroll=True, can_aml=True, can_approve_reports=True
            )
        )
        result = CreatedOrg(org.id, user.id)
    engine.dispose()
    return result


def add_user(
    org_id: uuid.UUID,
    email: str,
    name: str,
    role: str,
    *,
    subject: str | None = None,
    can_payroll: bool = False,
    can_aml: bool = False,
    can_approve_reports: bool = False,
    owner_url: str | None = None,
) -> uuid.UUID:
    """Lägg till användare (användartabellen är global – därför ägarrollen)."""
    engine = create_engine(owner_url or get_settings().database_url_owner)
    with Session(engine) as s, s.begin():
        user = s.scalar(select(m.AppUser).where(m.AppUser.email == email))
        if user is None:
            user = m.AppUser(email=email, name=name, idp_subject=subject or f"dev:{email}")
            s.add(user)
            s.flush()
        s.add(
            m.Membership(
                org_id=org_id,
                user_id=user.id,
                role=role,
                can_payroll=can_payroll,
                can_aml=can_aml,
                can_approve_reports=can_approve_reports,
            )
        )
        uid = user.id
    engine.dispose()
    return uid


def create_company(
    ctx: TenantContext, name: str, org_number: str | None, *, assign_to: list[uuid.UUID] | None = None, **fields: object
) -> uuid.UUID:
    with tenant_session(ctx) as s:
        c = m.Company(org_id=ctx.org_id, name=name, org_number=org_number, **fields)
        s.add(c)
        s.flush()
        for uid in assign_to or []:
            s.add(m.CompanyAssignment(org_id=ctx.org_id, company_id=c.id, user_id=uid))
        return c.id


def seed_demo(as_of: date = date(2026, 10, 12), owner_url: str | None = None) -> CreatedOrg:
    """Skapa en demobyrå med fyra kunder och importera genererad bokföring."""
    from redovisningai.devdata.generator import DEMO_PROFILES, generate
    from redovisningai.jobs.pipeline import import_sie
    from redovisningai.sie.writer import write_sie4

    org = create_organization(
        "Demobyrån Redovisning AB", "anna@demobyran.se", "Anna Konsult", org_number="559999-0001", owner_url=owner_url
    )
    viewer = add_user(org.org_id, "lisa@demobyran.se", "Lisa Läsare", "VIEWER", owner_url=owner_url)
    admin_ctx = TenantContext(
        org.org_id, org.admin_user_id, "ADMIN", payroll=True, aml=True, user_email="anna@demobyran.se"
    )
    for p in DEMO_PROFILES:
        cid = create_company(
            admin_ctx,
            p.name,
            p.org_number,
            assign_to=[org.admin_user_id, viewer],
            legal_form=p.legal_form,
            industry=p.industry,
            vat_period=p.vat_period,
            food_retail=p.food_retail,
            source_system="fortnox" if p.seed in (11, 44) else "sie_file",
            settings={"person_names": ["Erik"]},
        )
        g = generate(p, as_of)
        for y in g.ledger.years:
            raw = write_sie4(g.ledger, y, include_previous_summary=False)
            import_sie(admin_ctx, cid, f"{p.name}-{y.fiscal_year.label}.se", raw, run_review=False)
        from redovisningai.jobs.pipeline import review_company

        review_company(TenantContext.worker(org.org_id), cid)
    return org
