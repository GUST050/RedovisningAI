"""Autentisering, behörighet och gemensamma beroenden för API:t."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from redovisningai.config import get_settings
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.session import TenantContext, anonymous_session, tenant_session
from redovisningai.review.analysis import CompanyAnalysis


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    email: str
    name: str
    org_id: uuid.UUID
    org_name: str
    role: str
    can_payroll: bool
    can_aml: bool
    can_approve_reports: bool
    memberships: tuple[dict[str, Any], ...]

    @property
    def ctx(self) -> TenantContext:
        return TenantContext(
            self.org_id, self.user_id, self.role, payroll=self.can_payroll, aml=self.can_aml, user_email=self.email
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user": {"id": str(self.user_id), "email": self.email, "name": self.name},
            "org": {"id": str(self.org_id), "name": self.org_name},
            "role": self.role,
            "permissions": {
                "payroll": self.can_payroll,
                "aml": self.can_aml,
                "approve_reports": self.can_approve_reports,
                "write": self.role in ("ADMIN", "CONSULTANT"),
                "admin": self.role == "ADMIN",
            },
            "memberships": list(self.memberships),
        }


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    url = get_settings().oidc_jwks_url
    if not url:
        raise HTTPException(500, "OIDC är inte konfigurerat")
    return jwt.PyJWKClient(url)


def _subject_from_request(request: Request, authorization: str | None, dev_user: str | None) -> str:
    s = get_settings()
    if s.auth_mode == "dev":
        email = dev_user or request.cookies.get("rai_dev_user")
        if not email:
            raise HTTPException(401, "Inte inloggad")
        return f"dev:{email}"
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Token saknas")
    token = authorization.split(" ", 1)[1]
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=s.oidc_audience, issuer=s.oidc_issuer)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Ogiltig token") from exc
    return str(claims["sub"])


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    subject = _subject_from_request(request, authorization, x_dev_user)
    with anonymous_session() as s:
        rows = s.execute(text("select * from auth_memberships(:s)"), {"s": subject}).mappings().all()
    if not rows:
        raise HTTPException(403, "Användaren saknar behörighet till någon byrå")
    wanted = x_org_id or request.cookies.get("rai_org")
    row = next((r for r in rows if wanted and str(r["org_id"]) == wanted), rows[0])
    memberships = tuple({"org_id": str(r["org_id"]), "org_name": r["org_name"], "role": r["role"]} for r in rows)
    return Principal(
        user_id=row["user_id"],
        email=row["email"],
        name=row["name"],
        org_id=row["org_id"],
        org_name=row["org_name"],
        role=row["role"],
        can_payroll=row["can_payroll"],
        can_aml=row["can_aml"],
        can_approve_reports=row["can_approve_reports"],
        memberships=memberships,
    )


def db(principal: Principal = Depends(get_principal)) -> Iterator[Session]:
    with tenant_session(principal.ctx) as s:
        yield s


def require_write(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role not in ("ADMIN", "CONSULTANT"):
        raise HTTPException(403, "Läsbehörighet räcker inte för den här åtgärden")
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "ADMIN":
        raise HTTPException(403, "Kräver byråadmin")
    return principal


def require_aml(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.can_aml:
        raise HTTPException(403, "Kräver behörigheten PTL-ansvarig")
    return principal


def get_company(s: Session, company_id: uuid.UUID) -> m.Company:
    c = s.get(m.Company, company_id)
    if c is None:
        raise HTTPException(404, "Kunden finns inte eller saknar behörighet")
    return c


# ---------------------------------------------------------------------------- analyscache

_CACHE: OrderedDict[tuple[str, tuple[tuple[str, int], ...], str], CompanyAnalysis] = OrderedDict()
_LOCK = threading.Lock()
_MAX = 32


def load_analysis(principal: Principal, company_id: uuid.UUID) -> CompanyAnalysis:
    """Behörighet kontrolleras i användarens kontext; beräkningen görs sedan i arbetarkontext
    (lönerader behövs för korrekta totaler). Svar maskar lönerader för användare utan behörighet."""
    with tenant_session(principal.ctx) as s:
        company = get_company(s, company_id)
        seqs = tuple(sorted((str(k), v.seq) for k, v in repo.latest_imports(s, company_id).items()))
        settings_key = str(company.settings) + str(company.materiality) + company.vat_period
    key = (str(company_id), seqs, settings_key)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    with tenant_session(TenantContext.worker(principal.org_id)) as s:
        company = get_company(s, company_id)
        ledger, _ = repo.load_ledger(s, company)
        analysis = CompanyAnalysis(ledger, repo.company_context(s, company))
    with _LOCK:
        _CACHE[key] = analysis
        while len(_CACHE) > _MAX:
            _CACHE.popitem(last=False)
    return analysis


def invalidate_cache(company_id: uuid.UUID | None = None) -> None:
    with _LOCK:
        for k in list(_CACHE):
            if company_id is None or k[0] == str(company_id):
                _CACHE.pop(k, None)
