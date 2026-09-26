"""Granskare (V): kontrollerar varje AI-påstående innan det visas.

Regler (deterministiska):
1. Varje fact_id måste finnas i paketet som skickades, och i kundriktad text vara CLIENT_SAFE.
2. Siffror får inte skrivas som text (belopp, procent, stora tal). De ska komma via {f:id}.
3. OBSERVATION och EXPLANATION kräver minst ett fact_id.
4. Orsaksord ("på grund av", "beror på" …) kräver EXPLANATION med avvikelsekomponent – annars
   nedgraderas påståendet till HYPOTHESIS.
5. Inga länkar, bilder eller HTML (skydd mot dataexfiltration via rendering).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from redovisningai.facts.model import PLACEHOLDER_RE, FactStore, Visibility, render_text

CLAIM_TYPES = ("OBSERVATION", "EXPLANATION", "HYPOTHESIS", "QUESTION")

CAUSAL = re.compile(
    r"\b(på grund av|beror på|berodde på|orsakad[e]? av|orsakas av|orsaken är|ledde till|leder till|"
    r"eftersom|till följd av|drivs av|drevs av|förklaras av)\b",
    re.I,
)
# Tal som ser ut som belopp/procent/mängd. Tillåtna: årtal, kontonummer och id:n i paketet.
NUMBER = re.compile(r"(?<![\w{:])[-+−]?\d[\d\s .,]*\d?(?:\s?(?:%|procent|kr|tkr|mkr|msek|sek|kronor))?", re.I)
UNIT_AFTER = re.compile(r"^\s?(%|procent|kr|tkr|mkr|msek|sek|kronor)", re.I)
UNSAFE = re.compile(r"(https?://|www\.|!\[|\]\(|<\s*img|<\s*a\s|<\s*script|javascript:)", re.I)

CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(CLAIM_TYPES)},
        "text": {"type": "string"},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "text", "fact_ids"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class Rejection:
    claim: dict[str, Any]
    reason: str
    # Skäl utan innehåll (t.ex. "literal_number") som kan sparas i AI-spåret även när texten inte får det.
    code: str = "other"


@dataclass(slots=True)
class VerificationResult:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    downgraded: int = 0

    @property
    def ok(self) -> bool:
        return not self.rejected

    def feedback(self) -> str:
        return "\n".join(f'- "{r.claim.get("text", "")[:120]}": {r.reason}' for r in self.rejected)


def _allowed_number(token: str, allowed: set[str]) -> bool:
    t = token.strip().rstrip(".,")
    digits = re.sub(r"[^\d]", "", t)
    if not digits:
        return True
    if t in allowed or digits in allowed:
        return True
    if re.fullmatch(r"(19|20)\d{2}", t):  # årtal
        return True
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", t):  # datum/period
        return True
    return len(digits) <= 2 and not UNIT_AFTER.search(token)


def find_literal_numbers(text: str, allowed: set[str]) -> list[str]:
    without = PLACEHOLDER_RE.sub(" ", text)
    bad = []
    for m in NUMBER.finditer(without):
        tok = m.group(0)
        if not re.search(r"\d", tok):
            continue
        has_unit = bool(re.search(r"(%|procent|kr|tkr|mkr|msek|sek|kronor)\s*$", tok, re.I))
        if has_unit or not _allowed_number(tok, allowed):
            bad.append(tok.strip())
    return bad


def verify_claims(
    claims: list[dict[str, Any]],
    store: FactStore,
    *,
    client_facing: bool = False,
    allowed_identifiers: set[str] | None = None,
) -> VerificationResult:
    allowed = set(allowed_identifiers or set())
    res = VerificationResult()
    for raw in claims:
        claim = {
            "type": str(raw.get("type", "")).upper(),
            "text": str(raw.get("text", "")),
            "fact_ids": [str(x) for x in raw.get("fact_ids", [])],
        }
        text = claim["text"]
        if claim["type"] not in CLAIM_TYPES:
            res.rejected.append(Rejection(claim, "okänd påståendetyp", "unknown_type"))
            continue
        if UNSAFE.search(text):
            res.rejected.append(Rejection(claim, "länkar, bilder eller HTML är inte tillåtna", "unsafe_content"))
            continue
        referenced = set(PLACEHOLDER_RE.findall(text)) | set(claim["fact_ids"])
        unknown = [f for f in referenced if f not in store]
        if unknown:
            res.rejected.append(Rejection(claim, f"okända fakta-id: {', '.join(sorted(unknown))}", "unknown_fact"))
            continue
        if client_facing:
            internal = [f for f in referenced if store.get(f).visibility is not Visibility.CLIENT_SAFE]  # type: ignore[union-attr]
            if internal:
                res.rejected.append(Rejection(claim, "interna uppgifter får inte användas i kundtext", "internal_fact"))
                continue
        literal = find_literal_numbers(text, allowed)
        if literal:
            res.rejected.append(
                Rejection(
                    claim,
                    "siffror skrivna som text: " + ", ".join(literal[:3]) + " – använd {f:id}",
                    "literal_number",
                )
            )
            continue
        if claim["type"] in ("OBSERVATION", "EXPLANATION") and not referenced:
            res.rejected.append(Rejection(claim, f"{claim['type']} kräver minst ett fakta-id", "missing_fact"))
            continue
        if CAUSAL.search(text) and claim["type"] != "HYPOTHESIS":
            has_component = any(store.get(f).kind == "variance_component" for f in referenced)  # type: ignore[union-attr]
            if claim["type"] != "EXPLANATION" or not has_component:
                claim["type"] = "HYPOTHESIS"
                res.downgraded += 1
        claim["fact_ids"] = sorted(referenced)
        res.accepted.append(claim)
    return res


def render_claims(claims: list[dict[str, Any]], store: FactStore) -> list[dict[str, Any]]:
    return [{**c, "rendered": render_text(c["text"], store)} for c in claims]
