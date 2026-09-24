"""Strömmande parser för SIE typ 1–4 (fokus SIE 4).

Pipeline: råa bytes → teckenkodning → tokenizer (per rad) → poster → SieDocument.

Principer:
- Parsern kastar bara undantag för filer som inte alls är SIE. Allt annat blir `ParseIssue`
  (varning/info) så att konsulten ser exakt vad som var konstigt, med radnummer.
- Varje verifikation och rad behåller sitt radnummer i källfilen.
- Belopp lagras som Decimal, aldrig float.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from redovisningai.domain.ledger import Account, AccountType, Row, RowStatus, Voucher

PARSER_VERSION = "1.0.0"

Token = str | list[str]

_SWEDISH = set("åäöÅÄÖéÉüÜ")
# Tecken som typiskt uppstår när fel teckentabell används för svenska bokstäver.
_SUSPICIOUS = set("σΣ÷ÕΘ†„”Ž™‰Š›œ") | {chr(c) for c in range(0x2500, 0x2580)}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class ParseIssue:
    line: int | None
    code: str
    message: str
    severity: Severity = Severity.WARNING


class SieFormatError(ValueError):
    """Filen går inte att tolka som SIE över huvud taget."""


@dataclass(slots=True)
class SieDocument:
    encoding: str = "cp437"
    flag: int | None = None
    program: str | None = None
    format: str | None = None
    generated: date | None = None
    sie_type: int | None = None
    prosa: str | None = None
    company_id: str | None = None  # #FNR
    org_number: str | None = None
    company_name: str | None = None
    company_type: str | None = None  # #FTYP (AB, E, HB …)
    address: list[str] = field(default_factory=list)
    fiscal_years: dict[int, tuple[date, date]] = field(default_factory=dict)  # årsnr → (start, slut)
    tax_year: int | None = None
    period_end: date | None = None  # #OMFATTN
    chart_type: str | None = None  # #KPTYP
    currency: str = "SEK"
    accounts: dict[int, Account] = field(default_factory=dict)
    dimensions: dict[str, str] = field(default_factory=dict)
    objects: dict[tuple[str, str], str] = field(default_factory=dict)
    opening: dict[tuple[int, int], Decimal] = field(default_factory=dict)  # (årsnr, konto)
    closing: dict[tuple[int, int], Decimal] = field(default_factory=dict)
    result: dict[tuple[int, int], Decimal] = field(default_factory=dict)
    period_balances: dict[tuple[int, date, int], Decimal] = field(default_factory=dict)
    budget: dict[tuple[int, date, int], Decimal] = field(default_factory=dict)
    vouchers: list[Voucher] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    has_checksum: bool = False
    line_count: int = 0

    def warn(self, line: int | None, code: str, msg: str, sev: Severity = Severity.WARNING) -> None:
        self.issues.append(ParseIssue(line, code, msg, sev))


# --------------------------------------------------------------------------- teckenkodning


def _score(text: str) -> int:
    return sum(1 for c in text if c in _SWEDISH) - 3 * sum(1 for c in text if c in _SUSPICIOUS)


def decode_sie(raw: bytes) -> tuple[str, str]:
    """Avkoda en SIE-fil. Returnerar (text, använd teckenkodning).

    Standarden föreskriver IBM PC 8-bitars (CP437, "#FORMAT PC8"), men i praktiken förekommer
    även Windows-1252 och UTF-8. Vi väljer den avkodning som ger mest rimlig svensk text.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    try:
        text = raw.decode("utf-8")
        if any(ord(c) > 127 for c in text):
            return text, "utf-8"
        return text, "ascii"
    except UnicodeDecodeError:
        pass
    candidates: list[tuple[int, str, str]] = []
    declared_pc8 = b"#FORMAT PC8" in raw[:4000].upper()
    for enc in ("cp437", "cp1252"):
        text = raw.decode(enc, errors="replace")
        score = _score(text) + (1 if declared_pc8 and enc == "cp437" else 0)
        candidates.append((score, enc, text))
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, enc, text = candidates[0]
    return text, enc


# --------------------------------------------------------------------------- tokenizer


def tokenize(line: str) -> list[Token]:
    """Dela en SIE-rad i fält.

    - Fält separeras av blanksteg/tab.
    - Citattecken omger fält med blanksteg; \\" är ett escapat citattecken.
    - {…} är en objektlista och returneras som en lista av strängar.
    """
    tokens: list[Token] = []
    i, n = 0, len(line)
    current_list: list[str] | None = None
    while i < n:
        c = line[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "{":
            current_list = []
            i += 1
            continue
        if c == "}":
            tokens.append(current_list if current_list is not None else [])
            current_list = None
            i += 1
            continue
        if c == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                ch = line[i]
                if ch == "\\" and i + 1 < n and line[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                buf.append(ch)
                i += 1
            value = "".join(buf)
        else:
            start = i
            while i < n and line[i] not in ' \t\r\n{}"':
                i += 1
            value = line[start:i]
        if current_list is not None:
            current_list.append(value)
        else:
            tokens.append(value)
    if current_list is not None:  # obalanserad klammer – returnera ändå
        tokens.append(current_list)
    return tokens


# --------------------------------------------------------------------------- hjälpfunktioner


def _field(tokens: list[Token], idx: int) -> str | None:
    if idx < len(tokens):
        t = tokens[idx]
        if isinstance(t, str):
            return t
    return None


def _objects(tokens: list[Token], idx: int) -> tuple[list[str], int]:
    """Returnera (objektlista, index efter listan). Listan kan saknas i äldre filer."""
    if idx < len(tokens) and isinstance(tokens[idx], list):
        return list(tokens[idx]), idx + 1
    return [], idx


def _pairs(objs: list[str]) -> tuple[tuple[str, str], ...]:
    return tuple((objs[i], objs[i + 1]) for i in range(0, len(objs) - 1, 2))


_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        raise ValueError(f"Ogiltigt datum: {value!r}")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def parse_period(value: str) -> date:
    v = value.strip()
    if len(v) != 6 or not v.isdigit():
        raise ValueError(f"Ogiltig period: {value!r}")
    return date(int(v[:4]), int(v[4:]), 1)


def parse_amount(value: str | None) -> Decimal:
    if value is None or value == "":
        raise ValueError("Belopp saknas")
    v = value.strip()
    if "," in v and "." not in v:
        v = v.replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation as exc:
        raise ValueError(f"Ogiltigt belopp: {value!r}") from exc


def _account_no(value: str | None) -> int:
    if value is None or not value.strip().isdigit():
        raise ValueError(f"Ogiltigt kontonummer: {value!r}")
    return int(value)


_KTYP = {"T": AccountType.ASSET, "S": AccountType.LIABILITY, "K": AccountType.COST, "I": AccountType.INCOME}


# --------------------------------------------------------------------------- parser


@dataclass(slots=True)
class _OpenVoucher:
    series: str
    number: str
    date: date
    text: str
    reg_date: date | None
    signature: str | None
    line: int
    rows: list[Row] = field(default_factory=list)
    pending_added: Row | None = None


def parse_sie(raw: bytes | str) -> SieDocument:
    if isinstance(raw, bytes):
        text, encoding = decode_sie(raw)
    else:
        text, encoding = raw, "str"
    doc = SieDocument(encoding=encoding)
    lines = text.splitlines()
    doc.line_count = len(lines)
    if not any(line.lstrip().startswith("#") for line in lines[:50]):
        raise SieFormatError("Filen innehåller inga SIE-poster (#…) – är det verkligen en SIE-fil?")

    open_ver: _OpenVoucher | None = None
    awaiting_brace = False
    unknown_labels: set[str] = set()

    def flush_pending(v: _OpenVoucher) -> None:
        if v.pending_added is not None:
            v.rows.append(v.pending_added)
            v.pending_added = None

    def close_voucher(v: _OpenVoucher) -> None:
        flush_pending(v)
        voucher = Voucher(
            series=v.series,
            number=v.number,
            date=v.date,
            text=v.text,
            rows=tuple(v.rows),
            reg_date=v.reg_date,
            signature=v.signature,
            source_line=v.line,
        )
        if voucher.balance != 0:
            doc.warn(
                v.line,
                "UNBALANCED_VOUCHER",
                f"Verifikation {voucher.key} balanserar inte (differens {voucher.balance}).",
            )
        doc.vouchers.append(voucher)

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line == "{":
            if not awaiting_brace:
                doc.warn(idx, "UNEXPECTED_BRACE", "Oväntad '{' utanför verifikation.")
            awaiting_brace = False
            continue
        if line == "}":
            if open_ver is None:
                doc.warn(idx, "UNEXPECTED_BRACE", "Oväntad '}' utanför verifikation.")
            else:
                close_voucher(open_ver)
                open_ver = None
            continue
        if not line.startswith("#"):
            doc.warn(idx, "NOT_A_RECORD", "Raden är ingen SIE-post och ignoreras.", Severity.INFO)
            continue
        tokens = tokenize(line)
        label = str(tokens[0]).upper()
        try:
            if open_ver is not None and label in ("#TRANS", "#RTRANS", "#BTRANS"):
                _parse_trans(doc, open_ver, label, tokens, idx)
                continue
            if open_ver is not None and not awaiting_brace:
                # Ny post utan att verifikationen stängts – stäng den ändå.
                doc.warn(idx, "UNCLOSED_VOUCHER", "Verifikation saknar avslutande '}'.")
                close_voucher(open_ver)
                open_ver = None
            if label == "#VER":
                if open_ver is not None:
                    close_voucher(open_ver)
                open_ver = _parse_ver(tokens, idx)
                awaiting_brace = True
                continue
            if label in ("#TRANS", "#RTRANS", "#BTRANS"):
                doc.warn(idx, "ORPHAN_TRANS", f"{label} utanför verifikation ignoreras.")
                continue
            _parse_header(doc, label, tokens, idx, unknown_labels)
        except ValueError as exc:
            doc.warn(idx, "INVALID_RECORD", f"{label}: {exc}")

    if open_ver is not None:
        doc.warn(open_ver.line, "UNCLOSED_VOUCHER", "Filen slutar mitt i en verifikation.")
        close_voucher(open_ver)

    _post_validate(doc)
    return doc


def _parse_ver(tokens: list[Token], idx: int) -> _OpenVoucher:
    series = _field(tokens, 1) or ""
    number = _field(tokens, 2) or ""
    ver_date = parse_date(_field(tokens, 3))
    if ver_date is None:
        raise ValueError("verifikationsdatum saknas")
    text = _field(tokens, 4) or ""
    reg = _field(tokens, 5)
    return _OpenVoucher(
        series=series,
        number=number,
        date=ver_date,
        text=text,
        reg_date=parse_date(reg) if reg else None,
        signature=_field(tokens, 6),
        line=idx,
    )


def _parse_trans(doc: SieDocument, v: _OpenVoucher, label: str, tokens: list[Token], idx: int) -> None:
    account = _account_no(_field(tokens, 1))
    objs, pos = _objects(tokens, 2)
    amount = parse_amount(_field(tokens, pos))
    tdate_raw = _field(tokens, pos + 1)
    text = _field(tokens, pos + 2)
    qty_raw = _field(tokens, pos + 3)
    quantity = parse_amount(qty_raw) if qty_raw else None
    status = {"#TRANS": RowStatus.NORMAL, "#RTRANS": RowStatus.ADDED, "#BTRANS": RowStatus.REMOVED}[label]
    row = Row(
        account=account,
        amount=amount,
        trans_date=parse_date(tdate_raw) if tdate_raw else None,
        text=text or None,
        quantity=quantity,
        objects=_pairs(objs),
        status=status,
        source_line=idx,
    )
    if account not in doc.accounts:
        doc.accounts[account] = Account(number=account, name=f"Konto {account}")
        doc.warn(idx, "UNKNOWN_ACCOUNT", f"Konto {account} saknas i kontoplanen (#KONTO).")

    # Enligt SIE 4B ska #RTRANS följas av en identisk #TRANS (för program som inte förstår
    # #RTRANS). Då räknas raden bara en gång och markeras som tillagd.
    if status is RowStatus.ADDED:
        if v.pending_added is not None:
            v.rows.append(v.pending_added)
        v.pending_added = row
        return
    if v.pending_added is not None:
        p = v.pending_added
        v.pending_added = None
        if status is RowStatus.NORMAL and (p.account, p.amount, p.objects, p.trans_date, p.text) == (
            row.account,
            row.amount,
            row.objects,
            row.trans_date,
            row.text,
        ):
            v.rows.append(Row(**{**_row_dict(row), "status": RowStatus.ADDED}))
            return
        v.rows.append(p)
    v.rows.append(row)


def _row_dict(r: Row) -> dict[str, object]:
    return {
        "account": r.account,
        "amount": r.amount,
        "trans_date": r.trans_date,
        "text": r.text,
        "quantity": r.quantity,
        "objects": r.objects,
        "status": r.status,
        "source_line": r.source_line,
    }


def _parse_header(doc: SieDocument, label: str, tokens: list[Token], idx: int, unknown: set[str]) -> None:
    f = lambda i: _field(tokens, i)  # noqa: E731
    match label:
        case "#FLAGGA":
            doc.flag = int(f(1) or 0)
        case "#PROGRAM":
            doc.program = " ".join(x for x in (f(1), f(2)) if x)
        case "#FORMAT":
            doc.format = (f(1) or "").upper()
        case "#GEN":
            doc.generated = parse_date(f(1))
        case "#SIETYP":
            doc.sie_type = int(f(1) or 0)
        case "#PROSA":
            doc.prosa = f(1)
        case "#FNR":
            doc.company_id = f(1)
        case "#ORGNR":
            doc.org_number = (f(1) or "").strip() or None
        case "#FNAMN":
            doc.company_name = f(1)
        case "#FTYP":
            doc.company_type = f(1)
        case "#ADRESS":
            doc.address = [x for x in (f(1), f(2), f(3), f(4)) if x]
        case "#RAR":
            year_no = int(f(1) or "0")
            start, end = parse_date(f(2)), parse_date(f(3))
            if start is None or end is None:
                raise ValueError("start/slut saknas")
            doc.fiscal_years[year_no] = (start, end)
        case "#TAXAR":
            doc.tax_year = int(f(1) or 0)
        case "#OMFATTN":
            doc.period_end = parse_date(f(1))
        case "#KPTYP":
            doc.chart_type = f(1)
        case "#VALUTA":
            doc.currency = f(1) or "SEK"
        case "#KONTO":
            no = _account_no(f(1))
            old = doc.accounts.get(no)
            doc.accounts[no] = Account(
                number=no,
                name=f(2) or f"Konto {no}",
                type=old.type if old else None,
                sru=old.sru if old else None,
            )
        case "#KTYP":
            no = _account_no(f(1))
            ktyp = _KTYP.get((f(2) or "").upper())
            old = doc.accounts.get(no) or Account(number=no, name=f"Konto {no}")
            doc.accounts[no] = Account(number=no, name=old.name, type=ktyp, sru=old.sru)
        case "#SRU":
            no = _account_no(f(1))
            old = doc.accounts.get(no) or Account(number=no, name=f"Konto {no}")
            doc.accounts[no] = Account(number=no, name=old.name, type=old.type, sru=f(2))
        case "#ENHET":
            pass
        case "#DIM" | "#UNDERDIM":
            dim = f(1)
            if dim:
                doc.dimensions[dim] = f(2) or dim
        case "#OBJEKT":
            dim, obj = f(1), f(2)
            if dim is not None and obj is not None:
                doc.objects[(dim, obj)] = f(3) or obj
        case "#IB" | "#UB":
            year_no = int(f(1) or "0")
            acc = _account_no(f(2))
            amount = parse_amount(f(3))
            target = doc.opening if label == "#IB" else doc.closing
            target[(year_no, acc)] = amount
        case "#OIB" | "#OUB":
            pass  # saldon per objekt – används inte i MVP
        case "#RES":
            year_no = int(f(1) or "0")
            doc.result[(year_no, _account_no(f(2)))] = parse_amount(f(3))
        case "#PSALDO" | "#PBUDGET":
            year_no = int(f(1) or "0")
            period = parse_period(f(2) or "")
            acc = _account_no(f(3))
            objs, pos = _objects(tokens, 4)
            if objs:
                return  # vi använder bara totaler (utan objekt)
            amount = parse_amount(_field(tokens, pos))
            target = doc.period_balances if label == "#PSALDO" else doc.budget
            target[(year_no, period, acc)] = amount
        case "#KSUMMA":
            doc.has_checksum = True
        case "#BKOD" | "#TAXAR_" | "#KONTAKT":
            pass
        case _:
            if label not in unknown:
                unknown.add(label)
                doc.warn(idx, "UNKNOWN_LABEL", f"Okänd post {label} ignoreras.", Severity.INFO)


def _post_validate(doc: SieDocument) -> None:
    if doc.sie_type is None:
        doc.warn(None, "MISSING_SIETYP", "#SIETYP saknas – antar typ 4.", Severity.INFO)
        doc.sie_type = 4
    if doc.sie_type < 4 and doc.vouchers:
        doc.warn(None, "TYPE_MISMATCH", f"SIE typ {doc.sie_type} innehåller verifikationer.", Severity.INFO)
    if doc.sie_type < 4 and not doc.vouchers:
        doc.warn(
            None,
            "NO_VOUCHERS",
            f"SIE typ {doc.sie_type} saknar verifikationer – transaktionsanalys är inte möjlig.",
        )
    if 0 not in doc.fiscal_years:
        if doc.vouchers:
            dates = [v.date for v in doc.vouchers]
            start = date(min(dates).year, 1, 1)
            end = date(max(dates).year, 12, 31)
            doc.fiscal_years[0] = (start, end)
            doc.warn(None, "MISSING_RAR", "#RAR 0 saknas – räkenskapsår antaget från verifikationsdatum.")
        else:
            raise SieFormatError("Filen saknar både räkenskapsår (#RAR) och verifikationer.")
    start, end = doc.fiscal_years[0]
    for v in doc.vouchers:
        if not (start <= v.date <= end):
            doc.warn(
                v.source_line,
                "VOUCHER_OUTSIDE_YEAR",
                f"Verifikation {v.key} är daterad {v.date} utanför räkenskapsåret {start}–{end}.",
            )
    if doc.has_checksum:
        doc.warn(None, "CHECKSUM_NOT_VERIFIED", "#KSUMMA finns men kontrolleras inte.", Severity.INFO)
    if doc.format and doc.format != "PC8":
        doc.warn(None, "FORMAT", f"#FORMAT {doc.format} (standard är PC8).", Severity.INFO)
