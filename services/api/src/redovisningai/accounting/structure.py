"""Nyckeltalens uppbyggnad över tid – djupare än själva nyckeltalet.

För ett nyckeltal och en serie perioder (månader, kvartal, räkenskapsår, samma månad varje år,
hittills i år per år eller rullande 12 månader) visas

- nyckeltalets värde och datastatus per period,
- dess byggstenar per period (resultat-/balansrader, täljare och nämnare) med andel av basen,
  t.ex. varje kostnadsslag i procent av omsättningen,
- de största kontona under varje byggsten, med en summerad rad för övriga konton,
- exakta bryggor mellan varje par av perioder i serien och mellan första och sista perioden.

Allt räknas deterministiskt med Decimal. Saknad data blir status, aldrig noll.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.comparisons import validate_comparison
from redovisningai.accounting.metric_explanations import MetricExplanation, explain_metric, metric_parts
from redovisningai.accounting.metrics import REGISTRY, calculate_metric
from redovisningai.accounting.periods import (
    Period,
    add_months,
    fiscal_year_period,
    month,
    month_start,
    quarter,
    rolling,
    short_label,
    ytd,
)
from redovisningai.accounting.statements import StatementMapping
from redovisningai.domain.ledger import ZERO, FiscalYear, Ledger
from redovisningai.facts.model import FactStatus, FactStore
from redovisningai.rules.rates import RateTable

SERIES_KINDS: dict[str, tuple[str, int, int]] = {
    # typ: (beskrivning, högsta antal, standardantal)
    "months": ("Månader i följd", 36, 12),
    "quarters": ("Kvartal i följd", 16, 8),
    "fiscal_years": ("Räkenskapsår", 10, 3),
    "same_month": ("Samma månad varje år", 10, 3),
    "ytd": ("Hittills i år, år för år", 10, 3),
    "r12": ("Rullande 12 månader, månad för månad", 36, 12),
}
TOP_ACCOUNTS = 8


def _fiscal_year_for(d: date, ledger: Ledger) -> FiscalYear:
    year = ledger.year_for(d)
    if year is not None:
        return year.fiscal_year
    if ledger.years:
        ref = ledger.years[-1].fiscal_year
        start = date(d.year if d.month >= ref.start.month else d.year - 1, ref.start.month, 1)
        return FiscalYear(start, add_months(start, 12) - timedelta(days=1))
    return FiscalYear(date(d.year, 1, 1), date(d.year, 12, 31))


def period_series(kind: str, end: date, count: int, ledger: Ledger) -> list[Period]:
    """Perioder i stigande ordning som slutar med perioden som innehåller `end`."""
    if kind not in SERIES_KINDS:
        raise ValueError(f"Okänd serietyp: {kind}")
    _label, maximum, _default = SERIES_KINDS[kind]
    if not 2 <= count <= maximum:
        raise ValueError(f"Antal perioder för {kind} ska vara mellan 2 och {maximum}.")
    last = month_start(end)
    out: list[Period] = []
    if kind == "months":
        for i in range(count - 1, -1, -1):
            m = add_months(last, -i)
            out.append(month(m.year, m.month))
    elif kind == "quarters":
        q_start = date(last.year, 3 * ((last.month - 1) // 3) + 1, 1)
        for i in range(count - 1, -1, -1):
            m = add_months(q_start, -3 * i)
            out.append(quarter(m.year, (m.month - 1) // 3 + 1))
    elif kind == "same_month":
        for i in range(count - 1, -1, -1):
            out.append(month(last.year - i, last.month))
    elif kind == "r12":
        for i in range(count - 1, -1, -1):
            out.append(rolling(add_months(last, -i), 12))
    elif kind == "fiscal_years":
        fy = _fiscal_year_for(last, ledger)
        years = [fy]
        while len(years) < count:
            prev_end = years[0].start - timedelta(days=1)
            years.insert(0, _fiscal_year_for(prev_end, ledger))
        out = [fiscal_year_period(y) for y in years]
    else:  # ytd
        fy = _fiscal_year_for(last, ledger)
        offset = len(fy.months()) - len([m for m in fy.months() if m > last])
        years = [fy]
        while len(years) < count:
            prev_end = years[0].start - timedelta(days=1)
            years.insert(0, _fiscal_year_for(prev_end, ledger))
        for y in years:
            through = min(add_months(y.start, offset - 1), month_start(y.end))
            out.append(ytd(y, through))
    return out


def _q(value: Decimal, places: str = "0.1") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class AccountLine:
    account: int | None  # None = sammanslagen rad (övriga eller dolda lönekonton)
    name: str
    values: list[Decimal | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "name": self.name,
            "values": [None if v is None else str(v) for v in self.values],
        }


@dataclass(slots=True)
class StructureRow:
    code: str
    label: str
    role: str
    values: list[Decimal | None]
    shares: list[Decimal | None]
    accounts: list[AccountLine] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "role": self.role,
            "values": [None if v is None else str(v) for v in self.values],
            "shares": [None if v is None else str(v) for v in self.shares],
            "accounts": [a.to_dict() for a in self.accounts],
            "note": self.note,
        }


@dataclass(slots=True)
class StructureStep:
    """Förändringsbrygga mellan två perioder i serien."""

    current: str
    previous: str
    status: FactStatus
    change: Decimal | None
    components: list[tuple[str, str, Decimal]]
    warnings: list[str]
    current_label: str = ""
    previous_label: str = ""

    @classmethod
    def from_explanation(cls, explanation: MetricExplanation, current: Period, previous: Period) -> StructureStep:
        return cls(
            explanation.periods["current"],
            explanation.periods["previous"],
            explanation.status,
            explanation.change,
            [(c.code, c.display_label, c.effect) for c in explanation.components],
            list(explanation.warnings),
            short_label(current),
            short_label(previous),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "previous": self.previous,
            "current_label": self.current_label,
            "previous_label": self.previous_label,
            "status": self.status.value,
            "change": None if self.change is None else str(self.change),
            "components": [
                {"code": code, "label": label, "effect": str(effect)} for code, label, effect in self.components
            ],
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class PeriodPoint:
    spec: str
    label: str
    status: FactStatus
    value: Decimal | None
    display: str
    fact_id: str
    missing_months: list[str]
    note: str | None = None
    short: str = ""
    open: bool = False  # perioden sträcker sig efter senaste bokförda månad

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "label": self.label,
            "short": self.short,
            "open": self.open,
            "status": self.status.value,
            "value": None if self.value is None else str(self.value),
            "display": self.display,
            "fact_id": self.fact_id,
            "missing_months": self.missing_months,
            "note": self.note,
        }


@dataclass(slots=True)
class MetricStructure:
    code: str
    label: str
    unit: str
    formula: str
    better: str
    series: str
    periods: list[PeriodPoint]
    rows: list[StructureRow]
    base_label: str | None
    steps: list[StructureStep]
    overall: StructureStep | None
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "unit": self.unit,
            "formula": self.formula,
            "better": self.better,
            "series": self.series,
            "series_label": SERIES_KINDS[self.series][0],
            "periods": [p.to_dict() for p in self.periods],
            "rows": [r.to_dict() for r in self.rows],
            "base_label": self.base_label,
            "steps": [s.to_dict() for s in self.steps],
            "overall": None if self.overall is None else self.overall.to_dict(),
            "warnings": self.warnings,
        }


def _account_lines(
    per_period: list[dict[int, Decimal] | None],
    names: dict[int, str],
    hidden: AccountSet | None,
    limit: int,
) -> list[AccountLine]:
    """De största kontona över perioderna + en summerad rad så att raderna alltid stämmer."""
    accounts = sorted({a for values in per_period if values for a in values})
    visible = [a for a in accounts if hidden is None or a not in hidden]
    masked = [a for a in accounts if hidden is not None and a in hidden]
    weight = {a: sum((abs(v.get(a, ZERO)) for v in per_period if v), ZERO) for a in visible}
    top = sorted(visible, key=lambda a: (-weight[a], a))[:limit]
    lines = [
        AccountLine(a, names.get(a, f"Konto {a}"), [None if v is None else v.get(a, ZERO) for v in per_period])
        for a in sorted(top)
    ]
    rest = [a for a in visible if a not in top]
    if rest:
        lines.append(
            AccountLine(
                None,
                f"Övriga konton ({len(rest)} st)",
                [None if v is None else sum((v.get(a, ZERO) for a in rest), ZERO) for v in per_period],
            )
        )
    if masked:
        lines.append(
            AccountLine(
                None,
                "Lönekonton, sammanslagna (detaljer kräver behörigheten Lönedata)",
                [None if v is None else sum((v.get(a, ZERO) for a in masked), ZERO) for v in per_period],
            )
        )
    return lines


def metric_structure(
    code: str,
    index: LedgerIndex,
    periods: Sequence[Period],
    *,
    series: str,
    mapping: StatementMapping,
    rates: RateTable,
    hidden_accounts: AccountSet | None = None,
    account_limit: int = TOP_ACCOUNTS,
    store: FactStore | None = None,
) -> MetricStructure:
    if code not in REGISTRY:
        raise KeyError(f"Okänt nyckeltal: {code}")
    if len(periods) < 2:
        raise ValueError("Minst två perioder behövs för en jämförelse över tid.")
    definition = REGISTRY[code]
    store = store if store is not None else FactStore()
    names = {a: acc.name for a, acc in index.ledger.accounts.items()}
    points: list[PeriodPoint] = []
    decompositions = []
    horizon = index.data_horizon()
    for period in periods:
        fact = calculate_metric(code, index, period, store=store, mapping=mapping, rates=rates)
        is_open = horizon is not None and period.end > horizon and index.has_data(period)
        points.append(
            PeriodPoint(
                period.spec,
                period.label,
                fact.status,
                fact.value,
                fact.to_dict()["display"],
                fact.id,
                [m.strftime("%Y-%m") for m in index.missing_months(period)],
                fact.lineage.get("note")
                or (f"Pågående – bokföring t.o.m. {horizon.isoformat()}" if is_open and horizon else None),
                short_label(period),
                is_open,
            )
        )
        known = fact.status in (FactStatus.CALCULATED, FactStatus.PARTIAL) or (
            fact.status is FactStatus.NOT_APPLICABLE and index.has_data(period)
        )
        decompositions.append(metric_parts(code, index, period, mapping=mapping, rates=rates) if known else None)

    order: list[tuple[str, str, str, str | None]] = []
    for dec in decompositions:
        for piece in dec.parts if dec else ():
            if piece.code not in {o[0] for o in order}:
                order.append((piece.code, piece.label, piece.role, piece.note))
    rows: list[StructureRow] = []
    for part_code, label, role, note in order:
        values: list[Decimal | None] = []
        shares: list[Decimal | None] = []
        account_values: list[dict[int, Decimal] | None] = []
        for dec in decompositions:
            if dec is None:
                values.append(None)
                shares.append(None)
                account_values.append(None)
                continue
            part = next((p for p in dec.parts if p.code == part_code), None)
            value = part.value if part is not None else ZERO
            values.append(value)
            shares.append(_q(value / dec.base * 100) if dec.base else None)
            account_values.append(dict(part.accounts) if part is not None else {})
        if all(v in (None, ZERO) for v in values) and not any(account_values):
            continue  # raden är tom i alla perioder
        rows.append(
            StructureRow(
                part_code,
                label,
                role,
                values,
                shares,
                _account_lines(account_values, names, hidden_accounts, account_limit),
                note,
            )
        )

    steps: list[StructureStep] = []
    for previous, current in pairwise(periods):
        pair = validate_comparison(current, previous, index)
        steps.append(
            StructureStep.from_explanation(
                explain_metric(code, index, pair, mapping=mapping, rates=rates, store=store), current, previous
            )
        )
    overall = None
    if len(periods) > 2:
        pair = validate_comparison(periods[-1], periods[0], index)
        overall = StructureStep.from_explanation(
            explain_metric(code, index, pair, mapping=mapping, rates=rates, store=store), periods[-1], periods[0]
        )
    warnings: list[str] = []
    missing = [p.spec for p in points if p.status is FactStatus.INSUFFICIENT_DATA]
    if missing:
        warnings.append(f"Underlag saknas för {', '.join(missing)} – de perioderna visas utan värden.")
    if any(p.status is FactStatus.PARTIAL for p in points):
        warnings.append("Vissa perioder är preliminära (låg periodiseringsgrad) – jämför med försiktighet.")
    open_points = [p.short for p in points if p.open]
    if open_points and horizon is not None:
        warnings.append(
            f"{', '.join(open_points)} är inte avslutad (bokföring t.o.m. {horizon.isoformat()}) och är därför "
            "inte likvärdig med hela perioder – välj hittills i år för en likvärdig jämförelse."
        )
    return MetricStructure(
        code,
        definition.name,
        definition.unit.value,
        definition.formula,
        definition.better,
        series,
        points,
        rows,
        next((d.base_label for d in decompositions if d is not None), None),
        steps,
        overall,
        warnings,
    )
