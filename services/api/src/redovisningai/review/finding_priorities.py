"""Tolkningsbar och stabil prioritering av deterministiska fyndkandidater."""

from __future__ import annotations

from dataclasses import dataclass, replace

from redovisningai.analytics.finding_candidates import FindingCandidate

PRIORITY_VERSION = "finding-priority-v1"


@dataclass(frozen=True, slots=True)
class RankedFindings:
    top: tuple[FindingCandidate, ...]
    others: tuple[FindingCandidate, ...]


def _magnitude_points(candidate: FindingCandidate) -> int:
    value = abs(candidate.amount_effect)
    if candidate.unit == "SEK":
        return (
            30 if value >= 100_000 else 20 if value >= 25_000 else 10 if value >= 5_000 else 5 if value >= 1_000 else 1
        )
    return 20 if value >= 10 else 15 if value >= 5 else 10 if value >= 1 else 3


def _score(candidate: FindingCandidate) -> tuple[int, dict[str, int]]:
    parts = {
        "materiality": _magnitude_points(candidate),
        "evidence": 20
        if candidate.source_level == "account_voucher"
        else 10
        if candidate.source_level == "account"
        else 0,
        "traceability": min(15, 5 * len(candidate.fact_ids)),
        "coverage": -10 if candidate.warnings else 0,
        "actionability": 5 if candidate.sources else 0,
    }
    return sum(parts.values()), parts


def rank_findings(candidates: list[FindingCandidate], *, limit: int = 5) -> RankedFindings:
    """Return the default five and retain every excluded candidate with a reason."""
    if limit < 1 or limit > 5:
        raise ValueError("Standardvyn får visa mellan 1 och 5 prioriterade fynd.")
    prepared = []
    for candidate in candidates:
        score, parts = _score(candidate)
        prepared.append(
            replace(
                candidate,
                priority_score=score,
                score_parts=parts,
                versions={**candidate.versions, "priority": PRIORITY_VERSION},
            )
        )
    ordered = sorted(prepared, key=lambda item: (-item.priority_score, item.group_key, item.code))
    top = tuple(ordered[:limit])
    others = tuple(
        replace(
            candidate,
            demotion_reasons=(
                f"Prioritet {candidate.priority_score}; endast {limit} visas som standard.",
                "Granska övriga kandidater i den fullständiga listan.",
            ),
        )
        for candidate in ordered[limit:]
    )
    return RankedFindings(top, others)
