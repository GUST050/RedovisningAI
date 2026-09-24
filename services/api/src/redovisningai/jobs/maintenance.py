"""Retention: rensa AI-spår och källfiler enligt byråns inställningar."""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import delete, select, text

from redovisningai.db import models as m
from redovisningai.db.session import TenantContext, anonymous_session, tenant_session
from redovisningai.storage.objects import get_object_store

log = logging.getLogger(__name__)


def apply_retention(today: date | None = None) -> int:
    today = today or date.today()
    removed = 0
    with anonymous_session() as s:
        orgs = [r[0] for r in s.execute(text("select * from worker_org_ids()"))]
    store = get_object_store()
    for org_id in orgs:
        with tenant_session(TenantContext.worker(org_id)) as s:
            res = s.execute(delete(m.AITraceRow).where(m.AITraceRow.expires_at < datetime.now().astimezone()))
            removed += res.rowcount or 0
            for sf in s.scalars(select(m.SourceFile).where(m.SourceFile.delete_after < today)).all():
                store.delete(sf.object_key)
                sf.object_key = f"deleted:{sf.object_key}"
                sf.delete_after = None
                removed += 1
    log.info("Retention: %d objekt borttagna", removed)
    return removed
