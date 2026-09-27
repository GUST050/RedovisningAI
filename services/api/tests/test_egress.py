import json
import re
from datetime import date
from decimal import Decimal

import pytest

from leak_checks import leaks, strings
from redovisningai.ai.egress import CounterpartyPseudonyms, EgressGuard, render_counterparties
from redovisningai.ai.providers.base import ToolSpec
from redovisningai.ai.service import AIService, FakeProvider
from redovisningai.ai.tasks import period_commentary_input
from redovisningai.ai.tools import analyst_tools
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData
from redovisningai.facts.model import FactStore
from redovisningai.review.analysis import CompanyAnalysis, CompanyContext
from redovisningai.rules.engine import CompanySettings

AS_OF = date(2026, 10, 12)


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


def _tools(analysis, guard):  # type: ignore[no-untyped-def]
    period = analysis.period("2026-09")
    return {t.name: guard.wrap_tool(t) for t in analyst_tools(analysis, FactStore(), [], period)}


def test_a5_tools_send_codes_not_counterparty_names_or_voucher_text(analysis) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A5", analysis)
    tools = _tools(analysis, guard)

    outputs = [
        tools["get_counterparty_spend"].handler({"period": "2026-09"}),
        tools["get_account_movements"].handler({"period": "2026-09", "account": 6110}),
        tools["list_changes_since"].handler({"since": "2026-09-01"}),
    ]

    names = guard.pseudonyms.known_names()
    assert "Staples" in names and not leaks(strings(outputs), names)
    assert re.search(r"\bM\d+\b", strings(outputs))
    assert '"text"' not in json.dumps(outputs, ensure_ascii=False)


def test_a_counterparty_keeps_its_code_across_tool_calls_and_periods(analysis) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A5", analysis)
    spend = _tools(analysis, guard)["get_counterparty_spend"]
    unknown = {"Okänd motpart"}  # rader utan motpart (bankavgifter, omföringar): ingen motpart, ingen kod

    september = {c["name"] for c in spend.handler({"period": "2026-09"})["counterparties"]} - unknown
    august = {c["name"] for c in spend.handler({"period": "2026-08"})["counterparties"]} - unknown

    assert september & august
    assert (september | august) <= guard.pseudonyms.codes()


def test_names_in_the_retry_feedback_are_sent_as_codes(analysis, review) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A3", analysis)
    claim = {"type": "OBSERVATION", "text": "Staples fakturerade 12 kr", "fact_ids": []}
    provider = FakeProvider({"A3": lambda _: {"claims": [claim]}})
    package = period_commentary_input(analysis.commentary_package(review))

    AIService(provider, max_attempts=2).run("A3", package, review.store, org_id="o", company_id="c", egress=guard)

    assert len(provider.calls) == 2  # "12 kr" underkänns, så återkopplingen skickas
    feedback = provider.calls[1]["user_content"].split("underkändes av granskaren", 1)[1]
    assert not leaks(feedback, ["Staples"])
    assert re.search(r"\bM\d+ fakturerade\b", feedback)


def test_a_package_field_outside_the_task_allowlist_stops_the_call(analysis, review) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A3", analysis)
    provider = FakeProvider()
    package = {**period_commentary_input(analysis.commentary_package(review)), "vouchers": [{"text": "Faktura"}]}

    out = AIService(provider).run("A3", package, review.store, org_id="o", company_id="c", egress=guard)

    assert provider.calls == []
    assert out.source == "rules" and out.trace.error == "AI-gränsen stoppade sändningen"


def test_internal_answers_get_names_back_for_codes() -> None:
    names = {"M1": "Byggvaruhuset AB"}

    text = render_counterparties("{m:M1} och M1 ökade, M12 är okänd", names)

    assert text == "Byggvaruhuset AB och Byggvaruhuset AB ökade, M12 är okänd"


# ------------------------------------------------------------------ utöver planens prov


def _tool(output: object) -> ToolSpec:
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    return ToolSpec("t", "Testverktyg", schema, lambda _: output)


def _ledger(*texts: str) -> Ledger:
    year = YearData(FiscalYear(date(2026, 1, 1), date(2026, 12, 31)))
    year.vouchers = [
        Voucher("A", str(n), date(2026, 9, n), text, (Row(5010, Decimal(100)), Row(1930, Decimal(-100))))
        for n, text in enumerate(texts, 1)
    ]
    accounts = {5010: Account(5010, "Lokalhyra"), 1930: Account(1930, "Företagskonto")}
    return Ledger("Test AB", None, accounts, [year])


def test_counterparty_keys_are_masked_like_their_names(analysis) -> None:  # type: ignore[no-untyped-def]
    # Nedbrytningen visar motpartens nyckel, t.ex. "amazon web" för "Amazon Web Services".
    guard = EgressGuard.for_task("A5", analysis)

    movements = _tools(analysis, guard)["get_account_movements"].handler({"period": "2026-09", "account": 6540})

    assert "amazon web" in guard.pseudonyms.known_names()
    assert not leaks(strings(movements), guard.pseudonyms.known_names())


def test_a_confirmed_alias_and_the_voucher_text_share_one_code(analysis) -> None:  # type: ignore[no-untyped-def]
    pseudonyms = CounterpartyPseudonyms.from_ledger(analysis.ledger, {"staples": "Kontorsjätten AB"})

    masked = pseudonyms.mask("Kontorsjätten AB och Staples")

    assert masked == "M1 och M1"
    assert pseudonyms.names() == {"M1": "Kontorsjätten AB"}


def test_account_names_prefixes_and_numbers_are_not_counterparty_names() -> None:
    ledger = _ledger("Faktura 12", "Lokalhyra", "Swish", "Leverantörsfaktura Kvarnen AB")
    pseudonyms = CounterpartyPseudonyms.from_ledger(ledger, {})

    masked = pseudonyms.mask("Lokalhyra 2026-12 till Kvarnen AB, 12 kr via Swish")

    assert pseudonyms.known_names() == {"Kvarnen", "Kvarnen AB", "kvarnen"}
    assert masked == "Lokalhyra 2026-12 till M1, 12 kr via Swish"


def test_tool_answers_lose_free_text_and_payroll_rows(analysis) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A5", analysis)
    output = {
        "text": "Leverantörsfaktura Staples",
        "rows": [
            {"account": 7210, "name": "Löner till tjänstemän"},
            {"account": 6110, "name": "Staples", "description": "Kontorsmaterial från Staples"},
        ],
    }

    answer = guard.wrap_tool(_tool(output)).handler({})

    assert answer == {"rows": [{"account": 6110, "name": "M1"}]}


def test_a_personal_number_in_a_tool_answer_stops_the_run(analysis, caplog) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A5", analysis)
    provider = FakeProvider()
    package = {"question": "Hur går det?", "default_period": "2026-09", "months_with_data": []}
    leaky = _tool({"reference": 8112189876})  # ett tal maskeras inte som text; kontrollen före sändning tar det

    out = AIService(provider).run("A5", package, FactStore(), org_id="o", company_id="c", tools=[leaky], egress=guard)

    assert provider.calls == [] and out.source == "rules"
    assert out.trace.error == "AI-gränsen stoppade sändningen"
    assert "A5" in caplog.text and "8112189876" not in caplog.text


def test_names_in_the_consultants_question_are_sent_as_codes(analysis) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A5", analysis)
    provider = FakeProvider()
    package = {"question": "Varför ökade Staples?", "default_period": "2026-09", "months_with_data": []}

    AIService(provider).run("A5", package, FactStore(), org_id="o", company_id="c", egress=guard)

    assert provider.calls[0]["user_content"].split("\n", 1)[0] == "Fråga från konsulten: Varför ökade M1?"


def test_client_text_keeps_the_codes_and_internal_text_gets_the_names(analysis) -> None:  # type: ignore[no-untyped-def]
    guard = EgressGuard.for_task("A4", analysis)

    masked = guard.mask_text("Fråga om fakturan från Staples")

    assert masked == "Fråga om fakturan från M1"
    assert guard.unmask(masked, client_facing=True) == masked
    assert guard.unmask(masked, client_facing=False) == "Fråga om fakturan från Staples"
