"""Databassessioner med tenant-kontext.

Varje session öppnar en transaktion och sätter kontexten med set_config(..., true), vilket
motsvarar SET LOCAL: inställningen gäller bara transaktionen och kan därför aldrig läcka till
nästa klient som får samma anslutning från en pool (t.ex. PgBouncer i transaktionsläge).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from redovisningai.config import get_settings


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    role: str  # ADMIN | CONSULTANT | VIEWER | WORKER | PUBLIC_QUESTION
    payroll: bool = False
    aml: bool = False
    user_email: str | None = None
    question_id: uuid.UUID | None = None

    @staticmethod
    def worker(org_id: uuid.UUID) -> TenantContext:
        """Bakgrundsjobb: hela byrån, inkl. lönerader och PTL (för beräkningar – aldrig för visning)."""
        return TenantContext(org_id=org_id, user_id=None, role="WORKER", payroll=True, aml=True, user_email="system")


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache(maxsize=4)
def _factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


def apply_context(session: Session, ctx: TenantContext) -> None:
    session.execute(
        text(
            "SELECT set_config('app.org_id', :org, true), set_config('app.user_id', :usr, true), "
            "set_config('app.role', :role, true), set_config('app.payroll', :payroll, true), "
            "set_config('app.aml', :aml, true), set_config('app.question_id', :q, true)"
        ),
        {
            "org": str(ctx.org_id),
            "usr": str(ctx.user_id) if ctx.user_id else "",
            "role": ctx.role,
            "payroll": "on" if ctx.payroll else "off",
            "aml": "on" if ctx.aml else "off",
            "q": str(ctx.question_id) if ctx.question_id else "",
        },
    )


@contextmanager
def tenant_session(ctx: TenantContext, url: str | None = None) -> Iterator[Session]:
    """Session i en transaktion med tenant-kontext. Commit vid lyckat block, annars rollback."""
    session = _factory(url)()
    try:
        with session.begin():
            apply_context(session, ctx)
            yield session
    finally:
        session.close()


@contextmanager
def anonymous_session(url: str | None = None) -> Iterator[Session]:
    """Session utan tenant-kontext – ser inga kunddata (används för inloggningsuppslag)."""
    session = _factory(url)()
    try:
        with session.begin():
            yield session
    finally:
        session.close()
