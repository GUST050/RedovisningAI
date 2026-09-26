"""Intern periodkommentar (A3): evidenspaket, versionsmetadata och AI-körning.

Delas av API:t (konsulten skapar ett utkast) och pipelinen (automatisk analys efter granskning),
så att båda vägarna skickar exakt samma data-minimala paket och sparar samma metadata.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from redovisningai.accounting.comparisons import validate_comparison
from redovisningai.accounting.metric_explanations import explain_metric
from redovisningai.accounting.metrics import REGISTRY
from redovisningai.accounting.periods import Period
from redovisningai.ai.service import AIService
from redovisningai.ai.tasks import A3_PROMPT_VERSION, period_commentary_input
from redovisningai.analytics.finding_candidates import collect_candidates
from redovisningai.facts.model import CALC_VERSION, FactStatus, Unit, Visibility
from redovisningai.review.analysis import CompanyAnalysis, ReviewResult
from redovisningai.review.finding_priorities import rank_findings


class InvalidComparison(ValueError):
    """Jämförelseperioden går inte att använda för kommentaren."""


def a3_findings(
    analysis: CompanyAnalysis, review: ReviewResult, current: Period, previous: Period
) -> list[dict[str, Any]]:
    """Build the bounded, data-minimal transaction candidates for A3."""
    pair = validate_comparison(current, previous, analysis.index)
    explanations = [
        explain_metric(
            code,
            analysis.index,
            pair,
            mapping=analysis.ctx.statement_mapping,
            rates=analysis.rates,
            store=review.store,
        )
        for code in REGISTRY
    ]
    candidates = collect_candidates(
        analysis.index,
        pair,
        explanations,
        mapping_version=analysis.ctx.statement_mapping.version,
        aliases=analysis.ctx.aliases,
    )
    selected = rank_findings(candidates, limit=5).top
    output: list[dict[str, Any]] = []
    for candidate in selected:
        accounts: set[int] = set()
        evidence_count = 0
        for source in candidate.sources:
            for account in str(source.get("accounts", "")).split(","):
                try:
                    account_number = int(account)
                except ValueError:
                    continue
                if not (7000 <= account_number <= 7699 or 2710 <= account_number <= 2719):
                    accounts.add(account_number)
            references = source.get("references")
            if isinstance(references, list):
                evidence_count += len(references)
        safe_sources = [source for source in candidate.sources]
        fact = review.store.new(
            "variance_component",
            "ai_finding:" + candidate.code + ":" + sha256(candidate.group_key.encode()).hexdigest()[:16],
            candidate.code,
            candidate.amount_effect,
            Unit.SEK if candidate.unit == Unit.SEK.value else Unit.COUNT,
            period=current.spec,
            compare_period=previous.spec,
            status=FactStatus.PARTIAL if candidate.warnings else FactStatus.CALCULATED,
            visibility=Visibility.INTERNAL,
            lineage={"accounts": accounts, "source_level": candidate.source_level},
        )
        aggregate_source = next(iter(safe_sources), {})
        output.append(
            {
                "code": candidate.code,
                "period_pair": list(candidate.period_pair),
                "fact_id": fact.id,
                "fact_ids": list(candidate.fact_ids),
                "unit": fact.unit.value,
                "accounts": sorted(accounts),
                "metric_codes": list(candidate.metric_codes),
                "source_level": candidate.source_level,
                "evidence_count": evidence_count,
                "recurrence": aggregate_source.get("recurrence"),
                "current_count": aggregate_source.get("current_count"),
                "previous_count": aggregate_source.get("previous_count"),
                "before_monthly_average": aggregate_source.get("before_monthly_average"),
                "after_monthly_average": aggregate_source.get("after_monthly_average"),
            }
        )
    return output


def build_commentary(
    analysis: CompanyAnalysis,
    review: ReviewResult,
    *,
    ai: AIService,
    org_id: str,
    company_id: str,
    compare_spec: str | None = None,
) -> dict[str, Any]:
    """Kör A3 för granskningens period och returnera utkastet med versionsmetadata (sparas av anroparen)."""
    try:
        package = analysis.commentary_package(review, compare_spec=compare_spec)
    except ValueError as exc:
        raise InvalidComparison(str(exc)) from exc
    current = analysis.period(package["period"]["spec"])
    previous = analysis.period(package["compare"]["spec"])
    package["findings"] = a3_findings(analysis, review, current, previous)
    # Candidate facts were added to the shared, period-scoped fact store above;
    # the provider projection will retain only explicitly referenced IDs.
    package["facts"] = review.store.to_list()
    metadata: dict[str, Any] = {
        "period": current.spec,
        "compare_period": previous.spec,
        "source_fingerprint": analysis.source_fingerprint(current, previous, prompt_version=A3_PROMPT_VERSION),
        "mapping_version": analysis.ctx.statement_mapping.version,
        "category_version": analysis.ctx.category_mapping.version,
        "calculation_version": CALC_VERSION,
        "prompt_version": A3_PROMPT_VERSION,
        "task": "A3",
    }
    ai_package = period_commentary_input(package)
    allowed_fact_ids = {str(f["id"]) for f in ai_package["facts"]}
    allowed_fact_ids.update(str(account) for finding in ai_package["findings"] for account in finding["accounts"])
    out = ai.run(
        "A3",
        ai_package,
        review.store,
        org_id=org_id,
        company_id=company_id,
        # The provider sees only the projected aggregates. Claim verification
        # is limited to facts included in that exact package.
        allowed_identifiers=allowed_fact_ids,
    )
    metadata["provider"] = out.trace.provider
    metadata["model"] = out.trace.model
    metadata["region"] = out.trace.region
    metadata["source"] = out.source
    return {**out.to_dict(), "analysis_metadata": metadata, "compare_period": previous.spec}
