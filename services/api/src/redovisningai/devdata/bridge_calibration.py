"""Lokal kalibrering av signalen för stor enskild bokning.

Rapporten innehåller bara antal månader/signaler och aggregerade andelar.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.transaction_bridge import LARGE_BOOKING_MIN
from redovisningai.analytics.finding_candidates import RESULT_ACCOUNTS
from redovisningai.sie.convert import ledger_from_documents
from redovisningai.sie.parser import Severity, parse_sie

ZERO = Decimal("0")


def calibrate_large_bookings(
    raw: bytes, shares: list[Decimal], *, minimum: Decimal = LARGE_BOOKING_MIN
) -> dict[str, Any]:
    """Räkna signaler per tröskel på varje månads verifikationer, utan att lämna ut rader."""
    if not shares or any(not share.is_finite() or share <= 0 or share > 1 for share in shares):
        raise ValueError("Andelar måste vara tal större än 0 och högst 1")
    if not minimum.is_finite() or minimum < 0:
        raise ValueError("Minsta belopp måste vara ett icke-negativt tal")

    document = parse_sie(raw)
    if document.sie_type != 4 or any(issue.severity is Severity.ERROR for issue in document.issues):
        raise ValueError("Kalibrering kräver en giltig SIE4-fil med verifikationer")
    index = LedgerIndex.build(ledger_from_documents([(document, None)]))

    # Samma bas som transaction_bridge: absoluta radbelopp per konto och månad,
    # men varje signal bedöms på verifikationens netto på kontot.
    monthly_abs: dict[tuple[date, int], Decimal] = defaultdict(lambda: ZERO)
    bookings: list[tuple[date, int, Decimal]] = []
    for month, vouchers in index.vouchers_by_month.items():
        for voucher in vouchers:
            voucher_net: dict[int, Decimal] = defaultdict(lambda: ZERO)
            for row in voucher.effective_rows:
                if row.account in RESULT_ACCOUNTS:
                    monthly_abs[(month, row.account)] += abs(row.amount)
                    voucher_net[row.account] += row.amount
            bookings.extend((month, account, abs(amount)) for account, amount in voucher_net.items())

    total_abs = sum(monthly_abs.values(), ZERO)
    thresholds: dict[str, dict[str, int | str]] = {}
    for share in shares:
        matched = [
            amount
            for month, account, amount in bookings
            if amount >= minimum and amount >= share * monthly_abs[(month, account)]
        ]
        fraction = (sum(matched, ZERO) / total_abs if total_abs else ZERO).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        thresholds[str(share)] = {"signals": len(matched), "share_of_abs_amount": str(fraction)}
    return {"periods": len({month for month, _ in monthly_abs}), "thresholds": thresholds}
