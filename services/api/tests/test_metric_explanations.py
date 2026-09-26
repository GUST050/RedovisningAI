from datetime import date
from decimal import Decimal

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair, comparison_pair
from redovisningai.accounting.metric_evidence import evidence_for_component
from redovisningai.accounting.metric_explanations import MetricComponent, explain_metric, ratio_effects
from redovisningai.accounting.metrics import REGISTRY, calculate_metric, change_fact
from redovisningai.accounting.periods import month, rolling
from redovisningai.accounting.statements import StatementMapping, balance_sheet
from redovisningai.ai.tasks import A3_PROMPT_VERSION
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData
from redovisningai.facts.model import FactStatus, FactStore, Unit
from redovisningai.review.analysis import CompanyAnalysis, CompanyContext
from redovisningai.rules.rates import default_rates
from redovisningai.sie.convert import ledger_from_documents
from redovisningai.sie.parser import parse_sie


def test_comparison_pair_uses_requested_calendar_period() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)

    previous = comparison_pair(month(2026, 9), "previous", index.ledger, index)
    year_ago = comparison_pair(month(2026, 9), "yoy", index.ledger, index)

    assert previous.previous.spec == "2026-08"
    assert year_ago.previous.spec == "2025-09"
    assert previous.status is FactStatus.CALCULATED
    assert year_ago.status is FactStatus.CALCULATED


def test_comparison_pair_marks_missing_months_incomplete() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)

    pair = comparison_pair(month(2019, 5), "yoy", index.ledger, index)

    assert pair.status is FactStatus.INSUFFICIENT_DATA
    assert pair.warnings
    assert "2019-05" in " ".join(pair.warnings)


def test_change_fact_rejects_incomplete_metric() -> None:
    store = FactStore()
    missing = store.new(
        "metric", "metric:cash", "Kassa", Decimal("0"), Unit.SEK, period="2019-05", status=FactStatus.INSUFFICIENT_DATA
    )
    valid = store.new("metric", "metric:cash", "Kassa", Decimal("10"), Unit.SEK, period="2026-09")

    assert change_fact(store, valid, missing, "föregående") is None


def test_balance_metric_is_incomplete_without_a_covered_year() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)

    fact = calculate_metric("cash", index, month(2019, 5))

    assert fact.value is None
    assert fact.status is FactStatus.INSUFFICIENT_DATA


def test_metric_facts_does_not_compare_different_period_shapes() -> None:
    ledger = generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger
    analysis = CompanyAnalysis(ledger, CompanyContext("company", "org", "Demo"))

    facts = analysis.metric_facts(month(2026, 9), rolling(date(2025, 9, 30), 12), FactStore(), ["net_sales"])

    assert facts["net_sales"]["comparison_status"] == FactStatus.INSUFFICIENT_DATA.value
    assert "change" not in facts["net_sales"]


def test_symmetric_ratio_effects_sum_to_unrounded_change() -> None:
    assert sum(ratio_effects(Decimal("50"), Decimal("100"), Decimal("60"), Decimal("120"))) == Decimal("0")
    assert sum(ratio_effects(Decimal("40"), Decimal("100"), Decimal("60"), Decimal("120"))) == Decimal("10")


def test_every_registered_metric_has_an_exact_explanation() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)
    pair = comparison_pair(month(2026, 9), "yoy", index.ledger, index)

    assert pair.status is FactStatus.CALCULATED
    explanations = {
        code: explain_metric(
            code,
            index,
            pair,
            mapping=StatementMapping(),
            rates=default_rates(),
            store=FactStore(),
        )
        for code in REGISTRY
    }

    assert set(explanations) == set(REGISTRY)
    for explanation in explanations.values():
        assert explanation.status is FactStatus.CALCULATED
        assert sum((component.effect for component in explanation.components), Decimal("0")) == explanation.change
        for component in explanation.components:
            if component.source_level == "account":
                assert sum(component.current_accounts.values(), Decimal("0")) == component.current
                assert sum(component.previous_accounts.values(), Decimal("0")) == component.previous

    quick = explanations["quick_ratio"]
    numerator = next(component for component in quick.components if component.code == "current_assets_ex_inventory")
    denominator = next(component for component in quick.components if component.code == "current_liabilities")
    expected_numerator, expected_denominator = ratio_effects(
        numerator.previous, denominator.previous, numerator.current, denominator.current
    )
    assert numerator.effect == expected_numerator
    assert denominator.effect == expected_denominator
    assert sum(numerator.current_accounts.values(), Decimal("0")) == numerator.current
    assert sum(numerator.previous_accounts.values(), Decimal("0")) == numerator.previous
    tax_parameter = next(
        component for component in explanations["equity_ratio"].components if component.code == "corporate_tax"
    )
    assert evidence_for_component(index, tax_parameter, pair).source_level == "parameter"


def test_balance_ratio_evidence_keeps_opening_and_voucher_amounts() -> None:
    year = YearData(
        fiscal_year=FiscalYear(date(2026, 1, 1), date(2026, 12, 31)),
        opening={1930: Decimal("100"), 2099: Decimal("-100")},
        vouchers=[
            Voucher(
                "A",
                "1",
                date(2026, 1, 15),
                "Försäljning",
                (Row(1930, Decimal("10")), Row(3010, Decimal("-10"))),
            )
        ],
    )
    ledger = Ledger("Test", "556000-0000", {1930: Account(1930, "Bank")}, [year])
    index = LedgerIndex.build(ledger)
    statement = balance_sheet(index, date(2026, 1, 31))
    period = month(2026, 1)
    pair = ComparisonPair(period, period, FactStatus.CALCULATED)
    explanation = explain_metric(
        "equity_ratio", index, pair, mapping=StatementMapping(), rates=default_rates(), store=FactStore()
    )
    equity_part = next(component for component in explanation.components if component.code == "total_equity")
    assets_part = next(component for component in explanation.components if component.code == "total_assets")
    assert sum(equity_part.current_accounts.values(), Decimal("0")) == equity_part.current
    assert sum(assets_part.current_accounts.values(), Decimal("0")) == assets_part.current
    assert equity_part.current_accounts[3010] == Decimal("10")

    component = MetricComponent(
        "current_assets",
        "Omsättningstillgångar",
        Decimal("110"),
        Decimal("110"),
        Decimal("0"),
        Unit.SEK,
        "account",
        {1930: Decimal("110")},
        {1930: Decimal("110")},
    )
    evidence = evidence_for_component(index, component, pair)
    assert [(row.source, row.amount) for row in evidence.current_rows] == [
        ("opening_balance", Decimal("100")),
        ("voucher", Decimal("10")),
    ]
    assert evidence.other_current == Decimal("0")

    equity_component = MetricComponent(
        "total_equity",
        statement.line("total_equity").label,
        equity_part.current,
        equity_part.previous,
        Decimal("0"),
        Unit.SEK,
        "account",
        equity_part.current_accounts,
        equity_part.previous_accounts,
    )
    equity_evidence = evidence_for_component(index, equity_component, pair)
    assert {(row.account, row.source, row.amount) for row in equity_evidence.current_rows} == {
        (2099, "opening_balance", Decimal("100")),
        (3010, "voucher", Decimal("10")),
    }
    assert equity_evidence.other_current == Decimal("0")


def test_account_evidence_reconciles_selection_to_total() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)
    pair = comparison_pair(rolling(date(2026, 9, 30), 12), "yoy", index.ledger, index)
    explanation = explain_metric(
        "net_sales", index, pair, mapping=StatementMapping(), rates=default_rates(), store=FactStore()
    )

    evidence = evidence_for_component(index, explanation.components[0], pair, limit=3)

    assert len(evidence.current_rows) <= 3
    assert len(evidence.previous_rows) <= 3
    assert (
        sum((row.amount for row in evidence.current_rows), Decimal("0")) + evidence.other_current
        == evidence.current_total
    )
    assert (
        sum((row.amount for row in evidence.previous_rows), Decimal("0")) + evidence.other_previous
        == evidence.previous_total
    )
    assert evidence.source_level == "account_voucher"
    assert all(row.content_hash for row in evidence.current_rows + evidence.previous_rows)


def test_removed_adjustment_rows_are_not_presented_as_effective_evidence() -> None:
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "golden_small.se"
    doc = parse_sie(fixture.read_bytes())
    index = LedgerIndex.build(ledger_from_documents([(doc, "golden")]))
    period = month(2026, 1)
    pair = ComparisonPair(period, period, FactStatus.CALCULATED)
    component = MetricComponent(
        "account:6110",
        "Kontorsmaterial",
        Decimal("-400"),
        Decimal("0"),
        Decimal("-400"),
        Unit.SEK,
        "account",
        {6110: Decimal("-400")},
        {},
    )

    evidence = evidence_for_component(index, component, pair, limit=8)

    assert evidence.current_total == Decimal("-400.00")
    assert all(row.amount != Decimal("-500.00") for row in evidence.current_rows)


def test_psaldo_evidence_reports_no_voucher_rows() -> None:
    fy = FiscalYear(date(2026, 1, 1), date(2026, 12, 31))
    year = YearData(
        fiscal_year=fy,
        opening={1930: Decimal("0")},
        period_balances={(date(2026, 1, 1), 1930): Decimal("100")},
        has_vouchers=False,
    )
    index = LedgerIndex.build(Ledger("Test", "556000-0000", {1930: Account(1930, "Bank")}, [year]))
    period = month(2026, 1)
    pair = ComparisonPair(period, period, FactStatus.CALCULATED)
    component = MetricComponent(
        "cash",
        "Kassa och bank",
        Decimal("100"),
        Decimal("100"),
        Decimal("0"),
        Unit.SEK,
        "account",
        {1930: Decimal("100")},
        {1930: Decimal("100")},
    )

    evidence = evidence_for_component(index, component, pair)

    assert evidence.current_rows == ()
    assert evidence.source_level == "period_balance"
    assert evidence.warnings


def test_commentary_can_use_explicit_month_pair_and_fingerprint_is_pair_bound() -> None:
    ledger = generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger
    analysis = CompanyAnalysis(ledger, CompanyContext("company", "org", "Demo"))
    review = analysis.review(month(2026, 9))

    package = analysis.commentary_package(review, compare_spec="2026-08")

    assert package["period"]["spec"] == "2026-09"
    assert package["compare"]["spec"] == "2026-08"
    current, previous = analysis.period("2026-09"), analysis.period("2026-08")
    year_ago = analysis.period("2025-09")
    assert analysis.source_fingerprint(
        current, previous, prompt_version=A3_PROMPT_VERSION
    ) != analysis.source_fingerprint(current, year_ago, prompt_version=A3_PROMPT_VERSION)
    assert analysis.source_fingerprint(
        current, previous, prompt_version=A3_PROMPT_VERSION
    ) == analysis.source_fingerprint(current, previous, prompt_version=A3_PROMPT_VERSION)


def test_custom_comparison_pair_accepts_any_period_of_same_shape() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)

    pair = comparison_pair(month(2026, 9), "custom", index.ledger, index, month(2026, 3))
    assert (pair.current.spec, pair.previous.spec, pair.status) == ("2026-09", "2026-03", FactStatus.CALCULATED)

    shape = comparison_pair(month(2026, 9), "custom", index.ledger, index, rolling(date(2026, 9, 30), 12))
    assert shape.status is FactStatus.INSUFFICIENT_DATA
    assert "inte direkt jämförbara" in " ".join(shape.warnings)

    import pytest

    with pytest.raises(ValueError):
        comparison_pair(month(2026, 9), "custom", index.ledger, index, month(2026, 9))
    with pytest.raises(ValueError):
        comparison_pair(month(2026, 9), "custom", index.ledger, index)


def test_new_metrics_follow_their_formulas() -> None:
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)
    period = month(2026, 9)
    from redovisningai.accounting.statements import income_statement

    income = income_statement(index, period)
    balance = balance_sheet(index, period.end)
    value = {code: calculate_metric(code, index, period).value for code in REGISTRY}
    assert value["gross_profit"] == income.line("net_sales").amount + income.line("materials").amount
    assert value["ebitda"] == income.line("operating_result").amount - income.line("depreciation").amount
    assert value["working_capital"] == (
        balance.line("current_assets").amount - balance.line("current_liabilities").amount
    )
    expected_share = (-income.line("other_external").amount / income.line("net_sales").amount * 100).quantize(
        Decimal("0.1")
    )
    assert value["external_cost_share"] == expected_share
    assert REGISTRY["external_cost_share"].better == "lower"
    assert REGISTRY["receivables"].better == "neutral"


def test_full_year_comparison_works_with_summary_year_from_single_sie_file() -> None:
    from redovisningai.accounting.periods import parse_period
    from redovisningai.sie.writer import write_sie4

    generated = generate(DEMO_PROFILES[1], date(2026, 9, 30)).ledger
    year_2025 = next(y for y in generated.years if y.fiscal_year.start == date(2025, 1, 1))
    doc = parse_sie(write_sie4(generated, year_2025))
    single = LedgerIndex.build(ledger_from_documents([(doc, "2025.se")]))
    full = LedgerIndex.build(generated)
    fy_2024 = parse_period("FY:2024-01", single.ledger)
    pair = comparison_pair(parse_period("FY:2025-01", single.ledger), "yoy", single.ledger, single)

    assert pair.previous.spec == "FY:2024-01"
    assert pair.status is FactStatus.CALCULATED
    for code in REGISTRY:
        assert calculate_metric(code, single, fy_2024).value == calculate_metric(code, full, fy_2024).value, code
    explanation = explain_metric(
        "operating_margin", single, pair, mapping=StatementMapping(), rates=default_rates(), store=FactStore()
    )
    assert explanation.status is FactStatus.CALCULATED
    assert sum((c.effect for c in explanation.components), Decimal(0)) == explanation.change
    # Enskilda månader i sammandragsåret är inte kända.
    assert calculate_metric("net_sales", single, month(2024, 5)).status is FactStatus.INSUFFICIENT_DATA
    component = next(c for c in explanation.components if c.code == "net_sales")
    assert evidence_for_component(single, component, pair).source_level == "account_voucher"


def test_closed_summary_year_is_not_double_counted() -> None:
    """Fortnox-SIE: föregående år är bokslutsfört (resultatet ligger i #UB 2099) men #RES saknar 8999."""
    from pathlib import Path

    from redovisningai.accounting.periods import parse_period

    raw = (Path(__file__).parent / "fixtures" / "fortnox_like.se").read_bytes()
    index = LedgerIndex.build(ledger_from_documents([(parse_sie(raw), "fortnox_like.se")]))
    fy_2025 = parse_period("FY:2025-01", index.ledger)
    sheet = balance_sheet(index, fy_2025.end)

    assert sheet.line("total_assets").amount == sheet.line("total_equity_liabilities").amount == Decimal("52000.00")
    assert calculate_metric("equity_ratio", index, fy_2025).value == Decimal("100.0")
    assert calculate_metric("operating_result", index, fy_2025).value == Decimal("12000.00")
    # Nästa års härledda IB bygger på samma UB.
    assert index.opening_balances(index.ledger.years[-1])[2099] == Decimal("-27000.00")
