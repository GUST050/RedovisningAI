"""Validerade periodpar för nyckeltalsjämförelser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.periods import Period, PeriodKind, previous_period, same_period_previous_year
from redovisningai.domain.ledger import Ledger
from redovisningai.facts.model import FactStatus

CompareMode = Literal["yoy", "previous"]


@dataclass(frozen=True, slots=True)
class ComparisonPair:
    current: Period
    previous: Period
    status: FactStatus
    warnings: tuple[str, ...] = ()


def comparison_pair(current: Period, mode: CompareMode, ledger: Ledger, index: LedgerIndex) -> ComparisonPair:
    """Skapa och kontrollera föregående eller motsvarande historisk period.

    Ett par är bara direkt jämförbart när periodtyp och längd stämmer och båda
    perioderna har täckning för var och en av sina månader.
    """
    if mode not in ("yoy", "previous"):
        raise ValueError(f"Ogiltigt jämförelseläge: {mode}")

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
    return ComparisonPair(current, previous, status, tuple(warnings))


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
    )
