"""Periodmognad: hur klar är månaden, och hur brukar kunden bokföra?

Används för att undvika falsklarm: bolag som bara periodiserar vid bokslut eller använder
kontantmetoden ser "konstiga" ut månad för månad. Allt här är deterministiskt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from statistics import median
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.periods import Period, add_months, month_start

RECEIVABLES = AccountSet.of((1510, 1519))
PAYABLES = AccountSet.of((2440, 2449))
DEPRECIATION = AccountSet.of((7700, 7899))
FIXED_ASSETS = AccountSet.of((1100, 1299))
VACATION = AccountSet.of((2920, 2929))
SALARIES = AccountSet.of((7000, 7399), exclude=[(7290, 7299)])
BANK = AccountSet.of((1900, 1999))


class AccountingMethod(StrEnum):
    INVOICE = "invoice"  # faktureringsmetoden
    CASH = "cash"  # kontantmetoden (bokslutsmetoden)
    UNKNOWN = "unknown"


class PeriodStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PRELIMINARY = "PRELIMINARY"
    NO_DATA = "NO_DATA"


@dataclass(slots=True)
class Maturity:
    period: str
    status: PeriodStatus
    accounting_method: AccountingMethod
    monthly_depreciation: bool | None
    monthly_vacation_accrual: bool | None
    payroll_booked: bool | None
    completeness: Decimal | None
    closed_signal: bool
    low_periodization: bool
    recommended_view: str  # "month" | "r12"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "status": self.status.value,
            "accounting_method": self.accounting_method.value,
            "monthly_depreciation": self.monthly_depreciation,
            "monthly_vacation_accrual": self.monthly_vacation_accrual,
            "payroll_booked": self.payroll_booked,
            "completeness": None if self.completeness is None else str(self.completeness),
            "closed_signal": self.closed_signal,
            "low_periodization": self.low_periodization,
            "recommended_view": self.recommended_view,
            "notes": self.notes,
        }


def _months_with_movement(index: LedgerIndex, accounts: AccountSet, months: list[date]) -> int:
    return sum(1 for v in index.monthly_series(accounts, months) if v != 0)


def assess(
    index: LedgerIndex,
    period: Period,
    *,
    method_override: AccountingMethod | None = None,
) -> Maturity:
    m = month_start(period.end)
    history = index.history_months(add_months(m, 1), 12)  # inkl. aktuell månad
    history_before = [h for h in history if h < m]
    notes: list[str] = []
    if index.coverage.get(m) not in ("vouchers", "psaldo"):
        return Maturity(
            period.spec,
            PeriodStatus.NO_DATA,
            AccountingMethod.UNKNOWN,
            None,
            None,
            None,
            None,
            False,
            True,
            "r12",
            ["Ingen bokföring finns för perioden."],
        )

    year = index.ledger.year_for(m)
    fy_end_month = month_start(year.fiscal_year.end) if year else None
    regular = [h for h in history if h != fy_end_month]

    # Bokföringsmetod
    if method_override is not None:
        method = method_override
    elif len(regular) >= 3:
        share = _months_with_movement(index, RECEIVABLES, regular) + _months_with_movement(index, PAYABLES, regular)
        method = AccountingMethod.INVOICE if share >= len(regular) else AccountingMethod.CASH
    else:
        method = AccountingMethod.UNKNOWN

    # Avskrivningar
    has_assets = index.balance_at(FIXED_ASSETS, period.end) > 0
    monthly_dep: bool | None = None
    if has_assets and len(regular) >= 4:
        monthly_dep = _months_with_movement(index, DEPRECIATION, regular) >= len(regular) / 2

    # Löner och semesterlöneskuld
    salary_months = _months_with_movement(index, SALARIES, history_before[-6:])
    has_payroll = salary_months >= 3
    payroll_booked: bool | None = None
    monthly_vac: bool | None = None
    if has_payroll:
        payroll_booked = index.movement(SALARIES, period) != 0
        if len(regular) >= 4:
            monthly_vac = _months_with_movement(index, VACATION, regular) >= len(regular) / 2

    # Fullständighet (antal verifikationer mot normalt)
    counts = [len(index.vouchers_by_month.get(h, [])) for h in history_before[-6:]]
    current = len(index.vouchers_by_month.get(m, []))
    completeness: Decimal | None = None
    if counts and median(counts) > 0:
        completeness = (Decimal(current) / Decimal(median(counts))).quantize(Decimal("0.01"))

    last = index.last_voucher_date()
    closed_signal = last is not None and last > period.end

    status = PeriodStatus.COMPLETE
    if payroll_booked is False:
        status = PeriodStatus.PRELIMINARY
        notes.append("Löner saknas för perioden trots att bolaget normalt har löner.")
    if completeness is not None and completeness < Decimal("0.6"):
        status = PeriodStatus.PRELIMINARY
        notes.append(f"Ovanligt få verifikationer ({current} mot normalt ca {int(median(counts))}).")
    if not closed_signal:
        notes.append("Inga verifikationer efter periodens slut – perioden kan vara ofullständig.")

    low = method is AccountingMethod.CASH or monthly_dep is False or monthly_vac is False
    if method is AccountingMethod.CASH:
        notes.append("Kontantmetoden: intäkter och kostnader bokförs vid betalning – jämför hellre R12.")
    if monthly_dep is False:
        notes.append("Avskrivningar bokförs inte månadsvis (troligen vid bokslut).")
    if monthly_vac is False:
        notes.append("Semesterlöneskuld bokförs inte månadsvis.")
    return Maturity(
        period=period.spec,
        status=status,
        accounting_method=method,
        monthly_depreciation=monthly_dep,
        monthly_vacation_accrual=monthly_vac,
        payroll_booked=payroll_booked,
        completeness=completeness,
        closed_signal=closed_signal,
        low_periodization=low,
        recommended_view="r12" if low else "month",
        notes=notes,
    )
