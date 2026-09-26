"""Nyckeltalens uppbyggnad över tid: serier, byggstenar, konton och exakta bryggor."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.metrics import REGISTRY
from redovisningai.accounting.statements import StatementMapping
from redovisningai.accounting.structure import SERIES_KINDS, metric_structure, period_series
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.facts.model import FactStatus
from redovisningai.review.analysis import PAYROLL
from redovisningai.rules.rates import default_rates


@pytest.fixture(scope="module")
def bygg() -> LedgerIndex:
    return LedgerIndex.build(generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger)


@pytest.fixture(scope="module")
def konsult() -> LedgerIndex:  # brutet räkenskapsår juli–juni
    return LedgerIndex.build(generate(DEMO_PROFILES[2], date(2026, 9, 30)).ledger)


def test_period_series_kinds(bygg: LedgerIndex, konsult: LedgerIndex) -> None:
    end = date(2026, 9, 30)
    assert [p.spec for p in period_series("months", end, 3, bygg.ledger)] == ["2026-07", "2026-08", "2026-09"]
    assert [p.spec for p in period_series("quarters", end, 2, bygg.ledger)] == ["2026-Q2", "2026-Q3"]
    assert [p.spec for p in period_series("same_month", end, 3, bygg.ledger)] == ["2024-09", "2025-09", "2026-09"]
    assert [p.spec for p in period_series("r12", end, 2, bygg.ledger)] == ["R12:2026-08", "R12:2026-09"]
    assert [p.spec for p in period_series("fiscal_years", end, 3, bygg.ledger)] == [
        "FY:2024-01",
        "FY:2025-01",
        "FY:2026-01",
    ]
    # Brutet räkenskapsår: samma antal månader från respektive års start.
    ytd = period_series("ytd", end, 2, konsult.ledger)
    assert [(p.start, p.end, p.months_count) for p in ytd] == [
        (date(2025, 7, 1), date(2025, 9, 30), 3),
        (date(2026, 7, 1), date(2026, 9, 30), 3),
    ]
    assert [p.spec for p in period_series("fiscal_years", end, 2, konsult.ledger)] == ["FY:2025-07", "FY:2026-07"]
    with pytest.raises(ValueError):
        period_series("months", end, 1, bygg.ledger)
    with pytest.raises(ValueError):
        period_series("veckor", end, 3, bygg.ledger)


@pytest.mark.parametrize("code", list(REGISTRY))
def test_structure_rows_and_steps_reconcile_exactly(bygg: LedgerIndex, code: str) -> None:
    periods = period_series("months", date(2026, 9, 30), 4, bygg.ledger)
    structure = metric_structure(
        code, bygg, periods, series="months", mapping=StatementMapping(), rates=default_rates()
    )
    assert [p.spec for p in structure.periods] == [p.spec for p in periods]
    for step in structure.steps:
        if step.change is not None:
            assert sum((effect for _c, _l, effect in step.components), Decimal(0)) == step.change
    unit = REGISTRY[code].unit.value
    for i, point in enumerate(structure.periods):
        if point.status not in (FactStatus.CALCULATED, FactStatus.PARTIAL):
            continue
        for row in structure.rows:
            if row.values[i] is None:
                continue
            # Kontoraderna (inkl. "övriga") stämmer exakt med byggstenen.
            assert sum((a.values[i] or Decimal(0) for a in row.accounts), Decimal(0)) == row.values[i] or (
                not row.accounts
            ), (code, row.code)
        if unit == "SEK":
            total = sum((row.values[i] or Decimal(0) for row in structure.rows), Decimal(0))
            assert total == point.value, (code, point.spec)
        else:
            numerator = [row for row in structure.rows if row.role == "numerator"]
            shares = sum((row.shares[i] or Decimal(0) for row in numerator), Decimal(0))
            assert point.value is not None
            assert abs(shares - point.value) <= Decimal("0.1") * (len(numerator) + 1), (code, point.spec)


def test_structure_marks_missing_periods_instead_of_zero(bygg: LedgerIndex) -> None:
    periods = period_series("same_month", date(2026, 9, 30), 4, bygg.ledger)  # 2023-09 saknas
    structure = metric_structure(
        "operating_margin", bygg, periods, series="same_month", mapping=StatementMapping(), rates=default_rates()
    )
    first = structure.periods[0]
    assert first.spec == "2023-09" and first.status is FactStatus.INSUFFICIENT_DATA and first.value is None
    assert all(row.values[0] is None and row.shares[0] is None for row in structure.rows)
    assert structure.steps[0].status is FactStatus.INSUFFICIENT_DATA
    assert structure.steps[0].change is None
    assert any("2023-09" in w for w in structure.warnings)
    assert structure.steps[-1].status is FactStatus.CALCULATED


def test_payroll_accounts_are_aggregated_without_changing_totals(bygg: LedgerIndex) -> None:
    periods = period_series("months", date(2026, 9, 30), 3, bygg.ledger)
    open_view = metric_structure(
        "operating_result", bygg, periods, series="months", mapping=StatementMapping(), rates=default_rates()
    )
    masked = metric_structure(
        "operating_result",
        bygg,
        periods,
        series="months",
        mapping=StatementMapping(),
        rates=default_rates(),
        hidden_accounts=PAYROLL,
    )
    personnel_open = next(r for r in open_view.rows if r.code == "personnel")
    personnel_masked = next(r for r in masked.rows if r.code == "personnel")
    assert personnel_open.values == personnel_masked.values
    assert any(a.account is not None and a.account in PAYROLL for a in personnel_open.accounts)
    assert all(a.account is None or a.account not in PAYROLL for a in personnel_masked.accounts)
    assert "Lönekonton" in personnel_masked.accounts[-1].name


def test_series_defaults_are_within_limits() -> None:
    for _label, maximum, default in SERIES_KINDS.values():
        assert 2 <= default <= maximum


def test_short_labels_and_open_current_fiscal_year(bygg: LedgerIndex, konsult: LedgerIndex) -> None:
    from redovisningai.accounting.comparisons import comparison_pair
    from redovisningai.accounting.periods import parse_period, short_label

    assert short_label(parse_period("2026-09")) == "sep 2026"
    assert short_label(parse_period("2026-Q3")) == "Q3 2026"
    assert short_label(parse_period("FY:2026-01", bygg.ledger)) == "2026"
    assert short_label(parse_period("FY:2025-07", konsult.ledger)) == "2025/26"
    assert short_label(parse_period("YTD:2026-09", bygg.ledger)) == "jan–sep 2026"
    assert short_label(parse_period("R12:2026-09")) == "R12 sep 2026"

    # Pågående räkenskapsår: jämförelsen tillåts men märks, så att 9 månader inte ser ut som ett helt år.
    pair = comparison_pair(parse_period("FY:2026-01", bygg.ledger), "yoy", bygg.ledger, bygg)
    assert pair.status is FactStatus.CALCULATED
    assert pair.notices and "inte avslutad" in pair.notices[0]
    periods = period_series("fiscal_years", date(2026, 9, 30), 3, bygg.ledger)
    structure = metric_structure(
        "net_sales", bygg, periods, series="fiscal_years", mapping=StatementMapping(), rates=default_rates()
    )
    assert [p.open for p in structure.periods] == [False, False, True]
    assert any("hittills i år" in w for w in structure.warnings)
    month_pair = comparison_pair(parse_period("2026-09"), "yoy", bygg.ledger, bygg)
    assert month_pair.notices == ()


def test_ratio_bridges_mark_the_denominator(bygg: LedgerIndex) -> None:
    periods = period_series("months", date(2026, 9, 30), 2, bygg.ledger)
    structure = metric_structure(
        "gross_margin", bygg, periods, series="months", mapping=StatementMapping(), rates=default_rates()
    )
    labels = [label for _code, label, _effect in structure.steps[0].components]
    assert "Nettoomsättning" in labels and "Nettoomsättning (nämnare)" in labels
