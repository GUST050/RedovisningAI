"""Källbunden periodanalys för rapporter, beräknad lokalt från bokföringen.

Texten beskriver bokförda resultateffekter och motpartsgruppernas mönster. Den
påstår inte pris-, volym- eller affärsorsaker som SIE-underlaget inte kan styrka.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from redovisningai.accounting.comparisons import validate_comparison
from redovisningai.accounting.periods import Period
from redovisningai.accounting.transaction_bridge import (
    TransactionBridge,
    bridge_unavailable_reason,
    transaction_bridge,
)
from redovisningai.accounting.variance import BridgeComponent, result_bridge
from redovisningai.facts.model import FactStatus, FactStore, format_sek
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis

MAX_DRIVERS = 4
EXPENSE_LINES = {"materials", "other_external", "personnel", "depreciation", "other_operating_expenses"}
REPORT_LABELS = {
    "net_sales": "Nettoomsättningen",
    "materials": "Råvarukostnaderna",
    "other_external": "Övriga externa kostnader",
    "personnel": "Personalkostnaderna",
    "depreciation": "Avskrivningarna",
}
REPORT_NOUNS = {
    "materials": "råvarukostnader",
    "other_external": "övriga externa kostnader",
    "personnel": "personalkostnader",
    "depreciation": "avskrivningar",
    "other_operating_expenses": "övriga rörelsekostnader",
}
PART_LABELS = {
    "current_only": "grupper som bara syns i aktuell period",
    "previous_only": "grupper som bara syns i jämförelseperioden",
    "both": "grupper som syns i båda perioderna",
    "unknown": "poster utan identifierad motpart",
}


@dataclass(frozen=True, slots=True)
class ReportAnalysis:
    summary: str | None
    drivers: tuple[str, ...]
    limitation: str | None = None


def _label(component: BridgeComponent) -> str:
    return REPORT_LABELS.get(component.code, component.label)


def _contribution(component: BridgeComponent) -> str:
    label = _label(component)
    if component.code in EXPENSE_LINES:
        direction = "minskade" if component.effect > 0 else "ökade"
        noun = REPORT_NOUNS.get(component.code, component.label.lower())
        return f"{direction} {noun} ({format_sek(component.effect, signed=True)})"
    return f"{label.lower()} ({format_sek(component.effect, signed=True)})"


def _join(items: list[str]) -> str:
    if len(items) < 2:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " och " + items[-1]


def _result_summary(current: Decimal, previous: Decimal, components: list[BridgeComponent]) -> str:
    change = current - previous
    if change > 0:
        opening = f"Rörelseresultatet förbättrades från {format_sek(previous)} till {format_sek(current)}, en ökning med {format_sek(change)}."
    elif change < 0:
        opening = f"Rörelseresultatet försämrades från {format_sek(previous)} till {format_sek(current)}, en minskning med {format_sek(abs(change))}."
    else:
        opening = f"Rörelseresultatet var {format_sek(current)}, oförändrat mot jämförelseperioden."
    positive = [_contribution(c) for c in components if c.effect > 0]
    negative = [_contribution(c) for c in components if c.effect < 0]
    if change < 0:
        if negative:
            opening += f" De största negativa bokföringsbidragen var {_join(negative)}."
        if positive:
            opening += f" Positiva bidrag som dämpade försämringen var {_join(positive)}."
    else:
        if positive:
            opening += f" De största positiva bokföringsbidragen var {_join(positive)}."
        if negative:
            opening += f" Motverkande bidrag var {_join(negative)}."
    remaining = change - sum((part.effect for part in components), Decimal(0))
    if remaining:
        opening += f" Övriga resultatrader bidrog netto med {format_sek(remaining, signed=True)}."
    return opening


def _driver_intro(component: BridgeComponent) -> str:
    label = _label(component)
    if component.code in EXPENSE_LINES:
        direction = "minskade" if component.effect > 0 else "ökade"
        result = "förbättrade" if component.effect > 0 else "försämrade"
        return f"{label} {direction} med {format_sek(abs(component.effect))}, vilket {result} rörelseresultatet med samma belopp."
    direction = "ökade" if component.effect > 0 else "minskade"
    return f"{label} {direction} med {format_sek(abs(component.effect))} och bidrog med {format_sek(component.effect, signed=True)} till rörelseresultatet."


def _transaction_detail(bridge: TransactionBridge, *, expense: bool) -> str:
    threshold = max(Decimal("1000"), abs(bridge.change) * Decimal("0.02"))
    all_parts = sorted(
        (part for part in bridge.parts if part.effect),
        key=lambda part: abs(part.effect),
        reverse=True,
    )
    if not all_parts:
        return "Inga bokförda transaktioner förklarar någon ytterligare skillnad."
    parts = [part for part in all_parts if abs(part.effect) >= threshold] or all_parts[:1]
    largest, *other = parts
    count = largest.current_count if largest.code == "current_only" else largest.previous_count
    count_text = (
        f", fördelat på {count} {'verifikation' if count == 1 else 'verifikationer'}"
        if count and largest.code != "both"
        else ""
    )
    line = "kostnadsraden" if expense else "intäktsraden"
    text = (
        f"Den största delposten på {line} är {format_sek(largest.effect, signed=True)} "
        f"hos {PART_LABELS[largest.code]}{count_text}."
    )
    if other:
        opposing = [part for part in other if part.effect * largest.effect < 0]
        supporting = [part for part in other if part.effect * largest.effect > 0]
        if opposing:
            text += (
                " Den motverkas av "
                + _join([f"{format_sek(part.effect, signed=True)} hos {PART_LABELS[part.code]}" for part in opposing])
                + "."
            )
        if supporting:
            text += (
                " I samma riktning bidrar "
                + _join([f"{format_sek(part.effect, signed=True)} hos {PART_LABELS[part.code]}" for part in supporting])
                + "."
            )
    both = next((part for part in parts if part.code == "both"), None)
    if both and both.count_effect is not None and both.amount_effect is not None:
        effects = []
        if both.count_effect:
            effects.append(f"antalseffekten ({format_sek(both.count_effect, signed=True)})")
        if both.amount_effect:
            effects.append(f"ändrat genomsnittsbelopp per verifikation ({format_sek(both.amount_effect, signed=True)})")
        if effects:
            text += f" Hos grupper i båda perioderna består skillnaden av {_join(effects)}."
    if bridge.identified_share_abs.get("unknown", Decimal(0)) >= Decimal("0.20"):
        text += " En betydande del saknar identifierad motpart; tolka grupperingen försiktigt."
    return text


def build_report_analysis(analysis: CompanyAnalysis, current: Period, previous: Period) -> ReportAnalysis:
    """Prioritera resultatbidrag och förklara dem med samma exakta brygga som detaljvyn."""
    pair = validate_comparison(current, previous, analysis.index)
    if pair.status is not FactStatus.CALCULATED:
        return ReportAnalysis(
            None,
            (),
            "Perioderna kan inte analyseras som ett fullständigt jämförbart par: " + " ".join(pair.warnings),
        )

    result = result_bridge(analysis.index, current, previous, mapping=analysis.ctx.statement_mapping)
    ranked = sorted(
        (part for part in result.components if part.effect), key=lambda part: abs(part.effect), reverse=True
    )
    selected = ranked[:MAX_DRIVERS]
    summary = _result_summary(result.current_total, result.previous_total, selected)
    unavailable = bridge_unavailable_reason(analysis.index, pair)
    drivers: list[str] = []
    for component in selected:
        paragraph = _driver_intro(component)
        if unavailable is None:
            target = f"line:{component.code}"
            accounts, sign = analysis.target_accounts(target)
            safe_accounts = {account for account in accounts if account not in PAYROLL}
            if safe_accounts and safe_accounts == accounts:
                bridge = transaction_bridge(
                    analysis.index,
                    safe_accounts,
                    pair,
                    target=target,
                    aliases=analysis.ctx.aliases,
                    sign=sign,
                    store=FactStore(),
                )
                if component.effect != -sign * bridge.change:
                    raise ValueError(f"Rapportens transaktionsbrygga stämmer inte med resultatraden {target}")
                paragraph += " " + _transaction_detail(bridge, expense=component.code in EXPENSE_LINES)
        drivers.append(paragraph)
    limitation = (
        f"Transaktionsförklaring saknas: {unavailable}."
        if unavailable
        else "Motpartsgrupperna bygger på bekräftade alias eller bokföringstext. Plus på en kostnadsrad betyder högre bokförd kostnad. Grupperna visar bokföringsmönster, inte säkert varför affären förändrades. Mindre delposter ingår i nettot men utelämnas i löptexten. Belopp i löptext är avrundade; bilagorna visar kronor och ören."
    )
    return ReportAnalysis(summary, tuple(drivers), limitation)
