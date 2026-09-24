"""Prioriteringspoäng för portföljvyn – "var behöver jag lägga min tid i dag?".

Poängen räknas med fasta regler (ingen AI) och delarna visas alltid för användaren.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

WEIGHTS = {
    "high": 10,
    "medium": 3,
    "low": 1,
    "changed_after_approval": 15,
    "margin_drop": 8,
    "stale_review": 5,
    "missing_data": 10,
    "connection_error": 10,
    "unanswered_question": 3,
    "preliminary": 2,
}


@dataclass(slots=True)
class PortfolioInputs:
    open_high: int = 0
    open_medium: int = 0
    open_low: int = 0
    changed_after_approval: bool = False
    margin_change_pp: Decimal | None = None
    last_review: date | None = None
    latest_data_month: date | None = None
    expected_month: date | None = None
    connection_ok: bool = True
    unanswered_questions_over_7d: int = 0
    preliminary: bool = False


@dataclass(slots=True)
class PriorityScore:
    score: int
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "reasons": self.reasons}


def score(inp: PortfolioInputs, today: date | None = None) -> PriorityScore:
    today = today or date.today()
    parts: list[tuple[str, int, str]] = []
    if inp.open_high:
        parts.append(("high", inp.open_high * WEIGHTS["high"], f"{inp.open_high} allvarliga fynd"))
    if inp.open_medium:
        parts.append(
            ("medium", inp.open_medium * WEIGHTS["medium"], f"{inp.open_medium} fynd med medelhög allvarlighet")
        )
    if inp.open_low:
        parts.append(("low", min(inp.open_low, 10) * WEIGHTS["low"], f"{inp.open_low} mindre fynd"))
    if inp.changed_after_approval:
        parts.append(("changed_after_approval", WEIGHTS["changed_after_approval"], "Ändrad efter godkännande"))
    if inp.margin_change_pp is not None and inp.margin_change_pp <= Decimal("-5"):
        parts.append(
            (
                "margin_drop",
                WEIGHTS["margin_drop"],
                f"Rörelsemarginalen har sjunkit {abs(inp.margin_change_pp):.1f} procentenheter",
            )
        )
    if inp.last_review is None or (today - inp.last_review).days > 35:
        parts.append(
            (
                "stale_review",
                WEIGHTS["stale_review"],
                "Aldrig granskad"
                if inp.last_review is None
                else f"Senast granskad för {(today - inp.last_review).days} dagar sedan",
            )
        )
    if inp.expected_month and (inp.latest_data_month is None or inp.latest_data_month < inp.expected_month):
        parts.append(("missing_data", WEIGHTS["missing_data"], f"Saknar data för {inp.expected_month:%Y-%m}"))
    if not inp.connection_ok:
        parts.append(("connection_error", WEIGHTS["connection_error"], "Kopplingen fungerar inte"))
    if inp.unanswered_questions_over_7d:
        parts.append(
            (
                "unanswered_question",
                inp.unanswered_questions_over_7d * WEIGHTS["unanswered_question"],
                f"{inp.unanswered_questions_over_7d} obesvarade kundfrågor > 7 dagar",
            )
        )
    if inp.preliminary:
        parts.append(("preliminary", WEIGHTS["preliminary"], "Perioden är preliminär"))
    total = sum(p[1] for p in parts)
    return PriorityScore(total, [{"code": c, "points": pts, "text": t} for c, pts, t in parts])
