"""Avvikelsebryggor och nedbrytning: "varför ändrades siffran?".

All matematik sker här – deterministiskt. Bryggan summerar alltid exakt till totalen.
Varje komponent blir ett Fact (kind="variance_component") som AI kan referera till.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.categories import CategoryMapping
from redovisningai.accounting.periods import Period
from redovisningai.accounting.statements import INCOME_LINES, StatementMapping, income_statement
from redovisningai.analytics.counterparties import counterparty_for_row
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import Fact, FactStore, Unit

OPERATING_COMPONENTS = (
    "net_sales",
    "inventory_change",
    "capitalized_work",
    "other_operating_income",
    "materials",
    "other_external",
    "personnel",
    "depreciation",
    "other_operating_expenses",
)
NET_COMPONENTS = (
    *OPERATING_COMPONENTS,
    "financial_assets_result",
    "interest_income",
    "interest_expense",
    "appropriations",
    "tax",
)


@dataclass(slots=True)
class BridgeComponent:
    code: str
    label: str
    current: Decimal
    previous: Decimal
    effect: Decimal  # positivt = förbättrar resultatet
    share_of_worsening: Decimal | None = None
    fact_id: str | None = None


@dataclass(slots=True)
class Bridge:
    target: str
    target_label: str
    current_total: Decimal
    previous_total: Decimal
    components: list[BridgeComponent]
    period: str
    compare_period: str
    facts: list[Fact] = field(default_factory=list)

    @property
    def change(self) -> Decimal:
        return self.current_total - self.previous_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_label": self.target_label,
            "current_total": str(self.current_total),
            "previous_total": str(self.previous_total),
            "change": str(self.change),
            "period": self.period,
            "compare_period": self.compare_period,
            "components": [
                {
                    "code": c.code,
                    "label": c.label,
                    "current": str(c.current),
                    "previous": str(c.previous),
                    "effect": str(c.effect),
                    "share_of_worsening": None if c.share_of_worsening is None else str(c.share_of_worsening),
                    "fact_id": c.fact_id,
                }
                for c in self.components
            ],
            "facts": [f.to_dict() for f in self.facts],
        }


def result_bridge(
    index: LedgerIndex,
    period: Period,
    compare: Period,
    *,
    target: str = "operating_result",
    mapping: StatementMapping | None = None,
    store: FactStore | None = None,
) -> Bridge:
    """Brygga för rörelseresultat (eller årets resultat) mellan två perioder."""
    store = store if store is not None else FactStore()
    st = income_statement(index, period, compare, mapping)
    codes = OPERATING_COMPONENTS if target == "operating_result" else NET_COMPONENTS
    comps: list[BridgeComponent] = []
    for code in codes:
        ln = st.line(code)
        prev = ln.compare or ZERO
        if ln.amount == 0 and prev == 0:
            continue
        comps.append(BridgeComponent(code, ln.label, ln.amount, prev, ln.amount - prev))
    _shares(comps)
    tgt = st.line(target)
    bridge = Bridge(target, tgt.label, tgt.amount, tgt.compare or ZERO, comps, period.spec, compare.spec)
    total_fact = store.new(
        "change",
        f"line:{target}:change",
        f"Förändring {tgt.label.lower()}",
        bridge.change,
        Unit.SEK,
        period=period.spec,
        compare_period=compare.spec,
        lineage={"current": str(bridge.current_total), "previous": str(bridge.previous_total)},
    )
    bridge.facts.append(total_fact)
    for c in comps:
        f = store.new(
            "variance_component",
            f"line:{c.code}",
            c.label,
            c.effect,
            Unit.SEK,
            period=period.spec,
            compare_period=compare.spec,
            lineage={
                "current": str(c.current),
                "previous": str(c.previous),
                "target": target,
                "accounts": sorted(st.line(c.code).accounts),
            },
        )
        c.fact_id = f.id
        bridge.facts.append(f)
        if c.share_of_worsening is not None:
            bridge.facts.append(
                store.new(
                    "share",
                    f"line:{c.code}:share",
                    f"Andel av försämringen: {c.label}",
                    c.share_of_worsening,
                    Unit.PERCENT,
                    period=period.spec,
                    compare_period=compare.spec,
                    lineage={"component": f.id},
                )
            )
    return bridge


def _shares(comps: list[BridgeComponent]) -> None:
    negatives = [c for c in comps if c.effect < 0]
    total_neg = sum((c.effect for c in negatives), ZERO)
    if total_neg == 0:
        return
    for c in negatives:
        c.share_of_worsening = (c.effect / total_neg * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def category_bridge(
    index: LedgerIndex,
    period: Period,
    compare: Period,
    *,
    mapping: CategoryMapping | None = None,
    store: FactStore | None = None,
) -> Bridge:
    """Kostnadsförändring per management-kategori (toppnivå). Positivt effect = lägre kostnad."""
    mapping = mapping or CategoryMapping()
    store = store if store is not None else FactStore()
    comps: list[BridgeComponent] = []
    cur_total = prev_total = ZERO
    for cat in mapping.children(None):
        accs = mapping.accounts_in(cat.code, index.accounts_used)
        if not accs:
            continue
        cur = index.movement(accs, period)
        prev = index.movement(accs, compare)
        cur_total += cur
        prev_total += prev
        if cur == 0 and prev == 0:
            continue
        comps.append(BridgeComponent(cat.code, cat.name, cur, prev, prev - cur))
    _shares(comps)
    bridge = Bridge("costs", "Kostnader", cur_total, prev_total, comps, period.spec, compare.spec)
    for c in comps:
        f = store.new(
            "variance_component",
            f"category:{c.code}",
            c.label,
            c.current - c.previous,
            Unit.SEK,
            period=period.spec,
            compare_period=compare.spec,
            lineage={"current": str(c.current), "previous": str(c.previous)},
        )
        c.fact_id = f.id
        bridge.facts.append(f)
    return bridge


@dataclass(slots=True)
class AccountChange:
    account: int
    name: str
    current: Decimal
    previous: Decimal
    fact_id: str | None = None

    @property
    def diff(self) -> Decimal:
        return self.current - self.previous


@dataclass(slots=True)
class CounterpartyChange:
    key: str
    name: str
    current: Decimal
    previous: Decimal
    is_new: bool
    fact_id: str | None = None

    @property
    def diff(self) -> Decimal:
        return self.current - self.previous


@dataclass(slots=True)
class Drilldown:
    accounts: list[AccountChange]
    counterparties: list[CounterpartyChange]
    top_vouchers: list[dict[str, Any]]
    facts: list[Fact]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accounts": [
                {
                    "account": a.account,
                    "name": a.name,
                    "current": str(a.current),
                    "previous": str(a.previous),
                    "diff": str(a.diff),
                    "fact_id": a.fact_id,
                }
                for a in self.accounts
            ],
            "counterparties": [
                {
                    "key": c.key,
                    "name": c.name,
                    "current": str(c.current),
                    "previous": str(c.previous),
                    "diff": str(c.diff),
                    "is_new": c.is_new,
                    "fact_id": c.fact_id,
                }
                for c in self.counterparties
            ],
            "top_vouchers": self.top_vouchers,
            "facts": [f.to_dict() for f in self.facts],
        }


def drilldown(
    index: LedgerIndex,
    accounts: AccountSet | set[int],
    period: Period,
    compare: Period,
    *,
    store: FactStore | None = None,
    limit: int = 8,
    sign: int = 1,
) -> Drilldown:
    """Nedbrytning av en förändring: konton → motparter → största verifikationer.

    `sign` = 1 för kostnader (debet positivt), −1 för intäkter.
    """
    store = store if store is not None else FactStore()
    accs = accounts if isinstance(accounts, AccountSet) else set(accounts)
    cur_by = defaultdict(lambda: ZERO)
    prev_by = defaultdict(lambda: ZERO)
    cp_cur: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cp_prev: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cp_names: dict[str, str] = {}
    top: list[tuple[Decimal, dict[str, Any]]] = []
    for per, target, cp_target in ((period, cur_by, cp_cur), (compare, prev_by, cp_prev)):
        for v in index.vouchers_in(per):
            for r in v.effective_rows:
                if r.account not in accs:
                    continue
                amt = r.amount * sign
                target[r.account] += amt
                g = counterparty_for_row(v, r)
                key = g.key or "(okänd motpart)"
                cp_names.setdefault(key, g.name or "Okänd motpart")
                cp_target[key] += amt
                if per is period:
                    top.append(
                        (
                            abs(r.amount),
                            {
                                "voucher": str(v.key),
                                "date": v.date.isoformat(),
                                "text": r.text or v.text,
                                "account": r.account,
                                "amount": str(amt),
                                "counterparty": g.name or None,
                            },
                        )
                    )
    account_changes: list[AccountChange] = []
    for a in set(cur_by) | set(prev_by):
        ac = AccountChange(a, index.ledger.account_name(a), cur_by[a], prev_by[a])
        if ac.diff != 0:
            f = store.new(
                "change",
                f"account:{a}",
                f"Förändring konto {a} {ac.name}",
                ac.diff,
                Unit.SEK,
                period=period.spec,
                compare_period=compare.spec,
                lineage={"current": str(ac.current), "previous": str(ac.previous)},
            )
            ac.fact_id = f.id
        account_changes.append(ac)
    account_changes.sort(key=lambda a: abs(a.diff), reverse=True)
    cp_changes: list[CounterpartyChange] = []
    for k in set(cp_cur) | set(cp_prev):
        cc = CounterpartyChange(k, cp_names.get(k, k), cp_cur[k], cp_prev[k], is_new=cp_prev[k] == 0 and cp_cur[k] != 0)
        if cc.diff != 0:
            f = store.new(
                "change",
                f"counterparty:{k}",
                f"Förändring {cc.name}",
                cc.diff,
                Unit.SEK,
                period=period.spec,
                compare_period=compare.spec,
                lineage={"current": str(cc.current), "previous": str(cc.previous), "is_new": cc.is_new},
            )
            cc.fact_id = f.id
        cp_changes.append(cc)
    cp_changes.sort(key=lambda c: abs(c.diff), reverse=True)
    top.sort(key=lambda t: t[0], reverse=True)
    facts = [store.get(x.fact_id) for x in account_changes[:limit] + cp_changes[:limit] if x.fact_id]  # type: ignore[operator]
    return Drilldown(
        account_changes[:limit], cp_changes[:limit], [t[1] for t in top[:limit]], [f for f in facts if f is not None]
    )


def line_accounts(code: str, index: LedgerIndex, mapping: StatementMapping | None = None) -> set[int]:
    mapping = mapping or StatementMapping()
    if code not in {ln.code for ln in INCOME_LINES}:
        raise KeyError(code)
    return mapping.accounts_for(code, index.accounts_used)
