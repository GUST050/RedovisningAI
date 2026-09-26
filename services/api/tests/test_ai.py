import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from redovisningai.ai.providers.anthropic_provider import AnthropicConfig, AnthropicProvider
from redovisningai.ai.providers.base import (
    FailoverProvider,
    ModelTier,
    ProviderError,
    ProviderUnavailable,
    RefusalError,
    ToolBudget,
    ToolSpec,
    Usage,
)
from redovisningai.ai.pseudonymize import Pseudonymizer, luhn_ok
from redovisningai.ai.service import AIService, FakeProvider, InMemoryBudget
from redovisningai.ai.tasks import period_commentary_input
from redovisningai.ai.tools import analyst_tools
from redovisningai.ai.verifier import find_literal_numbers, verify_claims
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.facts.model import FactStore, Unit, Visibility
from redovisningai.portfolio.brief import brief_package
from redovisningai.review.analysis import CompanyAnalysis, CompanyContext
from redovisningai.rules.engine import CompanySettings

AS_OF = date(2026, 10, 12)


def test_a3_provider_payload_is_restricted_to_approved_aggregates() -> None:
    safe_fact_id = "net_sales_123"
    package = {
        "company": "Känsligt AB",
        "period": {"spec": "2026-09", "label": "September"},
        "compare": {"spec": "2025-09", "label": "September"},
        "comparison_status": "CALCULATED",
        "comparison_warnings": [],
        "metrics": {"net_sales": {"id": safe_fact_id, "change_id": None, "change_pct_id": "growth_456"}},
        "bridge": {
            "components": [
                {
                    "code": "personnel",
                    "label": "Kundens fria etikett Olofsson",
                    "effect": "-5000",
                    "fact_id": "cost_789",
                }
            ]
        },
        "maturity": {"low_periodization": False, "recommended_view": "month", "notes": ["Do not send"]},
        "open_cases": {"high": 1, "titles": ["Olofsson saknar lönespecifikation"]},
        "findings": [
            {
                "code": "recurring_cost_change",
                "period_pair": ["2026-09", "2025-09"],
                "fact_id": "candidate_222",
                "fact_ids": ["cost_789"],
                "unit": "SEK",
                "accounts": [6540, 7010, 2710],
                "metric_codes": ["operating_result"],
                "source_level": "account_voucher",
                "evidence_count": 3,
                "current_count": 1,
                "sources": [{"references": [{"voucher": "A55", "content_hash": "secret"}], "name": "private vendor"}],
                "label": "private vendor name",
            }
        ],
        "facts": [
            {
                "id": safe_fact_id,
                "value": "120000",
                "unit": "SEK",
                "status": "CALCULATED",
                "period": "2026-09",
                "label": "private vendor",
            },
            {
                "id": "growth_456",
                "value": "0.12",
                "unit": "percent",
                "status": "CALCULATED",
                "period": "2026-09",
                "text_value": "voucher text",
            },
            {
                "id": "cost_789",
                "value": "-5000",
                "unit": "SEK",
                "status": "CALCULATED",
                "period": "2026-09",
                "lineage": {"accounts": [6540, 7010]},
            },
            {"id": "candidate_222", "value": "5000", "unit": "SEK", "status": "PARTIAL", "period": "2026-09"},
            {
                "id": "payroll_111",
                "value": "9000",
                "unit": "SEK",
                "status": "CALCULATED",
                "period": "2026-09",
                "visibility": "INTERNAL",
            },
        ],
    }

    projected = period_commentary_input(package)
    serialized = json.dumps(projected, ensure_ascii=False)

    assert projected["period"]["spec"] == "2026-09"
    assert projected["compare"]["spec"] == "2025-09"
    assert projected["facts"] == [
        {"id": safe_fact_id, "value": "120000", "unit": "SEK", "status": "CALCULATED", "period": "2026-09"},
        {"id": "growth_456", "value": "0.12", "unit": "percent", "status": "CALCULATED", "period": "2026-09"},
        {
            "id": "cost_789",
            "value": "-5000",
            "unit": "SEK",
            "status": "CALCULATED",
            "period": "2026-09",
            "accounts": [6540],
        },
        {"id": "candidate_222", "value": "5000", "unit": "SEK", "status": "PARTIAL", "period": "2026-09"},
    ]
    assert projected["bridge"]["components"][0]["source_level"] == "aggregated_account_bridge"
    assert projected["findings"][0]["accounts"] == [6540]
    assert projected["findings"][0]["evidence_count"] == 3
    assert projected["findings"][0]["label"] == "förändring i återkommande kostnad"
    assert "7010" not in serialized and "payroll_111" not in serialized
    for forbidden in (
        "Känsligt AB",
        "Olofsson",
        "lönespecifikation",
        "voucher text",
        "Do not send",
        "private vendor",
        "A55",
        "secret",
        "2710",
    ):
        assert forbidden not in serialized


def test_ai_trace_redacts_payload_output_rejection_text_and_tool_arguments() -> None:
    provider = FakeProvider(
        responses={
            "A3": lambda _content: {
                "claims": [{"type": "HYPOTHESIS", "text": "Hemligt AB hade 98765 kr i kostnad.", "fact_ids": []}]
            }
        }
    )
    traces = []
    service = AIService(provider, trace_sink=traces.append, keep_payloads=False)

    outcome = service.run("A3", {"company": "Hemligt AB"}, FactStore(), org_id="org-1")

    assert outcome.trace.package is None
    assert outcome.trace.output is None
    assert traces[0].package is None and traces[0].output is None
    assert traces[0].rejected[0]["text"] == ""
    assert traces[0].rejected[0]["reason"]
    # Skälet sparas som en kod utan innehåll, så att underkännanden kan följas upp utan kunddata.
    assert traces[0].rejected[0]["code"] == "literal_number"
    assert "98765" not in json.dumps(traces[0].rejected)
    assert traces[0].tool_calls == []


@pytest.fixture(scope="module")
def analysis() -> CompanyAnalysis:
    g = generate(DEMO_PROFILES[0], AS_OF)
    ctx = CompanyContext(
        "c1", "o1", g.ledger.company_name, CompanySettings(vat_period="quarter"), person_names=["Erik"]
    )
    return CompanyAnalysis(g.ledger, ctx)


@pytest.fixture(scope="module")
def review(analysis):  # type: ignore[no-untyped-def]
    return analysis.review(analysis.period("2026-09"))


# ------------------------------------------------------------------ granskaren


def _store() -> FactStore:
    s = FactStore()
    s.new("amount", "a", "Kostnad", Decimal("410000"), Unit.SEK, period="2026-09")
    s.new("variance_component", "line:consulting", "Konsulter", Decimal("-410000"), Unit.SEK)
    s.new("amount", "internal", "Intern", Decimal("5"), Unit.SEK, visibility=Visibility.INTERNAL)
    return s


def test_verifier_rejects_literal_numbers_and_unknown_ids() -> None:
    s = _store()
    a = next(f.id for f in s if f.subject == "a")
    res = verify_claims(
        [
            {"type": "OBSERVATION", "text": "Konsultkostnaden ökade med 410 000 kr.", "fact_ids": []},
            {"type": "OBSERVATION", "text": "Kostnaden var {f:" + a + "} på konto 6550 år 2026.", "fact_ids": [a]},
            {"type": "OBSERVATION", "text": "Ökning {f:okänd}.", "fact_ids": []},
            {"type": "QUESTION", "text": "Är konsulterna tillfälliga?", "fact_ids": []},
            {"type": "OBSERVATION", "text": "Se ![bild](https://evil.example/x?d=1)", "fact_ids": [a]},
        ],
        s,
        allowed_identifiers={"6550"},
    )
    assert len(res.accepted) == 2
    reasons = " ".join(r.reason for r in res.rejected)
    assert "siffror" in reasons and "okända" in reasons and "länkar" in reasons
    assert [r.code for r in res.rejected] == ["literal_number", "unknown_fact", "unsafe_content"]


def test_verifier_downgrades_unsupported_causal_claims() -> None:
    s = _store()
    a = next(f.id for f in s if f.subject == "a")
    comp = next(f.id for f in s if f.kind == "variance_component")
    res = verify_claims(
        [
            {"type": "OBSERVATION", "text": "Resultatet föll på grund av {f:" + a + "}.", "fact_ids": [a]},
            {
                "type": "EXPLANATION",
                "text": "Resultatet försämrades på grund av konsulter {f:" + comp + "}.",
                "fact_ids": [comp],
            },
        ],
        s,
    )
    assert [c["type"] for c in res.accepted] == ["HYPOTHESIS", "EXPLANATION"]
    assert res.downgraded == 1


def test_verifier_blocks_internal_facts_in_client_text() -> None:
    s = _store()
    internal = next(f.id for f in s if f.subject == "internal")
    res = verify_claims(
        [{"type": "OBSERVATION", "text": "{f:" + internal + "}", "fact_ids": [internal]}], s, client_facing=True
    )
    assert not res.accepted and "interna" in res.rejected[0].reason


def test_literal_number_detection() -> None:
    assert find_literal_numbers("ökade 31 %", set())
    assert find_literal_numbers("1,24 Mkr", set())
    assert not find_literal_numbers("konto 6540 under 2026 och verifikation A122", {"6540", "A122"})
    assert not find_literal_numbers("PERSON_1 och {f:abc_123}", set())


# ------------------------------------------------------------------ pseudonymisering


def test_pseudonymizer_round_trip() -> None:
    assert luhn_ok("8112189876")
    p = Pseudonymizer(["Anna Andersson"])
    text = "Utlägg Anna Andersson 811218-9876, anna@firma.se, 070-123 45 67. Konto 6540."
    masked = p.mask(text)
    for secret in ("Anna Andersson", "811218-9876", "anna@firma.se", "070-123 45 67"):
        assert secret not in masked
    assert "6540" in masked
    assert p.unmask(masked) == text


# ------------------------------------------------------------------ tjänsten


def test_service_without_provider_uses_rules(analysis, review) -> None:  # type: ignore[no-untyped-def]
    svc = AIService(None)
    out = svc.run("A3", analysis.commentary_package(review), review.store, org_id="o1")
    assert out.source == "rules" and out.data["claims"]
    assert all("rendered" in c for c in out.data["claims"])
    assert all("{f:" not in c["rendered"] for c in out.data["claims"])


def test_service_regenerates_then_drops_bad_claims(analysis, review) -> None:  # type: ignore[no-untyped-def]
    pkg = analysis.commentary_package(review)
    fid = pkg["metrics"]["net_sales"]["id"]
    attempts = {"n": 0}

    def bad(_: str) -> dict[str, Any]:
        attempts["n"] += 1
        return {
            "claims": [
                {"type": "OBSERVATION", "text": "Omsättningen ökade 17 %.", "fact_ids": []},
                {"type": "OBSERVATION", "text": "Omsättningen var {f:" + fid + "}.", "fact_ids": [fid]},
            ]
        }

    prov = FakeProvider({"A3": bad})
    out = AIService(prov).run("A3", pkg, review.store, org_id="o1")
    assert attempts["n"] == 2  # en omskrivning
    assert out.source == "ai"
    assert len(out.data["claims"]) == 1
    assert out.trace.rejected and "siffror" in out.trace.rejected[0]["reason"]
    assert "Underkändes" not in prov.calls[0]["user_content"]
    assert "underkändes" in prov.calls[1]["user_content"]


def test_service_test_limits_one_attempt_and_output_tokens(analysis, review) -> None:  # type: ignore[no-untyped-def]
    seen: list[int] = []

    class CapturingProvider(FakeProvider):
        def structured(self, **kwargs: Any) -> Any:
            seen.append(kwargs["max_tokens"])
            return super().structured(**kwargs)

    prov = CapturingProvider(
        {"A3": lambda _: {"claims": [{"type": "OBSERVATION", "text": "Omsättningen ökade 17 %.", "fact_ids": []}]}}
    )
    out = AIService(prov, max_output_tokens=1_500, max_attempts=1).run(
        "A3", analysis.commentary_package(review), review.store, org_id="o1"
    )
    assert seen == [1_500]
    assert out.trace.attempts == 1


def test_service_masks_person_names(analysis, review) -> None:  # type: ignore[no-untyped-def]
    prov = FakeProvider()
    AIService(prov).run("A2", analysis.case_package(review), review.store, org_id="o1", names_to_mask=["Erik"])
    assert "Erik" not in prov.calls[0]["user_content"]  # "Utlägg ägare Erik" i verifikationstexten


def test_case_package_never_contains_aml(analysis, review) -> None:  # type: ignore[no-untyped-def]
    pkg = json.dumps(analysis.case_package(review), ensure_ascii=False)
    assert "AML_" not in pkg
    client = json.dumps(analysis.client_package(review), ensure_ascii=False)
    assert "RESTRICTED_AML" not in client and "INTERNAL" not in client


def test_case_builder_keeps_every_finding_and_protects_high(analysis, review) -> None:  # type: ignore[no-untyped-def]
    pkg = analysis.case_package(review)
    ids = [f["id"] for c in pkg["cases"] for f in c["findings"]]
    high = next(f["id"] for c in pkg["cases"] for f in c["findings"] if f["severity"] == "HIGH")

    def lazy(_: str) -> dict[str, Any]:
        return {
            "cases": [
                {
                    "finding_ids": [high],
                    "title": "Allt ok",
                    "root_cause": [],
                    "suggestion": "LIKELY_OK",
                    "rationale": [],
                }
            ]
        }

    out = AIService(FakeProvider({"A2": lazy})).run("A2", pkg, review.store, org_id="o1")
    got = [i for c in out.data["cases"] for i in c["finding_ids"]]
    assert sorted(got) == sorted(ids)
    assert next(c for c in out.data["cases"] if high in c["finding_ids"])["suggestion"] == "INVESTIGATE"


def test_budget_exhaustion_falls_back(analysis, review) -> None:  # type: ignore[no-untyped-def]
    from redovisningai.ai.service import InMemoryBudget

    svc = AIService(FakeProvider(), budget=InMemoryBudget(monthly_tokens=0))
    out = svc.run("A3", analysis.commentary_package(review), review.store, org_id="o1")
    assert out.source == "rules" and "budget" in (out.trace.error or "")


def test_analyst_tools_mask_payroll_and_aml(analysis, review) -> None:  # type: ignore[no-untyped-def]
    tools = {t.name: t for t in analyst_tools(analysis, review.store, review.records, review.period)}
    payroll_voucher = next(v for v in analysis.ledger.all_vouchers() if v.series == "L")
    view = tools["get_voucher"].handler({"voucher": str(payroll_voucher.key)})
    assert all(not (7000 <= r["account"] <= 7699) for r in view["rows"])
    assert view.get("payroll_rows_masked")
    findings = tools["list_findings"].handler({"only_open": True})["findings"]
    assert findings and not any(f["rule"].startswith("AML_") for f in findings)
    acc = tools["get_account_movements"].handler({"period": "2026-09", "account": 7210})
    assert acc["drilldown"]["top_vouchers"] == []
    cmp_ = tools["compare_periods"].handler({"period": "YTD:2026-09", "compare": "YTD:2025-09"})
    assert cmp_["net_sales"]["current"]["fact_id"] in review.store


# ------------------------------------------------------------------ Anthropic-leverantören (mockad klient)


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


class _Messages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw: Any) -> Any:
        self.calls.append(dict(kw, messages=list(kw.get("messages", []))))  # ögonblicksbild som SDK:n
        return self.responses.pop(0)


def _resp(stop: str, content: list[_Block]) -> Any:
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=3, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop, content=content, usage=usage, model="claude-opus-5", stop_details=None)


def _provider(responses: list[Any], platform: str = "anthropic") -> tuple[AnthropicProvider, _Messages]:
    msgs = _Messages(responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    return AnthropicProvider(AnthropicConfig(platform=platform, region=None), client=client), msgs


def test_anthropic_structured_request_shape() -> None:
    prov, msgs = _provider([_resp("end_turn", [_Block("text", text='{"claims": []}')])])
    res = prov.structured(task="A3", tier=ModelTier.STRONG, system="S", user_content="U", schema={"type": "object"})
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default"
    assert res.data == {"claims": []} and res.usage.cache_read_tokens == 3


def test_anthropic_bedrock_model_prefix() -> None:
    prov, msgs = _provider([_resp("end_turn", [_Block("text", text="{}")])], platform="bedrock")
    prov.config.refusal_fallback_model = None
    prov.structured(task="A1", tier=ModelTier.SMALL, system="S", user_content="U", schema={})
    assert msgs.calls[0]["model"] == "anthropic.claude-opus-5"
    assert msgs.calls[0]["output_config"]["effort"] == "low"
    assert "fallbacks" not in msgs.calls[0]


def test_anthropic_refusal_raises() -> None:
    prov, _ = _provider([_resp("refusal", [])])
    with pytest.raises(RefusalError):
        prov.structured(task="A3", tier=ModelTier.STRONG, system="S", user_content="U", schema={})


def test_anthropic_cut_off_answer_still_reports_the_billed_usage() -> None:
    prov, _ = _provider([_resp("max_tokens", [_Block("text", text='{"claims": [')])])
    with pytest.raises(ProviderError, match="max_tokens") as caught:
        prov.structured(task="A3", tier=ModelTier.STRONG, system="S", user_content="U", schema={})
    assert caught.value.usage.total == 18


def test_unusable_answer_still_counts_against_budget_and_trace() -> None:
    class CutOffProvider:
        name, region = "stub", None

        def structured(self, **_: Any) -> Any:
            raise ProviderError("avbrutet vid max_tokens", usage=Usage(input_tokens=100, output_tokens=1_500))

    budget = InMemoryBudget()
    package = {"companies": [{"name": "Bygg & Co AB", "score": 80, "reasons": []}], "allowed": []}
    out = AIService(CutOffProvider(), budget=budget).run(  # type: ignore[arg-type]
        "A7", package, FactStore(), org_id="o", allowed_identifiers={"Bygg & Co AB"}
    )
    assert out.source == "rules"
    assert out.trace.error == "ProviderError: avbrutet vid max_tokens"
    assert (out.trace.usage["input_tokens"], out.trace.usage["output_tokens"]) == (100, 1_500)
    assert sum(budget.used.values()) == 1_600


def test_anthropic_tool_loop_with_budget() -> None:
    calls: list[dict[str, Any]] = []
    tool = ToolSpec(
        "t",
        "d",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        lambda inp: calls.append(inp) or {"ok": True},
    )
    responses = [
        _resp("tool_use", [_Block("tool_use", id="1", name="t"), _Block("tool_use", id="2", name="t")]),
        _resp("tool_use", [_Block("tool_use", id="3", name="t")]),
        _resp("end_turn", [_Block("text", text='{"claims": []}')]),
    ]
    prov, msgs = _provider(responses)
    res = prov.run_tools(
        task="A5",
        tier=ModelTier.STRONG,
        system="S",
        user_content="Q",
        tools=[tool],
        schema={},
        budget=ToolBudget(max_tool_calls=2, max_iterations=5),
    )
    assert len(calls) == 2  # tredje anropet nekas av budgeten
    assert msgs.calls[0]["tools"][0]["strict"] is True
    # Båda verktygsresultaten skickas i ett och samma meddelande
    assert len(msgs.calls[1]["messages"][-1]["content"]) == 2
    assert msgs.calls[2].get("tool_choice") == {"type": "none"}
    assert res.data == {"claims": []} and len(res.tool_calls) == 2


def test_failover_provider() -> None:
    class Down:
        name, region = "down", "eu-north-1"

        def structured(self, **_: Any) -> Any:
            raise ProviderUnavailable("regionalt avbrott")

    up = FakeProvider({"A3": lambda _: {"claims": []}})
    fo = FailoverProvider([Down(), up])  # type: ignore[list-item]
    res = fo.structured(task="A3", tier=ModelTier.STRONG, system="", user_content="", schema={})
    assert res.provider == "fake"


def test_eval_suite_passes_with_fake_provider() -> None:
    from redovisningai.ai.evals import run_evals

    results = run_evals(AIService(FakeProvider()))
    failed = [r.to_dict() for r in results if not r.passed]
    assert not failed, failed
    assert {r.task for r in results} == {"A1", "A2", "A3", "A4", "findings"}


def test_rule_based_fallbacks_survive_verification() -> None:
    """Reservtexterna (utan AI) måste klara verifieraren, annars blir svaret tomt."""
    service = AIService(None)
    brief_store = FactStore()
    company = {
        "name": "Bygg & Co AB",
        "open_findings": {"high": 6},
        "priority": {"score": 80, "reasons": [{"code": "high", "points": 30, "text": "6 allvarliga fynd"}]},
    }
    brief = service.run(
        "A7", brief_package([company], brief_store), brief_store, org_id="o", allowed_identifiers={"Bygg & Co AB"}
    )
    assert brief.source == "rules"
    assert [c["rendered"] for c in brief.data["claims"]] == ["Bygg & Co AB: 6 allvarliga fynd."]
    ask = service.run("A5", {"question": "Varför?"}, FactStore(), org_id="o")
    assert ask.data["claims"]
