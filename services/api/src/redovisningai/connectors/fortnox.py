"""Fortnox-koppling.

- Auktorisering: OAuth2 authorization code med `account_type=service` (service account). Därefter
  hämtas åtkomsttoken med client credentials och huvudet `TenantId` – inga refresh-tokens att hantera.
- Data: SIE 4 per räkenskapsår via `GET /3/sie/4?financialyear=<id>` (hela året i ett anrop).
- Rate limit: 25 anrop per 5 sekunder per klient-id och tenant (glidande fönster) – begränsas här.
- Leverantörsfakturor (V1.5): `GET /3/supplierinvoices` med sidindelning.

Endpoints och parametrar ska verifieras mot Fortnox utvecklardokumentation vid partnerregistrering.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from redovisningai.connectors.base import AuthorizationRevoked, ConnectorError, RemoteFiscalYear, SieExport

SCOPES = ["bookkeeping", "companyinformation", "supplierinvoice", "invoice"]


class RateLimiter:
    """Glidande fönster: högst `limit` anrop per `window` sekunder."""

    def __init__(self, limit: int = 25, window: float = 5.0, clock: Any = time.monotonic, sleep: Any = time.sleep):
        self.limit, self.window = limit, window
        self.calls: deque[float] = deque()
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= self.window:
                self.calls.popleft()
            if len(self.calls) >= self.limit:
                wait = self.window - (now - self.calls[0]) + 0.01
                self.sleep(wait)
                now = self.clock()
                while self.calls and now - self.calls[0] >= self.window:
                    self.calls.popleft()
            self.calls.append(now)


@dataclass(slots=True)
class FortnoxApp:
    client_id: str
    client_secret: str
    redirect_uri: str
    api_base: str = "https://api.fortnox.se"
    auth_base: str = "https://apps.fortnox.se"

    def authorize_url(self, state: str) -> str:
        q = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "access_type": "offline",
            "response_type": "code",
            "account_type": "service",
        }
        return f"{self.auth_base}/oauth-v1/auth?{urllib.parse.urlencode(q)}"

    def _basic(self) -> str:
        return "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()

    def exchange_code(self, code: str, http: httpx.Client | None = None) -> dict[str, Any]:
        """Byt engångskoden mot token. Svaret innehåller access_token; tenant-id läses ur token."""
        client = http or httpx.Client(timeout=30)
        r = client.post(
            f"{self.auth_base}/oauth-v1/token",
            headers={"Authorization": self._basic(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri},
        )
        if r.status_code != 200:
            raise ConnectorError(f"Fortnox token-utbyte misslyckades ({r.status_code})")
        data: dict[str, Any] = r.json()
        data["tenant_id"] = tenant_from_token(data.get("access_token", ""))
        return data

    def service_token(self, tenant_id: str, http: httpx.Client | None = None) -> str:
        client = http or httpx.Client(timeout=30)
        r = client.post(
            f"{self.auth_base}/oauth-v1/token",
            headers={
                "Authorization": self._basic(),
                "TenantId": tenant_id,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        if r.status_code in (400, 401, 403):
            raise AuthorizationRevoked("Fortnox-kopplingen är inte längre auktoriserad")
        if r.status_code != 200:
            raise ConnectorError(f"Fortnox token misslyckades ({r.status_code})")
        return str(r.json()["access_token"])


def tenant_from_token(token: str) -> str | None:
    """Läs tenant-id ur JWT (utan signaturkontroll – bara för att veta vilken kund kopplingen gäller)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        tid = claims.get("tenantId") or claims.get("tenant_id")
        return str(tid) if tid is not None else None
    except Exception:
        return None


_LIMITERS: dict[str, RateLimiter] = {}


class FortnoxConnector:
    source = "fortnox"

    def __init__(
        self, app: FortnoxApp, tenant_id: str, http: httpx.Client | None = None, limiter: RateLimiter | None = None
    ) -> None:
        self.app = app
        self.tenant_id = tenant_id
        self.http = http or httpx.Client(timeout=60)
        self.limiter = limiter or _LIMITERS.setdefault(f"{app.client_id}:{tenant_id}", RateLimiter())
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = self.app.service_token(self.tenant_id, self.http)
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(5):
            self.limiter.acquire()
            r = self.http.get(f"{self.app.api_base}{path}", params=params, headers=self._headers())
            if r.status_code == 401 and attempt == 0:
                self._token = None  # token har gått ut – hämta ny
                continue
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "2")))
                continue
            if r.status_code in (401, 403):
                raise AuthorizationRevoked(f"Fortnox nekade åtkomst ({r.status_code})")
            if r.status_code >= 400:
                raise ConnectorError(f"Fortnox {path}: {r.status_code}")
            return r
        raise ConnectorError(f"Fortnox {path}: för många försök")

    def fiscal_years(self) -> list[RemoteFiscalYear]:
        data = self._get("/3/financialyears").json()
        out = []
        for fy in data.get("FinancialYears", []):
            out.append(
                RemoteFiscalYear(str(fy["Id"]), date.fromisoformat(fy["FromDate"]), date.fromisoformat(fy["ToDate"]))
            )
        return sorted(out, key=lambda f: f.start)

    def export_sie4(self, year: RemoteFiscalYear) -> SieExport:
        r = self._get("/3/sie/4", params={"financialyear": year.remote_id})
        return SieExport(year, r.content)

    def supplier_invoices(self, from_date: date, to_date: date) -> list[dict[str, Any]]:
        """Leverantörsfakturor (motpart, belopp, förfallodatum, betalstatus) – för spend-analys."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "/3/supplierinvoices",
                params={"fromdate": from_date.isoformat(), "todate": to_date.isoformat(), "page": page},
            ).json()
            out.extend(data.get("SupplierInvoices", []))
            meta = data.get("MetaInformation", {})
            if page >= int(meta.get("@TotalPages", 1)):
                return out
            page += 1
