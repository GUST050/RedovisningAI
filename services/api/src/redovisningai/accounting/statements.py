"""Resultat- och balansräkning (kostnadsslagsindelad uppställning enligt ÅRL bilaga 2/K2/K3).

Standardmappningen följer BAS-kontoplanens intervall. Den kan överstyras per byrå och
kund (`StatementMapping.overrides`) – varje överstyrning ger en ny mappningsversion.

Presentationstecken: intäkter, eget kapital och skulder visas positiva; kostnader negativa i
resultaträkningen. Lagringen följer alltid SIE (debet +, kredit −).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.periods import Period
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import pct_change


@dataclass(frozen=True, slots=True)
class LineDef:
    code: str
    label: str
    accounts: AccountSet | None = None  # None = summeringsrad
    sum_of: tuple[str, ...] = ()
    level: int = 1


INCOME_LINES: tuple[LineDef, ...] = (
    LineDef("net_sales", "Nettoomsättning", AccountSet.of((3000, 3799))),
    LineDef(
        "inventory_change",
        "Förändring av lager av produkter i arbete, färdiga varor och pågående arbete",
        AccountSet.of((4940, 4999)),
    ),
    LineDef("capitalized_work", "Aktiverat arbete för egen räkning", AccountSet.of((3800, 3899))),
    LineDef("other_operating_income", "Övriga rörelseintäkter", AccountSet.of((3900, 3999))),
    LineDef(
        "operating_income",
        "Summa rörelseintäkter",
        sum_of=("net_sales", "inventory_change", "capitalized_work", "other_operating_income"),
        level=0,
    ),
    LineDef("materials", "Råvaror, förnödenheter och handelsvaror", AccountSet.of((4000, 4939))),
    LineDef("other_external", "Övriga externa kostnader", AccountSet.of((5000, 6999))),
    LineDef("personnel", "Personalkostnader", AccountSet.of((7000, 7699))),
    LineDef(
        "depreciation",
        "Av- och nedskrivningar av materiella och immateriella anläggningstillgångar",
        AccountSet.of((7700, 7899)),
    ),
    LineDef("other_operating_expenses", "Övriga rörelsekostnader", AccountSet.of((7900, 7999))),
    LineDef(
        "operating_result",
        "Rörelseresultat",
        sum_of=(
            "operating_income",
            "materials",
            "other_external",
            "personnel",
            "depreciation",
            "other_operating_expenses",
        ),
        level=0,
    ),
    LineDef("financial_assets_result", "Resultat från finansiella anläggningstillgångar", AccountSet.of((8000, 8299))),
    LineDef("interest_income", "Övriga ränteintäkter och liknande resultatposter", AccountSet.of((8300, 8399))),
    LineDef("interest_expense", "Räntekostnader och liknande resultatposter", AccountSet.of((8400, 8499))),
    LineDef(
        "result_after_financial",
        "Resultat efter finansiella poster",
        sum_of=("operating_result", "financial_assets_result", "interest_income", "interest_expense"),
        level=0,
    ),
    LineDef("appropriations", "Bokslutsdispositioner", AccountSet.of((8800, 8899))),
    LineDef("result_before_tax", "Resultat före skatt", sum_of=("result_after_financial", "appropriations"), level=0),
    LineDef("tax", "Skatt på årets resultat", AccountSet.of((8900, 8989))),
    LineDef("net_result", "Årets resultat", sum_of=("result_before_tax", "tax"), level=0),
)

# Konton 8500–8799 används sällan; de läggs under finansiella poster för att inget ska tappas.
_UNMAPPED_RESULT_FALLBACK = "interest_expense"

BALANCE_LINES: tuple[LineDef, ...] = (
    LineDef("intangible", "Immateriella anläggningstillgångar", AccountSet.of((1000, 1099))),
    LineDef("tangible", "Materiella anläggningstillgångar", AccountSet.of((1100, 1299))),
    LineDef("financial_fixed", "Finansiella anläggningstillgångar", AccountSet.of((1300, 1399))),
    LineDef(
        "fixed_assets", "Summa anläggningstillgångar", sum_of=("intangible", "tangible", "financial_fixed"), level=0
    ),
    LineDef("inventory", "Varulager m.m.", AccountSet.of((1400, 1499))),
    LineDef("receivables", "Kortfristiga fordringar", AccountSet.of((1500, 1799))),
    LineDef("short_investments", "Kortfristiga placeringar", AccountSet.of((1800, 1899))),
    LineDef("cash", "Kassa och bank", AccountSet.of((1900, 1999))),
    LineDef(
        "current_assets",
        "Summa omsättningstillgångar",
        sum_of=("inventory", "receivables", "short_investments", "cash"),
        level=0,
    ),
    LineDef("total_assets", "SUMMA TILLGÅNGAR", sum_of=("fixed_assets", "current_assets"), level=0),
    LineDef("equity", "Eget kapital", AccountSet.of((2000, 2099))),
    LineDef("current_result", "Beräknat resultat (ej bokslutsfört)"),
    LineDef("total_equity", "Summa eget kapital", sum_of=("equity", "current_result"), level=0),
    LineDef("untaxed_reserves", "Obeskattade reserver", AccountSet.of((2100, 2199))),
    LineDef("provisions", "Avsättningar", AccountSet.of((2200, 2299))),
    LineDef("long_term_liabilities", "Långfristiga skulder", AccountSet.of((2300, 2399))),
    LineDef("current_liabilities", "Kortfristiga skulder", AccountSet.of((2400, 2999))),
    LineDef(
        "total_equity_liabilities",
        "SUMMA EGET KAPITAL OCH SKULDER",
        sum_of=("total_equity", "untaxed_reserves", "provisions", "long_term_liabilities", "current_liabilities"),
        level=0,
    ),
)

ASSET_LINES = {"intangible", "tangible", "financial_fixed", "inventory", "receivables", "short_investments", "cash"}


@dataclass(slots=True)
class StatementMapping:
    """Konto → rad. Överstyrningar har företräde (System → Bransch → Byrå → Kund)."""

    overrides: dict[int, str] = field(default_factory=dict)
    source: str = "system"

    @property
    def version(self) -> str:
        raw = json.dumps(sorted(self.overrides.items()))
        return "m-" + hashlib.sha256(raw.encode()).hexdigest()[:8]

    def line_for(self, account: int) -> str | None:
        if account in self.overrides:
            return self.overrides[account]
        lines = INCOME_LINES if account >= 3000 else BALANCE_LINES
        for ln in lines:
            if ln.accounts is not None and account in ln.accounts:
                return ln.code
        if 3000 <= account <= 8989:
            return _UNMAPPED_RESULT_FALLBACK
        return None

    def accounts_for(self, code: str, used: set[int]) -> set[int]:
        return {a for a in used if self.line_for(a) == code}


@dataclass(slots=True)
class StatementLine:
    code: str
    label: str
    level: int
    amount: Decimal
    compare: Decimal | None = None
    accounts: dict[int, Decimal] = field(default_factory=dict)
    compare_accounts: dict[int, Decimal] = field(default_factory=dict)

    @property
    def diff(self) -> Decimal | None:
        return None if self.compare is None else self.amount - self.compare

    @property
    def diff_pct(self) -> Decimal | None:
        return None if self.compare is None else pct_change(self.amount, self.compare)

    def to_dict(self, names: dict[int, str] | None = None) -> dict[str, Any]:
        names = names or {}
        return {
            "code": self.code,
            "label": self.label,
            "level": self.level,
            "amount": str(self.amount),
            "compare": None if self.compare is None else str(self.compare),
            "diff": None if self.diff is None else str(self.diff),
            "diff_pct": None if self.diff_pct is None else str(self.diff_pct),
            "accounts": [
                {
                    "account": a,
                    "name": names.get(a, f"Konto {a}"),
                    "amount": str(v),
                    "compare": str(self.compare_accounts.get(a, ZERO)) if self.compare is not None else None,
                }
                for a, v in sorted({**{k: ZERO for k in self.compare_accounts}, **self.accounts}.items())
            ],
        }


@dataclass(slots=True)
class Statement:
    kind: str  # income | balance
    period_label: str
    compare_label: str | None
    lines: list[StatementLine]
    mapping_version: str
    complete: bool = True
    missing_months: list[date] = field(default_factory=list)

    def line(self, code: str) -> StatementLine:
        return next(ln for ln in self.lines if ln.code == code)

    def to_dict(self, names: dict[int, str] | None = None) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "period": self.period_label,
            "compare": self.compare_label,
            "mapping_version": self.mapping_version,
            "complete": self.complete,
            "missing_months": [m.isoformat() for m in self.missing_months],
            "lines": [ln.to_dict(names) for ln in self.lines],
        }


def _income_amounts(index: LedgerIndex, period: Period, mapping: StatementMapping) -> dict[str, dict[int, Decimal]]:
    out: dict[str, dict[int, Decimal]] = {ln.code: {} for ln in INCOME_LINES}
    for m in period.months():
        for acc, amt in index.movements.get(m, {}).items():
            if acc < 3000 or acc >= 8990:
                continue
            code = mapping.line_for(acc)
            if code is None:
                continue
            out[code][acc] = out[code].get(acc, ZERO) - amt  # presentation: kredit positiv
    return out


def income_statement(
    index: LedgerIndex, period: Period, compare: Period | None = None, mapping: StatementMapping | None = None
) -> Statement:
    mapping = mapping or StatementMapping()
    cur = _income_amounts(index, period, mapping)
    cmp_ = _income_amounts(index, compare, mapping) if compare else None
    lines: list[StatementLine] = []
    totals: dict[str, Decimal] = {}
    ctotals: dict[str, Decimal] = {}
    for ln in INCOME_LINES:
        if ln.sum_of:
            amount = sum((totals[c] for c in ln.sum_of), ZERO)
            camount = sum((ctotals[c] for c in ln.sum_of), ZERO) if cmp_ is not None else None
            accs: dict[int, Decimal] = {}
            caccs: dict[int, Decimal] = {}
        else:
            accs = {a: v for a, v in cur[ln.code].items() if v != 0}
            caccs = {a: v for a, v in cmp_[ln.code].items() if v != 0} if cmp_ is not None else {}
            amount = sum(accs.values(), ZERO)
            camount = sum(caccs.values(), ZERO) if cmp_ is not None else None
        totals[ln.code] = amount
        if camount is not None:
            ctotals[ln.code] = camount
        lines.append(StatementLine(ln.code, ln.label, ln.level, amount, camount, accs, caccs))
    missing = index.missing_months(period) + (index.missing_months(compare) if compare else [])
    return Statement(
        kind="income",
        period_label=period.label,
        compare_label=compare.label if compare else None,
        lines=lines,
        mapping_version=mapping.version,
        complete=not missing,
        missing_months=missing,
    )


def _balance_amounts(index: LedgerIndex, at: date, mapping: StatementMapping) -> dict[str, dict[int, Decimal]]:
    out: dict[str, dict[int, Decimal]] = {ln.code: {} for ln in BALANCE_LINES}
    for acc, bal in index.balances_at(at).items():
        code = mapping.line_for(acc)
        if code is None:
            continue
        out[code][acc] = bal if code in ASSET_LINES else -bal
    return out


def balance_sheet(
    index: LedgerIndex, at: date, compare_at: date | None = None, mapping: StatementMapping | None = None
) -> Statement:
    mapping = mapping or StatementMapping()
    cur = _balance_amounts(index, at, mapping)
    cmp_ = _balance_amounts(index, compare_at, mapping) if compare_at else None
    result_now = index.result_to_date(at, include_closing_entries=True)
    result_cmp = index.result_to_date(compare_at, include_closing_entries=True) if compare_at else None
    lines: list[StatementLine] = []
    totals: dict[str, Decimal] = {}
    ctotals: dict[str, Decimal] = {}
    for ln in BALANCE_LINES:
        accs: dict[int, Decimal] = {}
        caccs: dict[int, Decimal] = {}
        if ln.code == "current_result":
            amount = result_now
            camount = result_cmp
        elif ln.sum_of:
            amount = sum((totals[c] for c in ln.sum_of), ZERO)
            camount = sum((ctotals[c] for c in ln.sum_of), ZERO) if cmp_ is not None else None
        else:
            accs = cur[ln.code]
            caccs = cmp_[ln.code] if cmp_ is not None else {}
            amount = sum(accs.values(), ZERO)
            camount = sum(caccs.values(), ZERO) if cmp_ is not None else None
        totals[ln.code] = amount
        if camount is not None:
            ctotals[ln.code] = camount
        lines.append(StatementLine(ln.code, ln.label, ln.level, amount, camount, accs, caccs))
    return Statement(
        kind="balance",
        period_label=f"Per {at.isoformat()}",
        compare_label=f"Per {compare_at.isoformat()}" if compare_at else None,
        lines=lines,
        mapping_version=mapping.version,
    )
