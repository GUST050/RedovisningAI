from calendar import monthrange
from datetime import date
from decimal import Decimal

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metric_explanations import MetricComponent, MetricExplanation
from redovisningai.accounting.periods import month
from redovisningai.analytics.finding_candidates import collect_candidates
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData
from redovisningai.facts.model import FactStatus, Unit
from redovisningai.review.finding_priorities import rank_findings


def _explanation(code: str, effect: str, accounts: dict[int, str], status=FactStatus.CALCULATED) -> MetricExplanation:
    components = tuple(
        MetricComponent(
            f"account:{account}",
            f"Konto {account}",
            Decimal(amount),
            Decimal("0"),
            Decimal(amount),
            Unit.SEK,
            "account_voucher",
            {account: Decimal(amount)},
            {},
            f"fact:{code}:{account}",
        )
        for account, amount in ((int(k), v) for k, v in accounts.items())
    )
    return MetricExplanation(
        code,
        code,
        Unit.SEK,
        Decimal(effect),
        Decimal("0"),
        Decimal(effect),
        status,
        components,
        (),
        {"current": "2026-09", "previous": "2025-09"},
        {"mapping": "1"},
        tuple(c.fact_id for c in components if c.fact_id),
    )


def test_candidates_group_same_account_evidence_and_keep_period_lineage() -> None:
    current, previous = month(2026, 9), month(2025, 9)
    pair = ComparisonPair(current, previous, FactStatus.CALCULATED)
    explanations = [
        _explanation("net_sales", "12000", {"3010": "12000"}),
        _explanation("operating_margin", "4", {"3010": "4"}),
        _explanation("operating_result", "12000", {"6110": "12000"}),
    ]
    index = LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)

    candidates = collect_candidates(index, pair, explanations, mapping_version="mapping-v1")

    assert len(candidates) == 2
    sales = next(candidate for candidate in candidates if candidate.group_key == "accounts:3010")
    assert set(sales.fact_ids) == {"fact:net_sales:3010", "fact:operating_margin:3010"}
    assert sales.period_pair == ("2026-09", "2025-09")
    assert sales.versions["mapping"] == "mapping-v1"
    costs = next(candidate for candidate in candidates if candidate.group_key == "accounts:6110")
    assert any(reference["content_hash"] for source in costs.sources for reference in source["references"])
    ranked = rank_findings(candidates)
    assert ranked.top[0].group_key == "accounts:3010"
    assert ranked.others == ()


def test_incomplete_explanations_and_zero_effects_never_become_candidates() -> None:
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.INSUFFICIENT_DATA, ("missing month",))
    incomplete = _explanation("net_sales", "50000", {"3010": "50000"}, FactStatus.PARTIAL)
    zero = _explanation("cash", "0", {"1930": "0"})

    assert collect_candidates(None, pair, [incomplete, zero], mapping_version="mapping-v1") == []


def test_ranked_result_retains_non_top_candidates_with_deterministic_reasons() -> None:
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)
    explanations = [_explanation(f"m{i}", str(i * 1000), {str(6000 + i): str(i * 1000)}) for i in range(1, 8)]

    ranked = rank_findings(collect_candidates(None, pair, explanations, mapping_version="m1"), limit=5)

    assert len(ranked.top) == 5
    assert len(ranked.others) == 2
    assert all(item.demotion_reasons for item in ranked.others)


def test_exact_duplicate_postings_are_human_review_candidates_not_conclusions() -> None:
    posting = (Row(6110, Decimal("1000")), Row(2641, Decimal("250")), Row(2440, Decimal("-1250")))
    voucher_date = date(2026, 9, 10)
    year = YearData(
        FiscalYear(date(2026, 1, 1), date(2026, 12, 31)),
        vouchers=[
            Voucher("A", "1", voucher_date, "Leverantör A", posting),
            Voucher("B", "2", voucher_date, "Annat beskrivningsfält", posting),
        ],
    )
    index = LedgerIndex.build(
        Ledger(
            "Demo",
            None,
            {6110: Account(6110, "Kostnad"), 2641: Account(2641, "Moms"), 2440: Account(2440, "Leverantörsskuld")},
            [year],
        )
    )
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)

    candidates = collect_candidates(index, pair, [], mapping_version="map-v1")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.code == "possible_duplicate"
    assert candidate.amount_effect == Decimal("1000")
    assert candidate.fact_ids == ()
    assert len(candidate.sources[0]["references"]) == 2
    assert "inte bevis" in candidate.warnings[0]
    assert all("text" not in reference for reference in candidate.sources[0]["references"])


def test_recurrent_cost_change_requires_a_confirmed_alias_and_full_history() -> None:
    vouchers = []
    number = 1
    for year in (2025, 2026):
        end_month = 12 if year == 2025 else 9
        for month_number in range(1, end_month + 1):
            occurrence_count = 3 if (year, month_number) == (2026, 9) else 1
            for occurrence in range(occurrence_count):
                amount = Decimal("1000")
                voucher_date = date(year, month_number, min(10 + occurrence, monthrange(year, month_number)[1]))
                vouchers.append(
                    Voucher(
                        "A",
                        str(number),
                        voucher_date,
                        "Faktura ACME",
                        (Row(6110, amount, text="ACME"), Row(2440, -amount)),
                    )
                )
                number += 1
    ledger = Ledger(
        "Demo",
        None,
        {6110: Account(6110, "Kostnad"), 2440: Account(2440, "Leverantörsskuld")},
        [
            YearData(FiscalYear(date(2025, 1, 1), date(2025, 12, 31)), vouchers[:12]),
            YearData(FiscalYear(date(2026, 1, 1), date(2026, 12, 31)), vouchers[12:]),
        ],
    )
    index = LedgerIndex.build(ledger)
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)

    unconfirmed = collect_candidates(index, pair, [], mapping_version="map-v1")
    confirmed = collect_candidates(index, pair, [], mapping_version="map-v1", aliases={"acme": "ACME AB"})

    assert not unconfirmed
    recurring = next(candidate for candidate in confirmed if candidate.code == "recurring_cost_change")
    frequency = next(candidate for candidate in confirmed if candidate.code == "transaction_frequency_change")
    assert recurring.amount_effect == Decimal("2000")
    assert recurring.sources[0]["recurrence"] == "monthly"
    assert recurring.sources[0]["references"]
    assert frequency.amount_effect == Decimal("2")
    assert frequency.sources[0]["current_count"] == 3
    assert frequency.sources[0]["previous_count"] == 1


def test_single_occurrence_is_not_promoted_to_a_recurring_cost_candidate() -> None:
    years = []
    for year in (2025, 2026):
        vouchers = []
        if year == 2026:
            vouchers.append(
                Voucher(
                    "A",
                    "one-off",
                    date(2026, 9, 10),
                    "ACME engångsköp",
                    (Row(6110, Decimal("5000"), text="ACME"), Row(2440, Decimal("-5000"))),
                )
            )
        years.append(YearData(FiscalYear(date(year, 1, 1), date(year, 12, 31)), vouchers))
    index = LedgerIndex.build(
        Ledger("Demo", None, {6110: Account(6110, "Kostnad"), 2440: Account(2440, "Leverantörsskuld")}, years)
    )
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)

    candidates = collect_candidates(index, pair, [], mapping_version="map-v1", aliases={"acme": "ACME AB"})

    assert not candidates


def test_level_shift_requires_two_years_of_vouchers_and_links_both_sides() -> None:
    years = []
    for year in (2024, 2025, 2026):
        vouchers = []
        start_month, end_month = (1, 12) if year < 2026 else (1, 9)
        for month_number in range(start_month, end_month + 1):
            if year == 2024:
                continue
            amount = Decimal("3000") if (year, month_number) >= (2026, 4) else Decimal("1000")
            vouchers.append(
                Voucher(
                    "A",
                    f"{year}-{month_number}",
                    date(year, month_number, 10),
                    "Faktura ACME",
                    (Row(6110, amount, text="ACME"), Row(2440, -amount)),
                )
            )
        years.append(YearData(FiscalYear(date(year, 1, 1), date(year, 12, 31)), vouchers))
    index = LedgerIndex.build(
        Ledger("Demo", None, {6110: Account(6110, "Kostnad"), 2440: Account(2440, "Leverantörsskuld")}, years)
    )
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)

    candidates = collect_candidates(index, pair, [], mapping_version="map-v1", aliases={"acme": "ACME AB"})

    shift = next(candidate for candidate in candidates if candidate.code == "recurring_level_shift")
    assert shift.sources[0]["break_month"] == "2026-04-01"
    assert shift.sources[0]["before_monthly_average"] == "833.33"
    assert shift.sources[0]["after_monthly_average"] == "3000.00"
    assert shift.sources[0]["references"]


def test_repeated_quarterly_seasonality_does_not_create_a_level_shift() -> None:
    years = []
    for year in (2024, 2025, 2026):
        vouchers = []
        if year > 2024:
            last_month = 12 if year == 2025 else 9
            for month_number in range(2, last_month + 1, 3):
                vouchers.append(
                    Voucher(
                        "A",
                        f"{year}-{month_number}",
                        date(year, month_number, 10),
                        "Faktura ACME",
                        (Row(6110, Decimal("1000"), text="ACME"), Row(2440, Decimal("-1000"))),
                    )
                )
        years.append(YearData(FiscalYear(date(year, 1, 1), date(year, 12, 31)), vouchers))
    index = LedgerIndex.build(
        Ledger("Demo", None, {6110: Account(6110, "Kostnad"), 2440: Account(2440, "Leverantörsskuld")}, years)
    )
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)

    candidates = collect_candidates(index, pair, [], mapping_version="map-v1", aliases={"acme": "ACME AB"})

    assert not any(candidate.code in {"recurring_cost_change", "recurring_level_shift"} for candidate in candidates)
