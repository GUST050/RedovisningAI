"""Skriv SIE 4-filer (PC8/CP437). Används för testdata, demo och export."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from redovisningai.domain.ledger import Account, AccountType, Ledger, RowStatus, YearData


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _a(a: Decimal) -> str:
    return f"{a:.2f}"


def write_sie4(
    ledger: Ledger,
    year: YearData,
    *,
    program: str = "RedovisningAI",
    generated: date | None = None,
    company_type: str | None = "AB",
    include_previous_summary: bool = True,
    extra_lines: list[str] | None = None,
    encoding: str = "cp437",
) -> bytes:
    """Skriv ett räkenskapsår som SIE 4E (med föregående år som sammandrag)."""
    lines: list[str] = [
        "#FLAGGA 0",
        f"#PROGRAM {_q(program)} 1.0",
        "#FORMAT PC8",
        f"#GEN {_d(generated or year.fiscal_year.end)}",
        "#SIETYP 4",
        f"#FNAMN {_q(ledger.company_name)}",
    ]
    if ledger.org_number:
        lines.append(f"#ORGNR {ledger.org_number}")
    if company_type:
        lines.append(f"#FTYP {company_type}")
    lines.append(f"#RAR 0 {_d(year.fiscal_year.start)} {_d(year.fiscal_year.end)}")
    prev = ledger.previous_year(year) if include_previous_summary else None
    if prev is not None:
        lines.append(f"#RAR -1 {_d(prev.fiscal_year.start)} {_d(prev.fiscal_year.end)}")
    lines.append("#KPTYP BAS2024")
    for dim, name in sorted(ledger.dimensions.items()):
        lines.append(f"#DIM {dim} {_q(name)}")
    for (dim, obj), name in sorted(ledger.objects.items()):
        lines.append(f"#OBJEKT {dim} {_q(obj)} {_q(name)}")
    for no in sorted(ledger.accounts):
        acc: Account = ledger.accounts[no]
        if acc.name.startswith("__undeclared__"):
            continue
        lines.append(f"#KONTO {no} {_q(acc.name)}")
        if acc.type is not None:
            lines.append(f"#KTYP {no} {acc.type.value}")
        else:
            lines.append(f"#KTYP {no} {_default_ktyp(no).value}")
    for year_no, yd in ((0, year), (-1, prev)):
        if yd is None:
            continue
        for acc_no, amt in sorted(yd.opening.items()):
            lines.append(f"#IB {year_no} {acc_no} {_a(amt)}")
        for acc_no, amt in sorted(yd.closing.items()):
            lines.append(f"#UB {year_no} {acc_no} {_a(amt)}")
        for acc_no, amt in sorted(yd.result.items()):
            lines.append(f"#RES {year_no} {acc_no} {_a(amt)}")
    for (period, acc_no), amt in sorted(year.budget.items()):
        lines.append(f"#PBUDGET 0 {period:%Y%m} {acc_no} {{}} {_a(amt)}")
    if extra_lines:
        lines.extend(extra_lines)
    for v in year.vouchers:
        reg = f" {_d(v.reg_date)}" if v.reg_date else ""
        lines.append(f"#VER {_q(v.series)} {_q(v.number)} {_d(v.date)} {_q(v.text)}{reg}")
        lines.append("{")
        for r in v.rows:
            objs = " ".join(f"{d} {_q(o)}" for d, o in r.objects)
            tdate = _d(r.trans_date) if r.trans_date else '""'
            text = _q(r.text) if r.text else '""'
            body = f"{r.account} {{{objs}}} {_a(r.amount)} {tdate} {text}"
            if r.status is RowStatus.ADDED:
                lines.append(f"   #RTRANS {body}")
                lines.append(f"   #TRANS {body}")
            elif r.status is RowStatus.REMOVED:
                lines.append(f"   #BTRANS {body}")
            else:
                lines.append(f"   #TRANS {body}")
        lines.append("}")
    text = "\r\n".join(lines) + "\r\n"
    if encoding.lower() in ("cp437", "ibm437"):
        text = text.translate(_CP437_FALLBACK)
    return text.encode(encoding, errors="replace")


# Vanliga tecken som saknas i CP437 ersätts med närmaste motsvarighet i stället för "?".
_CP437_FALLBACK = str.maketrans({"–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...", "€": "E"})


def _default_ktyp(no: int) -> AccountType:
    if no < 2000:
        return AccountType.ASSET
    if no < 3000:
        return AccountType.LIABILITY
    if no < 4000:
        return AccountType.INCOME
    if 8000 <= no < 8400 and no not in range(8270, 8300):
        return AccountType.INCOME
    return AccountType.COST
