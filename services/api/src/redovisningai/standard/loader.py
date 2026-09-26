"""Läs valfri källfil till standardmodellen.

SIE (typ 1–4), RedovisningAI:s standardformat (JSON), CSV och Excel (.xlsx) känns igen på
innehållet – inte bara på filändelsen – och översätts till samma `Ledger`. Resten av systemet
(nyckeltal, kontroller, jämförelser, rapporter) ser aldrig källformatet.
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import PurePath

from redovisningai.domain.ledger import FiscalYear, Ledger, YearData
from redovisningai.sie.convert import ledger_from_documents
from redovisningai.sie.parser import PARSER_VERSION, ParseIssue, SieDocument, SieFormatError, decode_sie, parse_sie
from redovisningai.standard.format import FORMAT_ID, StandardFormatError, is_standard, loads
from redovisningai.standard.tabular import TABULAR_PARSER_VERSION, TabularFormatError, parse_csv, parse_xlsx

STANDARD_PARSER_VERSION = "1.0.0"
SIE_LABELS = ("#FLAGGA", "#SIETYP", "#FORMAT", "#PROGRAM", "#FNAMN", "#RAR", "#KONTO", "#VER", "#ORGNR", "#GEN")


class SourceFormatError(ValueError):
    """Filen kan inte läsas som bokföring i något format som stöds."""


@dataclass(slots=True)
class LoadedSource:
    ledger: Ledger
    format: str  # SIE4, SIE3 …, RAI-JSON, CSV, XLSX
    parser_version: str
    issues: list[ParseIssue]
    encoding: str | None = None
    # "fiscal_year": filen innehåller hela räkenskapsår och ersätter tidigare data för dem (SIE,
    # standardformat). "date_range": filen gäller bara sina datum (del av år i en CSV-export).
    replaces: str = "fiscal_year"
    ranges: dict[date, tuple[date, date]] = field(default_factory=dict)
    sie: SieDocument | None = None
    columns: str | None = None

    @property
    def voucher_count(self) -> int:
        return sum(len(y.vouchers) for y in self.ledger.years)


def detect_format(raw: bytes, filename: str = "") -> str:
    """'sie' | 'standard' | 'csv' | 'xlsx'. Kastar SourceFormatError för format som inte stöds."""
    suffix = PurePath(filename).suffix.lower()
    if raw.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(BytesIO(raw)).namelist()
        except zipfile.BadZipFile as exc:
            raise SourceFormatError("Filen ser ut att vara en skadad zip- eller Excelfil.") from exc
        if "xl/workbook.xml" in names:
            return "xlsx"
        raise SourceFormatError("Zip-filer laddas upp via massuppladdningen (flera SIE-filer i en zip).")
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise SourceFormatError("Äldre Excelformat (.xls) stöds inte – spara filen som .xlsx eller CSV.")
    if raw.startswith(b"%PDF"):
        raise SourceFormatError("PDF-rapporter kan inte läsas – exportera SIE-fil, Excel eller CSV i stället.")
    head = raw[:8192]
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise SourceFormatError("Filen ser ut att vara binär och är varken SIE, CSV, Excel eller standardformat.")
    utf16 = head.startswith((b"\xff\xfe", b"\xfe\xff"))
    text = head.decode("utf-16", "replace") if utf16 else decode_sie(head)[0]
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("{"):
        if is_standard(stripped) or suffix == ".json":
            return "standard"
        raise SourceFormatError(f"JSON-filen är inte i RedovisningAI:s standardformat ({FORMAT_ID}).")
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()][:60]
    if any(ln.upper().startswith(SIE_LABELS) for ln in lines):
        return "sie"
    if suffix in (".se", ".si", ".sie"):
        return "sie"
    return "csv"


def load_source(
    raw: bytes,
    filename: str = "",
    *,
    fiscal_years: Sequence[FiscalYear] = (),
    fiscal_year_start_month: int | None = None,
) -> LoadedSource:
    """Tolka en fil till standardmodellen. `fiscal_years` = bolagets kända räkenskapsår (för CSV/Excel)."""
    kind = detect_format(raw, filename)
    stem = PurePath(filename).stem or "Okänt bolag"
    if kind == "sie":
        try:
            doc = parse_sie(raw)
        except SieFormatError as exc:
            raise SourceFormatError(str(exc)) from exc
        ledger = ledger_from_documents([(doc, filename or None)])
        return LoadedSource(
            ledger,
            f"SIE{doc.sie_type or ''}",
            PARSER_VERSION,
            list(doc.issues),
            encoding=doc.encoding,
            sie=doc,
        )
    if kind == "standard":
        try:
            ledger, issues = loads(raw)
        except StandardFormatError as exc:
            raise SourceFormatError(str(exc)) from exc
        return LoadedSource(ledger, "RAI-JSON", STANDARD_PARSER_VERSION, issues, encoding="utf-8")
    try:
        result = (
            parse_xlsx(raw, fiscal_years=fiscal_years, fiscal_year_start_month=fiscal_year_start_month)
            if kind == "xlsx"
            else parse_csv(raw, fiscal_years=fiscal_years, fiscal_year_start_month=fiscal_year_start_month)
        )
    except TabularFormatError as exc:
        raise SourceFormatError(str(exc)) from exc
    if result.ledger.company_name == "Okänt bolag" and stem:
        result.ledger.company_name = stem
    return LoadedSource(
        result.ledger,
        result.format,
        TABULAR_PARSER_VERSION,
        result.issues,
        encoding=result.encoding,
        replaces="date_range",
        ranges=result.ranges,
        columns=result.columns.describe(),
    )


def load_ledger(
    files: Sequence[tuple[bytes, str]], *, fiscal_year_start_month: int | None = None
) -> tuple[Ledger, list[tuple[str, ParseIssue]]]:
    """Slå ihop flera filer för samma bolag (t.ex. SIE-filer för tre år, eller SIE + CSV).

    Filer med hela räkenskapsår läses först; tabellfiler får då deras räkenskapsår som ram.
    Ett år med verifikationer ersätter ett sammandrag, aldrig tvärtom.
    """
    loaded: list[tuple[str, LoadedSource]] = []
    kinds = [(raw, name, detect_format(raw, name)) for raw, name in files]
    ordered = sorted(kinds, key=lambda item: item[2] in ("csv", "xlsx"))
    known: list[FiscalYear] = []
    for raw, name, _kind in ordered:
        src = load_source(raw, name, fiscal_years=known, fiscal_year_start_month=fiscal_year_start_month)
        loaded.append((name, src))
        known = sorted({*known, *(y.fiscal_year for y in src.ledger.years)}, key=lambda fy: fy.start)
    if not loaded:
        raise SourceFormatError("Inga filer att läsa.")
    # Filen med senast räkenskapsår ger bolagsnamn och organisationsnummer.
    named = [s for _, s in loaded if s.format.startswith("SIE") or s.format == "RAI-JSON"] or [s for _, s in loaded]
    newest = max(named, key=lambda s: s.ledger.years[-1].fiscal_year.start if s.ledger.years else date.min)
    ledger = Ledger(
        company_name=newest.ledger.company_name,
        org_number=newest.ledger.org_number,
        currency=newest.ledger.currency,
        program=newest.ledger.program,
    )
    issues: list[tuple[str, ParseIssue]] = []
    for name, src in loaded:
        for year in src.ledger.years:
            existing = next((y for y in ledger.years if y.fiscal_year.start == year.fiscal_year.start), None)
            if existing is not None and existing.has_vouchers and year.has_vouchers and src.replaces == "date_range":
                _merge_range(existing, year, src.ranges.get(year.fiscal_year.start))
                for acc in src.ledger.accounts.values():
                    ledger.accounts.setdefault(acc.number, acc)
                continue
            ledger.merge_year(year, src.ledger.accounts.values())
        ledger.dimensions.update(src.ledger.dimensions)
        ledger.objects.update(src.ledger.objects)
        issues.extend((name, issue) for issue in src.issues)
    ledger.accounts.update({a: acc for a, acc in newest.ledger.accounts.items() if not acc.name.startswith("Konto ")})
    ledger.sort()
    return ledger, issues


def _merge_range(existing: YearData, incoming: YearData, rng: tuple[date, date] | None) -> None:
    """Lägg in en dels-årsexport i ett år: den ersätter verifikationer inom sitt datumintervall."""
    lo, hi = rng or (incoming.fiscal_year.start, incoming.fiscal_year.end)
    kept = [v for v in existing.vouchers if not (lo <= v.date <= hi)]
    existing.vouchers = sorted([*kept, *incoming.vouchers], key=lambda v: (v.date, v.series, v.number))
    if existing.covered_months is not None:
        existing.covered_months = existing.covered_months | (incoming.covered_months or frozenset())
    if not existing.opening and incoming.opening:
        existing.opening = dict(incoming.opening)
        existing.opening_status = "known"
