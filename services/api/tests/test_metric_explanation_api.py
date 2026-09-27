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


def test_metric_explanation_bridge_targets_are_valid_or_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`bridge_target` is server-validated: every non-null value must work, cash has none."""
    client, company_id = _client(monkeypatch)
    bridge_url = f"/api/companies/{company_id}/transaction-bridge"

    personnel = client.get(f"/api/companies/{company_id}/metric-explanations/personnel_share?period=2026-09&mode=yoy")
    assert personnel.status_code == 200
    components = personnel.json()["components"]
    assert any(component["bridge_target"] is not None for component in components)
    for component in components:
        target = component["bridge_target"]
        if target is None:
            continue
        bridge = client.get(bridge_url, params={"target": target, "period": "2026-09", "mode": "yoy"})
        assert bridge.status_code == 200

    cash = client.get(f"/api/companies/{company_id}/metric-explanations/cash?period=2026-09&mode=yoy")
    assert cash.status_code == 200
    assert all(component["bridge_target"] is None for component in cash.json()["components"])


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
    payroll_body = payroll.json()
    assert payroll_body["masked"] is True
    assert payroll_body["groups"] == []
    assert payroll_body["signals"] == {}
    assert payroll_body["identified_share_abs"] is None
    assert payroll_body["change"] is not None  # förändringen visas ändå, bara motparterna döljs
    for part in payroll_body["parts"]:
        assert part["current"] is None
        assert part["previous"] is None
        assert part["effect"] is None
        assert part["current_count"] is None
        assert part["previous_count"] is None
        assert part["count_effect"] is None
        assert part["amount_effect"] is None
    assert client.get(url, params={"target": "account:abc", "period": "2026-09"}).status_code == 422
    assert client.get(url, params={"target": "category:bogus", "period": "2026-09"}).status_code == 422
    assert client.get(url, params={"target": "account:999999", "period": "2026-09"}).status_code == 422


def test_finding_bridge_targets_match_the_finding_amount_in_magnitude(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Ett fynds bryggmål (ett resultatkonto eller flera på samma rad) förklarar exakt det fyndet;
    fynd som spänner över flera rader (t.ex. margin_pressure) får inget mål och skippas här."""
    client, company_id = _client(monkeypatch)
    bridge_url = f"/api/companies/{company_id}/transaction-bridge"

    findings = client.get(f"/api/companies/{company_id}/analysis-findings?period=2026-09&mode=yoy")
    assert findings.status_code == 200
    items = findings.json()["top"] + findings.json()["others"]
    checked = 0
    for item in items:
        target = item["bridge_target"]
        if target is None:
            continue
        bridge = client.get(bridge_url, params={"target": target, "period": "2026-09", "mode": "yoy"})
        assert bridge.status_code == 200
        assert abs(Decimal(bridge.json()["change"])) == abs(Decimal(item["amount_effect"]))
        checked += 1
    assert checked > 0  # underlaget faktiskt prövat, inte bara tomma listor


def test_finding_bridge_target_reaches_payroll_accounts_for_a_payroll_user(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Icke-lönekonton filtreras inte bort ur bryggmålet; en lönebehörig konsult ska kunna öppna
    bryggan för ett lönefynd (rutten maskerar den ändå för användare utan behörigheten)."""
    client, company_id = _client(monkeypatch, can_payroll=True)

    findings = client.get(f"/api/companies/{company_id}/analysis-findings?period=2026-09&mode=yoy")
    assert findings.status_code == 200
    items = findings.json()["top"] + findings.json()["others"]
    payroll_items = [
        item
        for item in items
        if any(int(a) in PAYROLL for source in item["sources"] for a in source["accounts"].split(",") if a)
    ]
    assert payroll_items
    assert any(item["bridge_target"] is not None for item in payroll_items)


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


def test_voucher_lookup_uses_source_line_for_reused_same_day_number(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ledger = _reused_number_ledger()
    same_day = date(2026, 9, 3)
    ledger.years[0].vouchers = [
        Voucher("A", "1", same_day, "First", (Row(6110, Decimal("100")),), source_line=1),
        Voucher("A", "1", same_day, "Second", (Row(6110, Decimal("200")),), source_line=10),
    ]
    client, company_id = _client(monkeypatch, ledger=ledger)

    response = client.get(f"/api/companies/{company_id}/vouchers/A1?on=2026-09-03&source_line=10")

    assert response.status_code == 200
    assert response.json()["text"] == "Second"


def test_transaction_bridge_rejects_summary_only_comparison(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    prior = YearData(
        FiscalYear(date(2025, 1, 1), date(2025, 12, 31)),
        period_balances={(date(2025, 9, 1), 6110): Decimal("800")},
        has_vouchers=False,
    )
    current = YearData(
        FiscalYear(date(2026, 1, 1), date(2026, 12, 31)),
        [
            Voucher(
                "A",
                "1",
                date(2026, 9, 1),
                "Faktura",
                (
                    Row(6110, Decimal("1000"), text="Firm"),
                    Row(1930, Decimal("-1000")),
                ),
            )
        ],
    )
    ledger = Ledger(
        "Syntetbolaget AB",
        None,
        {n: Account(n, f"Konto {n}") for n in (1930, 6110)},
        [prior, current],
    )
    client, company_id = _client(monkeypatch, ledger=ledger)

    response = client.get(f"/api/companies/{company_id}/transaction-bridge?target=account:6110&period=2026-09")

    assert response.status_code == 422
    assert "verifikationer" in response.json()["detail"]
