from datetime import date, datetime
from decimal import Decimal

import pytest

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.periods import month, parse_period, same_period_previous_year
from redovisningai.analytics.budget import budget_vs_actual
from redovisningai.analytics.spend import Recurrence, detect_level_shift, detect_recurrence, spend_report
from redovisningai.analytics.tax_account import parse_tax_account_csv, reconcile_tax_account
from redovisningai.cases.builder import build_cases
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.findings.lifecycle import (
    FindingStatus,
    SuppressionRule,
    decide,
    precision_by_rule,
    reconcile,
)
from redovisningai.maturity.assess import assess
from redovisningai.memory.resolutions import apply_memory, remember
from redovisningai.portfolio.score import PortfolioInputs, score
from redovisningai.rules.engine import CompanySettings, RuleContext, run_rules

AS_OF = date(2026, 10, 12)


@pytest.fixture(scope="module")
def bygg():  # type: ignore[no-untyped-def]
    g = generate(DEMO_PROFILES[0], AS_OF)
    return g, LedgerIndex.build(g.ledger)


def _candidates(g, idx, y, m):  # type: ignore[no-untyped-def]
    p = month(y, m)
    ctx = RuleContext(g.ledger, idx, p, CompanySettings(vat_period=g.profile.vat_period), assess(idx, p))
    return run_rules(ctx)


def test_reconcile_keeps_decisions_and_autocloses(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    cands = _candidates(g, idx, 2026, 9)
    res = reconcile([], cands, "2026-09", now=datetime(2026, 10, 12))
    records = res.created
    assert records and all(r.status is FindingStatus.NEW for r in records)
    dup = next(r for r in records if r.rule_code == "DUPLICATE_CANDIDATE")
    decide(dup, FindingStatus.RESOLVED, "anna@byran.se", "Makulerad i Fortnox")
    # Samma körning igen: beslutet ligger kvar, inga nya fynd
    res2 = reconcile(records, cands, "2026-09", now=datetime(2026, 10, 13))
    assert not res2.created and dup.status is FindingStatus.RESOLVED
    # Dubbletten borttagen ur bokföringen → andra fynd för perioden stängs automatiskt när de försvinner
    remaining = [c for c in cands if c.rule_code != "SUSPENSE_ACCOUNT_BALANCE"]
    res3 = reconcile(records, remaining, "2026-09", now=datetime(2026, 10, 14))
    closed = [r for r in res3.auto_closed if r.rule_code == "SUSPENSE_ACCOUNT_BALANCE"]
    assert closed and closed[0].status is FindingStatus.AUTO_CLOSED


def test_accepted_ok_requires_note(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    rec = reconcile([], _candidates(g, idx, 2026, 9), "2026-09").created[0]
    with pytest.raises(ValueError):
        decide(rec, FindingStatus.ACCEPTED_OK, "anna")


def test_suppression_rule_and_aml_exempt(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    cands = _candidates(g, idx, 2026, 9)
    rule = SuppressionRule("s1", "TAX_ACCOUNT_BALANCE", "Stäms av kvartalsvis", "anna")
    aml = SuppressionRule("s2", "AML_ROUND_AMOUNTS_RELATED", "försök", "anna")
    res = reconcile([], cands, "2026-09", suppressions=[rule, aml])
    for r in res.created:
        if r.rule_code == "TAX_ACCOUNT_BALANCE":
            assert r.status is FindingStatus.SUPPRESSED
        if r.rule_code.startswith("AML_"):
            assert r.status is not FindingStatus.SUPPRESSED


def test_memory_suggests_same_decision_next_month() -> None:
    g = generate(DEMO_PROFILES[3], AS_OF)  # Nord Frakt: kvartalsvis försäkring
    idx = LedgerIndex.build(g.ledger)
    june = reconcile([], _candidates(g, idx, 2026, 6), "2026-06").created
    target = next((r for r in june if r.rule_code == "COST_DEVIATION" and 6310 in r.accounts), None)
    assert target is not None, [r.title for r in june]
    decide(target, FindingStatus.ACCEPTED_OK, "Anna", "Kvartalsvis försäkringspremie", now=datetime(2026, 7, 8))
    memory = [remember(target, "c1", g.ledger)]
    sept = reconcile([], _candidates(g, idx, 2026, 9), "2026-09").created
    n = apply_memory(sept, [m for m in memory if m], g.ledger, today=date(2026, 10, 12))
    hit = next(r for r in sept if r.rule_code == "COST_DEVIATION" and 6310 in r.accounts)
    assert n >= 1 and hit.memory_suggestion is not None
    assert "Kvartalsvis försäkringspremie" in hit.memory_suggestion["text"]
    assert "juli 2026" in hit.memory_suggestion["text"]


def test_cases_group_related_findings(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    recs = reconcile([], _candidates(g, idx, 2026, 9), "2026-09").created
    cases = build_cases(recs)
    assert len(cases) < len(recs)
    dup_case = next(c for c in cases if any(f.rule_code == "DUPLICATE_CANDIDATE" for f in c.findings))
    assert dup_case.severity == "HIGH"
    assert "dubbelbokning" in dup_case.title.lower()
    aml_cases = [c for c in cases if c.visibility == "RESTRICTED_AML"]
    for c in aml_cases:
        assert all(f.rule_code.startswith("AML_") for f in c.findings)
        assert not c.ask_client_suggested


def test_precision_stats(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    recs = reconcile([], _candidates(g, idx, 2026, 9), "2026-09").created
    decide(recs[0], FindingStatus.RESOLVED, "a", "x")
    decide(recs[1], FindingStatus.ACCEPTED_OK, "a", "ok")
    stats = {p.rule_code: p for p in precision_by_rule(recs)}
    assert stats[recs[0].rule_code].actioned >= 1


def test_portfolio_score_explains_itself() -> None:
    s = score(
        PortfolioInputs(
            open_high=2, changed_after_approval=True, margin_change_pp=Decimal("-6.2"), last_review=date(2026, 8, 1)
        ),
        today=date(2026, 10, 12),
    )
    assert s.score == 20 + 15 + 8 + 5
    assert any("Ändrad efter godkännande" in r["text"] for r in s.reasons)


def test_recurrence_and_level_shift() -> None:
    D = Decimal
    assert detect_recurrence([D(100)] * 12) is Recurrence.MONTHLY
    assert (
        detect_recurrence([D(0), D(0), D(5), D(0), D(0), D(5), D(0), D(0), D(5), D(0), D(0), D(5)])
        is Recurrence.QUARTERLY
    )
    assert detect_recurrence([D(0)] * 11 + [D(9)]) is Recurrence.ONE_OFF
    months = [date(2026, m, 1) for m in range(1, 10)]
    shift = detect_level_shift(months, [D(x) for x in (72, 69, 73, 113, 115, 111, 112, 114, 110)])
    assert shift is not None and shift.month == date(2026, 4, 1)


def test_spend_report_finds_new_consultant_and_microsoft_shift(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    p = parse_period("YTD:2026-09", g.ledger)
    rep = spend_report(idx, p, same_period_previous_year(p))
    assert any("Konsultgruppen" in n["name"] for n in rep.new_costs)
    ms = next(c for c in rep.counterparties if c["key"] == "microsoft")
    assert ms["recurrence"] == "monthly" and ms["annualized"] is not None
    assert any(s["key"] == "microsoft" and s["month"] == "2026-04-01" for s in rep.level_shifts)
    assert Decimal(rep.concentration["top5"]) > 0


def test_budget_vs_actual(bygg) -> None:  # type: ignore[no-untyped-def]
    g, idx = bygg
    lines = budget_vs_actual(idx, parse_period("YTD:2026-09", g.ledger))
    ns = next(ln for ln in lines if ln.code == "net_sales")
    assert ns.budget > 0 and ns.actual > 0


def test_tax_account_reconciliation(bygg) -> None:  # type: ignore[no-untyped-def]
    _, idx = bygg
    csv_text = (
        "Datum;Transaktion;Belopp\n2026-09-12;Inbetalning;100 000,00\n2026-09-12;Debiterad preliminärskatt;-5,00\n"
    )
    tx = parse_tax_account_csv(csv_text)
    assert tx[0].amount == Decimal("100000.00")
    rec = reconcile_tax_account(idx, tx, date(2026, 9, 30))
    assert rec.unmatched_skv  # testbeloppen finns inte i bokföringen
    assert rec.difference == rec.book_balance - rec.skv_balance
