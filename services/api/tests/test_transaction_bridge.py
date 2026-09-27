from datetime import date
from decimal import Decimal

import pytest

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair, validate_comparison
from redovisningai.accounting.periods import month
from redovisningai.accounting.transaction_bridge import TransactionBridge, transaction_bridge
from redovisningai.accounting.variance import drilldown
from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, Voucher, YearData
from redovisningai.facts.model import FactStatus, FactStore

PAIR = ComparisonPair(month(2026, 9), month(2025, 9), FactStatus.CALCULATED)


def _v(number: str, day: date, amount: str, text: str | None, voucher_text: str = "Faktura") -> Voucher:
    value = Decimal(amount)
    return Voucher("A", number, day, voucher_text, (Row(6110, value, text=text), Row(2440, -value)))


def _index(vouchers: list[Voucher]) -> LedgerIndex:
    accounts = {n: Account(n, f"Konto {n}") for n in (1930, 2440, 2990, 6110)}
    years = [
        YearData(FiscalYear(date(y, 1, 1), date(y, 12, 31)), [v for v in vouchers if v.date.year == y])
        for y in (2025, 2026)
    ]
    return LedgerIndex.build(Ledger("Syntetbolaget AB", None, accounts, years))


def _bridge(vouchers: list[Voucher]) -> TransactionBridge:
    return transaction_bridge(
        _index(vouchers), {6110}, PAIR, target="account:6110", aliases={}, sign=1, store=FactStore()
    )


def test_parts_sum_exactly_with_credit_notes_and_unknown_counterparties() -> None:
    # Jämförelse: A 2 × 1 000 och C 700 = 2 700. Aktuell: A 3 × 1 100 och en kreditfaktura på −200,
    # B 500 och en rad utan motpart på 300 = 3 900. Förändring 1 200 = A +1 100, B +500, C −700, okänd +300.
    bridge = _bridge(
        [
            _v("1", date(2025, 9, 5), "1000", "Leverantör A"),
            _v("2", date(2025, 9, 20), "1000", "Leverantör A"),
            _v("3", date(2025, 9, 12), "700", "Leverantör C"),
            _v("1", date(2026, 9, 4), "1100", "Leverantör A"),
            _v("2", date(2026, 9, 11), "1100", "Leverantör A"),
            _v("3", date(2026, 9, 18), "1100", "Leverantör A"),
            _v("4", date(2026, 9, 25), "-200", "Leverantör A", "Kreditfaktura"),
            _v("5", date(2026, 9, 14), "500", "Leverantör B"),
            _v("6", date(2026, 9, 28), "300", None, "Diverse"),
        ]
    )
    parts = {p.code: p for p in bridge.parts}

    assert bridge.change == Decimal("1200") == sum(p.effect for p in bridge.parts)
    assert [parts[c].effect for c in ("both", "current_only", "previous_only", "unknown")] == [
        Decimal("1100"),
        Decimal("500"),
        Decimal("-700"),
        Decimal("300"),
    ]
    # A: 2 → 4 verifikationer, snitt 1 000 → 775. X = 4 × 2 000 / 2 = 4 000.
    assert (parts["both"].previous_count, parts["both"].current_count) == (2, 4)
    assert (parts["both"].count_effect, parts["both"].amount_effect) == (Decimal("2000"), Decimal("-900"))
    # Absoluta belopp i båda perioderna: 2 700 + 4 300 = 7 000, varav 300 oidentifierat.
    assert bridge.identified_share_abs["unknown"] == Decimal("300") / Decimal("7000")
    assert bridge.signals == {}


def test_an_empty_comparison_period_and_reused_voucher_numbers_still_reconcile() -> None:
    # Jämförelseperioden saknar verifikationer; källsystemet återanvänder nummer A1 på två datum.
    bridge = _bridge(
        [
            _v("1", date(2026, 9, 3), "400", "Städbolaget"),
            _v("1", date(2026, 9, 17), "400", "Städbolaget"),
        ]
    )
    parts = {p.code: p for p in bridge.parts}

    assert bridge.change == Decimal("800") == sum(p.effect for p in bridge.parts)
    assert (parts["current_only"].current_count, parts["current_only"].previous_count) == (2, 0)
    assert parts["both"].effect == parts["previous_only"].effect == parts["unknown"].effect == Decimal("0")


def test_reversals_and_large_bookings_are_signals_not_extra_amounts() -> None:
    # En periodisering 31 augusti återförs 1 september; A har en stor bokning på 30 000 i september.
    accrual = Voucher(
        "A",
        "9",
        date(2026, 8, 31),
        "Periodisering",
        (Row(6110, Decimal("5000"), text="Konsult"), Row(2990, Decimal("-5000"))),
    )
    reversal = Voucher(
        "A",
        "10",
        date(2026, 9, 1),
        "Återföring",
        (Row(2990, Decimal("5000")), Row(6110, Decimal("-5000"), text="Konsult")),
    )
    bridge = _bridge(
        [
            _v("1", date(2025, 9, 5), "1000", "Leverantör A"),
            accrual,
            reversal,
            _v("11", date(2026, 9, 5), "1000", "Leverantör A"),
            _v("12", date(2026, 9, 15), "30000", "Leverantör A"),
        ]
    )
    groups = {g.name: g for g in bridge.groups}

    assert bridge.change == Decimal("25000") == sum(p.effect for p in bridge.parts)
    assert bridge.signals == {
        "large_booking": Decimal("30000"),
        "reversal": Decimal("-5000"),
        "periodization": Decimal("-5000"),
    }
    assert "large_booking" in groups["Leverantör A"].signals
    assert {"reversal", "periodization"} <= groups["Konsult"].signals


def test_large_booking_is_judged_per_account_not_per_target_basket() -> None:
    # En resultatrad eller kategori kan omfatta flera konton. 6 000 på 5010 och 6 000 på 5020 i
    # samma verifikation är inte en enskild bokning på 12 000 – varje konto bedöms för sig. Ett
    # konto som på egen hand går över tröskeln (25 % och minst 10 000 kr) flaggas ändå.
    vouchers = [
        Voucher(
            "A",
            "1",
            date(2026, 9, 10),
            "Diverse",
            (Row(5010, Decimal("6000")), Row(5020, Decimal("6000")), Row(1930, Decimal("-12000"))),
        ),
        Voucher(
            "A",
            "2",
            date(2026, 9, 20),
            "Stor faktura",
            (Row(5010, Decimal("20000")), Row(1930, Decimal("-20000"))),
        ),
    ]
    index = _index(vouchers)

    bridge = transaction_bridge(
        index, {5010, 5020}, PAIR, target="line:other_external", aliases={}, sign=1, store=FactStore()
    )

    assert bridge.signals == {"large_booking": Decimal("20000")}


def test_large_booking_badge_does_not_spread_to_other_account_in_same_voucher() -> None:
    vouchers = [
        Voucher(
            "A",
            "1",
            date(2026, 9, 10),
            "Faktura",
            (
                Row(6110, Decimal("20000"), text="Big Firm"),
                Row(6540, Decimal("100"), text="Tiny Firm"),
                Row(1930, Decimal("-20100")),
            ),
        ),
        Voucher(
            "A",
            "2",
            date(2026, 9, 20),
            "Faktura",
            (
                Row(6540, Decimal("900"), text="Tiny Firm"),
                Row(1930, Decimal("-900")),
            ),
        ),
    ]
    index = _index(vouchers)
    bridge = transaction_bridge(
        index, {6110, 6540}, PAIR, target="line:other_external", aliases={}, sign=1, store=FactStore()
    )

    groups = {group.name: group for group in bridge.groups}
    assert "large_booking" in groups["Big Firm"].signals
    assert "large_booking" not in groups["Tiny Firm"].signals
    assert bridge.signals["large_booking"] == Decimal("20000")


def test_summary_only_comparison_cannot_claim_a_transaction_bridge() -> None:
    accounts = {n: Account(n, f"Konto {n}") for n in (1930, 6110)}
    prior = YearData(
        FiscalYear(date(2025, 1, 1), date(2025, 12, 31)),
        period_balances={(date(2025, 9, 1), 6110): Decimal("800")},
        has_vouchers=False,
    )
    current = YearData(
        FiscalYear(date(2026, 1, 1), date(2026, 12, 31)),
        [
            Voucher(
                "A",
                "1",
                date(2026, 9, 1),
                "Faktura",
                (
                    Row(6110, Decimal("1000"), text="Firm"),
                    Row(1930, Decimal("-1000")),
                ),
            ),
        ],
    )
    index = LedgerIndex.build(Ledger("Syntetbolaget AB", None, accounts, [prior, current]))
    pair = validate_comparison(month(2026, 9), month(2025, 9), index)
    assert pair.status is FactStatus.CALCULATED
    assert index.movement({6110}, pair.current) - index.movement({6110}, pair.previous) == Decimal("200")

    with pytest.raises(ValueError, match="verifikationer"):
        transaction_bridge(index, {6110}, pair, target="account:6110", aliases={}, sign=1, store=FactStore())


def test_reused_same_day_vouchers_have_distinct_drillthrough_references() -> None:
    vouchers = [
        Voucher(
            "A",
            "1",
            date(2026, 9, 1),
            "Faktura",
            (
                Row(6110, Decimal("100"), text="Firm"),
                Row(1930, Decimal("-100")),
            ),
            source_line=1,
        ),
        Voucher(
            "A",
            "1",
            date(2026, 9, 1),
            "Faktura",
            (
                Row(6110, Decimal("200"), text="Firm"),
                Row(1930, Decimal("-200")),
            ),
            source_line=10,
        ),
    ]
    bridge = _bridge(vouchers)
    refs = bridge.groups[0].current_vouchers

    assert len(refs) == 2
    assert set(refs) == {("A1", "2026-09-01", 1), ("A1", "2026-09-01", 10)}


def test_bridge_facts_never_collide_with_drilldown_facts_for_the_same_account() -> None:
    # Task 14 lägger bryggfakta i samma faktalager som granskningens övriga fakta. Ett kind="change"
    # på samma ämne (kontot), period och jämförelseperiod som drilldown skapar måste inte få samma
    # id, annars skriver FactStore.add tyst över det.
    vouchers = [
        _v("1", date(2025, 9, 5), "1000", "Leverantör A"),
        _v("2", date(2026, 9, 4), "1100", "Leverantör A"),
    ]
    index = _index(vouchers)
    store = FactStore()
    dd = drilldown(index, {6110}, PAIR.current, PAIR.previous, store=store, sign=1)
    account_change = next(a for a in dd.accounts if a.account == 6110)
    before = {f.id: f.value for f in store}

    bridge = transaction_bridge(index, {6110}, PAIR, target="account:6110", aliases={}, sign=1, store=store)

    assert account_change.fact_id is not None
    assert bridge.change_fact_id != account_change.fact_id
    for fact_id, value in before.items():
        after = store.get(fact_id)
        assert after is not None
        assert after.value == value
