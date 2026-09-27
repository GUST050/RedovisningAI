import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from redovisningai.ai.egress import EgressViolation
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

    outcome = service.run("A3", period_commentary_input({"company": "Hemligt AB"}), FactStore(), org_id="org-1")

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


def test_a3_can_cite_maturity_and_open_cases_as_internal_facts(analysis, review) -> None:  # type: ignore[no-untyped-def]
    # Instruktionen ber A3 säga om perioden är preliminär; då måste det finnas ett faktum att hänvisa till.
    package = analysis.commentary_package(review)
    projected = period_commentary_input({**package, "findings": []})
    ids = {projected["maturity"]["fact_id"], projected["open_cases"]["fact_id"]}
    assert None not in ids and ids <= {f["id"] for f in projected["facts"]}
    assert all(review.store.get(i).visibility is Visibility.INTERNAL for i in ids)  # type: ignore[union-attr]
    client = analysis.client_package(review)
    assert not ids & {f["id"] for f in client["facts"]} and "fact_id" not in client["maturity"]  # aldrig hos kund


def test_a3_allows_the_account_numbers_its_package_shows(analysis, review) -> None:  # type: ignore[no-untyped-def]
    from redovisningai.review.commentary import a3_allowed_identifiers

    projected = period_commentary_input({**analysis.commentary_package(review), "findings": []})
    shown = {str(a) for f in projected["facts"] for a in f.get("accounts", [])}
    assert shown and shown <= a3_allowed_identifiers(projected)


def _keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_a3_tool_shows_the_bridge_behind_a_metric_without_names_or_text(analysis, review) -> None:  # type: ignore[no-untyped-def]
    # A3:s gräns: koder, kontonummer och fakta – inga etiketter, kontonamn, fritext eller verifikationer.
    from redovisningai.ai.tools import commentary_tools

    tools = {t.name: t for t in commentary_tools(analysis, review.store, review.period)}
    assert set(tools) == {"explain_metric_change"}
    out = tools["explain_metric_change"].handler({"metric": "operating_result", "period": "2026-09", "compare": ""})
    assert out["components"]
    assert not {"label", "name", "note", "vouchers", "text"} & _keys(out)
    refs = [c["effect"] for c in out["components"]] + [a["change"] for c in out["components"] for a in c["accounts"]]
    assert all(set(r) <= {"fact_id", "display", "status"} and r["fact_id"] in review.store for r in refs)


def test_a3_can_use_its_bounded_tool_and_name_accounts(analysis, review) -> None:  # type: ignore[no-untyped-def]
    from redovisningai.ai.providers.base import StructuredResult
    from redovisningai.review.commentary import build_commentary

    seen: dict[str, Any] = {}

    class Recorder(FakeProvider):
        def run_tools(self, **kwargs: Any) -> Any:
            seen["tools"], seen["budget"] = [t.name for t in kwargs["tools"]], kwargs["budget"]
            question = {"type": "QUESTION", "text": "Kan ni stämma av konto 1930 och konto 2440?", "fact_ids": []}
            return StructuredResult({"claims": [question]}, Usage(1, 1), "fake", "m", None, [])

    result = build_commentary(analysis, review, ai=AIService(Recorder()), org_id="o", company_id="c")

    assert seen["tools"] == ["explain_metric_change"] and seen["budget"].max_tool_calls <= 2
    assert [c["text"] for c in result["data"]["claims"]] == ["Kan ni stämma av konto 1930 och konto 2440?"]


def test_a_year_right_before_a_fact_reference_is_not_a_typed_number() -> None:
    assert find_literal_numbers("Inga personalkostnader för sep 2026 {f:line_personnel_1}.", set()) == []


def test_client_meeting_package_sends_only_what_the_text_needs(analysis, review) -> None:  # type: ignore[no-untyped-def]
    # Råvärden, härkomst (lineage) och interna fält kostar tokens och lockar till egna siffror.
    facts = analysis.client_package(review)["facts"]
    assert facts and all(set(f) <= {"id", "label", "display", "status", "period", "compare_period"} for f in facts)
    assert all(review.store.get(f["id"]) is not None for f in facts)


def test_weekly_brief_runs_on_the_light_model_tier() -> None:
    from redovisningai.ai.providers.base import ModelTier as Tier
    from redovisningai.ai.tasks import TASKS

    assert TASKS["A7"].spec.tier is Tier.SMALL


def test_a_list_of_account_numbers_is_not_one_decimal_number() -> None:
    # "2440, 2611" är en uppräkning av konton; svenska decimaltal skrivs utan mellanslag ("12,5").
    allowed = {"2440", "2611", "2641", "2920"}
    assert find_literal_numbers("Granska kontona 2440, 2611, 2641 och 2920.", allowed) == []
    assert find_literal_numbers("Marginalen var 12,5 % och kostnaden 1 234 kr.", allowed) == ["12,5 %", "1 234 kr"]


def test_account_names_that_contain_rates_are_not_typed_numbers(analysis) -> None:  # type: ignore[no-untyped-def]
    # "7510 Arbetsgivaravgifter 31,42 %" är ett kontonamn, inte en siffra som AI:n hittat på.
    named = {a.name for a in analysis.ledger.accounts.values() if any(ch.isdigit() for ch in a.name)}
    assert named and named <= analysis.allowed_identifiers()
    allowed = {"7510", "Arbetsgivaravgifter 31,42 %"}
    assert find_literal_numbers("Kostnaden på 7510 Arbetsgivaravgifter 31,42 % saknas i perioden.", allowed) == []
    assert find_literal_numbers("Avgifterna motsvarar 31,42 % av lönerna.", allowed)  # utan kontonamnet: egen siffra


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


def test_a_bridge_fact_never_makes_a_business_cause_an_explanation() -> None:
    """Planen §9.8: en korrekt bokföringsbrygga bevisar inte en affärsorsak (pris, volym, kunder,
    leverantörer, personalstyrka eller omvärld) – sådant blir en märkt hypotes."""
    s = _store()
    comp = next(f.id for f in s if f.kind == "variance_component")
    ref = "{f:" + comp + "}"
    texts = [
        ("EXPLANATION", "Resultatet förklaras av högre konsultkostnader " + ref + "."),
        ("EXPLANATION", "Kostnaden ökade eftersom leverantören höjde priset " + ref + "."),
        ("EXPLANATION", "Omsättningen föll på grund av lägre efterfrågan " + ref + "."),
        ("OBSERVATION", "Kunden har lagt färre order " + ref + "."),
        ("OBSERVATION", "Fler anställda ger högre personalkostnader " + ref + "."),
        ("OBSERVATION", "Kundfordringar, leverantörsskulder och kundförluster ökade " + ref + "."),
        ("EXPLANATION", "Avskrivningarna sänkte resultatet " + ref + "."),
    ]

    res = verify_claims([{"type": t, "text": text, "fact_ids": [comp]} for t, text in texts], s)

    assert [c["type"] for c in res.accepted] == [
        "EXPLANATION",  # bryggförklaring
        "HYPOTHESIS",  # pris
        "HYPOTHESIS",  # efterfrågan
        "HYPOTHESIS",  # kund och order
        "HYPOTHESIS",  # personalstyrka
        "OBSERVATION",  # kontonamn är inte affärshändelser
        "EXPLANATION",
    ]
    assert res.downgraded == 4


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


def test_pseudonymizer_masks_genitive_person_names() -> None:
    p = Pseudonymizer(["Erik"])
    text = "Eriks utlägg och Erik:s kvitto, men inte Eriksson."

    masked = p.mask(text)

    assert masked == "PERSON_1s utlägg och PERSON_1:s kvitto, men inte Eriksson."
    assert p.unmask(masked) == text


# ------------------------------------------------------------------ tjänsten


def test_service_without_provider_uses_rules(analysis, review) -> None:  # type: ignore[no-untyped-def]
    svc = AIService(None)
    out = svc.run("A3", analysis.commentary_package(review), review.store, org_id="o1")
    assert out.source == "rules" and out.data["claims"]
    assert all("rendered" in c for c in out.data["claims"])
    assert all("{f:" not in c["rendered"] for c in out.data["claims"])


def test_service_regenerates_then_drops_bad_claims(analysis, review) -> None:  # type: ignore[no-untyped-def]
    pkg = period_commentary_input(analysis.commentary_package(review))
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

        def run_tools(self, **kwargs: Any) -> Any:  # A3 har läsverktyg; gränsen gäller även där
            seen.append(kwargs["max_tokens"])
            return super().run_tools(**kwargs)

    prov = CapturingProvider(
        {"A3": lambda _: {"claims": [{"type": "OBSERVATION", "text": "Omsättningen ökade 17 %.", "fact_ids": []}]}}
    )
    out = AIService(prov, max_output_tokens=1_500, max_attempts=1).run(
        "A3", period_commentary_input(analysis.commentary_package(review)), review.store, org_id="o1"
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


def test_anthropic_tool_loop_stops_at_the_ai_boundary() -> None:
    def leaky(_: dict[str, Any]) -> Any:
        raise EgressViolation("personnummer i utgående data")

    tool = ToolSpec(
        "t", "d", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, leaky
    )
    prov, msgs = _provider([_resp("tool_use", [_Block("tool_use", id="1", name="t")])])

    with pytest.raises(EgressViolation):
        prov.run_tools(
            task="A5",
            tier=ModelTier.STRONG,
            system="S",
            user_content="Q",
            tools=[tool],
            schema={},
            budget=ToolBudget(),
        )

    assert len(msgs.calls) == 1  # verktygssvaret skickas aldrig till modellen


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
    assert {r.task for r in results} == {"A1", "A2", "A3", "A4", "findings", "locked"}


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
