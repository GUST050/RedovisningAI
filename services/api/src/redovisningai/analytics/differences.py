"""De viktigaste skillnaderna mellan två perioder – underlag för att välja vad en rapport ska ta upp.

Varje jämförelse blir en valbar post (`DifferenceItem`): ett nyckeltal, en resultat- eller
balansrad, en kostnadskategori, ett analysfynd eller ett nyckeltals utveckling över tid. Posterna
rangordnas med fasta, redovisade poäng (väsentlighet i förhållande till omsättningen, belopp,
relativ förändring, datakvalitet) så att de viktigaste kan föreslås – konsulten väljer.

Allt är deterministiskt. Texterna beskriver bara bokförda förändringar, aldrig orsaker.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.categories import CategoryMapping
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metric_explanations import MetricExplanation, explain_metric
from redovisningai.accounting.metrics import CORE_METRICS, REGISTRY
from redovisningai.accounting.statements import (
    BALANCE_LINES,
    INCOME_LINES,
    StatementMapping,
    balance_sheet,
    income_statement,
)
from redovisningai.analytics.finding_candidates import collect_candidates
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import FactStatus, FactStore, format_percent, format_sek
from redovisningai.review.finding_priorities import rank_findings
from redovisningai.rules.rates import RateTable

DIFFERENCES_VERSION = "differences-v1"
DEFAULT_RECOMMENDED = 6
KIND_ORDER = {"metric": 0, "line": 1, "category": 2, "finding": 3, "structure": 4}
# Resultatrader som är intäkter (positiva i uppställningen); övriga resultatrader är kostnader.
INCOME_SIDE = {
    "net_sales",
    "inventory_change",
    "capitalized_work",
    "other_operating_income",
    "financial_assets_result",
    "interest_income",
}
ASSET_LINES = {"intangible", "tangible", "financial_fixed", "inventory", "receivables", "short_investments", "cash"}
# Poster inom samma område beskriver ofta samma förändring (t.ex. rörelseresultat och EBITDA).
# Bara den starkaste per område föreslås; övriga går att välja till.
FAMILIES = {
    "metric:net_sales": "sales",
    "metric:operating_result": "result",
    "metric:result_after_financial": "result",
    "metric:ebitda": "result",
    "metric:operating_margin": "margin",
    "metric:profit_margin": "margin",
    "metric:gross_profit": "gross",
    "metric:gross_margin": "gross",
    "line:income:materials": "gross",
    "metric:personnel_share": "personnel",
    "line:income:personnel": "personnel",
    "metric:external_cost_share": "external",
    "line:income:other_external": "external",
    "metric:equity_ratio": "solvency",
    "line:balance:equity": "solvency",
    "metric:quick_ratio": "liquidity",
    "metric:current_ratio": "liquidity",
    "metric:working_capital": "liquidity",
    "metric:cash": "liquidity",
    "metric:receivables": "receivables",
    "line:balance:receivables": "receivables",
    "metric:payables": "payables",
    "line:balance:current_liabilities": "payables",
    "line:income:depreciation": "depreciation",
}
FAMILY_LABELS = {
    "sales": "omsättning",
    "result": "resultat",
    "margin": "marginal",
    "gross": "bruttoresultat",
    "personnel": "personalkostnader",
    "external": "externa kostnader",
    "solvency": "eget kapital",
    "liquidity": "likviditet",
    "receivables": "fordringar",
    "payables": "skulder",
    "depreciation": "avskrivningar",
}
BALANCE_METRICS = {code for code, d in REGISTRY.items() if not d.period_based}
PATTERN_FINDINGS = {
    "recurring_cost_change",
    "transaction_frequency_change",
    "recurring_level_shift",
    "possible_duplicate",
}
USABLE = (FactStatus.CALCULATED, FactStatus.PARTIAL)


@dataclass(slots=True)
class DifferenceItem:
    id: str
    kind: str  # metric | line | category | finding
    code: str
    title: str
    summary: str
    unit: str  # SEK | percent | count
    current: Decimal | None
    previous: Decimal | None
    change: Decimal | None
    change_pct: Decimal | None
    status: FactStatus
    score: int
    score_parts: dict[str, int]
    reason: str
    audience: str  # "client" = får tas med i kundrapport, "internal" = bara intern rapport
    better: str | None = None
    warnings: list[str] = field(default_factory=list)
    accounts: tuple[int, ...] = ()
    recommended: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def selectable(self) -> bool:
        return self.status in USABLE and self.change is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "code": self.code,
            "title": self.title,
            "summary": self.summary,
            "unit": self.unit,
            "current": None if self.current is None else str(self.current),
            "previous": None if self.previous is None else str(self.previous),
            "change": None if self.change is None else str(self.change),
            "change_pct": None if self.change_pct is None else str(self.change_pct),
            "status": self.status.value,
            "score": self.score,
            "score_parts": self.score_parts,
            "reason": self.reason,
            "audience": self.audience,
            "better": self.better,
            "warnings": self.warnings,
            "selectable": self.selectable,
            "recommended": self.recommended,
            "family": self.extra.get("family"),
            "not_recommended": self.extra.get("not_recommended"),
        }


@dataclass(slots=True)
class DifferenceSet:
    pair: ComparisonPair
    items: list[DifferenceItem]
    explanations: dict[str, MetricExplanation]
    base: Decimal
    versions: dict[str, str]

    def get(self, item_id: str) -> DifferenceItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def recommended(self) -> list[DifferenceItem]:
        return [i for i in self.items if i.recommended]


# ---------------------------------------------------------------------------- formatering


def _q(value: Decimal, places: str = "0.1") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_value(value: Decimal | None, unit: str, *, signed: bool = False) -> str:
    if value is None:
        return "–"
    if unit == "SEK":
        return format_sek(value, signed=signed)
    if unit in ("percent", "pp"):
        return format_percent(_q(value), signed=signed)
    return str(value)


def fmt_change(value: Decimal | None, unit: str) -> str:
    if value is None:
        return "–"
    if unit in ("percent", "pp"):
        body = format_percent(_q(value), signed=True).replace(" %", " procentenheter")
        return body
    return fmt_value(value, unit, signed=True)


def _pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return _q((current - previous) / abs(previous) * 100)


def _verb(change: Decimal) -> str:
    return "ökade" if change > 0 else "minskade" if change < 0 else "var oförändrad"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


# ---------------------------------------------------------------------------- poäng


def _sek_points(change: Decimal, previous: Decimal, base: Decimal) -> dict[str, int]:
    size = abs(change)
    share = size / base if base else Decimal(0)
    materiality = (
        40
        if share >= Decimal("0.10")
        else 30
        if share >= Decimal("0.05")
        else 20
        if share >= Decimal("0.02")
        else 10
        if share >= Decimal("0.01")
        else 3
        if size > 0
        else 0
    )
    amount = 15 if size >= 100_000 else 10 if size >= 25_000 else 5 if size >= 5_000 else 0
    if previous == 0:
        relative = 15 if size > 0 else 0
    else:
        r = size / abs(previous)
        relative = 15 if r >= Decimal("0.5") else 10 if r >= Decimal("0.2") else 5 if r >= Decimal("0.1") else 0
    return {"materiality": materiality, "amount": amount, "relative": relative}


def _pp_points(change: Decimal) -> dict[str, int]:
    size = abs(change)
    points = (
        60
        if size >= 5
        else 45
        if size >= 2
        else 30
        if size >= 1
        else 15
        if size >= Decimal("0.5")
        else 3
        if size > 0
        else 0
    )
    return {"percentage_points": points}


def _reason(
    parts: dict[str, int], change: Decimal, unit: str, base: Decimal, previous: Decimal, base_label: str
) -> str:
    """Kort motivering till poängen, t.ex. "8,3 % av omsättningen, +46,0 % mot jämförelsen"."""
    bits: list[str] = []
    if unit == "SEK":
        if base:
            bits.append(f"{format_percent(_q(abs(change) / base * 100))} av {base_label}")
        if previous == 0 and change:
            bits.append("ny post")
        elif (pct := _pct_change(previous + change, previous)) is not None:
            bits.append(f"{format_percent(pct, signed=True)} mot jämförelsen")
    else:
        bits.append(fmt_change(change, unit))
    if parts.get("headline"):
        bits.append("huvudnyckeltal")
    if parts.get("quality", 0) < 0:
        bits.append("preliminärt underlag")
    return ", ".join(b for b in bits if b)


# ---------------------------------------------------------------------------- poster


def _metric_item(code: str, explanation: MetricExplanation, bases: dict[str, tuple[Decimal, str]]) -> DifferenceItem:
    base, base_label = bases["balance" if code in BALANCE_METRICS else "income"]
    definition = REGISTRY[code]
    unit = definition.unit.value
    status = explanation.status
    current, previous, change = explanation.current, explanation.previous, explanation.change
    if status not in USABLE or change is None or current is None or previous is None:
        return DifferenceItem(
            f"metric:{code}",
            "metric",
            code,
            definition.name,
            f"{definition.name}: jämförelse saknas ({'; '.join(explanation.warnings) or status.value}).",
            unit,
            current,
            previous,
            None,
            None,
            status,
            0,
            {},
            "Underlaget räcker inte för en jämförelse.",
            "client",
            definition.better,
            list(explanation.warnings),
        )
    parts = _sek_points(change, previous, base) if unit == "SEK" else _pp_points(change)
    parts["headline"] = 5 if code in CORE_METRICS else 0
    parts["quality"] = -10 if status is FactStatus.PARTIAL else 0
    if unit == "SEK":
        summary = (
            f"{definition.name} {_verb(change)} från {fmt_value(previous, unit)} till {fmt_value(current, unit)} "
            f"({fmt_change(change, unit)}"
            + (f", {format_percent(pct, signed=True)}" if (pct := _pct_change(current, previous)) is not None else "")
            + ")."
        )
    else:
        summary = (
            f"{definition.name} {_verb(_q(change))} från {fmt_value(previous, unit)} till {fmt_value(current, unit)} "
            f"({fmt_change(change, unit)})."
        )
    drivers = sorted((c for c in explanation.components if c.effect), key=lambda c: -abs(c.effect))
    if len(drivers) > 1:
        shown = [c for c in drivers[:2] if abs(c.effect) >= abs(drivers[0].effect) / 10]
        text = ", ".join(
            f"{'nämnaren ' + c.label.lower() if c.role == 'denominator' else _lower_first(c.label)} "
            f"({fmt_change(c.effect, 'pp' if unit != 'SEK' else 'SEK')})"
            for c in shown
        )
        summary += f" Största poster i bryggan: {text}."
    accounts = tuple(sorted({a for c in explanation.components for a in (*c.current_accounts, *c.previous_accounts)}))
    return DifferenceItem(
        f"metric:{code}",
        "metric",
        code,
        definition.name,
        summary,
        unit,
        current,
        previous,
        change,
        _pct_change(current, previous) if unit == "SEK" else None,
        status,
        sum(parts.values()),
        parts,
        _reason(parts, change, unit, base, previous, base_label),
        "client",
        definition.better,
        list(explanation.warnings),
        accounts,
    )


def _line_item(
    statement_kind: str,
    code: str,
    label: str,
    current: Decimal,
    previous: Decimal,
    accounts_now: dict[int, Decimal],
    accounts_before: dict[int, Decimal],
    bases: dict[str, tuple[Decimal, str]],
    status: FactStatus,
    names: dict[int, str],
    hidden: AccountSet | None,
) -> DifferenceItem:
    base, base_label = bases["balance" if statement_kind == "balance" else "income"]
    change = current - previous
    parts = _sek_points(change, previous, base)
    parts["quality"] = -10 if status is FactStatus.PARTIAL else 0
    parts["detail"] = -5  # en rad är en nivå under nyckeltalen; nyckeltalet går före vid lika poäng
    if statement_kind == "income" and code not in INCOME_SIDE:
        # Kostnader är negativa i uppställningen: beskriv storleken, visa resultatpåverkan.
        size_now, size_before = -current, -previous
        summary = (
            f"{label} {_verb(size_now - size_before)} från {format_sek(size_before)} till {format_sek(size_now)}"
            + (
                f" ({format_percent(pct, signed=True)})"
                if (pct := _pct_change(size_now, size_before)) is not None
                else ""
            )
            + f"; påverkan på resultatet {format_sek(change, signed=True)}."
        )
        role = "cost"
    else:
        summary = (
            f"{label} {_verb(change)} från {format_sek(previous)} till {format_sek(current)} "
            f"({format_sek(change, signed=True)}"
            + (f", {format_percent(pct, signed=True)}" if (pct := _pct_change(current, previous)) is not None else "")
            + ")."
        )
        role = "income" if statement_kind == "income" else "asset" if code in ASSET_LINES else "liability"
    diffs = {
        a: accounts_now.get(a, ZERO) - accounts_before.get(a, ZERO) for a in set(accounts_now) | set(accounts_before)
    }
    visible = {a: d for a, d in diffs.items() if d and (hidden is None or a not in hidden)}
    if visible and len(diffs) > 1:
        top = max(visible, key=lambda a: (abs(visible[a]), -a))
        if abs(visible[top]) >= abs(change) * Decimal("0.3"):
            lead = "Störst påverkan" if role == "cost" else "Störst förändring"
            account = f"konto {top} {names.get(top, '')}".rstrip()
            summary += f" {lead}: {account} ({format_sek(visible[top], signed=True)})."
    return DifferenceItem(
        f"line:{statement_kind}:{code}",
        "line",
        code,
        label,
        summary,
        "SEK",
        current,
        previous,
        change,
        _pct_change(current, previous),
        status,
        sum(parts.values()),
        parts,
        _reason(parts, change, "SEK", base, previous, base_label),
        "client",
        "neutral",
        [],
        tuple(sorted(diffs)),
        extra={"statement": statement_kind, "role": role},
    )


def collect_differences(
    index: LedgerIndex,
    pair: ComparisonPair,
    *,
    mapping: StatementMapping,
    categories: CategoryMapping,
    rates: RateTable,
    aliases: dict[str, str] | None = None,
    hidden_accounts: AccountSet | None = None,
    include_findings: bool = True,
    recommend: int = DEFAULT_RECOMMENDED,
) -> DifferenceSet:
    """Alla jämförbara skillnader för periodparet, rangordnade, med de viktigaste markerade."""
    store = FactStore()
    explanations = {
        code: explain_metric(code, index, pair, mapping=mapping, rates=rates, store=store) for code in REGISTRY
    }
    cur_inc = income_statement(index, pair.current, pair.previous, mapping)
    bal = balance_sheet(index, pair.current.end, pair.previous.end, mapping)
    # Väsentlighet mäts mot omsättningen för resultatposter och mot balansomslutningen för balansposter.
    base = max(abs(cur_inc.line("net_sales").amount), abs(cur_inc.line("net_sales").compare or ZERO))
    base_label = "omsättningen"
    if base == 0:
        costs = sum((abs(ln.amount) for ln in cur_inc.lines if ln.level == 1 and ln.code not in INCOME_SIDE), ZERO)
        base, base_label = (costs, "kostnaderna") if costs else (Decimal(1), "omsättningen")
    total_assets = max(abs(bal.line("total_assets").amount), abs(bal.line("total_assets").compare or ZERO))
    bases = {
        "income": (base, base_label),
        "balance": (total_assets, "balansomslutningen") if total_assets else (base, base_label),
    }
    names = {a: acc.name for a, acc in index.ledger.accounts.items()}
    items: list[DifferenceItem] = [_metric_item(code, explanations[code], bases) for code in REGISTRY]
    comparable = pair.status in USABLE
    period_status = (
        FactStatus.PARTIAL
        if any(e.status is FactStatus.PARTIAL for e in explanations.values())
        else FactStatus.CALCULATED
    )
    if comparable:
        for definition in INCOME_LINES:
            if definition.accounts is None or definition.code == "net_sales":
                continue
            line = cur_inc.line(definition.code)
            previous = line.compare or ZERO
            if line.amount == 0 and previous == 0:
                continue
            items.append(
                _line_item(
                    "income",
                    line.code,
                    line.label,
                    line.amount,
                    previous,
                    line.accounts,
                    line.compare_accounts,
                    bases,
                    period_status,
                    names,
                    hidden_accounts,
                )
            )
        if bal.complete:
            for definition in BALANCE_LINES:
                if definition.accounts is None or definition.code == "cash":
                    continue
                line = bal.line(definition.code)
                previous = line.compare or ZERO
                if line.amount == 0 and previous == 0:
                    continue
                items.append(
                    _line_item(
                        "balance",
                        line.code,
                        line.label,
                        line.amount,
                        previous,
                        line.accounts,
                        line.compare_accounts,
                        bases,
                        FactStatus.CALCULATED,
                        names,
                        hidden_accounts,
                    )
                )
        items.extend(
            _category_items(index, pair, categories, mapping, bases["income"], period_status, hidden_accounts, names)
        )
        if include_findings:
            items.extend(_finding_items(index, pair, list(explanations.values()), mapping, aliases))
    if hidden_accounts is not None:
        # Fynd som bygger på lönekonton visas bara för den som har behörigheten Lönedata.
        items = [i for i in items if not (i.kind == "finding" and any(a in hidden_accounts for a in i.accounts))]
    _mark_recommended(items, recommend)
    items.sort(key=lambda i: (not i.selectable, -i.score, KIND_ORDER[i.kind], i.id))
    return DifferenceSet(
        pair,
        items,
        explanations,
        base,
        {"differences": DIFFERENCES_VERSION, "mapping": mapping.version, "categories": categories.version},
    )


def _category_items(
    index: LedgerIndex,
    pair: ComparisonPair,
    categories: CategoryMapping,
    mapping: StatementMapping,
    income_base: tuple[Decimal, str],
    status: FactStatus,
    hidden: AccountSet | None,
    names: dict[int, str],
) -> list[DifferenceItem]:
    base, base_label = income_base
    line_sets = {
        frozenset(mapping.accounts_for(d.code, index.accounts_used)) for d in INCOME_LINES if d.accounts is not None
    }
    out: list[DifferenceItem] = []
    for category in categories.children(None):
        accounts = categories.accounts_in(category.code, index.accounts_used)
        if not accounts or frozenset(accounts) in line_sets:
            continue  # samma konton som en resultatrad – visas redan där
        now_by = index.movement_by_account(AccountSet.of(*accounts), pair.current)
        before_by = index.movement_by_account(AccountSet.of(*accounts), pair.previous)
        current, previous = sum(now_by.values(), ZERO), sum(before_by.values(), ZERO)
        if current == 0 and previous == 0:
            continue
        change = current - previous
        parts = _sek_points(change, previous, base)
        parts["quality"] = -10 if status is FactStatus.PARTIAL else 0
        summary = (
            f"Kostnaderna för {_lower_first(category.name)} {_verb(change)} från {format_sek(previous)} till "
            f"{format_sek(current)}"
            + (f" ({format_percent(pct, signed=True)})" if (pct := _pct_change(current, previous)) is not None else "")
            + "."
        )
        diffs = {a: now_by.get(a, ZERO) - before_by.get(a, ZERO) for a in set(now_by) | set(before_by)}
        visible = {a: d for a, d in diffs.items() if d and (hidden is None or a not in hidden)}
        if len(diffs) > 1 and visible:
            top = max(visible, key=lambda a: (abs(visible[a]), -a))
            account = f"konto {top} {names.get(top, '')}".rstrip()
            summary += f" Störst förändring: {account} ({format_sek(visible[top], signed=True)})."
        out.append(
            DifferenceItem(
                f"category:{category.code}",
                "category",
                category.code,
                f"Kostnader: {category.name}",
                summary,
                "SEK",
                current,
                previous,
                change,
                _pct_change(current, previous),
                status,
                sum(parts.values()),
                parts,
                _reason(parts, change, "SEK", base, previous, base_label),
                "client",
                "lower",
                [],
                tuple(sorted(diffs)),
                extra={"role": "cost_category", "accounts": sorted(accounts)},
            )
        )
    return out


def _finding_items(
    index: LedgerIndex,
    pair: ComparisonPair,
    explanations: list[MetricExplanation],
    mapping: StatementMapping,
    aliases: dict[str, str] | None,
) -> list[DifferenceItem]:
    candidates = collect_candidates(index, pair, explanations, mapping_version=mapping.version, aliases=aliases)
    patterns = [c for c in candidates if c.code in PATTERN_FINDINGS]
    if not patterns:
        return []
    ranked = rank_findings(patterns, limit=min(5, len(patterns)))
    out: list[DifferenceItem] = []
    for candidate in (*ranked.top, *ranked.others):
        accounts = tuple(
            sorted(
                {
                    int(a)
                    for source in candidate.sources
                    for a in str(source.get("accounts", "")).split(",")
                    if a.strip().isdigit()
                }
            )
        )
        refs = [
            r
            for source in candidate.sources
            for r in (source.get("references") if isinstance(source.get("references"), list) else [])  # type: ignore[union-attr]
        ]
        unit = "SEK" if candidate.unit == "SEK" else "count"
        amount = (
            fmt_value(candidate.amount_effect, unit, signed=True) if unit == "SEK" else f"{candidate.amount_effect:+}"
        )
        out.append(
            DifferenceItem(
                f"finding:{candidate.group_key}",
                "finding",
                candidate.code,
                candidate.label,
                f"{candidate.label}: {amount}. {candidate.warnings[0] if candidate.warnings else ''}".strip(),
                unit,
                None,
                None,
                candidate.amount_effect,
                None,
                FactStatus.PARTIAL if candidate.warnings else FactStatus.CALCULATED,
                candidate.priority_score,
                dict(candidate.score_parts),
                "Regelbaserat analysfynd – kontrollera verifikationerna innan slutsats.",
                "internal",
                None,
                list(candidate.warnings),
                accounts,
                extra={"references": refs[:10], "source_level": candidate.source_level},
            )
        )
    return out


def _mark_recommended(items: Iterable[DifferenceItem], limit: int) -> None:
    """Föreslå de `limit` starkaste posterna, högst en per område."""
    ranked = sorted(
        (i for i in items if i.selectable and i.score > 0),
        key=lambda i: (-i.score, KIND_ORDER[i.kind], i.id),
    )
    chosen: dict[str, DifferenceItem] = {}
    for item in ranked:
        family = FAMILIES.get(item.id, item.id)
        item.extra["family"] = family
        if family in chosen:
            leader = chosen[family]
            item.extra["not_recommended"] = f"Samma område ({FAMILY_LABELS.get(family, family)}) som {leader.title}."
            continue
        if len(chosen) >= max(0, limit):
            continue
        chosen[family] = item
        item.recommended = True
