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
    # Räkenskapsår som bara finns som årssaldon (#RES/#UB utan verifikationer eller #PSALDO):
    # årets rörelse per konto. Hela året går att jämföra, enskilda månader inte.
    annual: dict[date, dict[int, Decimal]] = field(default_factory=dict)
    _openings: dict[date, dict[int, Decimal]] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, ledger: Ledger) -> LedgerIndex:
        idx = cls(ledger=ledger)
        mv: dict[date, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
        by_month: dict[date, list[Voucher]] = defaultdict(list)
        for year in ledger.years:
            months = year.fiscal_year.months()
            if year.has_vouchers:
                for m in months:
                    idx.coverage[m] = "vouchers" if year.covers(m) else "none"
                for v in year.vouchers:
                    m = month_start(v.date)
                    by_month[m].append(v)
                    for r in v.effective_rows:
                        mv[m][r.account] += r.amount
                        idx.accounts_used.add(r.account)
            else:
                if year.period_balances:
                    _fill_from_psaldo(year, mv, idx)
                else:
                    for m in months:
                        idx.coverage.setdefault(m, "none")
                if year.result or year.closing:
                    _fill_annual(year, idx)
        idx.movements = {m: dict(a) for m, a in mv.items()}
        idx.vouchers_by_month = dict(by_month)
        return idx

    # ------------------------------------------------------------------ frågor
    def annual_year(self, period: Period) -> YearData | None:
        """Året om perioden är ett helt räkenskapsår som bara finns som årssaldon (inte per månad)."""
        year = self.ledger.year_for(period.start)
        if year is None or year.fiscal_year.start not in self.annual:
            return None
        if period.start != year.fiscal_year.start or period.end != year.fiscal_year.end:
            return None
        if all(self.coverage.get(m) in ("vouchers", "psaldo") for m in period.months()):
            return None  # månaderna finns – använd dem
        return year

    def has_data(self, period: Period) -> bool:
        if self.annual_year(period) is not None:
            return True
        return all(self.coverage.get(m) in ("vouchers", "psaldo") for m in period.months())

    def missing_months(self, period: Period) -> list[date]:
        if self.annual_year(period) is not None:
            return []
        return [m for m in period.months() if self.coverage.get(m) not in ("vouchers", "psaldo")]

    def period_movements(self, period: Period) -> dict[int, Decimal]:
        """Rörelse per konto för perioden (månadernas summa, eller årssaldon för ett sammandragsår)."""
        year = self.annual_year(period)
        if year is not None:
            return dict(self.annual[year.fiscal_year.start])
        out: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for m in period.months():
            for acc, amt in self.movements.get(m, {}).items():
                out[acc] += amt
        return dict(out)

    def movement(self, accounts: AccountSet | Iterable[int] | int, period: Period) -> Decimal:
        wanted = accounts if isinstance(accounts, AccountSet | int) else set(accounts)
        return sum((amt for acc, amt in self.period_movements(period).items() if _matches(acc, wanted)), ZERO)

    def movement_by_account(self, accounts: AccountSet, period: Period) -> dict[int, Decimal]:
        return {a: v for a, v in self.period_movements(period).items() if a in accounts and v != 0}

    def monthly_series(self, accounts: AccountSet | Iterable[int] | int, months: Sequence[date]) -> list[Decimal]:
        return [
            sum((amt for acc, amt in self.movements.get(m, {}).items() if _matches(acc, accounts)), ZERO)
            for m in months
        ]

    def year_for(self, d: date) -> YearData | None:
        return self.ledger.year_for(d)

    def opening_balance(self, year: YearData, account: int) -> Decimal:
        return self.opening_balances(year).get(account, ZERO)

    def opening_balances(self, year: YearData) -> dict[int, Decimal]:
        """Ingående balans per konto.

        Källans IB används i första hand. Saknas den tas föregående års UB enligt källan, och
        saknas även den härleds UB ur föregående års IB och verifikationer (med ett ej
        bokslutsfört resultat fört till 2099). Härledning görs bara när föregående år är komplett.
        """
        key = year.fiscal_year.start
        cached = self._openings.get(key)
        if cached is None:
            cached = self._resolve_opening(year)
            self._openings[key] = cached
        return cached

    def _resolve_opening(self, year: YearData) -> dict[int, Decimal]:
        if year.opening:
            return dict(year.opening)
        prev = self._adjacent_previous(year)
        if prev is None:
            return {}
        if prev.closing:
            # UB enligt källan. Är föregående års resultat inte bokslutsfört balanserar UB inte;
            # då förs resultatet till eget kapital (2099) så att årets IB balanserar.
            derived_ub = {a: v for a, v in prev.closing.items() if a < 3000}
            imbalance = sum(derived_ub.values(), ZERO)
            if imbalance:
                derived_ub[2099] = derived_ub.get(2099, ZERO) - imbalance
            return {a: v for a, v in derived_ub.items() if v != 0}
        if not self._complete_voucher_year(prev):
            return {}
        end = prev.fiscal_year.end
        derived = dict(self.balances_at(end))
        unclosed = self.result_to_date(end, include_closing_entries=True)
        if unclosed:
            derived[2099] = derived.get(2099, ZERO) - unclosed
        return {a: v for a, v in derived.items() if v != 0}

    def _adjacent_previous(self, year: YearData) -> YearData | None:
        prev = self.ledger.previous_year(year)
        if prev is None or prev.fiscal_year.end + timedelta(days=1) != year.fiscal_year.start:
            return None
        return prev

    def _complete_voucher_year(self, year: YearData) -> bool:
        return year.has_vouchers and all(year.covers(m) for m in year.fiscal_year.months()) and self.opening_known(year)

    def opening_known(self, year: YearData) -> bool:
        """Är årets ingående balanser kända (ur källan eller härledda ur föregående år)?"""
        if year.opening or year.opening_status != "missing":
            return True
        prev = self._adjacent_previous(year)
        if prev is None:
            return False
        return bool(prev.closing) or self._complete_voucher_year(prev)

    def _annual_closing(self, year: YearData, at: date) -> bool:
        """Årsskiftet i ett sammandragsår: saldona är kända direkt ur källans UB."""
        return at == year.fiscal_year.end and year.fiscal_year.start in self.annual and bool(year.closing)

    def balance_complete(self, at: date) -> bool:
        """Kan balansposterna vid dagens slut beräknas? Kräver känd IB och alla månader till dagen."""
        year = self.ledger.year_for(at)
        if year is not None and self._annual_closing(year, at):
            return True
        if year is None or not self.opening_known(year):
            return False
        return all(self.coverage.get(m) in ("vouchers", "psaldo") for m in months_between(year.fiscal_year.start, at))

    def balance_at(self, accounts: AccountSet | Iterable[int] | int, at: date) -> Decimal:
        """Saldo för balanskonton vid dagens slut (IB + rörelser t.o.m. dagen)."""
        year = self.ledger.year_for(at)
        if year is None:
            return ZERO
        if self._annual_closing(year, at):
            return sum((v for a, v in year.closing.items() if a < 3000 and _matches(a, accounts)), ZERO)
        total = ZERO
        opening = self.opening_balances(year)
        accs: set[int] = set(opening) | self.accounts_used | set(self.ledger.accounts)
        for acc in accs:
            if acc >= 3000 or not _matches(acc, accounts):
                continue
            total += opening.get(acc, ZERO)
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
        if self._annual_closing(year, at):
            return {
                a: v for a, v in year.closing.items() if a < 3000 and v != 0 and (accounts is None or a in accounts)
            }
        out: dict[int, Decimal] = defaultdict(lambda: ZERO)
        opening = self.opening_balances(year)
        for acc, ob in opening.items():
            if acc < 3000 and ob and (accounts is None or acc in accounts):
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
        return sum(self.result_by_account_at(at, include_closing_entries=include_closing_entries).values(), ZERO)

    def result_by_account_at(self, at: date, *, include_closing_entries: bool = False) -> dict[int, Decimal]:
        """Resultatets kontobidrag t.o.m. datum, med samma datumgräns som result_to_date."""
        year = self.ledger.year_for(at)
        if year is None:
            return {}
        upper = 8999 if include_closing_entries else 8989
        if at == year.fiscal_year.end and year.fiscal_year.start in self.annual and year.closing:
            annual = {a: -v for a, v in year.result.items() if 3000 <= a <= upper and v != 0}
            if include_closing_entries:
                # UB är avgörande: det ej bokslutsförda resultatet är obalansen i UB. Saknar #RES
                # bokslutsposten (8999) för ett bokslutsfört år läggs den till här, så att
                # resultatet inte räknas två gånger i eget kapital.
                unclosed = sum((v for a, v in year.closing.items() if a < 3000), ZERO)
                gap = unclosed - sum(annual.values(), ZERO)
                if gap:
                    annual[8999] = annual.get(8999, ZERO) + gap
            return {a: v for a, v in annual.items() if v != 0}
        amounts: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for m in months_between(year.fiscal_year.start, at):
            month_mv = self.movements.get(m, {})
            if m == month_start(at) and at < _month_last_day(m):
                for v in self.vouchers_by_month.get(m, []):
                    if v.date <= at:
                        for r in v.effective_rows:
                            if 3000 <= r.account <= upper:
                                amounts[r.account] -= r.amount
                continue
            for acc, amt in month_mv.items():
                if 3000 <= acc <= upper:
                    amounts[acc] -= amt
        return {account: amount for account, amount in amounts.items() if amount != ZERO}

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

    def data_horizon(self) -> date | None:
        """Sista dagen i den senaste månaden med bokförda verifikationer (eller periodsaldon)."""
        latest = self.latest_month_with_data()
        if latest is None:
            psaldo = [m for m, c in self.coverage.items() if c == "psaldo"]
            latest = max(psaldo) if psaldo else None
        return None if latest is None else add_months(latest, 1) - timedelta(days=1)

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


def _fill_annual(year: YearData, idx: LedgerIndex) -> None:
    """#RES/#UB för ett år utan verifikationer: årets rörelse per konto (resultatkonton alltid,
    balanskonton bara när IB finns så att rörelsen blir UB − IB)."""
    totals = {a: v for a, v in year.result.items() if a >= 3000}
    if year.opening:
        for acc in set(year.closing) | set(year.opening):
            if acc < 3000:
                diff = year.closing.get(acc, ZERO) - year.opening.get(acc, ZERO)
                if diff:
                    totals[acc] = diff
    idx.annual[year.fiscal_year.start] = {a: v for a, v in totals.items() if v != 0}
    idx.accounts_used.update(idx.annual[year.fiscal_year.start])
    for m in year.fiscal_year.months():
        if idx.coverage.get(m) in (None, "none"):
            idx.coverage[m] = "annual"


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
