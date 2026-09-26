"""Evidens för AI:ns utredning av varför ett nyckeltal ändrats (plan §9.7–9.8).

Bygger på den deterministiska förklaringsmotorn (`explain_metric`). Varje siffra som lämnas ut
registreras som ett Fact i samma FactStore som verifieraren använder, så AI:n kan hänvisa till
beloppen men aldrig räkna själv. Bidragen summerar exakt till förändringen (inklusive en
`övriga`-rad när bara de största visas). Lönekonton lämnas bara ut aggregerat.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from redovisningai.accounting.comparisons import validate_comparison
from redovisningai.accounting.metric_explanations import MetricComponent, explain_metric
from redovisningai.accounting.periods import Period
from redovisningai.facts.model import Fact, FactStatus, FactStore, Unit
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis

MAX_COMPONENTS = 5
MAX_ACCOUNTS = 3
ZERO = Decimal(0)
NOTE = (
    "Bidragen summerar exakt till förändringen. Ett bidrag är en bokföringsmässig effekt, "
    "inte en bevisad affärsorsak; möjliga orsaker ska märkas som hypoteser."
)


def _ref(fact: Fact | None) -> dict[str, Any] | None:
    if fact is None:
        return None
    return {"fact_id": fact.id, "label": fact.label, "display": fact.to_dict()["display"], "status": fact.status.value}


def _change_unit(unit: Unit) -> Unit:
    return Unit.PP if unit is Unit.PERCENT else unit


def _accounts(
    analysis: CompanyAnalysis, component: MetricComponent, current: Period, previous: Period, store: FactStore
) -> dict[str, Any]:
    """Största kontoförändringarna i komponenten; lönekonton och resten bara som summor."""
    deltas = {
        account: component.current_accounts.get(account, ZERO) - component.previous_accounts.get(account, ZERO)
        for account in set(component.current_accounts) | set(component.previous_accounts)
    }
    payroll = sum((d for a, d in deltas.items() if a in PAYROLL), ZERO)
    has_payroll = any(a in PAYROLL for a in deltas)
    visible = sorted((a for a, d in deltas.items() if a not in PAYROLL and d != 0), key=lambda a: (-abs(deltas[a]), a))
    shown, rest = visible[:MAX_ACCOUNTS], visible[MAX_ACCOUNTS:]

    def amount(subject: str, label: str, value: Decimal, period: Period, compare: Period | None = None) -> Fact:
        return store.new(
            "amount",
            subject,
            label,
            value,
            Unit.SEK,
            period=period.spec,
            compare_period=compare.spec if compare else None,
            lineage={"component": component.code},
        )

    accounts = []
    for account in shown:
        name = f"{account} {analysis.ledger.account_name(account)}".strip()
        accounts.append(
            {
                "account": account,
                "name": name,
                "change": _ref(
                    amount(f"account:{account}:change", f"Förändring {name}", deltas[account], current, previous)
                ),
                "current": _ref(
                    amount(f"account:{account}", name, component.current_accounts.get(account, ZERO), current)
                ),
                "previous": _ref(
                    amount(f"account:{account}", name, component.previous_accounts.get(account, ZERO), previous)
                ),
            }
        )
    other = sum((deltas[a] for a in rest), ZERO)
    return {
        "accounts": accounts,
        "other_accounts": {
            "count": len(rest),
            "change": _ref(amount(f"{component.code}:other_accounts", "Övriga konton", other, current, previous)),
        }
        if rest
        else None,
        "payroll_accounts": {
            "change": _ref(amount(f"{component.code}:payroll", "Lönekonton (aggregerat)", payroll, current, previous))
        }
        if has_payroll
        else None,
    }


def metric_change_evidence(
    analysis: CompanyAnalysis, code: str, current: Period, previous: Period, store: FactStore
) -> dict[str, Any]:
    pair = validate_comparison(current, previous, analysis.index)
    explanation = explain_metric(
        code, analysis.index, pair, mapping=analysis.ctx.statement_mapping, rates=analysis.rates, store=store
    )
    current_fact, previous_fact = (store.get(fid) for fid in explanation.fact_ids[:2])
    out: dict[str, Any] = {
        "metric": code,
        "label": explanation.label,
        "status": explanation.status.value,
        "periods": explanation.periods,
        "warnings": list(explanation.warnings),
        "current": _ref(current_fact),
        "previous": _ref(previous_fact),
        "change": None,
        "components": [],
        "other_components": None,
        "note": NOTE,
    }
    if explanation.change is None or explanation.status not in (FactStatus.CALCULATED, FactStatus.PARTIAL):
        return out

    unit = _change_unit(explanation.unit)
    out["change"] = _ref(
        store.new(
            "change",
            f"metric:{code}:change",
            f"Förändring {explanation.label.lower()}",
            explanation.change,
            unit,
            period=current.spec,
            compare_period=previous.spec,
            status=explanation.status,
        )
    )
    ranked = sorted(explanation.components, key=lambda c: (-abs(c.effect), c.code))
    shown, rest = ranked[:MAX_COMPONENTS], ranked[MAX_COMPONENTS:]
    for position, component in enumerate(shown):
        effect = store.new(
            "variance_component",
            f"metric:{code}:{component.code}",
            component.label,
            component.effect,
            component.unit,
            period=current.spec,
            compare_period=previous.spec,
            lineage={"metric": code, "component": component.code, "source_level": component.source_level},
            extra=str(position),
        )
        out["components"].append(
            {
                "code": component.code,
                "label": component.label,
                "effect": _ref(effect),
                "source_level": component.source_level,
                "note": component.note,
                **_accounts(analysis, component, current, previous, store),
            }
        )
    if rest:
        other = store.new(
            "variance_component",
            f"metric:{code}:other_components",
            "Övriga bidrag",
            sum((c.effect for c in rest), ZERO),
            unit,
            period=current.spec,
            compare_period=previous.spec,
            lineage={"metric": code, "components": [c.code for c in rest]},
        )
        out["other_components"] = {"count": len(rest), "effect": _ref(other)}
    return out
