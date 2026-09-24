"""Igenkänning av rutin- och bokslutsmönster.

Forskningen om journal entry testing visar att regelbaserade flaggor ger mycket brus från
återföringar, periodiseringar och bokslutsposter. Vi klassar därför verifikationer innan
mönsterkontrollerna körs, så att rutinposter inte larmar.
"""

from __future__ import annotations

from enum import StrEnum

from redovisningai.domain.ledger import Voucher


class Pattern(StrEnum):
    VAT_SETTLEMENT = "vat_settlement"
    PAYROLL = "payroll"
    PAYROLL_TAX = "payroll_tax"
    DEPRECIATION = "depreciation"
    ACCRUAL = "accrual"
    TAX_ACCOUNT = "tax_account"
    BANK_FEE = "bank_fee"
    APPROPRIATION = "appropriation"  # bokslutsdispositioner 88xx
    CLOSING = "closing"  # 8990–8999, årets resultat
    CUSTOMER_PAYMENT = "customer_payment"
    SUPPLIER_PAYMENT = "supplier_payment"
    SUPPLIER_INVOICE = "supplier_invoice"
    CUSTOMER_INVOICE = "customer_invoice"


def _in(acc: int, lo: int, hi: int) -> bool:
    return lo <= acc <= hi


def classify(v: Voucher) -> set[Pattern]:
    accs = [r.account for r in v.effective_rows]
    if not accs:
        return set()
    tags: set[Pattern] = set()
    if all(_in(a, 2610, 2659) for a in accs):
        tags.add(Pattern.VAT_SETTLEMENT)
    if any(_in(a, 7000, 7399) for a in accs) and any(_in(a, 2710, 2739) or _in(a, 1900, 1999) for a in accs):
        tags.add(Pattern.PAYROLL)
    if all(_in(a, 7500, 7599) or _in(a, 2730, 2739) or _in(a, 2940, 2949) for a in accs):
        tags.add(Pattern.PAYROLL_TAX)
    if any(_in(a, 7700, 7899) for a in accs) and all(_in(a, 7700, 7899) or _in(a, 1000, 1399) for a in accs):
        tags.add(Pattern.DEPRECIATION)
    if any(_in(a, 1700, 1799) or _in(a, 2900, 2999) for a in accs) and any(_in(a, 3000, 8999) for a in accs):
        if not any(_in(a, 1900, 1999) for a in accs):
            tags.add(Pattern.ACCRUAL)
    if any(_in(a, 1630, 1639) for a in accs) and all(
        _in(a, 1630, 1639)
        or _in(a, 2500, 2799)
        or _in(a, 1900, 1999)
        or _in(a, 2650, 2659)
        or _in(a, 8400, 8499)
        or _in(a, 8300, 8399)
        for a in accs
    ):
        tags.add(Pattern.TAX_ACCOUNT)
    if set(accs) <= {6570, 1930, 1920, 1940} and 6570 in accs:
        tags.add(Pattern.BANK_FEE)
    if any(_in(a, 8800, 8899) for a in accs):
        tags.add(Pattern.APPROPRIATION)
    if any(_in(a, 8990, 8999) for a in accs):
        tags.add(Pattern.CLOSING)
    if any(_in(a, 1510, 1519) for a in accs) and any(_in(a, 1900, 1999) for a in accs) and len(set(accs)) <= 3:
        tags.add(Pattern.CUSTOMER_PAYMENT)
    if any(_in(a, 2440, 2449) for a in accs) and any(_in(a, 1900, 1999) for a in accs) and len(set(accs)) <= 3:
        tags.add(Pattern.SUPPLIER_PAYMENT)
    if any(_in(a, 2440, 2449) for a in accs) and any(_in(a, 4000, 7999) or _in(a, 1100, 1499) for a in accs):
        tags.add(Pattern.SUPPLIER_INVOICE)
    if any(_in(a, 1510, 1519) for a in accs) and any(_in(a, 3000, 3999) for a in accs):
        tags.add(Pattern.CUSTOMER_INVOICE)
    return tags


ROUTINE = {
    Pattern.VAT_SETTLEMENT,
    Pattern.PAYROLL,
    Pattern.PAYROLL_TAX,
    Pattern.DEPRECIATION,
    Pattern.ACCRUAL,
    Pattern.TAX_ACCOUNT,
    Pattern.BANK_FEE,
    Pattern.APPROPRIATION,
    Pattern.CLOSING,
    Pattern.CUSTOMER_PAYMENT,
    Pattern.SUPPLIER_PAYMENT,
}


def is_routine(v: Voucher) -> bool:
    return bool(classify(v) & ROUTINE)
