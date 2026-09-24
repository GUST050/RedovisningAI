"""Avstämning av konto 1630 mot kundens skattekonto hos Skatteverket.

Transaktionerna från skattekontot kommer antingen från Skatteverkets Skattekonto-API
(kräver organisationscertifikat och ombudsbehörighet – se connectors/skatteverket.py) eller
från en fil som konsulten laddar ner från Skatteverket och laddar upp (CSV).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.domain.ledger import ZERO

TAX_ACCOUNT = AccountSet.of((1630, 1639))


@dataclass(frozen=True, slots=True)
class TaxAccountTransaction:
    date: date
    text: str
    amount: Decimal  # positivt = insättning/kredit på skattekontot (minskar skulden)


def parse_tax_account_csv(content: str) -> list[TaxAccountTransaction]:
    """Tolka en CSV/TSV-export. Kolumner hittas på rubrik: datum, text/specifikation, belopp."""
    sample = content[:2000]
    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t") if sample else csv.excel
    reader = csv.reader(io.StringIO(content), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]

    def col(*names: str) -> int:
        for i, h in enumerate(header):
            if any(n in h for n in names):
                return i
        raise ValueError(f"Hittar ingen kolumn för {names} i rubriken {header}")

    di, ti, ai = col("datum", "date"), col("text", "specifikation", "transaktion"), col("belopp", "amount")
    out = []
    for r in rows[1:]:
        try:
            d = date.fromisoformat(r[di].strip()[:10])
            raw = re.sub(r"[\s ]", "", r[ai]).replace("−", "-").replace(",", ".")
            amt = Decimal(raw)
        except (ValueError, InvalidOperation, IndexError):
            continue
        out.append(TaxAccountTransaction(d, r[ti].strip(), amt))
    return out


@dataclass(slots=True)
class TaxReconciliation:
    as_of: date
    skv_balance: Decimal
    book_balance: Decimal
    unmatched_skv: list[TaxAccountTransaction] = field(default_factory=list)
    unmatched_book: list[dict[str, Any]] = field(default_factory=list)

    @property
    def difference(self) -> Decimal:
        return self.book_balance - self.skv_balance

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "skv_balance": str(self.skv_balance),
            "book_balance": str(self.book_balance),
            "difference": str(self.difference),
            "unmatched_skv": [
                {"date": t.date.isoformat(), "text": t.text, "amount": str(t.amount)} for t in self.unmatched_skv
            ],
            "unmatched_book": [{**r, "date": str(r["date"]), "amount": str(r["amount"])} for r in self.unmatched_book],
        }


def reconcile_tax_account(
    index: LedgerIndex,
    transactions: list[TaxAccountTransaction],
    as_of: date,
    *,
    opening_skv_balance: Decimal = ZERO,
    date_tolerance: int = 3,
) -> TaxReconciliation:
    """Jämför bokfört saldo på 1630 med skattekontots saldo och matcha transaktioner."""
    skv_tx = [t for t in transactions if t.date <= as_of]
    skv_balance = opening_skv_balance + sum((t.amount for t in skv_tx), ZERO)
    book_balance = index.balance_at(TAX_ACCOUNT, as_of)
    book_rows = []
    year = index.ledger.year_for(as_of)
    if year is not None:
        for v in year.vouchers:
            if v.date <= as_of:
                for r in v.effective_rows:
                    if r.account in TAX_ACCOUNT:
                        book_rows.append({"voucher": str(v.key), "date": v.date, "text": v.text, "amount": r.amount})
    unmatched_book = list(book_rows)
    unmatched_skv = []
    for t in skv_tx:
        if year is not None and t.date < year.fiscal_year.start:
            continue
        match = next(
            (b for b in unmatched_book if b["amount"] == t.amount and abs((b["date"] - t.date).days) <= date_tolerance),
            None,
        )
        if match is None:
            unmatched_skv.append(t)
        else:
            unmatched_book.remove(match)
    return TaxReconciliation(
        as_of=as_of,
        skv_balance=skv_balance,
        book_balance=book_balance,
        unmatched_skv=unmatched_skv,
        unmatched_book=[{**b, "date": b["date"].isoformat(), "amount": str(b["amount"])} for b in unmatched_book],
    )
