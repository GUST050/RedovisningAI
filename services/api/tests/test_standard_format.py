"""Standardformatet: alla källor till samma normaliserade bokföring, utan förlust."""

from __future__ import annotations

import io
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.metrics import REGISTRY, calculate_metric
from redovisningai.accounting.periods import month, parse_period
from redovisningai.accounting.statements import balance_sheet, income_statement
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import FiscalYear
from redovisningai.facts.model import FactStatus
from redovisningai.sie.writer import write_sie4
from redovisningai.standard import (
    FORMAT_ID,
    SourceFormatError,
    StandardFormatError,
    detect_format,
    dumps,
    load_ledger,
    load_source,
    loads,
    to_standard,
)
from redovisningai.standard.tabular import parse_amount, parse_day, split_voucher

FIXTURES = Path(__file__).parent / "fixtures"


def _voucher_year(data: dict[str, Any]) -> dict[str, Any]:
    return next(y for y in data["fiscal_years"] if y["vouchers"])


# ---------------------------------------------------------------------------- standardformatet


@pytest.mark.parametrize("name", ["golden_small.se", "fortnox_like.se"])
def test_sie_round_trips_through_standard_format(name: str) -> None:
    src = load_source((FIXTURES / name).read_bytes(), name)
    text = dumps(src.ledger)
    back, issues = loads(text)

    assert issues == []
    assert [v.content_hash() for y in back.years for v in y.vouchers] == [
        v.content_hash() for y in src.ledger.years for v in y.vouchers
    ]
    assert dumps(back) == text  # stabil serialisering
    data = json.loads(text)
    assert data["format"] == FORMAT_ID
    assert data["summary"]["vouchers"] == src.voucher_count


def test_standard_format_preserves_statements_for_generated_company() -> None:
    ledger = generate(DEMO_PROFILES[0], date(2026, 9, 30)).ledger
    back, _ = loads(dumps(ledger))
    a, b = LedgerIndex.build(ledger), LedgerIndex.build(back)
    for spec in ("2026-09", "YTD:2026-09", "R12:2026-09", "2025-02"):
        period = parse_period(spec, ledger)
        assert income_statement(a, period).to_dict() == income_statement(b, period).to_dict()
        assert balance_sheet(a, period.end).to_dict() == balance_sheet(b, period.end).to_dict()
    for code in REGISTRY:
        assert calculate_metric(code, a, month(2026, 9)).value == calculate_metric(code, b, month(2026, 9)).value


def test_amounts_are_exact_decimal_strings() -> None:
    src = load_source((FIXTURES / "golden_small.se").read_bytes(), "golden_small.se")
    data = to_standard(src.ledger)
    amounts = [r["amount"] for y in data["fiscal_years"] for v in y["vouchers"] for r in v["rows"]]
    assert amounts and all(isinstance(a, str) for a in amounts)
    # Flyttal i indata avvisas i stället för att tyst avrundas.
    _voucher_year(data)["vouchers"][0]["rows"][0]["amount"] = 0.1
    from redovisningai.standard import from_standard

    with pytest.raises(StandardFormatError, match="flyttal"):
        from_standard(data)


def test_standard_format_reports_path_to_structural_errors() -> None:
    src = load_source((FIXTURES / "golden_small.se").read_bytes(), "golden_small.se")
    data = to_standard(src.ledger)
    index = next(i for i, y in enumerate(data["fiscal_years"]) if y["vouchers"])
    data["fiscal_years"][index]["vouchers"][1]["rows"][0]["account"] = "abc"
    with pytest.raises(StandardFormatError, match=rf"fiscal_years\[{index}\]\.vouchers\[1\]\.rows\[0\]\.account"):
        loads(json.dumps(data))
    with pytest.raises(StandardFormatError, match="standardformat"):
        loads(json.dumps({"format": "annat"}))


def test_tampered_voucher_is_flagged_by_content_hash() -> None:
    src = load_source((FIXTURES / "golden_small.se").read_bytes(), "golden_small.se")
    data = to_standard(src.ledger)
    rows = _voucher_year(data)["vouchers"][0]["rows"]
    rows[0]["amount"], rows[1]["amount"] = rows[1]["amount"], rows[0]["amount"]
    _, issues = loads(json.dumps(data))
    assert "CONTENT_HASH_MISMATCH" in {i.code for i in issues}


def test_detect_format_uses_content_not_extension() -> None:
    sie = (FIXTURES / "golden_small.se").read_bytes()
    assert detect_format(sie, "export.txt") == "sie"
    assert detect_format(dumps(load_source(sie, "x.se").ledger).encode(), "fil.txt") == "standard"
    assert detect_format(b"Vernr;Datum;Konto;Belopp\nA1;2026-01-01;1930;100\n", "lista.txt") == "csv"
    with pytest.raises(SourceFormatError, match="xls"):
        detect_format(b"\xd0\xcf\x11\xe0" + b"\0" * 100, "gammal.xls")
    with pytest.raises(SourceFormatError, match="PDF"):
        detect_format(b"%PDF-1.7", "rapport.pdf")


# ---------------------------------------------------------------------------- värdetolkning


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234,50", Decimal("1234.50")),
        ("1 234,50", Decimal("1234.50")),
        ("-500", Decimal("-500")),
        ("−500,00", Decimal("-500.00")),
        ("500-", Decimal("-500")),
        ("(1 000,00)", Decimal("-1000.00")),
        ("1.234,50", Decimal("1234.50")),
        ("1,234.50", Decimal("1234.50")),
        ("1234.5", Decimal("1234.5")),
        ("12 500 kr", Decimal("12500")),
        ("", None),
        ("–", None),
        (1234.5, Decimal("1234.50")),
        (0.1 + 0.2, Decimal("0.30")),
        (42, Decimal("42")),
    ],
)
def test_parse_amount_handles_swedish_formats(raw: object, expected: Decimal | None) -> None:
    assert parse_amount(raw) == expected


def test_parse_amount_rejects_text() -> None:
    with pytest.raises(ValueError):
        parse_amount("tolv")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-05", date(2026, 1, 5)),
        ("2026-01-05 00:00:00", date(2026, 1, 5)),
        ("20260105", date(2026, 1, 5)),
        ("05.01.2026", date(2026, 1, 5)),
        (datetime(2026, 1, 5, 0, 0), date(2026, 1, 5)),
        (46027, date(2026, 1, 5)),
        (20260105, date(2026, 1, 5)),
        ("", None),
    ],
)
def test_parse_day(raw: object, expected: date | None) -> None:
    assert parse_day(raw) == expected


def test_split_voucher() -> None:
    assert split_voucher("A 12") == ("A", "12")
    assert split_voucher("a-12") == ("A", "12")
    assert split_voucher("B12") == ("B", "12")
    assert split_voucher("305") == ("", "305")


# ---------------------------------------------------------------------------- CSV och Excel

FLAT_CSV = """Verifikationslista;;;;;;
Testbolaget AB, org.nr 556123-4567;;;;;;
Period 2026-01-01 - 2026-03-31;;;;;;
Vernr;Datum;Konto;Benämning;Text;Debet;Kredit
IB;2026-01-01;1930;Företagskonto;Ingående balans;52 000,00;
IB;2026-01-01;2081;Aktiekapital;Ingående balans;;25 000,00
IB;2026-01-01;2099;Årets resultat;Ingående balans;;27 000,00
A1;2026-01-05;1510;Kundfordringar;Kundfaktura 1;12 500,00;
A1;2026-01-05;3001;Försäljning 25%;Kundfaktura 1;;10 000,00
A1;2026-01-05;2611;Utgående moms 25%;Kundfaktura 1;;2 500,00
A2;2026-01-15;1930;Företagskonto;Inbetalning;12 500,00;
A2;2026-01-15;1510;Kundfordringar;Inbetalning;;12 500,00
A3;2026-02-03;5010;Lokalhyra;Hyra feb;8 000,00;
A3;2026-02-03;2641;Ingående moms;Hyra feb;2 000,00;
A3;2026-02-03;1930;Företagskonto;Hyra feb;;10 000,00
;;;;Summa;87 000,00;87 000,00
"""


def test_flat_csv_export_with_opening_balances() -> None:
    src = load_source(FLAT_CSV.encode("cp1252"), "verifikationslista.csv")

    assert src.format == "CSV"
    assert src.replaces == "date_range"
    assert src.ledger.company_name == "Testbolaget AB"
    assert src.ledger.org_number == "556123-4567"
    year = src.ledger.years[0]
    assert year.opening_status == "known"
    assert year.opening == {1930: Decimal("52000.00"), 2081: Decimal("-25000.00"), 2099: Decimal("-27000.00")}
    assert [str(v.key) for v in year.vouchers] == ["A1", "A2", "A3"]
    assert all(v.balance == 0 for v in year.vouchers)
    assert year.covered_months == frozenset({date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)})
    assert src.ranges[date(2026, 1, 1)] == (date(2026, 1, 1), date(2026, 3, 31))
    assert src.ledger.accounts[3001].name == "Försäljning 25%"
    codes = {i.code for i in src.issues}
    assert {"COLUMNS_DETECTED", "PARTIAL_YEAR", "SKIPPED_ROWS"} <= codes
    assert "MISSING_OPENING_BALANCES" not in codes

    index = LedgerIndex.build(src.ledger)
    assert calculate_metric("net_sales", index, month(2026, 1)).value == Decimal("10000.00")
    assert calculate_metric("cash", index, month(2026, 2)).value == Decimal("54500.00")
    # Månader utanför exporten är saknade, inte noll.
    assert calculate_metric("net_sales", index, month(2026, 4)).status is FactStatus.INSUFFICIENT_DATA
    assert calculate_metric("cash", index, month(2026, 4)).status is FactStatus.INSUFFICIENT_DATA


def test_grouped_voucher_list_fills_down_voucher_number() -> None:
    csv_text = (
        "Vernr,Datum,Text,Konto,Kontonamn,Debet,Kredit\n"
        "A 1,2026-01-05,Kundfaktura 1,,,,\n"
        ',,,1510,Kundfordringar,"12500,00",\n'
        ',,,3001,Försäljning,,"10000,00"\n'
        ',,,2611,Utg moms,,"2500,00"\n'
        "A 2,2026-01-15,Inbetalning,,,,\n"
        ',,,1930,Bank,"12500,00",\n'
        ',,,1510,Kundfordringar,,"12500,00"\n'
    )
    src = load_source(csv_text.encode(), "lista.csv")
    vouchers = src.ledger.years[0].vouchers
    assert [(str(v.key), v.date, v.text, len(v.rows)) for v in vouchers] == [
        ("A1", date(2026, 1, 5), "Kundfaktura 1", 3),
        ("A2", date(2026, 1, 15), "Inbetalning", 2),
    ]
    assert src.ledger.years[0].opening_status == "missing"
    assert "MISSING_OPENING_BALANCES" in {i.code for i in src.issues}
    index = LedgerIndex.build(src.ledger)
    # Utan IB är balansposter otillräckligt underlag, resultatposter går att räkna.
    assert calculate_metric("cash", index, month(2026, 1)).status is FactStatus.INSUFFICIENT_DATA
    assert calculate_metric("net_sales", index, month(2026, 1)).value == Decimal("10000.00")


def test_general_ledger_layout_rebuilds_vouchers_across_account_sections() -> None:
    text = (
        "Konto\tNamn\tVernr\tDatum\tText\tDebet\tKredit\tSaldo\n"
        "1510\tKundfordringar\t\t\t\t\t\t\n"
        "\t\t\t\tIngående balans\t\t\t0,00\n"
        "\t\tA1\t2026-01-05\tKundfaktura 1\t12 500,00\t\t12 500,00\n"
        "\t\tA2\t2026-01-15\tInbetalning\t\t12 500,00\t0,00\n"
        "\t\t\t\tUtgående balans\t\t\t0,00\n"
        "1930\tBank\t\t\t\t\t\t\n"
        "\t\t\t\tIngående balans\t\t\t52 000,00\n"
        "\t\tA2\t2026-01-15\tInbetalning\t12 500,00\t\t64 500,00\n"
        "\t\t\t\tSumma\t12 500,00\t0,00\t\n"
        "2611\tUtgående moms\t\t\t\t\t\t\n"
        "\t\tA1\t2026-01-05\tKundfaktura 1\t\t2 500,00\t-2 500,00\n"
        "3001\tFörsäljning\t\t\t\t\t\t\n"
        "\t\tA1\t2026-01-05\tKundfaktura 1\t\t10 000,00\t-10 000,00\n"
    )
    src = load_source(text.encode(), "huvudbok.txt")
    year = src.ledger.years[0]
    assert year.opening == {1930: Decimal("52000.00")}
    assert year.opening_status == "known"
    by_key = {str(v.key): v for v in year.vouchers}
    assert sorted(r.account for r in by_key["A1"].rows) == [1510, 2611, 3001]
    assert sorted(r.account for r in by_key["A2"].rows) == [1510, 1930]
    assert all(v.balance == 0 for v in year.vouchers)
    assert {"SKIPPED_UB", "SKIPPED_ROWS"} <= {i.code for i in src.issues}


def test_single_amount_column_and_separate_series() -> None:
    text = "Serie;Nummer;Bokföringsdatum;Kontonummer;Belopp;Transaktionsinfo\nB;7;2026-05-02;6540;1 000,00;Licens\nB;7;2026-05-02;1930;-1 000,00;\n"
    src = load_source(text.encode(), "export.csv", fiscal_years=[FiscalYear(date(2025, 7, 1), date(2026, 6, 30))])
    year = src.ledger.years[0]
    assert year.fiscal_year == FiscalYear(date(2025, 7, 1), date(2026, 6, 30))
    voucher = year.vouchers[0]
    assert str(voucher.key) == "B7"
    assert voucher.rows[0].text == "Licens"
    assert "ASSUMED_FISCAL_YEAR" not in {i.code for i in src.issues}


def test_unbalanced_rows_and_bad_values_are_reported_not_hidden() -> None:
    text = (
        "Vernr;Datum;Konto;Debet;Kredit\n"
        "A1;2026-01-05;1930;100,00;\n"
        "A1;2026-01-05;3001;;90,00\n"
        "A2;2026-13-40;1930;50,00;\n"
        "A3;2026-01-07;19x0;50,00;\n"
    )
    src = load_source(text.encode(), "fel.csv")
    codes = [i.code for i in src.issues]
    assert "UNBALANCED_VOUCHER" in codes
    assert codes.count("INVALID_ROW") == 2


def test_csv_without_recognisable_header_gives_helpful_error() -> None:
    with pytest.raises(SourceFormatError, match="rubrikrad"):
        load_source(b"a;b;c\n1;2;3\n", "okand.csv")


def test_xlsx_export_with_native_dates_and_numbers() -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Verifikationer"
    ws.append(["Rapport: Verifikationslista"])
    ws.append([])
    ws.append(["Verifikationsnummer", "Verifikationsserie", "Bokföringsdatum", "Konto", "Debet", "Kredit", "Text"])
    ws.append([1, "A", datetime(2026, 3, 1), 1930, 1250.5, None, "Kundbetalning"])
    ws.append([1, "A", datetime(2026, 3, 1), 1510, None, 1250.5, "Kundbetalning"])
    buf = io.BytesIO()
    wb.save(buf)

    src = load_source(buf.getvalue(), "export.xlsx")
    assert src.format == "XLSX"
    voucher = src.ledger.years[0].vouchers[0]
    assert str(voucher.key) == "A1"
    assert voucher.date == date(2026, 3, 1)
    assert [r.amount for r in voucher.rows] == [Decimal("1250.50"), Decimal("-1250.50")]


# ---------------------------------------------------------------------------- flera källor


def test_csv_for_new_year_uses_opening_derived_from_previous_sie_year() -> None:
    # Lilja Livs AB har inga planterade fel, så IB 2026 = UB 2025 + resultatet 2025.
    generated = generate(DEMO_PROFILES[1], date(2026, 9, 30)).ledger
    prev = next(y for y in generated.years if y.fiscal_year.start == date(2025, 1, 1))
    sie_2025 = write_sie4(generated, prev, include_previous_summary=False)
    current = next(y for y in generated.years if y.fiscal_year.start == date(2026, 1, 1))
    lines = ["Vernr;Datum;Konto;Belopp;Text"]
    for v in current.vouchers:
        for r in v.effective_rows:
            lines.append(f"{v.series}{v.number};{v.date.isoformat()};{r.account};{r.amount};{v.text.replace(';', ' ')}")
    csv_2026 = "\n".join(lines).encode()

    ledger, _issues = load_ledger([(csv_2026, "2026.csv"), (sie_2025, "2025.se")])
    reference = LedgerIndex.build(generated)
    combined = LedgerIndex.build(ledger)
    year_2026 = next(y for y in ledger.years if y.fiscal_year.start == date(2026, 1, 1))
    assert year_2026.opening_status == "missing"
    assert combined.opening_known(year_2026)
    for code in ("cash", "equity_ratio", "net_sales", "operating_result"):
        assert (
            calculate_metric(code, combined, month(2026, 9)).value
            == calculate_metric(code, reference, month(2026, 9)).value
        )
