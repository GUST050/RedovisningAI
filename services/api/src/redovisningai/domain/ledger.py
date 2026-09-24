"""Normaliserad, systemoberoende bokföringsmodell.

Allt som kommer in (SIE-fil, Fortnox, Spiris …) översätts till den här modellen.
Ekonomimotorn, kontrollerna och analyserna arbetar bara mot den, aldrig mot källformatet.

Belopp följer SIE-konventionen: debet positivt, kredit negativt. Tecken vänds bara i
presentationslagret.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0.00")


class AccountType(StrEnum):
    ASSET = "T"  # Tillgång
    LIABILITY = "S"  # Skuld / eget kapital
    COST = "K"  # Kostnad
    INCOME = "I"  # Intäkt


class RowStatus(StrEnum):
    NORMAL = "normal"
    ADDED = "added"  # #RTRANS – rad tillagd i efterhand
    REMOVED = "removed"  # #BTRANS – rad borttagen i efterhand (räknas inte i saldon)


@dataclass(frozen=True, slots=True)
class Account:
    number: int
    name: str
    type: AccountType | None = None
    sru: str | None = None

    @property
    def is_balance(self) -> bool:
        if self.type is not None:
            return self.type in (AccountType.ASSET, AccountType.LIABILITY)
        return self.number < 3000

    @property
    def is_result(self) -> bool:
        return not self.is_balance


@dataclass(frozen=True, slots=True)
class Row:
    account: int
    amount: Decimal
    trans_date: date | None = None
    text: str | None = None
    quantity: Decimal | None = None
    objects: tuple[tuple[str, str], ...] = ()
    status: RowStatus = RowStatus.NORMAL
    source_line: int | None = None

    @property
    def is_effective(self) -> bool:
        return self.status is not RowStatus.REMOVED

    def canonical(self) -> str:
        objs = ",".join(f"{d}:{o}" for d, o in self.objects)
        return "|".join(
            [
                str(self.account),
                f"{self.amount:.2f}",
                self.trans_date.isoformat() if self.trans_date else "",
                (self.text or "").strip(),
                "" if self.quantity is None else str(self.quantity),
                objs,
                self.status.value,
            ]
        )


@dataclass(frozen=True, slots=True)
class VoucherKey:
    series: str
    number: str

    def __str__(self) -> str:
        return f"{self.series}{self.number}"


@dataclass(frozen=True, slots=True)
class Voucher:
    series: str
    number: str
    date: date
    text: str
    rows: tuple[Row, ...]
    reg_date: date | None = None
    signature: str | None = None
    source_line: int | None = None

    @property
    def key(self) -> VoucherKey:
        return VoucherKey(self.series, self.number)

    @property
    def effective_rows(self) -> tuple[Row, ...]:
        return tuple(r for r in self.rows if r.is_effective)

    @property
    def balance(self) -> Decimal:
        return sum((r.amount for r in self.effective_rows), ZERO)

    @property
    def debit_total(self) -> Decimal:
        return sum((r.amount for r in self.effective_rows if r.amount > 0), ZERO)

    def row_date(self, row: Row) -> date:
        return row.trans_date or self.date

    def content_hash(self) -> str:
        """Stabilt fingeravtryck av verifikationens innehåll (används för ändringsdiff)."""
        h = hashlib.sha256()
        h.update(f"{self.series}|{self.number}|{self.date.isoformat()}|{self.text.strip()}".encode())
        for r in self.rows:
            h.update(b"\n")
            h.update(r.canonical().encode())
        return h.hexdigest()


@dataclass(frozen=True, slots=True)
class FiscalYear:
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def months(self) -> list[date]:
        """Första dagen i varje månad i räkenskapsåret."""
        out: list[date] = []
        y, m = self.start.year, self.start.month
        while date(y, m, 1) <= self.end:
            out.append(date(y, m, 1))
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out

    @property
    def label(self) -> str:
        if self.start.month == 1 and self.start.day == 1 and self.end.month == 12:
            return str(self.start.year)
        return f"{self.start:%Y-%m}–{self.end:%Y-%m}"


@dataclass(slots=True)
class YearData:
    """All bokföring för ett räkenskapsår."""

    fiscal_year: FiscalYear
    vouchers: list[Voucher] = field(default_factory=list)
    opening: dict[int, Decimal] = field(default_factory=dict)  # #IB
    closing: dict[int, Decimal] = field(default_factory=dict)  # #UB (enligt filen)
    result: dict[int, Decimal] = field(default_factory=dict)  # #RES (enligt filen)
    period_balances: dict[tuple[date, int], Decimal] = field(default_factory=dict)  # #PSALDO
    budget: dict[tuple[date, int], Decimal] = field(default_factory=dict)  # #PBUDGET
    source_ref: str | None = None  # t.ex. import-id eller filnamn
    # False när året bara finns som sammandrag (t.ex. årsnr -1 i en SIE4-fil: IB/UB/RES men inga
    # verifikationer). Då kan månadsrörelser bara tas från #PSALDO.
    has_vouchers: bool = True


@dataclass(slots=True)
class Ledger:
    """Ett bolags bokföring över ett eller flera räkenskapsår."""

    company_name: str
    org_number: str | None
    accounts: dict[int, Account] = field(default_factory=dict)
    years: list[YearData] = field(default_factory=list)  # sorterade stigande
    dimensions: dict[str, str] = field(default_factory=dict)
    objects: dict[tuple[str, str], str] = field(default_factory=dict)
    currency: str = "SEK"
    program: str | None = None

    def sort(self) -> None:
        self.years.sort(key=lambda y: y.fiscal_year.start)

    @property
    def current(self) -> YearData:
        if not self.years:
            raise ValueError("Bokföringen innehåller inga räkenskapsår")
        return self.years[-1]

    def year_for(self, d: date) -> YearData | None:
        for y in self.years:
            if y.fiscal_year.contains(d):
                return y
        return None

    def previous_year(self, year: YearData) -> YearData | None:
        idx = self.years.index(year)
        return self.years[idx - 1] if idx > 0 else None

    def all_vouchers(self) -> Iterator[Voucher]:
        for y in self.years:
            yield from y.vouchers

    def account_name(self, number: int) -> str:
        acc = self.accounts.get(number)
        return acc.name if acc else f"Konto {number}"

    def account(self, number: int) -> Account:
        return self.accounts.get(number) or Account(number=number, name=f"Konto {number}")

    def merge_year(self, year: YearData, accounts: Iterable[Account]) -> None:
        """Lägg till eller ersätt ett räkenskapsår.

        Ett år med verifikationer ersätter alltid ett sammandragsår. Ett sammandragsår ersätter
        aldrig ett år som redan har verifikationer.
        """
        existing = next((y for y in self.years if y.fiscal_year.start == year.fiscal_year.start), None)
        if existing is not None and existing.has_vouchers and not year.has_vouchers:
            for acc in accounts:
                self.accounts.setdefault(acc.number, acc)
            return
        self.years = [y for y in self.years if y.fiscal_year.start != year.fiscal_year.start]
        self.years.append(year)
        for acc in accounts:
            self.accounts[acc.number] = acc
        self.sort()

    @property
    def first_date(self) -> date:
        return self.years[0].fiscal_year.start

    @property
    def last_date(self) -> date:
        return self.years[-1].fiscal_year.end
