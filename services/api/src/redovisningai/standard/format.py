"""RedovisningAI:s standardformat för bokföring (JSON).

Alla källor – SIE-fil, Fortnox, CSV/Excel-export – översätts till samma normaliserade modell
(`domain.ledger.Ledger`). Standardformatet är den modellen serialiserad som JSON:

- versionerat (`format` + `version`) så att filer från äldre versioner kan läsas,
- exakt: belopp är decimalsträngar, aldrig flyttal,
- spårbart: varje verifikation och rad bär sitt radnummer i originalfilen och verifikationens
  fingeravtryck, och källan (system, fil, SHA-256) följer med.

Formatet går att exportera för kontroll eller vidare bearbetning och att läsa in igen utan
förlust. Se docs/STANDARDFORMAT.md för fältbeskrivning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from redovisningai.domain.ledger import (
    Account,
    AccountType,
    FiscalYear,
    Ledger,
    Row,
    RowStatus,
    Voucher,
    YearData,
)
from redovisningai.sie.parser import ParseIssue, Severity

FORMAT_ID = "redovisningai.ledger"
FORMAT_VERSION = "1.0"
OPENING_STATUSES = ("known", "missing")


class StandardFormatError(ValueError):
    """Filen följer inte standardformatet (fel struktur eller ogiltiga värden)."""


# ---------------------------------------------------------------------------- export


def _amount(value: Decimal) -> str:
    return f"{value:.2f}" if value == value.quantize(Decimal("0.01")) else str(value)


def _balances(values: Mapping[int, Decimal]) -> list[dict[str, Any]]:
    return [{"account": a, "amount": _amount(v)} for a, v in sorted(values.items())]


def _period_amounts(values: Mapping[tuple[date, int], Decimal]) -> list[dict[str, Any]]:
    return [
        {"period": f"{p:%Y-%m}", "account": a, "amount": _amount(v)}
        for (p, a), v in sorted(values.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


def _row(row: Row) -> dict[str, Any]:
    return {
        "account": row.account,
        "amount": _amount(row.amount),
        "date": row.trans_date.isoformat() if row.trans_date else None,
        "text": row.text,
        "quantity": None if row.quantity is None else str(row.quantity),
        "objects": [[d, o] for d, o in row.objects],
        "status": row.status.value,
        "source_line": row.source_line,
    }


def _voucher(voucher: Voucher) -> dict[str, Any]:
    return {
        "series": voucher.series,
        "number": voucher.number,
        "date": voucher.date.isoformat(),
        "text": voucher.text,
        "registered": voucher.reg_date.isoformat() if voucher.reg_date else None,
        "signature": voucher.signature,
        "source_line": voucher.source_line,
        "content_hash": voucher.content_hash(),
        "rows": [_row(r) for r in voucher.rows],
    }


def to_standard(ledger: Ledger, *, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Serialisera en Ledger till standardformatet. Ordningen är stabil (samma data ger samma JSON)."""
    years = []
    for year in sorted(ledger.years, key=lambda y: y.fiscal_year.start):
        years.append(
            {
                "start": year.fiscal_year.start.isoformat(),
                "end": year.fiscal_year.end.isoformat(),
                "has_vouchers": year.has_vouchers,
                "opening_balances_status": year.opening_status,
                "covered_months": None
                if year.covered_months is None
                else [f"{m:%Y-%m}" for m in sorted(year.covered_months)],
                "source_ref": year.source_ref,
                "opening_balances": _balances(year.opening),
                "closing_balances": _balances(year.closing),
                "results": _balances(year.result),
                "period_balances": _period_amounts(year.period_balances),
                "budget": _period_amounts(year.budget),
                "vouchers": [_voucher(v) for v in year.vouchers],
            }
        )
    return {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "company": {"name": ledger.company_name, "org_number": ledger.org_number, "currency": ledger.currency},
        "source": dict(source or {"program": ledger.program}),
        "accounts": [
            {
                "number": a.number,
                "name": a.name,
                "type": a.type.value if a.type else None,
                "sru": a.sru,
            }
            for a in sorted(ledger.accounts.values(), key=lambda a: a.number)
        ],
        "dimensions": [{"id": d, "name": n} for d, n in sorted(ledger.dimensions.items())],
        "objects": [{"dimension": d, "id": o, "name": n} for (d, o), n in sorted(ledger.objects.items())],
        "fiscal_years": years,
        "summary": {
            "fiscal_years": len(ledger.years),
            "accounts": len(ledger.accounts),
            "vouchers": sum(len(y.vouchers) for y in ledger.years),
            "rows": sum(len(v.rows) for y in ledger.years for v in y.vouchers),
        },
    }


def dumps(ledger: Ledger, *, source: Mapping[str, Any] | None = None, indent: int | None = 2) -> str:
    return json.dumps(to_standard(ledger, source=source), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------- import


class _Reader:
    """Hjälpare som ger begripliga felmeddelanden med sökväg till felet."""

    def __init__(self) -> None:
        self.issues: list[ParseIssue] = []

    @staticmethod
    def fail(path: str, message: str) -> StandardFormatError:
        return StandardFormatError(f"{path}: {message}")

    def obj(self, value: Any, path: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise self.fail(path, "ska vara ett objekt")
        return value

    def items(self, parent: Mapping[str, Any], key: str, path: str) -> list[Any]:
        value = parent.get(key, [])
        if value is None:
            return []
        if not isinstance(value, list):
            raise self.fail(f"{path}.{key}", "ska vara en lista")
        return value

    def text(self, parent: Mapping[str, Any], key: str, path: str, *, required: bool = False) -> str | None:
        value = parent.get(key)
        if value is None:
            if required:
                raise self.fail(f"{path}.{key}", "saknas")
            return None
        if not isinstance(value, str):
            raise self.fail(f"{path}.{key}", "ska vara text")
        return value

    def integer(self, parent: Mapping[str, Any], key: str, path: str, *, required: bool = False) -> int | None:
        value = parent.get(key)
        if value is None:
            if required:
                raise self.fail(f"{path}.{key}", "saknas")
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.fail(f"{path}.{key}", "ska vara ett heltal")
        return value

    def account(self, parent: Mapping[str, Any], key: str, path: str) -> int:
        value = self.integer(parent, key, path, required=True)
        assert value is not None
        if value <= 0:
            raise self.fail(f"{path}.{key}", "kontonummer måste vara positivt")
        return value

    def amount(self, parent: Mapping[str, Any], key: str, path: str, *, required: bool = True) -> Decimal | None:
        value = parent.get(key)
        if value is None:
            if required:
                raise self.fail(f"{path}.{key}", "belopp saknas")
            return None
        if isinstance(value, bool | float):
            raise self.fail(f"{path}.{key}", "belopp ska anges som decimalsträng, inte flyttal")
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise self.fail(f"{path}.{key}", f"ogiltigt belopp {value!r}") from exc
        if not result.is_finite():
            raise self.fail(f"{path}.{key}", f"ogiltigt belopp {value!r}")
        return result

    def day(self, parent: Mapping[str, Any], key: str, path: str, *, required: bool = False) -> date | None:
        value = self.text(parent, key, path, required=required)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise self.fail(f"{path}.{key}", f"ogiltigt datum {value!r} (ska vara ÅÅÅÅ-MM-DD)") from exc

    def month(self, value: Any, path: str) -> date:
        if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
            raise self.fail(path, f"ogiltig period {value!r} (ska vara ÅÅÅÅ-MM)")
        try:
            return date(int(value[:4]), int(value[5:]), 1)
        except ValueError as exc:
            raise self.fail(path, f"ogiltig period {value!r}") from exc

    def warn(self, code: str, message: str, line: int | None = None, severity: Severity = Severity.WARNING) -> None:
        self.issues.append(ParseIssue(line, code, message, severity))


def _balance_map(r: _Reader, year: Mapping[str, Any], key: str, path: str) -> dict[int, Decimal]:
    out: dict[int, Decimal] = {}
    for i, item in enumerate(r.items(year, key, path)):
        p = f"{path}.{key}[{i}]"
        entry = r.obj(item, p)
        account = r.account(entry, "account", p)
        amount = r.amount(entry, "amount", p)
        assert amount is not None
        out[account] = out.get(account, Decimal(0)) + amount
    return out


def _period_map(r: _Reader, year: Mapping[str, Any], key: str, path: str) -> dict[tuple[date, int], Decimal]:
    out: dict[tuple[date, int], Decimal] = {}
    for i, item in enumerate(r.items(year, key, path)):
        p = f"{path}.{key}[{i}]"
        entry = r.obj(item, p)
        period = r.month(entry.get("period"), f"{p}.period")
        account = r.account(entry, "account", p)
        amount = r.amount(entry, "amount", p)
        assert amount is not None
        out[(period, account)] = out.get((period, account), Decimal(0)) + amount
    return out


def _parse_row(r: _Reader, item: Any, path: str) -> Row:
    entry = r.obj(item, path)
    objects: list[tuple[str, str]] = []
    for j, pair in enumerate(r.items(entry, "objects", path)):
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)):
            raise r.fail(f"{path}.objects[{j}]", "ska vara [dimension, objekt] som text")
        objects.append((pair[0], pair[1]))
    status_raw = r.text(entry, "status", path) or RowStatus.NORMAL.value
    try:
        status = RowStatus(status_raw)
    except ValueError as exc:
        raise r.fail(f"{path}.status", f"okänd radstatus {status_raw!r} (normal, added, removed)") from exc
    amount = r.amount(entry, "amount", path)
    assert amount is not None
    return Row(
        account=r.account(entry, "account", path),
        amount=amount,
        trans_date=r.day(entry, "date", path),
        text=r.text(entry, "text", path),
        quantity=r.amount(entry, "quantity", path, required=False),
        objects=tuple(objects),
        status=status,
        source_line=r.integer(entry, "source_line", path),
    )


def _parse_voucher(r: _Reader, item: Any, path: str, fiscal_year: FiscalYear) -> Voucher:
    entry = r.obj(item, path)
    series = r.text(entry, "series", path) or ""
    number = entry.get("number")
    if isinstance(number, int) and not isinstance(number, bool):
        number = str(number)
    if not isinstance(number, str) or not number.strip():
        raise r.fail(f"{path}.number", "verifikationsnummer saknas")
    voucher_date = r.day(entry, "date", path, required=True)
    assert voucher_date is not None
    rows = [_parse_row(r, row, f"{path}.rows[{j}]") for j, row in enumerate(r.items(entry, "rows", path))]
    if not rows:
        raise r.fail(f"{path}.rows", "verifikationen saknar rader")
    voucher = Voucher(
        series=series,
        number=number,
        date=voucher_date,
        text=r.text(entry, "text", path) or "",
        rows=tuple(rows),
        reg_date=r.day(entry, "registered", path),
        signature=r.text(entry, "signature", path),
        source_line=r.integer(entry, "source_line", path),
    )
    expected_hash = r.text(entry, "content_hash", path)
    if expected_hash and expected_hash != voucher.content_hash():
        r.warn(
            "CONTENT_HASH_MISMATCH",
            f"Verifikation {voucher.key}: fingeravtrycket stämmer inte – innehållet har ändrats efter exporten.",
            voucher.source_line,
        )
    if voucher.balance != 0:
        r.warn(
            "UNBALANCED_VOUCHER",
            f"Verifikation {voucher.key} balanserar inte (differens {voucher.balance}).",
            voucher.source_line,
        )
    if not fiscal_year.contains(voucher.date):
        r.warn(
            "VOUCHER_OUTSIDE_YEAR",
            f"Verifikation {voucher.key} är daterad {voucher.date} utanför räkenskapsåret "
            f"{fiscal_year.start}–{fiscal_year.end}.",
            voucher.source_line,
        )
    return voucher


def from_standard(data: Mapping[str, Any]) -> tuple[Ledger, list[ParseIssue]]:
    """Läs standardformatet. Strukturfel ger StandardFormatError, avvikelser i innehållet ger varningar."""
    r = _Reader()
    root = r.obj(data, "$")
    if root.get("format") != FORMAT_ID:
        raise StandardFormatError(f"Filen är inte i RedovisningAI:s standardformat (format = {FORMAT_ID!r} saknas).")
    version = str(root.get("version") or "")
    if version.split(".")[0] != FORMAT_VERSION.split(".")[0]:
        raise StandardFormatError(f"Version {version or '?'} av standardformatet stöds inte (stöd: {FORMAT_VERSION}).")
    company = r.obj(root.get("company"), "$.company")
    ledger = Ledger(
        company_name=r.text(company, "name", "$.company", required=True) or "",
        org_number=r.text(company, "org_number", "$.company"),
        currency=r.text(company, "currency", "$.company") or "SEK",
    )
    source = root.get("source")
    if isinstance(source, Mapping) and isinstance(source.get("program"), str):
        ledger.program = source["program"]

    for i, item in enumerate(r.items(root, "accounts", "$")):
        p = f"$.accounts[{i}]"
        entry = r.obj(item, p)
        number = r.account(entry, "number", p)
        type_raw = r.text(entry, "type", p)
        try:
            acc_type = AccountType(type_raw) if type_raw else None
        except ValueError as exc:
            raise r.fail(f"{p}.type", f"okänd kontotyp {type_raw!r} (T, S, K eller I)") from exc
        ledger.accounts[number] = Account(
            number, r.text(entry, "name", p) or f"Konto {number}", acc_type, r.text(entry, "sru", p)
        )
    for i, item in enumerate(r.items(root, "dimensions", "$")):
        p = f"$.dimensions[{i}]"
        entry = r.obj(item, p)
        dim = r.text(entry, "id", p, required=True) or ""
        ledger.dimensions[dim] = r.text(entry, "name", p) or dim
    for i, item in enumerate(r.items(root, "objects", "$")):
        p = f"$.objects[{i}]"
        entry = r.obj(item, p)
        dim = r.text(entry, "dimension", p, required=True) or ""
        obj = r.text(entry, "id", p, required=True) or ""
        ledger.objects[(dim, obj)] = r.text(entry, "name", p) or obj

    seen_starts: set[date] = set()
    for i, item in enumerate(r.items(root, "fiscal_years", "$")):
        p = f"$.fiscal_years[{i}]"
        entry = r.obj(item, p)
        start = r.day(entry, "start", p, required=True)
        end = r.day(entry, "end", p, required=True)
        assert start is not None and end is not None
        if end < start:
            raise r.fail(p, "räkenskapsårets slut ligger före starten")
        if start in seen_starts:
            raise r.fail(p, f"räkenskapsåret {start} finns två gånger")
        seen_starts.add(start)
        fy = FiscalYear(start, end)
        has_vouchers = entry.get("has_vouchers", True)
        if not isinstance(has_vouchers, bool):
            raise r.fail(f"{p}.has_vouchers", "ska vara true eller false")
        status = r.text(entry, "opening_balances_status", p) or "known"
        if status not in OPENING_STATUSES:
            raise r.fail(f"{p}.opening_balances_status", f"ska vara en av {', '.join(OPENING_STATUSES)}")
        covered_raw = entry.get("covered_months")
        covered: frozenset[date] | None = None
        if covered_raw is not None:
            if not isinstance(covered_raw, list):
                raise r.fail(f"{p}.covered_months", "ska vara en lista med perioder eller null")
            covered = frozenset(r.month(m, f"{p}.covered_months[{j}]") for j, m in enumerate(covered_raw))
        year = YearData(
            fiscal_year=fy,
            source_ref=r.text(entry, "source_ref", p),
            has_vouchers=has_vouchers,
            opening_status=status,
            covered_months=covered,
        )
        year.opening = _balance_map(r, entry, "opening_balances", p)
        year.closing = _balance_map(r, entry, "closing_balances", p)
        year.result = _balance_map(r, entry, "results", p)
        year.period_balances = _period_map(r, entry, "period_balances", p)
        year.budget = _period_map(r, entry, "budget", p)
        year.vouchers = [
            _parse_voucher(r, v, f"{p}.vouchers[{j}]", fy) for j, v in enumerate(r.items(entry, "vouchers", p))
        ]
        if year.vouchers and not has_vouchers:
            raise r.fail(f"{p}.has_vouchers", "året har verifikationer men has_vouchers är false")
        ledger.years.append(year)
    if not ledger.years:
        raise StandardFormatError("Filen innehåller inga räkenskapsår.")
    ledger.sort()
    for a, b in pairwise(ledger.years):
        if b.fiscal_year.start <= a.fiscal_year.end:
            raise StandardFormatError(
                f"Räkenskapsåren {a.fiscal_year.start}–{a.fiscal_year.end} och "
                f"{b.fiscal_year.start}–{b.fiscal_year.end} överlappar."
            )
    used = {row.account for y in ledger.years for v in y.vouchers for row in v.rows}
    for account in sorted(used - set(ledger.accounts)):
        ledger.accounts[account] = Account(account, f"Konto {account}")
        r.warn("UNKNOWN_ACCOUNT", f"Konto {account} saknas i kontoplanen.")
    return ledger, r.issues


def loads(text: str | bytes) -> tuple[Ledger, list[ParseIssue]]:
    """Läs standardformatet från JSON-text. Decimaltal läses exakt."""
    try:
        data = json.loads(text, parse_float=_reject_float)
    except json.JSONDecodeError as exc:
        raise StandardFormatError(f"Ogiltig JSON: {exc.msg} (rad {exc.lineno}, kolumn {exc.colno})") from exc
    return from_standard(data)


def _reject_float(value: str) -> Decimal:
    # JSON-tal med decimaler läses som Decimal så att inget avrundas på vägen.
    return Decimal(value)


def is_standard(text: str) -> bool:
    head = text.lstrip()[:400]
    return head.startswith("{") and FORMAT_ID in head
