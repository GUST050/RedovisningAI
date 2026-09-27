import json
from calendar import monthrange
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metric_explanations import MetricComponent, MetricExplanation, explain_metric
from redovisningai.accounting.periods import month, months_between
from redovisningai.accounting.statements import StatementMapping
from redovisningai.analytics.finding_candidates import collect_candidates
from redovisningai.analytics.spend import collect_spend
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, RowStatus, Voucher, YearData
from redovisningai.facts.model import FactStatus, FactStore, Unit
from redovisningai.review.finding_priorities import rank_findings
from redovisningai.rules.rates import default_rates

# Ord som gör en kombination till en orsak; fyndens etiketter får aldrig innehålla dem.
CAUSAL_WORDS = ("orsak", "på grund av", "beror", "eftersom", "därför", "leder till")


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


def _index(vouchers: list[Voucher], years: tuple[int, ...] = (2026,)) -> LedgerIndex:
    accounts = {n: Account(n, f"Konto {n}") for n in (1930, 2440, 2990, 3010, 4010, 5010, 6110)}
    return LedgerIndex.build(
        Ledger(
            "Demo",
            None,
            accounts,
            [
                YearData(FiscalYear(date(y, 1, 1), date(y, 12, 31)), [v for v in vouchers if v.date.year == y])
                for y in years
            ],
        )
    )


def _voucher(number: str, day: date, rows: list[tuple[int, str]], text: str = "Verifikation") -> Voucher:
    return Voucher("A", number, day, text, tuple(Row(account, Decimal(amount)) for account, amount in rows))


def _reversals(index: LedgerIndex, pair: ComparisonPair) -> list:
    return [c for c in collect_candidates(index, pair, [], mapping_version="map-v1") if c.code == "correction_reversal"]


def test_reversal_of_an_earlier_accrual_links_both_vouchers_and_moves_the_result() -> None:
    index = _index(
        [
            _voucher("1", date(2026, 8, 31), [(6110, "5000"), (2990, "-5000")], "Periodisering konsult"),
            _voucher("2", date(2026, 9, 1), [(2990, "5000"), (6110, "-5000")], "Återföring periodisering"),
        ]
    )
    pair = ComparisonPair(month(2026, 9), month(2026, 8), FactStatus.CALCULATED)

    candidates = collect_candidates(index, pair, [], mapping_version="map-v1")

    assert [c.code for c in candidates] == ["correction_reversal"]
    reversal = candidates[0]
    assert reversal.amount_effect == Decimal("5000")  # återföringen höjer septembers resultat
    assert reversal.group_key == "reversal:A1:A2:2026-09-01"  # nummerserier börjar om varje år
    assert reversal.sources[0]["accounts"] == "2990,6110"
    references = reversal.sources[0]["references"]
    assert [(r["period"], r["voucher"]) for r in references] == [("2026-08", "A1"), ("2026-09", "A2")]
    assert all(r["content_hash"] and "text" not in r for r in references)
    assert not any(word in reversal.label.lower() for word in CAUSAL_WORDS)
    assert "periodiseringar" in reversal.warnings[0].lower()


def test_reversal_matches_the_corrected_rows_not_rows_removed_afterwards() -> None:
    corrected = Voucher(
        "A",
        "1",
        date(2026, 8, 31),
        "Periodisering",
        (
            Row(6110, Decimal("8000"), status=RowStatus.REMOVED),  # #BTRANS
            Row(2990, Decimal("-8000"), status=RowStatus.REMOVED),
            Row(6110, Decimal("5000"), status=RowStatus.ADDED),  # #RTRANS
            Row(2990, Decimal("-5000"), status=RowStatus.ADDED),
        ),
    )
    index = _index(
        [
            corrected,
            _voucher("2", date(2026, 9, 1), [(2990, "8000"), (6110, "-8000")]),  # motbokar borttagna rader
            _voucher("3", date(2026, 9, 2), [(2990, "5000"), (6110, "-5000")]),  # motbokar gällande rader
        ]
    )
    pair = ComparisonPair(month(2026, 9), month(2026, 8), FactStatus.CALCULATED)

    reversals = _reversals(index, pair)

    assert [c.group_key for c in reversals] == ["reversal:A1:A3:2026-09-02"]
    assert reversals[0].amount_effect == Decimal("5000")


def test_reversal_inside_the_period_or_below_the_threshold_is_not_a_candidate() -> None:
    index = _index(
        [
            _voucher("1", date(2026, 9, 3), [(6110, "20000"), (1930, "-20000")]),
            _voucher("2", date(2026, 9, 5), [(1930, "20000"), (6110, "-20000")]),  # regeln RAPID_REVERSAL
            _voucher("3", date(2026, 8, 31), [(6110, "900"), (2990, "-900")]),
            _voucher("4", date(2026, 9, 1), [(2990, "900"), (6110, "-900")]),  # under väsentlighetsgränsen
        ]
    )
    pair = ComparisonPair(month(2026, 9), month(2026, 8), FactStatus.CALCULATED)

    assert _reversals(index, pair) == []


def _september(year: int, sales: str, other_external: str) -> list[Voucher]:
    day = date(year, 9, 15)
    return [
        _voucher(f"{year}s", day, [(3010, f"-{sales}"), (1930, sales)]),
        _voucher(f"{year}m", day, [(4010, "30000"), (2440, "-30000")]),
        _voucher(f"{year}h", day, [(5010, "20000"), (2440, "-20000")]),
        _voucher(f"{year}k", day, [(6110, other_external), (2440, f"-{other_external}")]),
    ]


def _margin_candidates(sales_now: str, other_now: str) -> tuple[list, list, FactStore]:
    index = _index([*_september(2025, "100000", "2000"), *_september(2026, sales_now, other_now)], (2025, 2026))
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)
    store = FactStore()
    explanations = [
        explain_metric(code, index, pair, mapping=StatementMapping(), rates=default_rates(), store=store)
        for code in ("net_sales", "operating_result", "operating_margin")
    ]
    candidates = collect_candidates(index, pair, explanations, mapping_version="map-v1")
    return [c for c in candidates if c.code == "margin_pressure"], explanations, store


def test_margin_pressure_links_falling_sales_and_rising_costs_in_both_periods_without_a_cause() -> None:
    found, explanations, store = _margin_candidates("80000", "12000")

    assert len(found) == 1
    pressure = found[0]
    assert pressure.amount_effect == Decimal("-30000")  # -20 000 i omsättning, +10 000 i kostnader
    assert pressure.metric_codes == ("net_sales", "operating_margin", "operating_result")
    assert set(pressure.fact_ids) == {fid for explanation in explanations for fid in explanation.fact_ids}
    assert all(fid in store for fid in pressure.fact_ids)
    sales, costs = pressure.sources
    assert (sales["signal"], Decimal(sales["current"]), Decimal(sales["previous"])) == (
        "net_sales",
        Decimal("80000"),
        Decimal("100000"),
    )
    assert (costs["signal"], Decimal(costs["current"]), Decimal(costs["previous"])) == (
        "operating_costs",
        Decimal("62000"),
        Decimal("52000"),
    )
    assert costs["accounts"] == "6110"  # bara kontot vars kostnad ökade
    assert {r["period"] for r in sales["references"]} == {"2026-09", "2025-09"}
    assert {r["period"] for r in costs["references"]} == {"2026-09", "2025-09"}
    assert pressure.group_key == "margin_pressure:2026-09:2025-09"
    assert not any(word in pressure.label.lower() for word in CAUSAL_WORDS)
    assert "inte en fastställd orsak" in pressure.warnings[0]


@pytest.mark.parametrize(
    ("sales_now", "other_now"),
    [("80000", "2000"), ("120000", "12000")],
    ids=["bara-lagre-omsattning", "bara-hogre-kostnader"],
)
def test_margin_pressure_needs_both_falling_sales_and_rising_costs(sales_now: str, other_now: str) -> None:
    found, _, _ = _margin_candidates(sales_now, other_now)

    assert found == []


def test_similar_free_text_names_without_a_confirmed_alias_stay_uncertain_account_findings() -> None:
    spellings = ("ACME AB", "Acme AB.", "ACME Aktiebolag")
    months = months_between(date(2025, 1, 1), date(2026, 9, 30))
    vouchers = []
    for number, first_day in enumerate(months, start=1):
        name = spellings[number % 3]
        amount = Decimal("3000") if first_day == date(2026, 9, 1) else Decimal("1000")
        vouchers.append(
            Voucher(
                "A",
                str(number),
                first_day.replace(day=10),
                f"Faktura {name}",
                (Row(6110, amount, text=name), Row(2440, -amount)),
            )
        )
    index = _index(vouchers, (2025, 2026))
    pair = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)
    explanation = explain_metric(
        "operating_result", index, pair, mapping=StatementMapping(), rates=default_rates(), store=FactStore()
    )

    candidates = collect_candidates(index, pair, [explanation], mapping_version="map-v1")

    # Bara kontobryggan blir fynd: fritextnamnen blir varken motpart, återkommande kostnad eller frekvens.
    assert [(c.code, c.group_key) for c in candidates] == [("operating_result", "accounts:6110")]
    assert "acme" not in json.dumps([asdict(c) for c in candidates], default=str).lower()
    spend = collect_spend(index, months)
    assert set(spend) == {"acme", "acme aktiebolag"}  # snarlikt namn slås inte tyst ihop
    assert all(group.confidence < 0.98 for group in spend.values())  # fritext är ett osäkert förslag


def test_a3_drafts_go_stale_when_the_finding_rules_or_their_ranking_change() -> None:
    from redovisningai.ai.tasks import A3_FINDING_LABELS, A3_PROMPT_VERSION
    from redovisningai.analytics.finding_candidates import RULE_VERSION
    from redovisningai.review.finding_priorities import PRIORITY_VERSION

    assert RULE_VERSION in A3_PROMPT_VERSION and PRIORITY_VERSION in A3_PROMPT_VERSION
    assert {"correction_reversal", "margin_pressure"} <= set(A3_FINDING_LABELS)
    assert not any(word in label for label in A3_FINDING_LABELS.values() for word in CAUSAL_WORDS)
