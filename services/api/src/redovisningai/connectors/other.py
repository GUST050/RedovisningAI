"""Kopplingar som kräver avtal/dokumentation innan de kan färdigställas.

Gränssnitten finns så att resten av systemet kan byggas mot dem. Implementationen görs när
partneravtal och API-dokumentation finns (se docs/PLAN.md §4). Tills dess används SIE-fil
(Spiris, Björn Lundén) respektive filuppladdning av skattekontoutdrag (Skatteverket).
"""

from __future__ import annotations

from datetime import date

from redovisningai.connectors.base import ConnectorError, RemoteFiscalYear, SieExport


class SpirisConnector:
    """Spiris (f.d. Visma eEkonomi) – V1.5. Kräver API-tillägg hos kunden och partneravtal."""

    source = "spiris"

    def __init__(self, *_: object, **__: object) -> None:
        raise ConnectorError(
            "Spiris-kopplingen är inte aktiverad ännu. Exportera SIE4 från Spiris och ladda upp filen."
        )

    def fiscal_years(self) -> list[RemoteFiscalYear]:  # pragma: no cover
        raise NotImplementedError

    def export_sie4(self, year: RemoteFiscalYear) -> SieExport:  # pragma: no cover
        raise NotImplementedError


class SkatteverketTaxAccount:
    """Skatteverkets Skattekonto-API (v2.0) – V1.5.

    Kräver organisationscertifikat (ömsesidig TLS) och att byrån har läsbehörighet som ombud via
    tjänsten "Ombud och behörigheter". Tills dess: ladda upp skattekontoutdrag som CSV
    (analytics/tax_account.py tolkar filen och stämmer av mot konto 1630).
    """

    def __init__(self, *_: object, **__: object) -> None:
        raise ConnectorError(
            "Skattekonto-API:t kräver organisationscertifikat och ombudsbehörighet. Ladda upp utdrag som CSV."
        )

    def transactions(
        self, org_number: str, from_date: date, to_date: date
    ) -> list[dict[str, object]]:  # pragma: no cover
        raise NotImplementedError
