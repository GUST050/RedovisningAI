import base64
import json
from datetime import date

import httpx
import pytest

from redovisningai.connectors.base import AuthorizationRevoked
from redovisningai.connectors.fortnox import FortnoxApp, FortnoxConnector, RateLimiter, tenant_from_token
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.sie.parser import parse_sie
from redovisningai.sie.writer import write_sie4

APP = FortnoxApp("cid", "secret", "https://app.example/cb")


def _jwt(claims: dict) -> str:
    b = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{b}.s"


def test_authorize_url_requests_service_account() -> None:
    url = APP.authorize_url("state123")
    assert "account_type=service" in url and "state=state123" in url and "bookkeeping" in url


def test_tenant_from_token() -> None:
    assert tenant_from_token(_jwt({"tenantId": 4711})) == "4711"
    assert tenant_from_token("garbage") is None


def test_connector_fetches_years_and_sie_with_retry() -> None:
    g = generate(DEMO_PROFILES[0], date(2026, 10, 12))
    sie = write_sie4(g.ledger, g.ledger.current)
    calls = {"sie": 0, "token": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth-v1/token":
            calls["token"] += 1
            assert req.headers["TenantId"] == "4711"
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok"})
        assert req.headers["Authorization"] == "Bearer tok"
        if req.url.path == "/3/financialyears":
            return httpx.Response(
                200,
                json={
                    "FinancialYears": [
                        {"Id": 2, "FromDate": "2026-01-01", "ToDate": "2026-12-31"},
                        {"Id": 1, "FromDate": "2025-01-01", "ToDate": "2025-12-31"},
                    ]
                },
            )
        if req.url.path == "/3/sie/4":
            calls["sie"] += 1
            if calls["sie"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            assert req.url.params["financialyear"] == "2"
            return httpx.Response(200, content=sie)
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.fortnox.se")
    app = FortnoxApp("cid", "secret", "cb", api_base="https://api.fortnox.se", auth_base="https://apps.fortnox.se")
    conn = FortnoxConnector(app, "4711", http=http, limiter=RateLimiter(100, 5))
    years = conn.fiscal_years()
    assert [y.remote_id for y in years] == ["1", "2"]
    export = conn.export_sie4(years[-1])
    assert calls["sie"] == 2 and calls["token"] == 1
    assert len(parse_sie(export.content).vouchers) == len(g.ledger.current.vouchers)


def test_revoked_authorization() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401 if req.url.path == "/oauth-v1/token" else 200, json={})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    conn = FortnoxConnector(APP, "1", http=http, limiter=RateLimiter(100, 5))
    with pytest.raises(AuthorizationRevoked):
        conn.fiscal_years()


def test_rate_limiter_sliding_window() -> None:
    now = [0.0]
    slept = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    rl = RateLimiter(limit=25, window=5.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(25):
        rl.acquire()
    assert not slept
    rl.acquire()
    assert slept and 4.9 < slept[0] <= 5.1
