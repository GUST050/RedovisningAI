"""Synkronisering från koppling till import (nattligt eller manuellt)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from redovisningai.config import get_settings
from redovisningai.connectors.base import AuthorizationRevoked, ConnectorError, SourceConnector
from redovisningai.connectors.fortnox import FortnoxApp, FortnoxConnector
from redovisningai.db import models as m
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.jobs.pipeline import import_sie

log = logging.getLogger(__name__)
HISTORY_YEARS = 3  # innevarande + två föregående vid onboarding


def connector_for(conn: m.Connection) -> SourceConnector:
    s = get_settings()
    if conn.source == "fortnox":
        if not (s.fortnox_client_id and s.fortnox_client_secret and conn.tenant_ref):
            raise ConnectorError("Fortnox är inte konfigurerat eller kopplingen saknar tenant.")
        app = FortnoxApp(
            s.fortnox_client_id,
            s.fortnox_client_secret,
            s.fortnox_redirect_uri,
            s.fortnox_api_base,
            s.fortnox_auth_base,
        )
        return FortnoxConnector(app, conn.tenant_ref)
    raise ConnectorError(f"Kopplingstypen {conn.source} stöds inte för automatisk hämtning.")


def sync_company(org_id: uuid.UUID, company_id: uuid.UUID, connector: SourceConnector | None = None) -> dict[str, Any]:
    worker = TenantContext.worker(org_id)
    with tenant_session(worker) as s:
        conn = s.scalar(
            select(m.Connection).where(m.Connection.company_id == company_id, m.Connection.source != "sie_file")
        )
        if conn is None:
            return {"status": "no_connection"}
        conn_id = conn.id
        try:
            connector = connector or connector_for(conn)
        except ConnectorError as exc:
            conn.status, conn.last_error = "ERROR", str(exc)
            return {"status": "error", "error": str(exc)}
    imported, errors = [], []
    try:
        years = connector.fiscal_years()[-HISTORY_YEARS:]
        for fy in years:
            export = connector.export_sie4(fy)
            res = import_sie(
                worker, company_id, f"{connector.source}-{fy.start:%Y%m%d}.se", export.content, source=connector.source
            )
            if not res.skipped_duplicate:
                imported.append(fy.start.isoformat())
        status, error = "OK", None
    except AuthorizationRevoked as exc:
        status, error = "REVOKED", str(exc)
    except ConnectorError as exc:
        status, error = "ERROR", str(exc)
        errors.append(str(exc))
    with tenant_session(worker) as s:
        conn = s.get(m.Connection, conn_id)
        if conn is not None:
            conn.status = status
            conn.last_error = error
            if status == "OK":
                conn.last_success_at = datetime.now().astimezone()
    return {"status": status, "imported_years": imported, "errors": errors}
