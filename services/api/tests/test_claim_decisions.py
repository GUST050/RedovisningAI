import pytest

from redovisningai.api.routes_review import ClaimDecisionIn, _apply_claim_decisions


def test_every_client_summary_conclusion_requires_a_reasoned_decision() -> None:
    lines = ["Försäljningen utvecklades stabilt", "Kostnaderna bör följas upp"]
    decisions = [
        ClaimDecisionIn(index=0, statement=lines[0], decision="approve", reason="Kontrollerad mot rapporten"),
        ClaimDecisionIn(
            index=1,
            statement=lines[1],
            decision="correct",
            reason="Behöver nyanseras",
            corrected_text="Följ upp de största kostnadsposterna",
        ),
    ]

    claims, audit = _apply_claim_decisions(lines, decisions)

    assert [claim["text"] for claim in claims] == [lines[0], "Följ upp de största kostnadsposterna"]
    assert [entry["decision"] for entry in audit] == ["approve", "correct"]
    assert all(entry["reason"] for entry in audit)


def test_rejected_claim_is_removed_and_unreviewed_or_changed_claim_is_blocked() -> None:
    line = "En möjlig kostnadsförändring"
    rejected = ClaimDecisionIn(index=0, statement=line, decision="reject", reason="Stöds inte av underlaget")
    assert _apply_claim_decisions([line], [rejected])[0] == []

    with pytest.raises(ValueError, match="varje slutsats"):
        _apply_claim_decisions([line], [])
    with pytest.raises(ValueError, match="ändrats"):
        _apply_claim_decisions([line], [rejected.model_copy(update={"statement": "annan text"})])
