"""A7 (veckans prioriteringar) måste få fakta att hänvisa till, annars underkänns allt AI:n skriver."""

from __future__ import annotations

import re
from typing import Any

from redovisningai.ai.service import AIService
from redovisningai.ai.tasks import TASKS
from redovisningai.facts.model import FactStore
from redovisningai.portfolio.brief import brief_package

COMPANIES: list[dict[str, Any]] = [
    {
        "name": "Bygg & Co AB",
        "open_findings": {"high": 6, "medium": 13, "low": 10},
        "priority": {
            "score": 116,
            "reasons": [
                {"code": "high", "points": 60, "text": "6 allvarliga fynd"},
                {"code": "margin_drop", "points": 8, "text": "Rörelsemarginalen har sjunkit 5.3 procentenheter"},
            ],
        },
    },
    {
        "name": "Lugna Bolaget AB",
        "open_findings": {"high": 0, "medium": 0, "low": 0},
        "priority": {"score": 0, "reasons": []},
    },
]


def _package() -> tuple[dict[str, Any], FactStore]:
    store = FactStore()
    return brief_package(COMPANIES, store), store


def test_scores_and_finding_counts_are_facts_and_labels_carry_no_numbers() -> None:
    package, store = _package()
    bygg = package["companies"][0]
    assert store.get(bygg["score"]["fact_id"]).value == 116  # type: ignore[union-attr]
    high = next(r for r in bygg["reasons"] if r["code"] == "high")
    assert store.get(high["fact_id"]).value == 6  # type: ignore[union-attr]
    assert not any(re.search(r"\d", r["label"]) for c in package["companies"] for r in c["reasons"])


def test_claims_that_cite_the_facts_pass_and_typed_numbers_do_not() -> None:
    package, store = _package()
    high = next(r for r in package["companies"][0]["reasons"] if r["code"] == "high")
    answer = {
        "claims": [
            {
                "type": "OBSERVATION",
                "text": f"Bygg & Co AB har {{f:{high['fact_id']}}} allvarliga fynd.",
                "fact_ids": [high["fact_id"]],
            },
            {"type": "OBSERVATION", "text": "Bygg & Co AB har 60 poäng från allvarliga fynd.", "fact_ids": []},
        ]
    }
    _, result = TASKS["A7"].verify(answer, package, store, {"Bygg & Co AB", "Lugna Bolaget AB"})
    assert len(result.accepted) == 1
    assert [r.code for r in result.rejected] in (["literal_number"], ["missing_fact"])


def test_rule_based_brief_cites_the_counts_and_survives_verification() -> None:
    package, store = _package()
    out = AIService(None).run(
        "A7", package, store, org_id="o", allowed_identifiers={"Bygg & Co AB", "Lugna Bolaget AB"}
    )
    assert out.source == "rules"
    rendered = [c["rendered"] for c in out.data["claims"]]
    assert rendered and "Bygg & Co AB" in rendered[0] and "6" in rendered[0]
    assert all("Lugna Bolaget AB" not in text for text in rendered)  # inga skäl, ingen rad
