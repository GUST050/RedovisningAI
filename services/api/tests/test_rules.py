from datetime import date

import pytest

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.periods import month
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.facts.model import Visibility
from redovisningai.maturity.assess import AccountingMethod, PeriodStatus, assess
from redovisningai.rules.engine import (
    CompanySettings,
    RuleContext,
    default_catalog,
    registered_rules,
    run_rules,
)


def _run(profile_idx: int, y: int, m: int, as_of: date = date(2026, 10, 12), **settings):  # type: ignore[no-untyped-def]
    g = generate(DEMO_PROFILES[profile_idx], as_of)
    idx = LedgerIndex.build(g.ledger)
    p = month(y, m)
    mat = assess(idx, p)
    s = CompanySettings(
        vat_period=g.profile.vat_period,
        food_retail=g.profile.food_retail,
        industry=g.profile.industry,
        **settings,
    )
    ctx = RuleContext(ledger=g.ledger, index=idx, period=p, settings=s, maturity=mat)
    return g, run_rules(ctx)


def test_every_catalog_rule_is_implemented() -> None:
    impl = registered_rules()
    missing = [c for c in default_catalog() if c not in impl and c != "CHANGED_AFTER_APPROVAL"]
    assert missing == []


def test_catalog_rules_have_legal_basis_and_owner() -> None:
    for rd in default_catalog().values():
        assert rd.legal_basis and rd.owner and rd.reviewed_at, rd.code


@pytest.fixture(scope="module")
def bygg_findings():  # type: ignore[no-untyped-def]
    out = {}
    for m in range(1, 10):
        _, found = _run(0, 2026, m)
        for f in found:
            out.setdefault(f.rule_code, []).append(f)
    return out


@pytest.mark.parametrize(
    "code",
    [
        "UNBALANCED_VOUCHER",
        "IB_NE_PREV_UB",
        "VOUCHER_NUMBER_GAP",
        "LATE_BOOKING",
        "ACCOUNT_NOT_IN_CHART",
        "ABNORMAL_SIGN",
        "VAT_ACCOUNTS_NOT_CLEARED",
        "EMPLOYER_CONTRIBUTION_RATIO",
        "SUSPENSE_ACCOUNT_BALANCE",
        "ASSETS_WITHOUT_DEPRECIATION",
        "TAX_ALLOCATION_RESERVE_DUE",
        "RELATED_PARTY_RECEIVABLE",
        "DUPLICATE_CANDIDATE",
        "LARGE_MANUAL_POSTING",
        "RAPID_REVERSAL",
    ],
)
def test_planted_anomalies_are_found(bygg_findings, code: str) -> None:  # type: ignore[no-untyped-def]
    assert code in bygg_findings, f"{code} hittades inte"


def test_duplicate_is_exact_high(bygg_findings) -> None:  # type: ignore[no-untyped-def]
    dups = bygg_findings["DUPLICATE_CANDIDATE"]
    assert any(f.severity == "HIGH" and "Byggvaruhuset" in f.description for f in dups)


def test_findings_have_rendered_facts(bygg_findings) -> None:  # type: ignore[no-untyped-def]
    for fs in bygg_findings.values():
        for f in fs:
            fact_ids = {x.id for x in f.facts}
            import re

            for ref in re.findall(r"\{f:([^}]+)\}", f.description):
                assert ref in fact_ids, (f.rule_code, ref)


def test_aml_findings_are_restricted(bygg_findings) -> None:  # type: ignore[no-untyped-def]
    aml = [f for code, fs in bygg_findings.items() if code.startswith("AML_") for f in fs]
    assert aml, "Planterat jämnt belopp mot närstående ska ge PTL-signal"
    assert all(f.visibility is Visibility.RESTRICTED_AML for f in aml)
    assert all(x.visibility is Visibility.RESTRICTED_AML for f in aml for x in f.facts)


def test_fingerprints_are_stable_between_runs() -> None:
    _, a = _run(0, 2026, 9)
    _, b = _run(0, 2026, 9)
    assert sorted(f.fingerprint for f in a) == sorted(f.fingerprint for f in b)


def test_clean_company_has_few_findings() -> None:
    _, found = _run(3, 2026, 9)  # Nord Frakt – inga planterade fel
    serious = [f for f in found if f.severity == "HIGH"]
    assert serious == [], [f.title for f in serious]


def test_food_vat_rule_after_cut() -> None:
    _, may = _run(1, 2026, 5)
    food = [f for f in may if f.rule_code == "OUTPUT_VAT_RATIO"]
    assert food and "livsmedel" in food[0].description
    _, june = _run(1, 2026, 6)
    assert not any(f.rule_code == "OUTPUT_VAT_RATIO" for f in june)
    _, march = _run(1, 2026, 3)  # 12 % var rätt före 1 april
    assert not any(f.rule_code == "OUTPUT_VAT_RATIO" for f in march)


def test_maturity_cash_method_and_year_end_accruals() -> None:
    g = generate(DEMO_PROFILES[2], date(2026, 9, 30))
    idx = LedgerIndex.build(g.ledger)
    m = assess(idx, month(2026, 9))
    assert m.accounting_method is AccountingMethod.CASH
    assert m.monthly_depreciation is False
    assert m.low_periodization and m.recommended_view == "r12"


def test_maturity_preliminary_when_payroll_missing() -> None:
    g = generate(DEMO_PROFILES[3], date(2026, 9, 24))  # löner bokas den 25:e
    idx = LedgerIndex.build(g.ledger)
    m = assess(idx, month(2026, 9))
    assert m.status is PeriodStatus.PRELIMINARY
    assert m.payroll_booked is False
