"""Validerade periodpar för nyckeltalsjämförelser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.periods import Period, PeriodKind, previous_period, same_period_previous_year
from redovisningai.domain.ledger import Ledger
from redovisningai.facts.model import FactStatus

CompareMode = Literal["yoy", "previous", "custom"]
COMPARE_MODES = ("yoy", "previous", "custom")


@dataclass(frozen=True, slots=True)
class ComparisonPair:
    current: Period
    previous: Period
    status: FactStatus
    warnings: tuple[str, ...] = ()
    # Upplysningar som inte hindrar jämförelsen, t.ex. att en period inte är avslutad.
    notices: tuple[str, ...] = ()


def period_notices(index: LedgerIndex, *periods: Period) -> tuple[str, ...]:
    """Upplys om perioder som sträcker sig efter den senaste bokförda månaden (t.ex. pågående år)."""
    horizon = index.data_horizon()
    if horizon is None:
        return ()
    return tuple(
        f"{period.label} är inte avslutad – bokföringen sträcker sig t.o.m. {horizon.isoformat()}."
        for period in periods
        if period.end > horizon
    )


def comparison_pair(
    current: Period,
    mode: CompareMode,
    ledger: Ledger,
    index: LedgerIndex,
    compare: Period | None = None,
) -> ComparisonPair:
    """Skapa och kontrollera föregående, motsvarande historisk eller fritt vald period.

    Ett par är bara direkt jämförbart när periodtyp och längd stämmer och båda
    perioderna har täckning för var och en av sina månader. `mode="custom"` kräver
    `compare` (t.ex. mars mot augusti eller två räkenskapsår som användaren valt).
    """
    if mode not in COMPARE_MODES:
        raise ValueError(f"Ogiltigt jämförelseläge: {mode}")
    if mode == "custom":
        if compare is None:
            raise ValueError("Välj en jämförelseperiod.")
        if compare.start == current.start and compare.end == current.end:
            raise ValueError("Jämförelseperioden måste skilja sig från perioden.")
        return validate_comparison(current, compare, index)

    previous = same_period_previous_year(current, ledger) if mode == "yoy" else previous_period(current)
    warnings: list[str] = []

    if current.kind is not previous.kind or current.months_count != previous.months_count:
        warnings.append(
            f"Perioderna är inte direkt jämförbara: {current.spec} ({current.months_count} mån) "
            f"mot {previous.spec} ({previous.months_count} mån)."
        )

    for period in (current, previous):
        missing = index.missing_months(period)
        if missing:
            shown = ", ".join(month.strftime("%Y-%m") for month in missing[:6])
            suffix = " …" if len(missing) > 6 else ""
            warnings.append(f"Data saknas för {period.spec}: {shown}{suffix}.")

    status = FactStatus.INSUFFICIENT_DATA if warnings else FactStatus.CALCULATED
    return ComparisonPair(current, previous, status, tuple(warnings), period_notices(index, current, previous))


def validate_comparison(current: Period, previous: Period, index: LedgerIndex) -> ComparisonPair:
    """Validera ett uttryckligen angivet periodpar utan att ändra perioderna."""
    warnings: list[str] = []
    if current.kind is not previous.kind or current.months_count != previous.months_count:
        warnings.append(
            f"Perioderna är inte direkt jämförbara: {current.spec} ({current.months_count} mån) "
            f"mot {previous.spec} ({previous.months_count} mån)."
        )
    if current.kind is PeriodKind.FISCAL_YEAR and current.months_count != previous.months_count:
        warnings.append("Räkenskapsåren har olika längd; jämförelsen har begränsad förklaringsnivå.")
    for period in (current, previous):
        missing = index.missing_months(period)
        if missing:
            shown = ", ".join(month.strftime("%Y-%m") for month in missing[:6])
            suffix = " …" if len(missing) > 6 else ""
            warnings.append(f"Data saknas för {period.spec}: {shown}{suffix}.")
    return ComparisonPair(
        current,
        previous,
        FactStatus.INSUFFICIENT_DATA if warnings else FactStatus.CALCULATED,
        tuple(dict.fromkeys(warnings)),
        period_notices(index, current, previous),
    )
