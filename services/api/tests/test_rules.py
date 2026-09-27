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


def test_voucher_gaps_are_collapsed_into_ranges() -> None:
    from decimal import Decimal

    from redovisningai.domain.ledger import Row, Voucher

    g = generate(DEMO_PROFILES[3], date(2026, 10, 12))
    rows = (Row(6110, Decimal("100")), Row(1930, Decimal("-100")))
    g.ledger.current.vouchers.append(Voucher("D", "9000", date(2026, 9, 29), "Felnumrerad", rows))
    idx = LedgerIndex.build(g.ledger)
    p = month(2026, 9)
    ctx = RuleContext(g.ledger, idx, p, CompanySettings(), assess(idx, p))
    gaps = [f for f in run_rules(ctx, codes={"VOUCHER_NUMBER_GAP"})]
    assert len(gaps) == 1 and gaps[0].details["count"] > 8000


def test_maturity_invoice_method_when_customers_pay_same_month() -> None:
    """Kundfakturor som betalas samma månad ger nettot 0 på 1510 – ska ändå ge fakturametoden."""
    from pathlib import Path

    from redovisningai.accounting.balances import LedgerIndex
    from redovisningai.sie.convert import ledger_from_documents
    from redovisningai.sie.parser import parse_sie

    raw = (Path(__file__).parent / "fixtures" / "fortnox_like.se").read_bytes()
    idx = LedgerIndex.build(ledger_from_documents([(parse_sie(raw), "fortnox_like.se")]))
    assert assess(idx, month(2026, 8)).accounting_method.value == "invoice"


# --------------------------------------------------------------- dubblettregeln: grupper, inte par


def _dup_ctx(vouchers, period):  # type: ignore[no-untyped-def]
    from redovisningai.domain.ledger import Account, FiscalYear, Ledger, YearData

    names = {1930: "Bank", 2440: "Leverantörsskulder", 2641: "Ingående moms", 4010: "Varuinköp", 5410: "Förbrukning"}
    ledger = Ledger(
        "Syntetbolaget AB",
        None,
        {a: Account(a, n) for a, n in names.items()},
        [YearData(FiscalYear(date(2023, 1, 1), date(2023, 12, 31)), vouchers)],
    )
    idx = LedgerIndex.build(ledger)
    return RuleContext(
        ledger=ledger, index=idx, period=period, settings=CompanySettings(), maturity=assess(idx, period)
    )


def _invoice(key: str, day: date, amount: str, text: str):  # type: ignore[no-untyped-def]
    from decimal import Decimal

    from redovisningai.domain.ledger import Row, Voucher

    net, vat = Decimal(amount), Decimal(amount) / 4
    return Voucher("A", key, day, text, (Row(4010, net), Row(2641, vat), Row(2440, -(net + vat))))


def _duplicates(vouchers, period):  # type: ignore[no-untyped-def]
    return run_rules(_dup_ctx(vouchers, period), codes={"DUPLICATE_CANDIDATE"})


def test_a_daily_series_of_identical_invoices_is_not_reported_as_duplicates() -> None:
    """Två identiska fakturor per dag i en månad är en återkommande serie. Tidigare gav varje par
    inom 14 dagar ett eget fynd – det växer kvadratiskt och fick en riktig import att ta slut på minne."""
    series = [
        _invoice(f"{day:02d}{k}", date(2023, 1, day), "1000", "Leverantörsfaktura")
        for day in range(1, 31)
        for k in range(2)
    ]

    assert _duplicates(series, month(2023, 1)) == []


def test_an_invoice_booked_twice_gives_one_finding_with_both_vouchers() -> None:
    twice = [
        _invoice("300", date(2023, 3, 10), "5000", "Faktura Byggvaror 88213"),
        _invoice("301", date(2023, 3, 14), "5000", "Faktura Byggvaror 88213"),
        _invoice("302", date(2023, 5, 10), "5000", "Faktura Byggvaror 88999"),  # nästa månads faktura
    ]

    (finding,) = _duplicates(twice, month(2023, 3))

    assert finding.vouchers == ["A300", "A301"]
    assert finding.severity.value == "HIGH"  # identisk text
    assert finding.key == ("A300", "A301")  # samma identitet som tidigare parfynd


def test_three_bookings_close_in_time_give_one_finding_not_three_pairs() -> None:
    thrice = [_invoice(str(k), date(2023, 6, 5 + k), "2500", "Faktura Städ AB 4411") for k in range(3)]

    (finding,) = _duplicates(thrice, month(2023, 6))

    assert finding.vouchers == ["A0", "A1", "A2"]
    assert "3 verifikationer" in finding.title


def test_duplicate_findings_stay_bounded_however_many_identical_postings_there_are() -> None:
    crowded = [
        _invoice(f"{day:02d}{k:02d}", date(2023, 2, day), "1500", "Kortköp") for day in range(1, 29) for k in range(40)
    ]

    assert len(_duplicates(crowded, month(2023, 2))) <= len(crowded) // 2


def test_voucher_gaps_are_found_without_materialising_the_number_range() -> None:
    """En verklig export hade en serie med nummer 300 009 938–600 001 026. Regeln byggde en mängd av
    varje tal i spannet (300 miljoner) och importen tog slut på minne. Luckor ska räknas mellan
    intilliggande nummer, så minnet beror på antalet verifikationer och inte på spannet."""
    import tracemalloc
    from decimal import Decimal

    from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData

    rows = (Row(6110, Decimal("100")), Row(1930, Decimal("-100")))
    vouchers = [
        Voucher("S", "1", date(2023, 1, 5), "Första", rows),
        Voucher("S", "2", date(2023, 1, 6), "Andra", rows),
        Voucher("S", "5000000", date(2023, 1, 20), "Hopp i numreringen", rows),
    ]
    ledger = Ledger(
        "Syntetbolaget AB",
        None,
        {6110: Account(6110, "Kontorsmateriel"), 1930: Account(1930, "Bank")},
        [YearData(FiscalYear(date(2023, 1, 1), date(2023, 12, 31)), vouchers)],
    )
    idx = LedgerIndex.build(ledger)
    period = month(2023, 1)
    ctx = RuleContext(ledger, idx, period, CompanySettings(), assess(idx, period))

    tracemalloc.start()
    gaps = run_rules(ctx, codes={"VOUCHER_NUMBER_GAP"})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert [g.details["count"] for g in gaps] == [4_999_997]  # 3–4 999 999 saknas, ett fynd
    assert gaps[0].period == "2023-01"
    assert peak < 20 * 1024 * 1024  # tidigare hundratals MB för detta spann


def _numbered(numbers: list[str]):  # type: ignore[no-untyped-def]
    from datetime import timedelta
    from decimal import Decimal

    from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData

    rows = (Row(6110, Decimal("100")), Row(1930, Decimal("-100")))
    vouchers = [
        Voucher("S", number, date(2023, 1, 1) + timedelta(days=k % 28), f"Bunt {k}", rows)
        for k, number in enumerate(numbers)
    ]
    ledger = Ledger(
        "Syntetbolaget AB",
        None,
        {6110: Account(6110, "Kontorsmateriel"), 1930: Account(1930, "Bank")},
        [YearData(FiscalYear(date(2023, 1, 1), date(2023, 12, 31)), vouchers)],
    )
    idx = LedgerIndex.build(ledger)
    period = month(2023, 1)
    return run_rules(
        RuleContext(ledger, idx, period, CompanySettings(), assess(idx, period)), codes={"VOUCHER_NUMBER_GAP"}
    )


def test_many_reused_voucher_numbers_in_a_series_give_one_summary_finding() -> None:
    """Vissa system numrerar per dag eller bunt: en verklig export återanvände 4 253 nummer i samma
    serie och fick lika många fynd. Det ska synas som ett samlat fynd för serien."""
    found = _numbered([str(100 + k % 40) for k in range(200)])  # 40 nummer, vart och ett fem gånger

    (summary,) = found
    assert summary.title == "Nummerserie S: 40 verifikationsnummer förekommer flera gånger"
    assert summary.details == {"reused_numbers": 40, "vouchers": 200}
    assert summary.vouchers == ["S100", "S101", "S102", "S103", "S104"]


def test_a_few_reused_voucher_numbers_are_still_reported_one_by_one() -> None:
    found = _numbered(["1", "2", "2", "3", "3", "4"])

    assert [f.title for f in found] == ["Dubblett i nummerserie S: S2", "Dubblett i nummerserie S: S3"]
