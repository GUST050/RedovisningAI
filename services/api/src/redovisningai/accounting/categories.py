"""Management-kategorier (kostnadsträd) – skilt från den legala uppställningen.

Konsulten och kunden vill höra "IT och programvara ökade 31 %", inte "konto 6540 ökade".
Kategorierna har en hierarki och kan överstyras per byrå och kund.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.periods import Period
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import pct_change


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    name: str
    parent: str | None
    accounts: AccountSet | None = None  # None = grupp utan egna konton


DEFAULT_CATEGORIES: tuple[Category, ...] = (
    # Ordning spelar roll: specifika kategorier före breda.
    Category("technology", "IT och telekom", None),
    Category("software", "Programvara och IT-tjänster", "technology", AccountSet.of(5420, 6540)),
    Category("telecom", "Telefon och data", "technology", AccountSet.of((6200, 6299))),
    Category("external_services", "Konsulter och externa tjänster", None),
    Category("consulting", "Konsulttjänster", "external_services", AccountSet.of(6550)),
    Category("accounting_audit", "Redovisning och revision", "external_services", AccountSet.of(6530, (6420, 6429))),
    Category("legal", "Juridik", "external_services", AccountSet.of(6580)),
    Category(
        "other_services",
        "Övriga externa tjänster",
        "external_services",
        AccountSet.of((6500, 6599), exclude=[6530, 6540, 6550, 6570, 6580]),
    ),
    Category("finance", "Bank och finans", None, AccountSet.of(6570, (8400, 8499))),
    Category("materials", "Material och varor", None, AccountSet.of((4000, 4999))),
    Category("personnel", "Personal", None, AccountSet.of((7000, 7699))),
    Category("premises", "Lokaler", None, AccountSet.of((5000, 5199))),
    Category("vehicles", "Fordon och transporter", None, AccountSet.of((5600, 5799))),
    Category("travel", "Resor", None, AccountSet.of((5800, 5899))),
    Category("marketing", "Marknadsföring och försäljning", None, AccountSet.of((5900, 6099))),
    Category("office", "Kontor och förbrukning", None, AccountSet.of((5200, 5599), (6100, 6199), exclude=[5420])),
    Category("insurance", "Försäkringar och risk", None, AccountSet.of((6300, 6399))),
    Category(
        "administration", "Förvaltning och administration", None, AccountSet.of((6400, 6499), exclude=[(6420, 6429)])
    ),
    Category("depreciation", "Avskrivningar", None, AccountSet.of((7700, 7899))),
    Category("other", "Övrigt", None, AccountSet.of((6600, 6999), (7900, 7999))),
)


@dataclass(slots=True)
class CategoryMapping:
    categories: tuple[Category, ...] = DEFAULT_CATEGORIES
    overrides: dict[int, str] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return "c-" + hashlib.sha256(json.dumps(sorted(self.overrides.items())).encode()).hexdigest()[:8]

    def category_for(self, account: int) -> str | None:
        if account in self.overrides:
            return self.overrides[account]
        for c in self.categories:
            if c.accounts is not None and account in c.accounts:
                return c.code
        return None

    def get(self, code: str) -> Category:
        return next(c for c in self.categories if c.code == code)

    def children(self, code: str | None) -> list[Category]:
        return [c for c in self.categories if c.parent == code]

    def descendants_and_self(self, code: str) -> set[str]:
        out = {code}
        for ch in self.children(code):
            out |= self.descendants_and_self(ch.code)
        return out

    def accounts_in(self, code: str, used: set[int]) -> set[int]:
        codes = self.descendants_and_self(code)
        return {a for a in used if 4000 <= a <= 8499 and self.category_for(a) in codes}


@dataclass(slots=True)
class CostNode:
    code: str
    name: str
    amount: Decimal  # kostnad som positivt belopp
    compare: Decimal | None
    share: Decimal | None
    children: list[CostNode] = field(default_factory=list)
    accounts: dict[int, Decimal] = field(default_factory=dict)
    compare_accounts: dict[int, Decimal] = field(default_factory=dict)

    @property
    def diff(self) -> Decimal | None:
        return None if self.compare is None else self.amount - self.compare

    def to_dict(self, names: dict[int, str] | None = None) -> dict[str, Any]:
        names = names or {}
        return {
            "code": self.code,
            "name": self.name,
            "amount": str(self.amount),
            "compare": None if self.compare is None else str(self.compare),
            "diff": None if self.diff is None else str(self.diff),
            "diff_pct": None if self.compare is None else _s(pct_change(self.amount, self.compare)),
            "share": _s(self.share),
            "accounts": [
                {
                    "account": a,
                    "name": names.get(a, f"Konto {a}"),
                    "amount": str(v),
                    "compare": str(self.compare_accounts.get(a, ZERO)),
                }
                for a, v in sorted({**{k: ZERO for k in self.compare_accounts}, **self.accounts}.items())
            ],
            "children": [c.to_dict(names) for c in self.children],
        }


def _s(x: Decimal | None) -> str | None:
    return None if x is None else str(x)


def _costs_by_account(index: LedgerIndex, period: Period) -> dict[int, Decimal]:
    out: dict[int, Decimal] = {}
    for acc, amt in index.period_movements(period).items():
        if 4000 <= acc <= 8499 and not (8000 <= acc <= 8399):
            out[acc] = out.get(acc, ZERO) + amt
    return out


def cost_tree(
    index: LedgerIndex, period: Period, compare: Period | None = None, mapping: CategoryMapping | None = None
) -> CostNode:
    mapping = mapping or CategoryMapping()
    cur = _costs_by_account(index, period)
    cmp_ = _costs_by_account(index, compare) if compare else None
    total = sum(cur.values(), ZERO)

    def build(cat: Category) -> CostNode:
        own = {a: v for a, v in cur.items() if mapping.category_for(a) == cat.code and v != 0}
        own_c = {a: v for a, v in (cmp_ or {}).items() if mapping.category_for(a) == cat.code and v != 0}
        children = [build(ch) for ch in mapping.children(cat.code)]
        amount = sum(own.values(), ZERO) + sum((c.amount for c in children), ZERO)
        compare_amount: Decimal | None = None
        if cmp_ is not None:
            compare_amount = sum(own_c.values(), ZERO) + sum((c.compare or ZERO for c in children), ZERO)
        share = (amount / total * 100).quantize(Decimal("0.1")) if total else None
        return CostNode(
            cat.code,
            cat.name,
            amount,
            compare_amount,
            share,
            [c for c in children if c.amount or c.compare],
            own,
            own_c,
        )

    roots = [build(c) for c in mapping.children(None)]
    roots = [r for r in roots if r.amount or r.compare]
    roots.sort(key=lambda n: n.amount, reverse=True)
    unmapped = {a: v for a, v in cur.items() if mapping.category_for(a) is None and v}
    if unmapped:
        roots.append(
            CostNode("unmapped", "Ej kategoriserat", sum(unmapped.values(), ZERO), None, None, accounts=unmapped)
        )
    return CostNode(
        "total",
        "Totala kostnader",
        total,
        sum(cmp_.values(), ZERO) if cmp_ is not None else None,
        Decimal(100) if total else None,
        roots,
    )
