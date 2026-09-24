"""Index över kontorörelser och saldon.

Byggs en gång per Ledger och används av rapporter, nyckeltal, kontroller och analyser.
Allt räknas med Decimal. Perioder bestäms av verifikationsdatum.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from redovisningai.accounting.periods import Period, add_months, month_start, months_between
from redovisningai.domain.ledger import ZERO, Ledger, Voucher, YearData


@dataclass(frozen=True, slots=True)
class AccountRange:
    start: int
    end: int

    def __contains__(self, account: object) -> bool:
        return isinstance(account, int) and self.start <= account <= self.end


@dataclass(frozen=True, slots=True)
class AccountSet:
    """Mängd av konton definierad av intervall, med undantag."""

    ranges: tuple[AccountRange, ...]
    exclude: tuple[AccountRange, ...] = ()

    @staticmethod
    def of(*spec: tuple[int, int] | int, exclude: Sequence[tuple[int, int] | int] = ()) -> AccountSet:
        def conv(x: tuple[int, int] | int) -> AccountRange:
            return AccountRange(x, x) if isinstance(x, int) else AccountRange(x[0], x[1])

        return AccountSet(tuple(conv(s) for s in spec), tuple(conv(e) for e in exclude))

    def __contains__(self, account: object) -> bool:
        return any(account in r for r in self.ranges) and not any(account in r for r in self.exclude)

    def describe(self) -> str:
        parts = [f"{r.start}" if r.start == r.end else f"{r.start}–{r.end}" for r in self.ranges]
        text = ", ".join(parts)
        if self.exclude:
            text += " exkl. " + ", ".join(
                f"{r.start}" if r.start == r.end else f"{r.start}–{r.end}" for r in self.exclude
            )
        return text


@dataclass(slots=True)
class MonthCoverage:
    """Varifrån en månads siffror kommer."""

    source: str  # "vouchers" | "psaldo" | "none"


@dataclass(slots=True)
class LedgerIndex:
    ledger: Ledger
    movements: dict[date, dict[int, Decimal]] = field(default_factory=dict)
    coverage: dict[date, str] = field(default_factory=dict)
    vouchers_by_month: dict[date, list[Voucher]] = field(default_factory=dict)
    accounts_used: set[int] = field(default_factory=set)

    @classmethod
    def build(cls, ledger: Ledger) -> LedgerIndex:
        idx = cls(ledger=ledger)
        mv: dict[date, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
        by_month: dict[date, list[Voucher]] = defaultdict(list)
        for year in ledger.years:
            months = year.fiscal_year.months()
            if year.has_vouchers:
                for m in months:
                    idx.coverage[m] = "vouchers"
                for v in year.vouchers:
                    m = month_start(v.date)
                    by_month[m].append(v)
                    for r in v.effective_rows:
                        mv[m][r.account] += r.amount
                        idx.accounts_used.add(r.account)
            elif year.period_balances:
                _fill_from_psaldo(year, mv, idx)
            else:
                for m in months:
                    idx.coverage.setdefault(m, "none")
        idx.movements = {m: dict(a) for m, a in mv.items()}
        idx.vouchers_by_month = dict(by_month)
        return idx

    # ------------------------------------------------------------------ frågor
    def has_data(self, period: Period) -> bool:
        return all(self.coverage.get(m) in ("vouchers", "psaldo") for m in period.months())

    def missing_months(self, period: Period) -> list[date]:
        return [m for m in period.months() if self.coverage.get(m) not in ("vouchers", "psaldo")]

    def movement(self, accounts: AccountSet | Iterable[int] | int, period: Period) -> Decimal:
        total = ZERO
        for m in period.months():
            month_mv = self.movements.get(m)
            if not month_mv:
                continue
            for acc, amt in month_mv.items():
                if _matches(acc, accounts):
                    total += amt
        return total

    def movement_by_account(self, accounts: AccountSet, period: Period) -> dict[int, Decimal]:
        out: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for m in period.months():
            for acc, amt in self.movements.get(m, {}).items():
                if acc in accounts:
                    out[acc] += amt
        return {a: v for a, v in out.items() if v != 0}

    def monthly_series(self, accounts: AccountSet | Iterable[int] | int, months: Sequence[date]) -> list[Decimal]:
        return [
            sum((amt for acc, amt in self.movements.get(m, {}).items() if _matches(acc, accounts)), ZERO)
            for m in months
        ]

    def year_for(self, d: date) -> YearData | None:
        return self.ledger.year_for(d)

    def opening_balance(self, year: YearData, account: int) -> Decimal:
        if account in year.opening:
            return year.opening[account]
        prev = self.ledger.previous_year(year)
        if prev is not None and not year.opening and account in prev.closing:
            return prev.closing[account]
        return ZERO

    def balance_at(self, accounts: AccountSet | Iterable[int] | int, at: date) -> Decimal:
        """Saldo för balanskonton vid dagens slut (IB + rörelser t.o.m. dagen)."""
        year = self.ledger.year_for(at)
        if year is None:
            return ZERO
        total = ZERO
        accs: set[int] = set(year.opening) | self.accounts_used | set(self.ledger.accounts)
        for acc in accs:
            if acc >= 3000 or not _matches(acc, accounts):
                continue
            total += self.opening_balance(year, acc)
        cur_month = month_start(at)
        if cur_month > year.fiscal_year.start:
            for m in months_between(year.fiscal_year.start, add_months(cur_month, -1)):
                for acc, amt in self.movements.get(m, {}).items():
                    if acc < 3000 and _matches(acc, accounts):
                        total += amt
        if year.has_vouchers:
            for v in self.vouchers_by_month.get(cur_month, []):
                if v.date <= at:
                    for r in v.effective_rows:
                        if r.account < 3000 and _matches(r.account, accounts):
                            total += r.amount
        else:
            for acc, amt in self.movements.get(cur_month, {}).items():
                if acc < 3000 and _matches(acc, accounts):
                    total += amt
        return total

    def balances_at(self, at: date, accounts: AccountSet | None = None) -> dict[int, Decimal]:
        year = self.ledger.year_for(at)
        if year is None:
            return {}
        out: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for acc in set(year.opening) | set(self.ledger.accounts) | self.accounts_used:
            if acc < 3000 and (accounts is None or acc in accounts):
                ob = self.opening_balance(year, acc)
                if ob:
                    out[acc] += ob
        cur_month = month_start(at)
        if cur_month > year.fiscal_year.start:
            for m in months_between(year.fiscal_year.start, add_months(cur_month, -1)):
                for acc, amt in self.movements.get(m, {}).items():
                    if acc < 3000 and (accounts is None or acc in accounts):
                        out[acc] += amt
        if year.has_vouchers:
            for v in self.vouchers_by_month.get(cur_month, []):
                if v.date <= at:
                    for r in v.effective_rows:
                        if r.account < 3000 and (accounts is None or r.account in accounts):
                            out[r.account] += r.amount
        else:
            for acc, amt in self.movements.get(cur_month, {}).items():
                if acc < 3000 and (accounts is None or acc in accounts):
                    out[acc] += amt
        return {a: x for a, x in out.items() if x != 0}

    def result_to_date(self, at: date, *, include_closing_entries: bool = False) -> Decimal:
        """Räkenskapsårets resultat t.o.m. datum (positivt = vinst).

        Normalt exkluderas 8990–8999 (bokslutsföring av årets resultat mot 2099). Med
        `include_closing_entries=True` blir resultatet 0 när bokslutet redan är fört – det är
        det som ska läggas till eget kapital i en balansräkning.
        """
        year = self.ledger.year_for(at)
        if year is None:
            return ZERO
        upper = 8999 if include_closing_entries else 8989
        total = ZERO
        for m in months_between(year.fiscal_year.start, at):
            month_mv = self.movements.get(m, {})
            if m == month_start(at) and at < _month_last_day(m):
                for v in self.vouchers_by_month.get(m, []):
                    if v.date <= at:
                        for r in v.effective_rows:
                            if 3000 <= r.account <= upper:
                                total += r.amount
                continue
            for acc, amt in month_mv.items():
                if 3000 <= acc <= upper:
                    total += amt
        return -total

    def vouchers_in(self, period: Period) -> list[Voucher]:
        out: list[Voucher] = []
        for m in period.months():
            out.extend(v for v in self.vouchers_by_month.get(m, []) if period.contains(v.date))
        return out

    def last_voucher_date(self) -> date | None:
        dates = [v.date for v in self.ledger.all_vouchers()]
        return max(dates) if dates else None

    def latest_month_with_data(self) -> date | None:
        months = [m for m, c in self.coverage.items() if c == "vouchers" and m in self.vouchers_by_month]
        return max(months) if months else None

    def months_with_data(self) -> list[date]:
        return sorted(m for m, c in self.coverage.items() if c in ("vouchers", "psaldo"))

    def history_months(self, through: date, n: int) -> list[date]:
        """De n månaderna före `through` (exklusive) som har data."""
        out: list[date] = []
        cur = add_months(month_start(through), -1)
        while len(out) < n and cur >= month_start(self.ledger.first_date):
            if self.coverage.get(cur) in ("vouchers", "psaldo"):
                out.append(cur)
            cur = add_months(cur, -1)
        return list(reversed(out))


def _month_last_day(m: date) -> date:
    return add_months(m, 1) - timedelta(days=1)


def _matches(acc: int, accounts: AccountSet | Iterable[int] | int) -> bool:
    if isinstance(accounts, int):
        return acc == accounts
    if isinstance(accounts, AccountSet):
        return acc in accounts
    return acc in set(accounts)


def _fill_from_psaldo(year: YearData, mv: dict[date, dict[int, Decimal]], idx: LedgerIndex) -> None:
    """#PSALDO: resultatkonton = periodens rörelse, balanskonton = saldo vid periodens slut."""
    months = year.fiscal_year.months()
    months_in_psaldo = {p for (p, _a) in year.period_balances}
    for m in months:
        idx.coverage[m] = "psaldo" if m in months_in_psaldo else "none"
    by_acc: dict[int, dict[date, Decimal]] = defaultdict(dict)
    for (p, acc), amt in year.period_balances.items():
        by_acc[acc][p] = amt
    for acc, per in by_acc.items():
        prev_bal = year.opening.get(acc, ZERO)
        for m in months:
            if m not in per:
                continue
            if acc >= 3000:
                mv[m][acc] += per[m]
            else:
                mv[m][acc] += per[m] - prev_bal
                prev_bal = per[m]
            idx.accounts_used.add(acc)
