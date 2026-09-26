"""Underlag för A7 (veckans prioriteringar).

Poäng och antal fynd blir fakta i en FactStore så att AI:n kan hänvisa till dem med {f:id};
skälen skickas som koder med etiketter utan siffror, så att modellen inte frestas skriva egna tal
(verifieraren underkänner siffror i klartext).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from redovisningai.facts.model import Fact, FactStore, Unit

MAX_COMPANIES = 15
REASON_LABELS = {
    "high": "allvarliga fynd",
    "medium": "fynd med medelhög allvarlighet",
    "low": "mindre fynd",
    "changed_after_approval": "ändrad efter godkännande",
    "margin_drop": "sjunkande rörelsemarginal",
    "stale_review": "inte granskad på länge",
    "missing_data": "saknar data för senaste månaden",
    "connection_error": "kopplingen fungerar inte",
    "unanswered_question": "obesvarade kundfrågor äldre än sju dagar",
    "preliminary": "preliminär period",
}
FINDING_COUNT_REASONS = ("high", "medium", "low")  # skälkod = nyckel i open_findings


def _ref(fact: Fact) -> dict[str, Any]:
    return {"fact_id": fact.id, "display": fact.to_dict()["display"]}


def _reason(company: dict[str, Any], reason: dict[str, Any], store: FactStore) -> dict[str, Any]:
    code = str(reason.get("code", ""))
    label = REASON_LABELS.get(code, "övrigt skäl")
    entry: dict[str, Any] = {"code": code, "label": label}
    count = company.get("open_findings", {}).get(code) if code in FINDING_COUNT_REASONS else None
    if count is not None:
        fact = store.new("count", f"open_findings:{code}:{company['name']}", label, Decimal(count), Unit.COUNT)
        entry.update(_ref(fact))
    return entry


def brief_package(companies: list[dict[str, Any]], store: FactStore) -> dict[str, Any]:
    out = []
    for company in companies[:MAX_COMPANIES]:
        score = store.new(
            "count",
            f"priority:{company['name']}",
            f"Prioriteringspoäng {company['name']}",
            Decimal(company["priority"]["score"]),
            Unit.COUNT,
        )
        out.append(
            {
                "name": company["name"],
                "score": _ref(score),
                "reasons": [_reason(company, r, store) for r in company["priority"]["reasons"]],
            }
        )
    return {"companies": out, "allowed": []}
