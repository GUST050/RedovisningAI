"""Spend Intelligence: vart går pengarna?

Motparter (från leverantörsfakturor när sådana finns, annars härledda ur verifikationstexter),
återkommande kostnader, nya och försvunna kostnader, nivåskiften och koncentration.
Allt deterministiskt; AI (A6) används bara för att förfina motpartsnamn och kategorier.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from itertools import pairwise
from statistics import mean, pstdev
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.categories import CategoryMapping
from redovisningai.accounting.periods import Period, add_months, month_start, months_between
from redovisningai.analytics.counterparties import counterparty_for_row
from redovisningai.domain.ledger import ZERO

EXTERNAL_SPEND = AccountSet.of((4000, 6999))


class Recurrence(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    IRREGULAR = "irregular"
    ONE_OFF = "one_off"
    UNKNOWN = "unknown"


RECURRENCE_SV = {
    Recurrence.MONTHLY: "månatlig",
    Recurrence.QUARTERLY: "kvartalsvis",
    Recurrence.ANNUAL: "årlig",
    Recurrence.IRREGULAR: "oregelbunden",
    Recurrence.ONE_OFF: "engångs",
    Recurrence.UNKNOWN: "okänd",
}


@dataclass(slots=True)
class CounterpartySpend:
    key: str
    name: str
    monthly: dict[date, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    accounts: set[int] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    confidence: float = 0.0

    def total(self, months: list[date]) -> Decimal:
        return sum((self.monthly.get(m, ZERO) for m in months), ZERO)


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def collect_spend(
    index: LedgerIndex,
    months: list[date],
    *,
    aliases: dict[str, str] | None = None,
    mapping: CategoryMapping | None = None,
) -> dict[str, CounterpartySpend]:
    mapping = mapping or CategoryMapping()
    out: dict[str, CounterpartySpend] = {}
    for m in months:
        for v in index.vouchers_by_month.get(m, []):
            for r in v.effective_rows:
                if r.account not in EXTERNAL_SPEND:
                    continue
                g = counterparty_for_row(v, r, aliases)
                key = g.key or "okänd motpart"
                cs = out.get(key)
                if cs is None:
                    cs = out[key] = CounterpartySpend(key, g.name or "Okänd motpart", confidence=g.confidence)
                cs.monthly[m] += r.amount
                cs.accounts.add(r.account)
                cat = mapping.category_for(r.account)
                if cat:
                    cs.categories.add(cat)
    return out


def detect_recurrence(series: list[Decimal]) -> Recurrence:
    """Klassa en månadsserie (äldst först, minst 12 månader för säker bedömning)."""
    active = [i for i, x in enumerate(series) if x > 0]
    n = len(series)
    if not active:
        return Recurrence.UNKNOWN
    if len(active) == 1:
        return Recurrence.ONE_OFF if n >= 6 else Recurrence.UNKNOWN
    gaps = [b - a for a, b in pairwise(active)]
    if len(active) >= max(3, int(n * 0.75)) and all(g == 1 for g in gaps[-6:]):
        return Recurrence.MONTHLY
    if len(gaps) >= 2 and all(g == 3 for g in gaps):
        return Recurrence.QUARTERLY
    if all(g == 12 for g in gaps):
        return Recurrence.ANNUAL
    return Recurrence.IRREGULAR


@dataclass(slots=True)
class LevelShift:
    month: date
    before: Decimal
    after: Decimal

    @property
    def change_pct(self) -> Decimal:
        return ((self.after - self.before) / self.before * 100).quantize(Decimal("0.1")) if self.before else Decimal(0)


def detect_level_shift(
    months: list[date], series: list[Decimal], *, min_side: int = 3, min_change: Decimal = Decimal("0.2")
) -> LevelShift | None:
    """Hitta ett strukturellt nivåskifte (bästa brytpunkt med minst `min_side` månader per sida)."""
    vals = [float(x) for x in series]
    best: tuple[float, int] | None = None
    for k in range(min_side, len(vals) - min_side + 1):
        a, b = vals[:k], vals[k:]
        ma, mb = mean(a), mean(b)
        if ma <= 0:
            continue
        rel = abs(mb - ma) / ma
        noise = (pstdev(a) + pstdev(b)) / 2 or 1e-9
        stat = abs(mb - ma) / noise
        if rel >= float(min_change) and stat >= 3 and (best is None or stat > best[0]):
            best = (stat, k)
    if best is None:
        return None
    k = best[1]
    return LevelShift(months[k], _q(Decimal(str(mean(vals[:k])))), _q(Decimal(str(mean(vals[k:])))))


@dataclass(slots=True)
class SpendReport:
    period: str
    compare_period: str
    total: Decimal
    compare_total: Decimal
    counterparties: list[dict[str, Any]]
    new_costs: list[dict[str, Any]]
    disappeared: list[dict[str, Any]]
    level_shifts: list[dict[str, Any]]
    concentration: dict[str, str]
    recurring_share: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "compare_period": self.compare_period,
            "total": str(self.total),
            "compare_total": str(self.compare_total),
            "counterparties": self.counterparties,
            "new_costs": self.new_costs,
            "disappeared": self.disappeared,
            "level_shifts": self.level_shifts,
            "concentration": self.concentration,
            "recurring_share": None if self.recurring_share is None else str(self.recurring_share),
        }


def spend_report(
    index: LedgerIndex,
    period: Period,
    compare: Period,
    *,
    aliases: dict[str, str] | None = None,
    min_amount: Decimal = Decimal("1000"),
    limit: int = 25,
) -> SpendReport:
    hist_start = add_months(month_start(period.end), -23)
    months = [m for m in months_between(hist_start, period.end) if m in index.coverage]
    spend = collect_spend(index, months, aliases=aliases)
    cur_m, cmp_m = period.months(), compare.months()
    last12 = [m for m in months if m > add_months(month_start(period.end), -12)]
    rows = []
    total = sum((cs.total(cur_m) for cs in spend.values()), ZERO)
    # Andelar räknas mot summan av motparter med kostnad (krediteringar/negativa belopp exkluderas),
    # annars kan andelarna tillsammans bli över 100 %.
    gross = sum((t for cs in spend.values() if (t := cs.total(cur_m)) > 0), ZERO)
    compare_total = sum((cs.total(cmp_m) for cs in spend.values()), ZERO)
    recurring_amount = ZERO
    new_costs, disappeared, shifts = [], [], []
    for cs in spend.values():
        cur, prev = cs.total(cur_m), cs.total(cmp_m)
        series12 = [cs.monthly.get(m, ZERO) for m in last12]
        rec = detect_recurrence(series12)
        if rec in (Recurrence.MONTHLY, Recurrence.QUARTERLY, Recurrence.ANNUAL):
            recurring_amount += cur
        annualized = None
        if rec is Recurrence.MONTHLY:
            recent = [x for x in series12[-3:] if x > 0]
            annualized = _q(sum(recent, ZERO) / len(recent) * 12) if recent else None
        row = {
            "key": cs.key,
            "name": cs.name,
            "amount": str(cur),
            "compare": str(prev),
            "diff": str(cur - prev),
            "share": str((cur / gross * 100).quantize(Decimal("0.1"))) if gross and cur > 0 else None,
            "recurrence": rec.value,
            "recurrence_sv": RECURRENCE_SV[rec],
            "annualized": None if annualized is None else str(annualized),
            "accounts": sorted(cs.accounts),
            "categories": sorted(cs.categories),
            "confidence": cs.confidence,
        }
        if cur or prev:
            rows.append(row)
        if cur >= min_amount and prev == 0 and cs.key != "okänd motpart":
            first = min((m for m in months if cs.monthly.get(m, ZERO) > 0), default=None)
            new_costs.append({**row, "first_month": first.isoformat() if first else None})
        if prev >= min_amount and cur == 0 and cs.key != "okänd motpart":
            disappeared.append(row)
        full_series = [cs.monthly.get(m, ZERO) for m in months]
        if len([x for x in full_series if x > 0]) >= 6:
            shift = detect_level_shift(months, full_series)
            if shift and period.start <= shift.month <= period.end + (period.end - period.start):
                shifts.append(
                    {
                        "key": cs.key,
                        "name": cs.name,
                        "month": shift.month.isoformat(),
                        "before": str(shift.before),
                        "after": str(shift.after),
                        "change_pct": str(shift.change_pct),
                    }
                )
    rows.sort(key=lambda r: Decimal(r["amount"]), reverse=True)
    positive = sorted((Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) > 0), reverse=True)
    conc = {}
    for n in (1, 5, 10):
        conc[f"top{n}"] = str((sum(positive[:n], ZERO) / gross * 100).quantize(Decimal("0.1"))) if gross else "0"
    new_costs.sort(key=lambda r: Decimal(r["amount"]), reverse=True)
    disappeared.sort(key=lambda r: Decimal(r["compare"]), reverse=True)
    return SpendReport(
        period=period.spec,
        compare_period=compare.spec,
        total=total,
        compare_total=compare_total,
        counterparties=rows[:limit],
        new_costs=new_costs[:limit],
        disappeared=disappeared[:limit],
        level_shifts=shifts,
        concentration=conc,
        recurring_share=(recurring_amount / gross * 100).quantize(Decimal("0.1")) if gross else None,
    )
