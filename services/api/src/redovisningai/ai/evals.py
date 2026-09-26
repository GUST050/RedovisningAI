"""Evals per AI-uppgift. Körs i CI med FakeProvider och manuellt mot riktiga leverantörer
innan modellbyte ("inget modellbyte till produktion utan eval").

Mått:
- acceptance: andel påståenden som klarar granskaren utan omskrivning
- literal_numbers: antal siffror skrivna som text i slutresultatet (ska vara 0)
- client_leaks: interna/PTL-fakta i kundtext (ska vara 0)
- coverage (A2): alla fynd finns i något ärende; High föreslås aldrig "troligen OK"
- mapping_accuracy (A1): andel konton med rätt rad jämfört med facit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metric_explanations import MetricComponent, MetricExplanation
from redovisningai.accounting.periods import month
from redovisningai.accounting.statements import StatementMapping
from redovisningai.ai.service import AIService
from redovisningai.ai.verifier import find_literal_numbers
from redovisningai.analytics.finding_candidates import collect_candidates
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.facts.model import FactStatus, Unit, Visibility
from redovisningai.review.analysis import CompanyAnalysis, CompanyContext
from redovisningai.review.finding_priorities import rank_findings
from redovisningai.rules.engine import CompanySettings


@dataclass(slots=True)
class EvalResult:
    task: str
    company: str
    source: str
    metrics: dict[str, float]
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "company": self.company,
            "source": self.source,
            "metrics": self.metrics,
            "failures": self.failures,
            "passed": self.passed,
        }


def _claims(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for k in ("claims", "summary", "questions"):
        out.extend(data.get(k, []))
    for c in data.get("cases", []):
        out.extend(c.get("root_cause", []))
        out.extend(c.get("rationale", []))
    return out


def run_finding_evals() -> list[EvalResult]:
    """Syntetisk, offline eval för evidensbunden gruppering och top-five-grinden."""
    current, previous = month(2026, 9), month(2025, 9)
    pair = ComparisonPair(current, previous, FactStatus.CALCULATED)
    component_a = MetricComponent(
        "account:6110",
        "Konsultkostnader",
        Decimal("8000"),
        Decimal("2000"),
        Decimal("6000"),
        Unit.SEK,
        "account_voucher",
        {6110: Decimal("8000")},
        {6110: Decimal("2000")},
        "fact-a",
    )
    component_b = MetricComponent(
        "account:6110",
        "Konsultkostnader",
        Decimal("4"),
        Decimal("2"),
        Decimal("2"),
        Unit.PERCENT,
        "account_voucher",
        {6110: Decimal("8000")},
        {6110: Decimal("2000")},
        "fact-b",
    )
    explanations = [
        MetricExplanation(
            code,
            code,
            unit,
            change,
            Decimal("0"),
            change,
            FactStatus.CALCULATED,
            (component,),
            (),
            {"current": current.spec, "previous": previous.spec},
            {"calc": "1"},
            (component.fact_id or "",),
        )
        for code, unit, change, component in (
            ("external_costs", Unit.SEK, Decimal("6000"), component_a),
            ("operating_margin", Unit.PERCENT, Decimal("2"), component_b),
        )
    ]
    candidates = collect_candidates(None, pair, explanations, mapping_version="synthetic-map-v1")
    ranked = rank_findings(candidates)
    missing_pair = ComparisonPair(current, previous, FactStatus.INSUFFICIENT_DATA, ("synthetic missing month",))
    missing_candidates = collect_candidates(None, missing_pair, explanations, mapping_version="synthetic-map-v1")
    failures = []
    if len(candidates) != 1 or set(candidates[0].fact_ids) != {"fact-a", "fact-b"}:
        failures.append("relaterade mått grupperades inte med båda faktahänvisningarna")
    if missing_candidates:
        failures.append("ofullständigt periodpar gav analyskandidater")
    if len(ranked.top) > 5:
        failures.append("topplistan överskred fem kandidater")
    metrics = {
        "grouped_candidates": float(len(candidates)),
        "fact_references": float(len(candidates[0].fact_ids)) if candidates else 0.0,
        "incomplete_candidates": float(len(missing_candidates)),
        "top_five": float(len(ranked.top)),
    }
    return [EvalResult("findings", "synthetic", "rules", metrics, failures)]


def run_evals(service: AIService, *, as_of: date = date(2026, 10, 12), period: str = "2026-09") -> list[EvalResult]:
    results: list[EvalResult] = run_finding_evals()
    for profile in DEMO_PROFILES:
        g = generate(profile, as_of)
        ctx = CompanyContext(
            "eval",
            "eval",
            g.ledger.company_name,
            CompanySettings(vat_period=profile.vat_period, food_retail=profile.food_retail),
        )
        an = CompanyAnalysis(g.ledger, ctx)
        rev = an.review(an.period(period))
        allowed = an.allowed_identifiers()
        restricted = {f.id for f in rev.store if f.visibility is not Visibility.CLIENT_SAFE}

        for code, pkg, client in (
            ("A3", an.commentary_package(rev), False),
            ("A4", an.client_package(rev), True),
            ("A2", an.case_package(rev), False),
        ):
            out = service.run(code, pkg, rev.store, org_id="eval", allowed_identifiers=allowed)
            claims = _claims(out.data)
            literal = sum(len(find_literal_numbers(c["text"], allowed)) for c in claims)
            attempts = out.trace.attempts or 1
            metrics = {
                "claims": float(len(claims)),
                "rejected": float(len(out.trace.rejected)),
                "acceptance": 1.0 if attempts <= 1 and not out.trace.rejected else 0.0,
                "literal_numbers": float(literal),
            }
            failures = []
            if literal:
                failures.append(f"{literal} siffror skrivna som text")
            if client:
                leaks = sum(1 for c in claims for f in c.get("fact_ids", []) if f in restricted)
                metrics["client_leaks"] = float(leaks)
                if leaks:
                    failures.append(f"{leaks} interna fakta i kundtext")
            if code == "A2":
                wanted = {f["id"] for c in pkg["cases"] for f in c["findings"]}
                got = [i for c in out.data["cases"] for i in c["finding_ids"]]
                high = {f["id"] for c in pkg["cases"] for f in c["findings"] if f["severity"] == "HIGH"}
                metrics["coverage"] = len(set(got) & wanted) / len(wanted) if wanted else 1.0
                if set(got) != wanted or len(got) != len(set(got)):
                    failures.append("fynd saknas eller finns i flera ärenden")
                if any(c["suggestion"] == "LIKELY_OK" and set(c["finding_ids"]) & high for c in out.data["cases"]):
                    failures.append("High-fynd föreslaget som troligen OK")
            results.append(EvalResult(code, profile.name, out.source, metrics, failures))

        # A1: facit = BAS-standardmappning för konton med entydiga intervall
        accounts = [
            {"account": a.number, "name": a.name, "examples": []} for a in list(g.ledger.accounts.values())[:25]
        ]
        out = service.run("A1", {"accounts": accounts}, rev.store, org_id="eval")
        sm = StatementMapping()
        correct = sum(1 for s in out.data["suggestions"] if s["legal_line"] == sm.line_for(s["account"]))
        acc = correct / len(accounts) if accounts else 1.0
        results.append(
            EvalResult(
                "A1",
                profile.name,
                out.source,
                {"mapping_accuracy": acc},
                [] if acc >= 0.9 else [f"mappningsträffsäkerhet {acc:.0%}"],
            )
        )
    return results
