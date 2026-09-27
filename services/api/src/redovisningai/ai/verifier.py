"""Granskare (V): kontrollerar varje AI-påstående innan det visas.

Regler (deterministiska):
1. Varje fact_id måste finnas i paketet som skickades, och i kundriktad text vara CLIENT_SAFE.
2. Siffror får inte skrivas som text (belopp, procent, stora tal). De ska komma via {f:id}.
3. OBSERVATION och EXPLANATION kräver minst ett fact_id.
4. Orsaksord ("på grund av", "beror på" …) kräver EXPLANATION med avvikelsekomponent – annars
   nedgraderas påståendet till HYPOTHESIS.
5. Inga länkar, bilder eller HTML (skydd mot dataexfiltration via rendering).
6. Motparter skrivs {m:Mx} och bara med koder som delats ut i samma körning. I kundtext underkänns
   varje {m:…} och varje fristående utdelad kod ("M1", "M1s"): de blir aldrig namn där.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from redovisningai.ai.pseudonymize import WORD_END
from redovisningai.facts.model import PLACEHOLDER_RE, FactStore, Visibility, render_text

CLAIM_TYPES = ("OBSERVATION", "EXPLANATION", "HYPOTHESIS", "QUESTION")

CAUSAL = re.compile(
    r"\b(på grund av|beror på|berodde på|orsakad[e]? av|orsakas av|orsaken är|ledde till|leder till|"
    r"eftersom|till följd av|tack vare|med anledning av|drivs av|drevs av|förklaras av)\b",
    re.I,
)
# Affärshändelser som bokföringen (SIE) inte kan belägga: pris, volym, kunder, leverantörer,
# order/avtal, personalstyrka och omvärld. En korrekt resultatbrygga bevisar inte sådant (plan §9.8).
# Kontonamn som kundfordringar, leverantörsskulder, kundförluster och marknadsföring matchar inte.
BUSINESS_CAUSE = re.compile(
    r"\b(pris(et|er|erna|höjning\w*|ökning\w*|sänkning\w*)?|volym(en|er|erna)?|efterfrågan|"
    r"kund(en|er|erna)?|leverantör(en|er|erna)?|order|beställning(en|ar|arna)?|avtal(et|en)?|"
    r"kontrakt(et|en)?|\w*anställ(d|da|de|ning|ningar)|sjukskriv\w*|uppsägning\w*|varsel|permitter\w*|"
    r"kampanj(en|er)?|marknad(en|släget)?|konkurren\w*|\w*konjunktur\w*|inflation\w*|"
    r"valutakurs(en|er|erna)?|växelkurs(en|er|erna)?)\b",
    re.I,
)
# Tal som ser ut som belopp/procent/mängd. Tillåtna: årtal, kontonummer och id:n i paketet.
NUMBER = re.compile(r"(?<![\w{:])[-+−]?\d[\d\s .,]*\d?(?:\s?(?:%|procent|kr|tkr|mkr|msek|sek|kronor))?", re.I)
UNIT_AFTER = re.compile(r"^\s?(%|procent|kr|tkr|mkr|msek|sek|kronor)", re.I)
UNSAFE = re.compile(r"(https?://|www\.|!\[|\]\(|<\s*img|<\s*a\s|<\s*script|javascript:)", re.I)
# Varje motpartsplatshållare, även en felskriven ({m:x}); och en fristående kod, även i genitiv
# ("M1s", "M1:s") – samma ordgräns som när koderna blir namn (ai/egress.py).
COUNTERPARTY_PLACEHOLDER = re.compile(r"\{m:([^}]*)\}")
BARE_CODE = re.compile(rf"(?<!\w)(M\d+){WORD_END}")

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

    def feedback(self, mask: Callable[[str], str] | None = None) -> str:
        """Underkända påståenden till omförsöket. `mask` maskerar den citerade texten, och skälet som
        kan citera modellens egna fakta-id, före avkortningen så att inget namn kapas halvvägs."""
        lines = []
        for r in self.rejected:
            text, reason = str(r.claim.get("text", "")), r.reason
            if mask is not None:
                text, reason = mask(text), mask(reason)
            lines.append(f'- "{text[:120]}": {reason}')
        return "\n".join(lines)


def _allowed_number(token: str, allowed: set[str]) -> bool:
    # Ta bort avslutande skiljetecken och mellanrum, t.ex. "2026  ." när en {f:id} stod mellan.
    t = re.sub(r"[\s.,]+$", "", token.strip())
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
    # Tillåtna fraser, t.ex. kontonamnet "Arbetsgivaravgifter 31,42 %", är namn och inte siffror
    # som AI:n skrivit själv; bara den exakta frasen undantas (längsta först).
    for phrase in sorted((a for a in allowed if " " in a and re.search(r"\d", a)), key=len, reverse=True):
        without = re.sub(re.escape(phrase), " ", without, flags=re.IGNORECASE)
    bad = []
    for m in NUMBER.finditer(without):
        # Komma följt av mellanslag är en uppräkning ("2440, 2611"), inte ett decimaltal ("12,5").
        for tok in re.split(r",\s+", m.group(0)):
            if not re.search(r"\d", tok):
                continue
            has_unit = bool(re.search(r"(%|procent|kr|tkr|mkr|msek|sek|kronor)\s*$", tok, re.I))
            if has_unit or not _allowed_number(tok, allowed):
                bad.append(tok.strip())
    return bad


def _counterparty_rejection(claim: dict[str, Any], codes: Collection[str], client_facing: bool) -> Rejection | None:
    """{m:Mx} godkänns bara för koder ur samma körning och bara i intern text."""
    text = claim["text"]
    placeholders = COUNTERPARTY_PLACEHOLDER.findall(text)
    if client_facing:
        if placeholders or any(code in codes for code in BARE_CODE.findall(text)):
            return Rejection(claim, "motpartskoder får inte stå i kundtext", "counterparty_in_client_text")
        return None
    unknown = sorted({code for code in placeholders if code not in codes})
    if unknown:
        return Rejection(
            claim,
            f"okända motpartskoder: {', '.join(unknown)} – använd bara koder ur underlaget",
            "unknown_counterparty",
        )
    return None


def verify_claims(
    claims: list[dict[str, Any]],
    store: FactStore,
    *,
    client_facing: bool = False,
    allowed_identifiers: set[str] | None = None,
    pseudonyms: Collection[str] | None = None,
) -> VerificationResult:
    """`pseudonyms` är motpartskoderna som delats ut i körningen; utan dem är varje {m:…} okänd."""
    allowed = set(allowed_identifiers or set())
    codes = frozenset(pseudonyms or ())
    res = VerificationResult()
    for raw in claims:
        text = str(raw.get("text", ""))
        claim: dict[str, Any] = {
            "type": str(raw.get("type", "")).upper(),
            "text": text,
            "fact_ids": [str(x) for x in raw.get("fact_ids", [])],
        }
        if claim["type"] not in CLAIM_TYPES:
            res.rejected.append(Rejection(claim, "okänd påståendetyp", "unknown_type"))
            continue
        if UNSAFE.search(text):
            res.rejected.append(Rejection(claim, "länkar, bilder eller HTML är inte tillåtna", "unsafe_content"))
            continue
        counterparty = _counterparty_rejection(claim, codes, client_facing)
        if counterparty is not None:
            res.rejected.append(counterparty)
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
        if claim["type"] in ("OBSERVATION", "EXPLANATION") and BUSINESS_CAUSE.search(text):
            claim["type"] = "HYPOTHESIS"  # affärsorsak utan belägg i bokföringen, även med bryggfakta
            res.downgraded += 1
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
