from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.periods import (
    PeriodKind,
    month,
    parse_period,
    previous_period,
    rolling,
    same_period_previous_year,
    ytd,
)
from redovisningai.accounting.statements import balance_sheet, income_statement
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import FiscalYear
from redovisningai.sie.convert import ledger_from_documents
from redovisningai.sie.parser import parse_sie

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def golden_index() -> LedgerIndex:
    doc = parse_sie((FIXTURES / "golden_small.se").read_bytes())
    return LedgerIndex.build(ledger_from_documents([(doc, "golden")]))


@pytest.fixture(scope="module")
def bygg() -> LedgerIndex:
    return LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)


@pytest.fixture(scope="module")
def konsult() -> LedgerIndex:
    return LedgerIndex.build(generate(DEMO_PROFILES[2], date(2026, 9, 30)).ledger)


def test_period_parsing_and_comparisons() -> None:
    p = parse_period("2026-09")
    assert (p.start, p.end, p.kind) == (date(2026, 9, 1), date(2026, 9, 30), PeriodKind.MONTH)
    assert previous_period(p).spec == "2026-08"
    assert same_period_previous_year(p).spec == "2025-09"
    q = parse_period("2026-Q1")
    assert (q.start, q.end) == (date(2026, 1, 1), date(2026, 3, 31))
    assert previous_period(q).spec == "2025-Q4"
    r = parse_period("R12:2026-09")
    assert (r.start, r.end) == (date(2025, 10, 1), date(2026, 9, 30))
    assert same_period_previous_year(r).end == date(2025, 9, 30)
    assert parse_period("2026-02-01..2026-02-28").months_count == 1


def test_ytd_with_broken_fiscal_year() -> None:
    fy = FiscalYear(date(2026, 7, 1), date(2027, 6, 30))
    p = ytd(fy, date(2026, 9, 15))
    assert (p.start, p.end, p.months_count) == (date(2026, 7, 1), date(2026, 9, 30), 3)
    prev = same_period_previous_year(p)
    assert (prev.start, prev.end) == (date(2025, 7, 1), date(2025, 9, 30))


def test_golden_income_statement_exact(golden_index: LedgerIndex) -> None:
    st = income_statement(golden_index, month(2026, 1))
    assert st.line("net_sales").amount == Decimal("40000.00")
    assert st.line("other_external").amount == Decimal("-20400.00")  # 20 000 hyra + 400 material (rättad rad)
    assert st.line("personnel").amount == Decimal("-30000.00")
    assert st.line("operating_result").amount == Decimal("-10400.00")
    assert st.line("net_result").amount == Decimal("-10400.00")


def test_golden_balance_sheet_balances(golden_index: LedgerIndex) -> None:
    bs = balance_sheet(golden_index, date(2026, 1, 31))
    assert bs.line("cash").amount == Decimal("69600.00")
    assert bs.line("receivables").amount == Decimal("50000.00")
    # Momskonton (26xx) ligger kvar bland kortfristiga skulder enligt BAS: 10 000 − 5 000 + 25 000 (2440)
    assert bs.line("current_liabilities").amount == Decimal("30000.00")
    assert bs.line("current_result").amount == Decimal("-10400.00")
    assert bs.line("total_assets").amount == bs.line("total_equity_liabilities").amount


@pytest.mark.parametrize("at", [date(2024, 3, 31), date(2025, 12, 31), date(2026, 6, 15), date(2026, 9, 30)])
def test_generated_balance_sheet_always_balances(bygg: LedgerIndex, at: date) -> None:
    bs = balance_sheet(bygg, at)
    # Bygg & Co har en planterad obalanserad verifikation (1 kr) i maj 2026.
    tolerance = Decimal("1.00") if at >= date(2026, 5, 20) else Decimal("0")
    assert abs(bs.line("total_assets").amount - bs.line("total_equity_liabilities").amount) <= tolerance


def test_broken_fiscal_year_balance(konsult: LedgerIndex) -> None:
    bs = balance_sheet(konsult, date(2026, 9, 30))
    assert bs.line("total_assets").amount == bs.line("total_equity_liabilities").amount
    assert konsult.ledger.current.fiscal_year.start == date(2026, 7, 1)


def test_movement_and_series(bygg: LedgerIndex) -> None:
    sales = bygg.movement(AccountSet.of((3000, 3799)), rolling(date(2026, 9, 1), 12))
    assert sales < 0
    series = bygg.monthly_series(6550, [date(2026, m, 1) for m in range(1, 10)])
    assert series[:3] == [Decimal(0)] * 3 and all(x > 0 for x in series[3:])


def test_income_statement_with_comparison(bygg: LedgerIndex) -> None:
    p = parse_period("YTD:2026-09", bygg.ledger)
    st = income_statement(bygg, p, same_period_previous_year(p))
    ns = st.line("net_sales")
    assert ns.compare is not None and ns.diff is not None and ns.diff_pct is not None
    assert st.complete


def test_metrics_have_status_and_lineage(bygg: LedgerIndex) -> None:
    from redovisningai.accounting.metrics import CORE_METRICS, calculate_metric
    from redovisningai.facts.model import FactStatus, FactStore, render_value

    store = FactStore()
    p = parse_period("YTD:2026-09", bygg.ledger)
    facts = {c: calculate_metric(c, bygg, p, store=store) for c in CORE_METRICS}
    assert all(f.status in (FactStatus.CALCULATED, FactStatus.PARTIAL) for f in facts.values())
    assert facts["net_sales"].value is not None and facts["net_sales"].value > 0
    assert "formula" in facts["equity_ratio"].lineage
    assert render_value(facts["operating_margin"]).endswith("%")
    # Period utan data → INSUFFICIENT_DATA, aldrig 0
    missing = calculate_metric("net_sales", bygg, parse_period("2019-05"), store=store)
    assert missing.status is FactStatus.INSUFFICIENT_DATA


def test_result_bridge_sums_exactly(bygg: LedgerIndex) -> None:
    from redovisningai.accounting.variance import result_bridge

    p = parse_period("YTD:2026-09", bygg.ledger)
    b = result_bridge(bygg, p, same_period_previous_year(p))
    assert sum((c.effect for c in b.components), Decimal(0)) == b.change
    shares = [c.share_of_worsening for c in b.components if c.share_of_worsening is not None]
    assert sum(shares, Decimal(0)) == pytest.approx(Decimal(100), abs=Decimal("0.5"))
    assert all(c.fact_id for c in b.components)


def test_drilldown_finds_new_consultant(bygg: LedgerIndex) -> None:
    from redovisningai.accounting.variance import drilldown

    p = parse_period("YTD:2026-09", bygg.ledger)
    d = drilldown(bygg, {6550}, p, same_period_previous_year(p))
    assert d.counterparties[0].is_new
    assert "Konsultgruppen" in d.counterparties[0].name
    assert d.top_vouchers


def test_cost_tree_totals(bygg: LedgerIndex) -> None:
    from redovisningai.accounting.categories import cost_tree

    p = parse_period("YTD:2026-09", bygg.ledger)
    tree = cost_tree(bygg, p, same_period_previous_year(p))
    assert sum((c.amount for c in tree.children), Decimal(0)) == tree.amount
    tech = next(c for c in tree.children if c.code == "technology")
    assert {c.code for c in tech.children} >= {"software", "telecom"}


def test_fact_rendering_swedish() -> None:
    from redovisningai.facts.model import format_percent, format_sek

    assert format_sek(Decimal("1240000")) == "1,24 Mkr"
    assert format_sek(Decimal("412300")) == "412 tkr"
    assert format_sek(Decimal("-8450")) == "−8 450 kr"
    assert format_sek(Decimal("330000"), signed=True) == "+330 tkr"
    assert format_percent(Decimal("36.24"), signed=True) == "+36,2 %"
