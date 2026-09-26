"""Läsverktyget explain_metric_change: AI:n utreder förändringen bakom ett nyckeltal.

Allt verktyget returnerar av siffror ska vara fakta i analysens FactStore (så att AI:n bara kan
hänvisa, inte räkna), bidragen ska summera exakt till förändringen och lönekonton får bara
förekomma aggregerat.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from redovisningai.accounting.metrics import REGISTRY
from redovisningai.ai.providers.base import ToolSpec
from redovisningai.ai.tools import analyst_tools
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.facts.model import FactStore
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis, CompanyContext
from redovisningai.rules.engine import CompanySettings
from redovisningai.sie.convert import ledger_from_documents
from redovisningai.sie.parser import parse_sie
from sie_samples import MONTHLY_RENT, minimal_sie_with_missing_rent


def _minimal() -> CompanyAnalysis:
    ledger = ledger_from_documents([(parse_sie(minimal_sie_with_missing_rent()), "minimal.se")])
    return CompanyAnalysis(ledger, CompanyContext("org", "company", ledger.company_name, CompanySettings()))


def _bygg() -> CompanyAnalysis:
    g = generate(DEMO_PROFILES[0], date(2026, 10, 12))
    ctx = CompanyContext(
        "c1", "o1", g.ledger.company_name, CompanySettings(vat_period="quarter"), person_names=["Erik"]
    )
    return CompanyAnalysis(g.ledger, ctx)


def _tool(analysis: CompanyAnalysis, store: FactStore) -> ToolSpec:
    review = analysis.review(analysis.period("2026-09"))
    return next(
        t for t in analyst_tools(analysis, store, review.records, review.period) if t.name == "explain_metric_change"
    )


def _facts(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, dict):
        own = [obj] if "fact_id" in obj else []
        return own + [f for value in obj.values() for f in _facts(value)]
    if isinstance(obj, list):
        return [f for value in obj for f in _facts(value)]
    return []


def _value(store: FactStore, fact: dict[str, Any] | None) -> Decimal:
    found = store.get(fact["fact_id"]) if fact else None
    assert found is not None and found.value is not None
    return found.value


def test_contributions_are_citable_facts_that_add_up_to_the_change() -> None:
    store = FactStore()
    out = _tool(_minimal(), store).handler({"metric": "operating_result", "period": "2026-09", "compare": "2026-08"})

    assert out["status"] == "CALCULATED"
    change = _value(store, out["change"])
    effects = sum((_value(store, c["effect"]) for c in out["components"]), Decimal(0))
    others = _value(store, out["other_components"]["effect"]) if out["other_components"] else Decimal(0)
    assert change == effects + others == Decimal(MONTHLY_RENT)  # hyran föll bort i september
    rent = next(a for c in out["components"] for a in c["accounts"] if a["account"] == 5010)
    assert abs(_value(store, rent["change"])) == Decimal(MONTHLY_RENT)
    assert all(f["fact_id"] in store for f in _facts(out))


def test_contributions_are_accounting_components_the_verifier_accepts_as_explanations() -> None:
    store = FactStore()
    out = _tool(_minimal(), store).handler({"metric": "operating_result", "period": "2026-09", "compare": "2026-08"})
    kinds = {store.get(c["effect"]["fact_id"]).kind for c in out["components"]}  # type: ignore[union-attr]
    assert kinds == {"variance_component"}


def test_missing_comparison_month_gives_status_and_no_invented_change() -> None:
    store = FactStore()
    out = _tool(_minimal(), store).handler({"metric": "operating_result", "period": "2026-09", "compare": ""})
    assert out["status"] == "INSUFFICIENT_DATA"  # standard är samma månad i fjol, som saknas
    assert out["change"] is None and out["components"] == []


def test_ratio_bridge_adds_up_exactly_in_percentage_points() -> None:
    store = FactStore()
    out = _tool(_bygg(), store).handler({"metric": "operating_margin", "period": "2026-09", "compare": ""})
    assert out["status"] in ("CALCULATED", "PARTIAL")
    effects = sum((_value(store, c["effect"]) for c in out["components"]), Decimal(0))
    others = _value(store, out["other_components"]["effect"]) if out["other_components"] else Decimal(0)
    assert effects + others == _value(store, out["change"])


def test_payroll_accounts_are_only_given_in_aggregate() -> None:
    store = FactStore()
    out = _tool(_bygg(), store).handler({"metric": "operating_result", "period": "2026-09", "compare": ""})
    listed = [a["account"] for c in out["components"] for a in c["accounts"]]
    assert listed and not any(account in PAYROLL for account in listed)
    assert any(c["payroll_accounts"] for c in out["components"])


def test_schema_is_strict_and_offers_every_registered_metric() -> None:
    schema = _tool(_minimal(), FactStore()).input_schema
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["properties"]["metric"]["enum"]) == set(REGISTRY)
