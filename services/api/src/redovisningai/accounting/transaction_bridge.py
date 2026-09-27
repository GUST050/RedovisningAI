"""Transaktionsbryggan: förklarar en förändring på ett konto, en resultatrad eller en kostnadskategori
efter motpart – lokal, exakt och utan AI (plan §9.10).

Förändringen delas i fyra ömsesidigt uteslutande delar efter var motparten förekommer: `both`
(båda perioderna), `current_only`, `previous_only` och `unknown`. Delen `both` delas vidare exakt
i antals- och beloppseffekt per motpartsgrupp. Periodisering, återföring/rättelse och stor enskild
bokning är signaler på raderna, aldrig egna belopp. Motpartsnamn och -nycklar får aldrig stå i ett
fakta-id: gruppfakta använder `counterparty_subject`, som hashar nyckeln.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.periods import Period
from redovisningai.analytics.counterparties import CounterpartyGuess, counterparty_subject, guess_counterparty
from redovisningai.analytics.finding_candidates import match_reversals
from redovisningai.domain.ledger import Row, Voucher
from redovisningai.facts.model import CALC_VERSION, Fact, FactStatus, FactStore, Unit, Visibility
from redovisningai.rules.patterns import Pattern, classify

BRIDGE_VERSION = "transaction-bridge-v1"
LARGE_BOOKING_SHARE = Decimal("0.25")
LARGE_BOOKING_MIN = Decimal("10000")
ZERO = Decimal("0")
CENT = Decimal("0.01")
MAX_GROUP_VOUCHERS = 5
PART_CODES = ("both", "current_only", "previous_only", "unknown")
PART_LABELS = {
    "both": "Motpart i båda perioderna",
    "current_only": "Bara i aktuell jämförelseperiod",
    "previous_only": "Bara i den tidigare perioden",
    "unknown": "Okänd motpart",
}
SOURCE_KEYS = ("alias", "row_text", "voucher_text", "unknown")
VoucherIdentity = tuple[str, str, str, int]


def voucher_identity(v: Voucher) -> VoucherIdentity:
    """Verifikationens identitet som vid import: serie, nummer, datum och ordning i filen."""
    return (v.series, v.number, v.date.isoformat(), v.source_line or 0)


@dataclass(frozen=True, slots=True)
class BridgePart:
    code: str
    current: Decimal
    previous: Decimal
    effect: Decimal
    current_count: int
    previous_count: int
    count_effect: Decimal | None
    amount_effect: Decimal | None
    fact_id: str
    count_effect_fact_id: str | None
    amount_effect_fact_id: str | None


@dataclass(frozen=True, slots=True)
class GroupLine:
    key: str
    name: str | None
    source: str
    part: str
    current: Decimal
    previous: Decimal
    current_count: int
    previous_count: int
    signals: frozenset[str]
    current_vouchers: tuple[tuple[str, str, int], ...]
    previous_vouchers: tuple[tuple[str, str, int], ...]
    fact_id: str


@dataclass(frozen=True, slots=True)
class TransactionBridge:
    target: str
    change: Decimal
    change_fact_id: str
    parts: tuple[BridgePart, ...]
    groups: tuple[GroupLine, ...]
    identified_share_abs: dict[str, Decimal]
    identified_share_fact_id: str
    signals: dict[str, Decimal]
    versions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "change": str(self.change),
            "change_fact_id": self.change_fact_id,
            "parts": [
                {
                    "code": p.code,
                    "current": str(p.current),
                    "previous": str(p.previous),
                    "effect": str(p.effect),
                    "current_count": p.current_count,
                    "previous_count": p.previous_count,
                    "count_effect": None if p.count_effect is None else str(p.count_effect),
                    "amount_effect": None if p.amount_effect is None else str(p.amount_effect),
                    "fact_id": p.fact_id,
                    "count_effect_fact_id": p.count_effect_fact_id,
                    "amount_effect_fact_id": p.amount_effect_fact_id,
                }
                for p in self.parts
            ],
            "groups": [
                {
                    "key": g.key,
                    "name": g.name,
                    "source": g.source,
                    "part": g.part,
                    "current": str(g.current),
                    "previous": str(g.previous),
                    "current_count": g.current_count,
                    "previous_count": g.previous_count,
                    "signals": sorted(g.signals),
                    "current_vouchers": [list(pair) for pair in g.current_vouchers],
                    "previous_vouchers": [list(pair) for pair in g.previous_vouchers],
                    "fact_id": g.fact_id,
                }
                for g in self.groups
            ],
            "identified_share_abs": {key: str(value) for key, value in self.identified_share_abs.items()},
            "identified_share_fact_id": self.identified_share_fact_id,
            "signals": {key: str(value) for key, value in self.signals.items()},
            "versions": self.versions,
        }


@dataclass(slots=True)
class _GroupAcc:
    """Mutabel ackumulator under uppbyggnaden; blir en frusen `GroupLine` när bryggan är klar."""

    key: str
    name: str | None
    source: str
    current: Decimal = ZERO
    previous: Decimal = ZERO
    current_vouchers: dict[VoucherIdentity, tuple[Voucher, Decimal]] = field(default_factory=dict)
    previous_vouchers: dict[VoucherIdentity, tuple[Voucher, Decimal]] = field(default_factory=dict)
    signals: set[str] = field(default_factory=set)

    def add(self, label: str, voucher: Voucher, amount: Decimal, signals: set[str]) -> None:
        vouchers = self.current_vouchers if label == "current" else self.previous_vouchers
        if label == "current":
            self.current += amount
        else:
            self.previous += amount
        ident = voucher_identity(voucher)
        _, existing_amount = vouchers.get(ident, (voucher, ZERO))
        vouchers[ident] = (voucher, existing_amount + amount)
        self.signals |= signals


def transaction_bridge(
    index: LedgerIndex,
    accounts: set[int],
    pair: ComparisonPair,
    *,
    target: str,
    aliases: dict[str, str],
    sign: int,
    store: FactStore,
    large_booking_share: Decimal = LARGE_BOOKING_SHARE,
    large_booking_min: Decimal = LARGE_BOOKING_MIN,
) -> TransactionBridge:
    """Dela en förändring på `accounts` mellan `pair.current` och `pair.previous` efter motpart.

    `sign` följer `drilldown`: 1 för kostnader (debet positivt), −1 för intäkter. Fakta för
    förändringen, delarna, antals- och beloppseffekten samt andelen oidentifierat är kundsäkra;
    gruppfakta (per motpart) är interna, eftersom motpartens namn hör till dem.
    """
    unavailable = bridge_unavailable_reason(index, pair)
    if unavailable:
        raise ValueError(unavailable)
    reversal_ids = _reversal_voucher_identities(index, pair)
    rows_by_period = {
        "current": _target_rows(index, pair.current, accounts),
        "previous": _target_rows(index, pair.previous, accounts),
    }
    voucher_change = sign * (
        sum((row.amount for _, row in rows_by_period["current"]), ZERO)
        - sum((row.amount for _, row in rows_by_period["previous"]), ZERO)
    )
    ledger_change = sign * (index.movement(accounts, pair.current) - index.movement(accounts, pair.previous))
    if voucher_change != ledger_change:
        raise ValueError("Transaktionsbryggan stämmer inte mot bokföringens kontorörelse")
    abs_totals_by_account = {label: _abs_by_account(rows) for label, rows in rows_by_period.items()}

    groups: dict[str, _GroupAcc] = {}
    signal_totals: dict[str, Decimal] = {}
    identified_abs: dict[str, Decimal] = dict.fromkeys(SOURCE_KEYS, ZERO)

    for label, rows in rows_by_period.items():
        for ident, (voucher, voucher_rows) in _by_voucher(rows).items():
            net_by_account = _net_by_account(voucher_rows, sign)
            shared_signals, large_accounts = _voucher_signals(
                voucher,
                ident,
                net_by_account,
                abs_totals_by_account[label],
                reversal_ids,
                large_booking_share,
                large_booking_min,
            )
            for row in voucher_rows:
                signals = shared_signals | ({"large_booking"} if row.account in large_accounts else set())
                guess, source = _group_guess(voucher, row, aliases)
                amount = row.amount * sign
                identified_abs[source] += abs(row.amount)
                group = groups.get(guess.key)
                if group is None:
                    group = groups[guess.key] = _GroupAcc(guess.key, guess.name or None, source)
                group.add(label, voucher, amount, signals)
                for signal in signals:
                    signal_totals[signal] = signal_totals.get(signal, ZERO) + amount

    group_lines: list[GroupLine] = []
    part_members: dict[str, list[_GroupAcc]] = {code: [] for code in PART_CODES}
    for acc in groups.values():
        part = _group_part(acc)
        part_members[part].append(acc)
        fact = _group_fact(store, acc, part, pair, target)
        group_lines.append(
            GroupLine(
                key=acc.key,
                name=acc.name,
                source=acc.source,
                part=part,
                current=acc.current,
                previous=acc.previous,
                current_count=len(acc.current_vouchers),
                previous_count=len(acc.previous_vouchers),
                signals=frozenset(acc.signals),
                current_vouchers=_top_vouchers(acc.current_vouchers),
                previous_vouchers=_top_vouchers(acc.previous_vouchers),
                fact_id=fact.id,
            )
        )
    group_lines.sort(key=lambda g: abs(g.current - g.previous), reverse=True)

    fact_extra = _bridge_fact_extra(target)
    parts = tuple(_build_part(code, part_members[code], pair, target, store) for code in PART_CODES)
    change = sum((p.effect for p in parts), ZERO)
    if change != ledger_change:
        raise AssertionError("Bryggans delar stämmer inte mot kontorörelsen")
    change_fact = store.new(
        "change",
        target,
        f"Förändring: {target}",
        change,
        Unit.SEK,
        period=pair.current.spec,
        compare_period=pair.previous.spec,
        extra=fact_extra,
    )

    total_abs = sum(identified_abs.values(), ZERO)
    shares = (
        dict.fromkeys(SOURCE_KEYS, ZERO)
        if total_abs == 0
        else {key: identified_abs[key] / total_abs for key in SOURCE_KEYS}
    )
    share_fact = store.new(
        "share",
        f"{target}:identified_share:unknown",
        "Andel oidentifierat",
        shares["unknown"],
        Unit.RATIO,
        period=pair.current.spec,
        compare_period=pair.previous.spec,
        visibility=Visibility.INTERNAL,
        extra=fact_extra,
    )

    return TransactionBridge(
        target=target,
        change=change,
        change_fact_id=change_fact.id,
        parts=parts,
        groups=tuple(group_lines),
        identified_share_abs=shares,
        identified_share_fact_id=share_fact.id,
        signals=dict(signal_totals),
        versions={"calc": CALC_VERSION, "bridge": BRIDGE_VERSION},
    )


def bridge_unavailable_reason(index: LedgerIndex, pair: ComparisonPair) -> str | None:
    """Förklara varför ett periodpar saknar komplett verifikationsunderlag för en exakt brygga."""
    if pair.status is not FactStatus.CALCULATED:
        return "Transaktionsbryggan kräver jämförbara perioder med fullständig täckning"
    if any(
        index.coverage.get(month) != "vouchers" for period in (pair.current, pair.previous) for month in period.months()
    ):
        return "Transaktionsbryggan kräver verifikationer i båda perioderna; sammandrag eller PSALDO räcker inte"
    return None


def _target_rows(index: LedgerIndex, period: Period, accounts: set[int]) -> list[tuple[Voucher, Row]]:
    return [(v, r) for v in index.vouchers_in(period) for r in v.effective_rows if r.account in accounts]


def _abs_by_account(rows: list[tuple[Voucher, Row]]) -> dict[int, Decimal]:
    """Periodens absoluta belopp per konto – stor enskild bokning bedöms konto för konto, aldrig
    mot en korg av flera konton (en resultatrad eller kategori kan omfatta flera)."""
    totals: dict[int, Decimal] = {}
    for _, row in rows:
        totals[row.account] = totals.get(row.account, ZERO) + abs(row.amount)
    return totals


def _net_by_account(rows: list[Row], sign: int) -> dict[int, Decimal]:
    """En verifikations nettobelopp per konto bland dess rader på målets konton."""
    totals: dict[int, Decimal] = {}
    for row in rows:
        totals[row.account] = totals.get(row.account, ZERO) + row.amount * sign
    return totals


def _by_voucher(rows: list[tuple[Voucher, Row]]) -> dict[VoucherIdentity, tuple[Voucher, list[Row]]]:
    out: dict[VoucherIdentity, tuple[Voucher, list[Row]]] = {}
    for voucher, row in rows:
        ident = voucher_identity(voucher)
        if ident not in out:
            out[ident] = (voucher, [])
        out[ident][1].append(row)
    return out


def _group_guess(voucher: Voucher, row: Row, aliases: dict[str, str]) -> tuple[CounterpartyGuess, str]:
    """Motpart: bekräftat alias, sedan radtext, sist verifikationstext."""
    row_guess = guess_counterparty(row.text, aliases)
    if row_guess.key:
        return row_guess, "alias" if row_guess.source == "alias" else "row_text"
    voucher_guess = guess_counterparty(voucher.text, aliases)
    if voucher_guess.key:
        return voucher_guess, "alias" if voucher_guess.source == "alias" else "voucher_text"
    return voucher_guess, "unknown"


def _voucher_signals(
    voucher: Voucher,
    ident: VoucherIdentity,
    net_by_account: dict[int, Decimal],
    period_abs_by_account: dict[int, Decimal],
    reversal_ids: set[VoucherIdentity],
    large_booking_share: Decimal,
    large_booking_min: Decimal,
) -> tuple[set[str], set[int]]:
    signals: set[str] = set()
    if Pattern.ACCRUAL in classify(voucher):
        signals.add("periodization")
    if ident in reversal_ids:
        signals.add("reversal")
    large_accounts: set[int] = set()
    for account, net in net_by_account.items():
        period_abs_total = period_abs_by_account.get(account, ZERO)
        if abs(net) >= large_booking_share * period_abs_total and abs(net) >= large_booking_min:
            large_accounts.add(account)
    return signals, large_accounts


def _reversal_voucher_identities(index: LedgerIndex, pair: ComparisonPair) -> set[VoucherIdentity]:
    ids: set[VoucherIdentity] = set()
    for period in (pair.current, pair.previous):
        for reversal, original in match_reversals(index, period):
            ids.add(voucher_identity(reversal))
            ids.add(voucher_identity(original))
    return ids


def _group_part(acc: _GroupAcc) -> str:
    if not acc.key:
        return "unknown"
    if acc.current_vouchers and acc.previous_vouchers:
        return "both"
    return "current_only" if acc.current_vouchers else "previous_only"


def _top_vouchers(vouchers: dict[VoucherIdentity, tuple[Voucher, Decimal]]) -> tuple[tuple[str, str, int], ...]:
    ranked = sorted(vouchers.values(), key=lambda pair: abs(pair[1]), reverse=True)
    return tuple(
        (str(voucher.key), voucher.date.isoformat(), voucher.source_line or 0)
        for voucher, _ in ranked[:MAX_GROUP_VOUCHERS]
    )


def _bridge_fact_extra(target: str) -> str:
    """Egen extra-sträng för varje bryggfakta, så inget id kan sammanfalla med `drilldown`,
    nyckeltalsförklaringar eller en annan brygga – även när kind, ämne och perioder råkar stämma."""
    return f"transaction-bridge:{target}"


def _group_fact(store: FactStore, acc: _GroupAcc, part: str, pair: ComparisonPair, target: str) -> Fact:
    subject = counterparty_subject(acc.key) if acc.key else "counterparty:unknown"
    label = f"Motpart: {acc.name}" if acc.name else "Okänd motpart"
    return store.new(
        "change",
        subject,
        label,
        acc.current - acc.previous,
        Unit.SEK,
        period=pair.current.spec,
        compare_period=pair.previous.spec,
        lineage={"current": str(acc.current), "previous": str(acc.previous), "part": part},
        visibility=Visibility.INTERNAL,
        extra=_bridge_fact_extra(target),
    )


def _build_part(code: str, members: list[_GroupAcc], pair: ComparisonPair, target: str, store: FactStore) -> BridgePart:
    current = sum((a.current for a in members), ZERO)
    previous = sum((a.previous for a in members), ZERO)
    effect = current - previous
    current_count = sum(len(a.current_vouchers) for a in members)
    previous_count = sum(len(a.previous_vouchers) for a in members)
    count_effect: Decimal | None = None
    amount_effect: Decimal | None = None
    count_fact_id: str | None = None
    amount_fact_id: str | None = None
    fact_extra = _bridge_fact_extra(target)
    if code == "both":
        count_effect, amount_effect = _both_effects(members)
        count_fact = store.new(
            "count_effect",
            f"{target}:both:count_effect",
            "Antalseffekt",
            count_effect,
            Unit.SEK,
            period=pair.current.spec,
            compare_period=pair.previous.spec,
            extra=fact_extra,
        )
        amount_fact = store.new(
            "amount_effect",
            f"{target}:both:amount_effect",
            "Beloppseffekt",
            amount_effect,
            Unit.SEK,
            period=pair.current.spec,
            compare_period=pair.previous.spec,
            extra=fact_extra,
        )
        count_fact_id, amount_fact_id = count_fact.id, amount_fact.id
    part_fact = store.new(
        "variance_component",
        f"{target}:{code}",
        PART_LABELS[code],
        effect,
        Unit.SEK,
        period=pair.current.spec,
        compare_period=pair.previous.spec,
        extra=fact_extra,
    )
    return BridgePart(
        code=code,
        current=current,
        previous=previous,
        effect=effect,
        current_count=current_count,
        previous_count=previous_count,
        count_effect=count_effect,
        amount_effect=amount_effect,
        fact_id=part_fact.id,
        count_effect_fact_id=count_fact_id,
        amount_effect_fact_id=amount_fact_id,
    )


def _both_effects(members: list[_GroupAcc]) -> tuple[Decimal, Decimal]:
    """X = n1 × belopp0 / n0 avrundat till öre; antalseffekt = X − belopp0; beloppseffekt = belopp1 − X.

    Räknas per motpartsgrupp (där n0 alltid är > 0, eftersom `both` kräver rader i båda perioderna)
    och summeras – antal verifikationer summeras aldrig som belopp.
    """
    count_effect = ZERO
    amount_effect = ZERO
    for acc in members:
        n0, n1 = len(acc.previous_vouchers), len(acc.current_vouchers)
        belopp0, belopp1 = acc.previous, acc.current
        x = (Decimal(n1) * belopp0 / Decimal(n0)).quantize(CENT, rounding=ROUND_HALF_UP)
        count_effect += x - belopp0
        amount_effect += belopp1 - x
    return count_effect, amount_effect
