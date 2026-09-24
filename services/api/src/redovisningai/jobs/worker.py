"""Bakgrundsjobb via Procrastinate (jobbkö i PostgreSQL – ingen separat broker).

Starta arbetaren:   procrastinate --app=redovisningai.jobs.worker.app worker
Skapa köns schema:  procrastinate --app=redovisningai.jobs.worker.app schema --apply  (som ägarrollen)

Jobbens argument innehåller bara id:n – aldrig kunddata.
"""

from __future__ import annotations

import logging
import uuid

import procrastinate
from sqlalchemy import select

from redovisningai.ai.factory import build_ai_service
from redovisningai.config import get_settings
from redovisningai.db import models as m
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.jobs import pipeline

log = logging.getLogger(__name__)


def _conninfo() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=_conninfo()))


@app.task(queue="review", retry=2)
def review_company_task(org_id: str, company_id: str, periods: list[str] | None = None) -> list[str]:
    return pipeline.review_company(
        TenantContext.worker(uuid.UUID(org_id)),
        uuid.UUID(company_id),
        periods=periods,
        ai=build_ai_service(uuid.UUID(org_id)),
    )


@app.task(queue="sync", retry=3)
def sync_company_task(org_id: str, company_id: str) -> dict[str, object]:
    """Hämta bokföring via kopplingen (t.ex. Fortnox) och importera."""
    from redovisningai.connectors.sync import sync_company

    return sync_company(uuid.UUID(org_id), uuid.UUID(company_id))


@app.periodic(cron="0 1 * * *", periodic_id="nightly_sync")
@app.task(queue="sync")
def nightly_sync(timestamp: int) -> int:
    """Lägg synkjobb för alla bolag med aktiv koppling, spridda över natten."""
    from sqlalchemy import text

    from redovisningai.db.session import anonymous_session

    n = 0
    with anonymous_session() as s:
        orgs = [r[0] for r in s.execute(text("select * from worker_org_ids()"))]
    for org_id in orgs:
        with tenant_session(TenantContext.worker(org_id)) as s:
            for c in s.scalars(select(m.Connection).where(m.Connection.status == "OK")).all():
                sync_company_task.configure(lock=f"sync:{c.company_id}").defer(
                    org_id=str(org_id), company_id=str(c.company_id)
                )
                n += 1
    log.info("Nattlig synk: %d jobb", n)
    return n


@app.periodic(cron="30 3 * * *", periodic_id="retention")
@app.task(queue="maintenance")
def retention_task(timestamp: int) -> int:
    """Rensa AI-spår och källfiler enligt retention (se docs/PLAN.md §11)."""
    from redovisningai.jobs.maintenance import apply_retention

    return apply_retention()
