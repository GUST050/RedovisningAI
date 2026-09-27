import uuid
from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from redovisningai.accounting.metrics import REGISTRY
from redovisningai.api import routes_company
from redovisningai.api.deps import Principal
from redovisningai.api.routes_other import _analysis_metadata_current
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis, CompanyContext


def _client(monkeypatch, *, can_payroll: bool = False, ledger: Ledger | None = None) -> tuple[TestClient, str]:  # type: ignore[no-untyped-def]
    company_id = uuid.uuid4()
    org_id = uuid.uuid4()
    principal = Principal(
        uuid.uuid4(),
        "consultant@example.test",
        "Consultant",
        org_id,
        "Test org",
        "CONSULTANT",
        can_payroll,
        False,
        False,
        (),
    )
    ledger = ledger if ledger is not None else generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger
    analysis = CompanyAnalysis(ledger, CompanyContext(str(company_id), str(org_id), "Demo AB"))
    monkeypatch.setattr(routes_company, "load_analysis", lambda _principal, _company_id: analysis)
    app = FastAPI()
    app.include_router(routes_company.router)
    app.dependency_overrides[routes_company.get_principal] = lambda: principal
    return TestClient(app), str(company_id)


def test_metric_comparison_api_returns_all_registry_codes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch)

    response = client.get(f"/api/companies/{company_id}/metric-comparisons?period=2026-09&mode=yoy")

    assert response.status_code == 200
    body = response.json()
    assert set(body["metrics"]) == set(REGISTRY)
    assert all("current_rows" not in metric for metric in body["metrics"].values())
    assert body["periods"] == {"current": "2026-09", "previous": "2025-09"}


def test_metric_explanation_api_masks_payroll_accounts_but_keeps_totals(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch)

    response = client.get(f"/api/companies/{company_id}/metric-explanations/personnel_share?period=2026-09&mode=yoy")

    assert response.status_code == 200
    body = response.json()
    assert body["current"] is not None
    all_rows_client, all_rows_company = _client(monkeypatch, can_payroll=True)
    privileged = all_rows_client.get(
        f"/api/companies/{all_rows_company}/metric-explanations/personnel_share?period=2026-09&mode=yoy"
    ).json()
    assert [c["evidence"]["current_total"] for c in body["components"]] == [
        c["evidence"]["current_total"] for c in privileged["components"]
    ]
    for component in body["components"]:
        for key in ("current_accounts", "previous_accounts"):
            assert all(not 7000 <= account["account"] <= 7699 for account in component[key])
        for key in ("current_rows", "previous_rows"):
            assert all(not 7000 <= row["account"] <= 7699 for row in component["evidence"][key])


def test_metric_comparison_api_rejects_unknown_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch)

    response = client.get(f"/api/companies/{company_id}/metric-comparisons?period=2026-09&mode=arbitrary")

    assert response.status_code == 422


def test_analysis_findings_are_bounded_and_hide_payroll_accounts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch)

    response = client.get(f"/api/companies/{company_id}/analysis-findings?period=2026-09&mode=yoy")

    assert response.status_code == 200
    body = response.json()
    assert len(body["top"]) <= 5
    assert body["count"] >= len(body["top"])
    assert all(
        not any(
            int(account) in PAYROLL
            for source in item["sources"]
            for account in source["accounts"].split(",")
            if account
        )
        for item in body["top"] + body["others"]
    )
    assert all(item["versions"]["priority"] for item in body["top"] + body["others"])


def test_explicit_comparison_flows_through_overview_and_analysis_fingerprint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch)
    response = client.get(f"/api/companies/{company_id}/overview?period=2026-09&compare=2026-08")
    assert response.status_code == 200
    assert response.json()["sections"]["month"]["compare"]["spec"] == "2026-08"

    analysis = routes_company.load_analysis(None, uuid.UUID(company_id))
    current, previous = analysis.period("2026-09"), analysis.period("2026-08")
    from redovisningai.ai.tasks import A3_PROMPT_VERSION

    metadata = {
        "task": "A3",
        "prompt_version": A3_PROMPT_VERSION,
        "period": current.spec,
        "compare_period": previous.spec,
        "source_fingerprint": analysis.source_fingerprint(current, previous, prompt_version=A3_PROMPT_VERSION),
    }
    assert _analysis_metadata_current(analysis, metadata)
    assert not _analysis_metadata_current(analysis, {**metadata, "prompt_version": "A3-old"})


def test_transaction_bridge_api_reconciles_and_hides_payroll_groups(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch)
    url = f"/api/companies/{company_id}/transaction-bridge"

    costs = client.get(url, params={"target": "account:6110", "period": "2026-09", "mode": "yoy"})
    payroll = client.get(url, params={"target": "account:7210", "period": "2026-09", "mode": "yoy"})

    assert costs.status_code == 200 and payroll.status_code == 200
    body = costs.json()
    assert sum(Decimal(p["effect"]) for p in body["parts"]) == Decimal(body["change"])
    assert any(g["name"] == "Staples" for g in body["groups"])
    assert payroll.json()["masked"] is True and payroll.json()["groups"] == []
    assert client.get(url, params={"target": "account:abc", "period": "2026-09"}).status_code == 422


def _reused_number_ledger() -> Ledger:
    rows = (Row(6110, Decimal("400")), Row(1930, Decimal("-400")))
    vouchers = [Voucher("A", "1", date(2026, 9, day), f"Städning {day}", rows) for day in (3, 17)]
    accounts = {n: Account(n, f"Konto {n}") for n in (1930, 6110)}
    year = YearData(FiscalYear(date(2026, 1, 1), date(2026, 12, 31)), vouchers)
    return Ledger("Syntetbolaget AB", None, accounts, [year])


def test_voucher_lookup_prefers_the_exact_date_when_a_number_is_reused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, company_id = _client(monkeypatch, ledger=_reused_number_ledger())

    later = client.get(f"/api/companies/{company_id}/vouchers/A1?on=2026-09-17")

    assert later.status_code == 200 and later.json()["date"].startswith("2026-09-17")
