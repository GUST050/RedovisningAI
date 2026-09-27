"""A3:s utökade underlag (plan §9.10): transaktionsbryggan för periodens största förändringar.

Hela bryggan räknas lokalt; urvalet görs först här – högst fem förändringar och fem motpartsgrupper
per förändring. Till leverantören går bara koder, antal och fakta-id: motparter som M1, M2 … ur
körningens pseudonymer, aldrig namn eller text. Lönekonton tas bort ur varje mål, så ingen lönerad
når underlaget. Bryggans fakta läggs i granskningens faktalager, där verifieraren slår upp dem.
"""

from __future__ import annotations

from typing import Any

from redovisningai.accounting.comparisons import validate_comparison
from redovisningai.accounting.periods import Period
from redovisningai.accounting.transaction_bridge import TransactionBridge, transaction_bridge
from redovisningai.accounting.variance import result_bridge
from redovisningai.ai.egress import CounterpartyPseudonyms
from redovisningai.facts.model import FactStore
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis, ReviewResult

MAX_CHANGES = 5
MAX_GROUPS = 5


def _bridge_accounts(analysis: CompanyAnalysis, target: str) -> tuple[set[int], int]:
    """Målets konton utan lönekonton, och tecknet från `target_accounts`."""
    accounts, sign = analysis.target_accounts(target)
    return {account for account in accounts if account not in PAYROLL}, sign


def select_changes(
    analysis: CompanyAnalysis, current: Period, previous: Period, *, limit: int = MAX_CHANGES
) -> list[str]:
    """Resultatraderna med störst absolut effekt i resultatbryggan. En rad utan förändring eller med
    bara lönekonton (personalkostnader) hoppas över."""
    bridge = result_bridge(analysis.index, current, previous, mapping=analysis.ctx.statement_mapping, store=FactStore())
    ranked = sorted((c for c in bridge.components if c.effect), key=lambda c: abs(c.effect), reverse=True)
    targets = [f"line:{c.code}" for c in ranked]
    return [target for target in targets if _bridge_accounts(analysis, target)[0]][: max(limit, 0)]


def a3_transactions(
    analysis: CompanyAnalysis,
    review: ReviewResult,
    current: Period,
    previous: Period,
    pseudonyms: CounterpartyPseudonyms,
    *,
    limit: int = MAX_CHANGES,
) -> list[dict[str, Any]]:
    """Bryggorna för de största förändringarna, med motparter som koder ur samma körnings pseudonymer."""
    pair = validate_comparison(current, previous, analysis.index)
    rows = []
    for target in select_changes(analysis, current, previous, limit=limit):
        accounts, sign = _bridge_accounts(analysis, target)
        bridge = transaction_bridge(
            analysis.index, accounts, pair, target=target, aliases=analysis.ctx.aliases, sign=sign, store=review.store
        )
        rows.append(_bridge_row(bridge, pseudonyms))
    return rows


def a4_transaction_summary(
    analysis: CompanyAnalysis, review: ReviewResult, current: Period, previous: Period
) -> list[dict[str, str]]:
    """A4:s underlag: samma urval av förändringar som A3, men bara delfakta – ingen motpartskod, inget
    namn och ingen text. Faktumen läggs i granskningens faktalager (`review.store`), där verifieraren
    och kundpaketets facts-lista slår upp dem."""
    pair = validate_comparison(current, previous, analysis.index)
    rows = []
    for target in select_changes(analysis, current, previous):
        accounts, sign = _bridge_accounts(analysis, target)
        bridge = transaction_bridge(
            analysis.index, accounts, pair, target=target, aliases=analysis.ctx.aliases, sign=sign, store=review.store
        )
        parts = {part.code: part for part in bridge.parts}
        both = parts["both"]
        if both.count_effect_fact_id is None or both.amount_effect_fact_id is None:
            raise AssertionError("Bryggans del för båda perioderna saknar effektfakta")
        rows.append(
            {
                "target": target,
                "current_only_fact_id": parts["current_only"].fact_id,
                "previous_only_fact_id": parts["previous_only"].fact_id,
                "count_effect_fact_id": both.count_effect_fact_id,
                "amount_effect_fact_id": both.amount_effect_fact_id,
            }
        )
    return rows


def _bridge_row(bridge: TransactionBridge, pseudonyms: CounterpartyPseudonyms) -> dict[str, Any]:
    return {
        "target": bridge.target,
        "change_fact_id": bridge.change_fact_id,
        "parts": [
            {
                "code": part.code,
                "fact_id": part.fact_id,
                "current_count": part.current_count,
                "previous_count": part.previous_count,
                "count_effect_fact_id": part.count_effect_fact_id,
                "amount_effect_fact_id": part.amount_effect_fact_id,
            }
            for part in bridge.parts
        ],
        # Grupperna är sorterade efter absolut förändring; en okänd motpart får ingen kod.
        "groups": [
            {
                "code": pseudonyms.code_for(group.key) if group.key else None,
                "part": group.part,
                "current_count": group.current_count,
                "previous_count": group.previous_count,
                "fact_id": group.fact_id,
                "signals": sorted(group.signals),
            }
            for group in bridge.groups[:MAX_GROUPS]
        ],
        "identified_share_fact_id": bridge.identified_share_fact_id,
    }
