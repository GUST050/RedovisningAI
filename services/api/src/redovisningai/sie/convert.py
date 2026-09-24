"""Översätt SieDocument till den normaliserade Ledger-modellen."""

from __future__ import annotations

from collections.abc import Iterable

from redovisningai.domain.ledger import FiscalYear, Ledger, YearData
from redovisningai.sie.parser import SieDocument


def document_years(doc: SieDocument, source_ref: str | None = None) -> list[YearData]:
    """Ett YearData per #RAR i filen. Årsnr 0 har verifikationer, äldre år är sammandrag."""
    years: list[YearData] = []
    for year_no, (start, end) in sorted(doc.fiscal_years.items()):
        yd = YearData(
            fiscal_year=FiscalYear(start, end),
            source_ref=source_ref,
            has_vouchers=(year_no == 0 and doc.sie_type is not None and doc.sie_type >= 4),
        )
        if year_no == 0:
            yd.vouchers = list(doc.vouchers)
        yd.opening = {acc: amt for (y, acc), amt in doc.opening.items() if y == year_no}
        yd.closing = {acc: amt for (y, acc), amt in doc.closing.items() if y == year_no}
        yd.result = {acc: amt for (y, acc), amt in doc.result.items() if y == year_no}
        yd.period_balances = {(p, acc): amt for (y, p, acc), amt in doc.period_balances.items() if y == year_no}
        yd.budget = {(p, acc): amt for (y, p, acc), amt in doc.budget.items() if y == year_no}
        years.append(yd)
    return years


def ledger_from_documents(docs: Iterable[tuple[SieDocument, str | None]]) -> Ledger:
    """Bygg en Ledger av en eller flera SIE-filer för samma bolag.

    Filer med verifikationer har företräde framför sammandrag (#RAR -1 i en nyare fil).
    """
    docs = list(docs)
    if not docs:
        raise ValueError("Inga dokument")
    # Nyaste filen ger bolagsnamn och kontonamn.
    docs.sort(key=lambda d: d[0].fiscal_years.get(0, (None, None))[0] or 0)  # type: ignore[arg-type,return-value]
    newest = docs[-1][0]
    ledger = Ledger(
        company_name=newest.company_name or "Okänt bolag",
        org_number=newest.org_number,
        currency=newest.currency,
        program=newest.program,
    )
    for doc, ref in docs:
        for yd in document_years(doc, ref):
            ledger.merge_year(yd, doc.accounts.values())
        ledger.dimensions.update(doc.dimensions)
        ledger.objects.update(doc.objects)
    # Kontonamn från nyaste filen vinner.
    ledger.accounts.update(newest.accounts)
    ledger.sort()
    return ledger
