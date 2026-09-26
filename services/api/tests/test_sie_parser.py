from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import RowStatus
from redovisningai.sie.convert import ledger_from_documents
from redovisningai.sie.parser import SieFormatError, decode_sie, parse_sie, tokenize
from redovisningai.sie.writer import write_sie4

FIXTURES = Path(__file__).parent / "fixtures"


def test_tokenize_quotes_escapes_and_objects() -> None:
    tokens = tokenize('#TRANS 3001 {1 "10" 6 "P 1"} -40000.00 20260105 "Text med \\"citat\\""')
    assert tokens == ["#TRANS", "3001", ["1", "10", "6", "P 1"], "-40000.00", "20260105", 'Text med "citat"']


def test_tokenize_empty_object_list_and_empty_strings() -> None:
    assert tokenize('#TRANS 1930 {} -1.00 "" ""') == ["#TRANS", "1930", [], "-1.00", "", ""]


@pytest.mark.parametrize("enc", ["cp437", "cp1252", "utf-8"])
def test_encoding_detection_keeps_swedish_letters(enc: str) -> None:
    text = '#FLAGGA 0\r\n#FORMAT PC8\r\n#FNAMN "Åkeriet Örebro Ängar AB"\r\n#KONTO 1930 "Företagskonto"\r\n'
    decoded, used = decode_sie(text.encode(enc))
    assert "Åkeriet Örebro Ängar AB" in decoded
    assert "Företagskonto" in decoded
    assert used.startswith(enc.split("-")[0]) or (enc == "utf-8" and used == "utf-8")


def test_golden_file_parses_exactly() -> None:
    doc = parse_sie((FIXTURES / "golden_small.se").read_bytes())
    assert doc.company_name == "Golden Test AB"
    assert doc.org_number == "556000-0001"
    assert doc.fiscal_years[0] == (date(2026, 1, 1), date(2026, 12, 31))
    assert len(doc.vouchers) == 4
    v1 = doc.vouchers[0]
    assert v1.text == 'Kundfaktura "Kund 1"'
    assert v1.rows[1].objects == (("1", "10"),)
    assert v1.rows[1].source_line is not None
    assert doc.vouchers[1].reg_date == date(2026, 1, 11)
    # Rättad verifikation: BTRANS räknas inte, RTRANS+TRANS räknas en gång.
    v4 = doc.vouchers[3]
    statuses = [r.status for r in v4.rows]
    assert statuses == [RowStatus.REMOVED, RowStatus.ADDED, RowStatus.NORMAL]
    assert v4.balance == 0
    assert all(v.balance == 0 for v in doc.vouchers)
    assert doc.period_balances[(0, date(2026, 1, 1), 3001)] == Decimal("-40000.00")
    assert doc.budget[(0, date(2026, 1, 1), 3001)] == Decimal("-45000.00")
    assert doc.result[(-1, 3001)] == Decimal("-500000.00")
    assert doc.objects[("1", "10")] == "Stockholm"
    assert not [i for i in doc.issues if i.severity == "warning"]


def test_unbalanced_and_unknown_account_are_reported_not_fatal() -> None:
    raw = (
        '#SIETYP 4\n#RAR 0 20260101 20261231\n#KONTO 1930 "Bank"\n'
        '#VER A 1 20260105 "x"\n{\n#TRANS 1930 {} 100.00\n#TRANS 9999 {} -99.00\n}\n'
    )
    doc = parse_sie(raw.encode())
    codes = {i.code for i in doc.issues}
    assert {"UNBALANCED_VOUCHER", "UNKNOWN_ACCOUNT"} <= codes
    assert len(doc.vouchers) == 1


def test_invalid_record_is_reported_with_line() -> None:
    raw = "#SIETYP 4\n#RAR 0 20260101 20261231\n#IB 0 1930 inte_ett_belopp\n"
    doc = parse_sie(raw.encode())
    issue = next(i for i in doc.issues if i.code == "INVALID_RECORD")
    assert issue.line == 3


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_non_finite_amount_is_reported_and_not_imported(amount: str) -> None:
    raw = f"#SIETYP 4\n#RAR 0 20260101 20261231\n#IB 0 1930 {amount}\n"
    doc = parse_sie(raw)
    assert (0, 1930) not in doc.opening
    assert any(issue.code == "INVALID_RECORD" and issue.line == 3 for issue in doc.issues)


def test_missing_rar_is_inferred_from_vouchers() -> None:
    raw = '#SIETYP 4\n#VER A 1 20250105 "x"\n{\n#TRANS 1930 {} 1.00\n#TRANS 3001 {} -1.00\n}\n'
    doc = parse_sie(raw.encode())
    assert doc.fiscal_years[0] == (date(2025, 1, 1), date(2025, 12, 31))


def test_not_sie_raises() -> None:
    with pytest.raises(SieFormatError):
        parse_sie(b"hello world\nthis is not sie")


def test_voucher_without_braces_and_unclosed() -> None:
    raw = (
        "#SIETYP 4\n#RAR 0 20260101 20261231\n"
        '#VER A 1 20260105 "x"\n{\n#TRANS 1930 {} 1.00\n#TRANS 3001 {} -1.00\n'
        '#VER A 2 20260106 "y"\n{\n#TRANS 1930 {} 2.00\n#TRANS 3001 {} -2.00\n}\n'
    )
    doc = parse_sie(raw.encode())
    assert [v.number for v in doc.vouchers] == ["1", "2"]
    assert any(i.code == "UNCLOSED_VOUCHER" for i in doc.issues)


def test_generated_company_round_trip_preserves_every_voucher() -> None:
    g = generate(DEMO_PROFILES[0], date(2026, 9, 30))
    docs = []
    for y in g.ledger.years:
        raw = write_sie4(g.ledger, y)
        docs.append((parse_sie(raw), y.fiscal_year.label))
    ledger = ledger_from_documents(docs)
    assert [y.fiscal_year for y in ledger.years] == [y.fiscal_year for y in g.ledger.years]
    for original, parsed in zip(g.ledger.years, ledger.years, strict=True):
        assert parsed.has_vouchers
        assert len(parsed.vouchers) == len(original.vouchers)
        for a, b in zip(original.vouchers, parsed.vouchers, strict=True):
            assert a.content_hash() == b.content_hash(), (a.key, b.key)
        assert parsed.opening == original.opening


def test_fortnox_like_export_rtrans_pair_without_date_and_text() -> None:
    """#RTRANS följd av en kompletterande #TRANS utan datum/text räknas en gång."""
    doc = parse_sie((FIXTURES / "fortnox_like.se").read_bytes())
    assert doc.company_name == "Testbolaget Åäö AB"
    v = next(v for v in doc.vouchers if v.series == "A" and v.number == "99")
    assert v.balance == 0
    assert [(r.account, r.status.value) for r in v.rows] == [(6570, "removed"), (5010, "added"), (1930, "normal")]
    assert v.rows[1].text == "rätt konto"
    assert not [i for i in doc.issues if i.code == "UNBALANCED_VOUCHER"]
