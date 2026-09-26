"""Perioder och jämförelseperioder.

Stödjer månad, kvartal, hittills i år (YTD, relativt räkenskapsårets start), helt
räkenskapsår, rullande N månader och fritt datumintervall. Brutna räkenskapsår hanteras.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from redovisningai.domain.ledger import FiscalYear, Ledger

MONTHS_SV = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


class PeriodKind(StrEnum):
    MONTH = "month"
    QUARTER = "quarter"
    YTD = "ytd"
    FISCAL_YEAR = "fy"
    ROLLING = "rolling"
    CUSTOM = "custom"


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def months_between(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = month_start(start)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date
    kind: PeriodKind
    label: str
    months_count: int = 1

    @property
    def spec(self) -> str:
        match self.kind:
            case PeriodKind.MONTH:
                return f"{self.end:%Y-%m}"
            case PeriodKind.QUARTER:
                return f"{self.end.year}-Q{(self.end.month - 1) // 3 + 1}"
            case PeriodKind.YTD:
                return f"YTD:{self.end:%Y-%m}"
            case PeriodKind.FISCAL_YEAR:
                return f"FY:{self.start:%Y-%m}"
            case PeriodKind.ROLLING:
                return f"R{self.months_count}:{self.end:%Y-%m}"
            case _:
                return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def months(self) -> list[date]:
        return months_between(self.start, self.end)


def short_label(p: Period) -> str:
    """Kort periodnamn för tabellrubriker: "sep 2026", "Q3 2026", "2026", "2025/26", "jan–sep 2026"."""
    s, e = p.start, p.end
    match p.kind:
        case PeriodKind.MONTH:
            return f"{MONTHS_SV[e.month - 1]} {e.year}"
        case PeriodKind.QUARTER:
            return f"Q{(e.month - 1) // 3 + 1} {e.year}"
        case PeriodKind.FISCAL_YEAR:
            return str(s.year) if (s.month, s.day) == (1, 1) and s.year == e.year else f"{s.year}/{str(e.year)[2:]}"
        case PeriodKind.YTD | PeriodKind.CUSTOM:
            if s.year == e.year:
                return f"{MONTHS_SV[s.month - 1]}–{MONTHS_SV[e.month - 1]} {e.year}"
            return f"{MONTHS_SV[s.month - 1]} {s.year}–{MONTHS_SV[e.month - 1]} {e.year}"
        case PeriodKind.ROLLING:
            return f"R{p.months_count} {MONTHS_SV[e.month - 1]} {e.year}"
    return p.label


def month(y: int, m: int) -> Period:
    s = date(y, m, 1)
    return Period(s, month_end(s), PeriodKind.MONTH, f"{MONTHS_SV[m - 1]} {y}")


def quarter(y: int, q: int) -> Period:
    s = date(y, 3 * (q - 1) + 1, 1)
    e = month_end(add_months(s, 2))
    return Period(s, e, PeriodKind.QUARTER, f"Q{q} {y}", 3)


def fiscal_year_period(fy: FiscalYear) -> Period:
    n = len(fy.months())
    return Period(fy.start, fy.end, PeriodKind.FISCAL_YEAR, f"Räkenskapsår {fy.label}", n)


def ytd(fy: FiscalYear, through: date) -> Period:
    e = month_end(through)
    n = len(months_between(fy.start, e))
    return Period(fy.start, e, PeriodKind.YTD, f"Hittills i år t.o.m. {MONTHS_SV[e.month - 1]} {e.year}", n)


def rolling(through: date, n: int) -> Period:
    e = month_end(through)
    s = add_months(month_start(e), -(n - 1))
    return Period(s, e, PeriodKind.ROLLING, f"Rullande {n} mån t.o.m. {MONTHS_SV[e.month - 1]} {e.year}", n)


def custom(start: date, end: date) -> Period:
    return Period(
        start, end, PeriodKind.CUSTOM, f"{start.isoformat()} – {end.isoformat()}", len(months_between(start, end))
    )


def same_period_previous_year(p: Period, ledger: Ledger | None = None) -> Period:
    """Motsvarande period ett år tidigare (YoY)."""
    if p.kind is PeriodKind.MONTH:
        return month(p.start.year - 1, p.start.month)
    if p.kind is PeriodKind.QUARTER:
        return quarter(p.start.year - 1, (p.start.month - 1) // 3 + 1)
    if p.kind is PeriodKind.YTD:
        prev_fy = FiscalYear(add_months(p.start, -12), add_months(p.start, 0) - timedelta(days=1))
        return ytd(prev_fy, add_months(p.end, -12))
    if p.kind is PeriodKind.FISCAL_YEAR:
        if ledger is not None:
            for y in ledger.years:
                if y.fiscal_year.end == p.start - timedelta(days=1):
                    return fiscal_year_period(y.fiscal_year)
        return fiscal_year_period(FiscalYear(add_months(p.start, -12), p.start - timedelta(days=1)))
    if p.kind is PeriodKind.ROLLING:
        return rolling(add_months(p.end, -12), p.months_count)
    e = add_months(p.end, -12)
    if p.end == month_end(p.end):
        e = month_end(e)
    return custom(add_months(p.start, -12), e)


def previous_period(p: Period) -> Period:
    """Föregående period av samma längd (MoM, QoQ …)."""
    if p.kind is PeriodKind.MONTH:
        prev = add_months(p.start, -1)
        return month(prev.year, prev.month)
    if p.kind is PeriodKind.QUARTER:
        prev = add_months(p.start, -3)
        return quarter(prev.year, (prev.month - 1) // 3 + 1)
    if p.kind is PeriodKind.ROLLING:
        return rolling(add_months(p.end, -p.months_count), p.months_count)
    return same_period_previous_year(p)


_SPEC_RE = {
    "month": re.compile(r"^(\d{4})-(\d{2})$"),
    "quarter": re.compile(r"^(\d{4})-Q([1-4])$", re.I),
    "ytd": re.compile(r"^YTD:(\d{4})-(\d{2})$", re.I),
    "rolling": re.compile(r"^R(\d{1,2}):(\d{4})-(\d{2})$", re.I),
    "fy": re.compile(r"^FY:(\d{4})-(\d{2})$", re.I),
    "custom": re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$"),
}


def parse_period(spec: str, ledger: Ledger | None = None) -> Period:
    """Tolka t.ex. '2026-09', '2026-Q3', 'YTD:2026-09', 'R12:2026-09', 'FY:2026-01', 'a..b'."""
    s = spec.strip()
    if m := _SPEC_RE["month"].match(s):
        return month(int(m.group(1)), int(m.group(2)))
    if m := _SPEC_RE["quarter"].match(s):
        return quarter(int(m.group(1)), int(m.group(2)))
    if m := _SPEC_RE["ytd"].match(s):
        through = date(int(m.group(1)), int(m.group(2)), 1)
        fy = _fiscal_year_for(through, ledger)
        return ytd(fy, through)
    if m := _SPEC_RE["rolling"].match(s):
        return rolling(date(int(m.group(2)), int(m.group(3)), 1), int(m.group(1)))
    if m := _SPEC_RE["fy"].match(s):
        start = date(int(m.group(1)), int(m.group(2)), 1)
        fy = _fiscal_year_for(start, ledger)
        return fiscal_year_period(fy)
    if m := _SPEC_RE["custom"].match(s):
        return custom(date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)))
    raise ValueError(f"Okänd periodangivelse: {spec!r}")


def _fiscal_year_for(d: date, ledger: Ledger | None) -> FiscalYear:
    if ledger is not None:
        y = ledger.year_for(d)
        if y is not None:
            return y.fiscal_year
        if ledger.years:  # anta samma brytmånad som senaste året
            ref = ledger.years[-1].fiscal_year
            start = date(d.year if d.month >= ref.start.month else d.year - 1, ref.start.month, 1)
            return FiscalYear(start, add_months(start, 12) - timedelta(days=1))
    return FiscalYear(date(d.year, 1, 1), date(d.year, 12, 31))
