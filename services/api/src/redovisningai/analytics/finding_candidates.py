"""Deterministiska, fact-bundna kandidater från validerade nyckeltalsbryggor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from hashlib import sha256

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metric_evidence import evidence_for_component
from redovisningai.accounting.metric_explanations import MetricExplanation
from redovisningai.accounting.periods import add_months, month_start, months_between
from redovisningai.accounting.periods import month as month_period
from redovisningai.analytics.counterparties import counterparty_for_row
from redovisningai.analytics.spend import collect_spend, detect_level_shift, spend_report
from redovisningai.domain.ledger import Voucher
from redovisningai.facts.model import FactStatus

RULE_VERSION = "finding-candidates-v1"


@dataclass(frozen=True, slots=True)
class FindingCandidate:
    code: str
    label: str
    period_pair: tuple[str, str]
    amount_effect: Decimal
    unit: str
    fact_ids: tuple[str, ...]
    sources: tuple[dict[str, object], ...]
    source_level: str
    warnings: tuple[str, ...]
    group_key: str
    versions: dict[str, str]
    metric_codes: tuple[str, ...] = ()
    priority_score: int = 0
    score_parts: dict[str, int] = field(default_factory=dict)
    demotion_reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class _CandidateGroup:
    candidate: FindingCandidate
    fact_ids: set[str] = field(default_factory=set)
    sources: dict[str, dict[str, object]] = field(default_factory=dict)
    metric_codes: set[str] = field(default_factory=set)
    warnings: set[str] = field(default_factory=set)


def collect_candidates(
    index: LedgerIndex | None,
    pair: ComparisonPair,
    explanations: list[MetricExplanation],
    *,
    mapping_version: str,
    aliases: dict[str, str] | None = None,
) -> list[FindingCandidate]:
    """Gruppera relaterade kontobidrag; gör aldrig fritextnamn till identitet.

    Underliggande verifikationer bärs som begränsade, hashade referenser; fritext
    används aldrig som identitet eller som bevisad orsak.
    """
    if pair.status not in (FactStatus.CALCULATED, FactStatus.PARTIAL):
        return []

    groups: dict[str, _CandidateGroup] = {}
    for explanation in explanations:
        if explanation.status not in (FactStatus.CALCULATED, FactStatus.PARTIAL):
            continue
        for component in explanation.components:
            if component.effect == 0:
                continue
            accounts = sorted(set(component.current_accounts) | set(component.previous_accounts))
            if not accounts:
                # Parameterförändringar utan konto är inte transaktionskandidater.
                continue
            group_key = "accounts:" + ",".join(map(str, accounts))
            references: list[dict[str, object]] = []
            if index is not None:
                evidence = evidence_for_component(index, component, pair, limit=3)
                references = [
                    {
                        "period": row.period,
                        "voucher": row.voucher,
                        "date": row.date.isoformat() if row.date else None,
                        "source_line": row.source_line,
                        "content_hash": row.content_hash,
                    }
                    for row in (*evidence.current_rows, *evidence.previous_rows)
                ]
            source: dict[str, object] = {
                "accounts": ",".join(map(str, accounts)),
                "fact_id": component.fact_id or "",
                "source_level": component.source_level,
                "references": references,
            }
            candidate = FindingCandidate(
                code=explanation.code,
                label=component.label,
                period_pair=(pair.current.spec, pair.previous.spec),
                amount_effect=component.effect,
                unit=component.unit.value,
                fact_ids=(component.fact_id,) if component.fact_id else explanation.fact_ids,
                sources=(source,),
                source_level=component.source_level,
                warnings=tuple(dict.fromkeys((*explanation.warnings, *pair.warnings))),
                group_key=group_key,
                versions={**explanation.versions, "mapping": mapping_version, "finding_rules": RULE_VERSION},
                metric_codes=(explanation.code,),
            )
            group = groups.get(group_key)
            if group is None:
                group = groups[group_key] = _CandidateGroup(candidate)
            # Preserve the contribution with the greatest absolute magnitude as
            # the representative while retaining all facts/metrics in the group.
            if abs(candidate.amount_effect) > abs(group.candidate.amount_effect):
                group.candidate = candidate
            group.fact_ids.update(candidate.fact_ids)
            group.sources[json.dumps(source, sort_keys=True)] = source
            group.metric_codes.add(explanation.code)
            group.warnings.update(candidate.warnings)

    out: list[FindingCandidate] = []
    for group in groups.values():
        candidate = group.candidate
        out.append(
            FindingCandidate(
                code=candidate.code,
                label=candidate.label,
                period_pair=candidate.period_pair,
                amount_effect=candidate.amount_effect,
                unit=candidate.unit,
                fact_ids=tuple(sorted(group.fact_ids)),
                sources=tuple(group.sources[key] for key in sorted(group.sources)),
                source_level=candidate.source_level,
                warnings=tuple(sorted(group.warnings)),
                group_key=candidate.group_key,
                versions=candidate.versions,
                metric_codes=tuple(sorted(group.metric_codes)),
            )
        )
    out.extend(_spend_pattern_candidates(index, pair, aliases=aliases, mapping_version=mapping_version))
    out.extend(_possible_duplicate_candidates(index, pair, mapping_version=mapping_version))
    return sorted(out, key=lambda item: (item.group_key, item.code))


def _spend_pattern_candidates(
    index: LedgerIndex | None,
    pair: ComparisonPair,
    *,
    aliases: dict[str, str] | None,
    mapping_version: str,
) -> list[FindingCandidate]:
    """Convert existing spend cadence analysis into candidates only for confirmed aliases."""
    if index is None or not aliases or not index.has_data(pair.current) or not index.has_data(pair.previous):
        return []

    report = spend_report(index, pair.current, pair.previous, aliases=aliases, limit=10000)
    recent_history = months_between(add_months(month_start(pair.current.end), -11), pair.current.end)
    has_full_recent_history = len(recent_history) == 12 and all(
        index.coverage.get(month) == "vouchers" for month in recent_history
    )
    level_history = months_between(add_months(month_start(pair.current.end), -23), pair.current.end)
    has_full_level_history = len(level_history) == 24 and all(
        index.coverage.get(month) == "vouchers" for month in level_history
    )
    results: list[FindingCandidate] = []
    for row in report.counterparties:
        recurring = row["recurrence"] in {"monthly", "quarterly", "annual"}
        if not has_full_recent_history or row["confidence"] < 0.98 or not recurring:
            continue
        current_amount = Decimal(row["amount"])
        previous_amount = Decimal(row["compare"])
        change = current_amount - previous_amount
        threshold = max(Decimal("1000"), abs(previous_amount) * Decimal("0.20"))
        disappeared = current_amount < Decimal("1000") and previous_amount >= Decimal("1000")
        if (current_amount < Decimal("1000") and not disappeared) or (
            previous_amount != 0 and abs(change) < threshold and not disappeared
        ):
            continue

        key = str(row["key"])
        accounts = tuple(sorted(int(account) for account in row["accounts"]))
        references: list[dict[str, object]] = []
        transaction_counts: dict[str, int] = {}
        for period in (pair.current, pair.previous):
            matching_rows: list[dict[str, object]] = []
            matching_vouchers: set[str] = set()
            for voucher in index.vouchers_in(period):
                for ledger_row in voucher.effective_rows:
                    guess = counterparty_for_row(voucher, ledger_row, aliases)
                    if guess.source == "alias" and guess.key == key and ledger_row.account in accounts:
                        matching_vouchers.add(str(voucher.key))
                        matching_rows.append(
                            {
                                "period": period.spec,
                                "voucher": str(voucher.key),
                                "date": voucher.date.isoformat(),
                                "source_line": voucher.source_line,
                                "content_hash": voucher.content_hash(),
                            }
                        )
            references.extend(matching_rows[:3])
            transaction_counts[period.spec] = len(matching_vouchers)
        label = (
            "Utebliven återkommande kostnad"
            if disappeared
            else "Ny återkommande kostnad"
            if previous_amount == 0
            else "Förändring i återkommande kostnad"
        )
        results.append(
            FindingCandidate(
                code="recurring_cost_change",
                label=f"{label} – bekräftad motpart, {row['recurrence_sv']}",
                period_pair=(pair.current.spec, pair.previous.spec),
                amount_effect=-previous_amount if disappeared else change if previous_amount else current_amount,
                unit="SEK",
                fact_ids=(),
                sources=(
                    {
                        "accounts": ",".join(map(str, accounts)),
                        "source_level": "account_voucher",
                        "recurrence": row["recurrence"],
                        "references": references,
                    },
                ),
                source_level="account_voucher" if references else "account",
                warnings=(
                    "Återkommande mönster bygger på en av byrån bekräftad motpart; granska bokföringsunderlaget före åtgärd.",
                ),
                group_key=f"confirmed-counterparty:{key}",
                versions={"mapping": mapping_version, "finding_rules": RULE_VERSION},
            )
        )
        if pair.current.kind.value == "month" and pair.previous.kind.value == "month":
            current_count = transaction_counts.get(pair.current.spec, 0)
            previous_count = transaction_counts.get(pair.previous.spec, 0)
            frequency_change = current_count - previous_count
            materially_changed = abs(frequency_change) >= 2 and (
                previous_count == 0 or Decimal(abs(frequency_change)) / Decimal(previous_count) >= Decimal("0.5")
            )
            if materially_changed:
                results.append(
                    FindingCandidate(
                        code="transaction_frequency_change",
                        label="Ändrat antal verifikationer för bekräftad motpart",
                        period_pair=(pair.current.spec, pair.previous.spec),
                        amount_effect=Decimal(frequency_change),
                        unit="transaktioner",
                        fact_ids=(),
                        sources=(
                            {
                                "accounts": ",".join(map(str, accounts)),
                                "source_level": "account_voucher",
                                "current_count": current_count,
                                "previous_count": previous_count,
                                "references": references,
                            },
                        ),
                        source_level="account_voucher" if references else "account",
                        warnings=(
                            "Verifikationsfrekvens är en granskningssignal; bedöm tillsammans med periodisering och affärsunderlag.",
                        ),
                        group_key=f"confirmed-counterparty-frequency:{key}",
                        versions={"mapping": mapping_version, "finding_rules": RULE_VERSION},
                    )
                )

    if has_full_level_history and pair.current.kind.value == "month":
        recent_break_start = add_months(month_start(pair.current.end), -5)
        for spend in collect_spend(index, level_history, aliases=aliases).values():
            if spend.confidence < 0.98:
                continue
            shift = detect_level_shift(
                level_history, [spend.monthly.get(month, Decimal("0")) for month in level_history]
            )
            if (
                shift is None
                or not recent_break_start <= shift.month <= month_start(pair.current.end)
                or abs(shift.after - shift.before) < Decimal("1000")
            ):
                continue
            shift_references: list[dict[str, object]] = []
            evidence_months = [add_months(shift.month, offset) for offset in (-3, -2, -1, 0, 1, 2)]
            for evidence_month in evidence_months:
                evidence_month_start = month_start(evidence_month)
                for voucher in index.vouchers_in(month_period(evidence_month_start.year, evidence_month_start.month)):
                    for ledger_row in voucher.effective_rows:
                        guess = counterparty_for_row(voucher, ledger_row, aliases)
                        if guess.source == "alias" and guess.key == spend.key and ledger_row.account in spend.accounts:
                            shift_references.append(
                                {
                                    "period": f"{evidence_month_start:%Y-%m}",
                                    "voucher": str(voucher.key),
                                    "date": voucher.date.isoformat(),
                                    "source_line": voucher.source_line,
                                    "content_hash": voucher.content_hash(),
                                }
                            )
                            break
                    if len(shift_references) >= 6:
                        break
                if len(shift_references) >= 6:
                    break
            results.append(
                FindingCandidate(
                    code="recurring_level_shift",
                    label="Möjligt bestående nivåskifte i kostnadsmönster",
                    period_pair=(pair.current.spec, pair.previous.spec),
                    amount_effect=shift.after - shift.before,
                    unit="SEK",
                    fact_ids=(),
                    sources=(
                        {
                            "accounts": ",".join(map(str, sorted(spend.accounts))),
                            "source_level": "account_voucher",
                            "break_month": shift.month.isoformat(),
                            "before_monthly_average": str(shift.before),
                            "after_monthly_average": str(shift.after),
                            "references": shift_references,
                        },
                    ),
                    source_level="account_voucher" if shift_references else "account",
                    warnings=(
                        "Nivåskiftet är en statistisk signal från 24 kompletta månader, inte en fastställd orsak eller prisförändring.",
                    ),
                    group_key=f"confirmed-counterparty-level:{spend.key}:{shift.month.isoformat()}",
                    versions={"mapping": mapping_version, "finding_rules": RULE_VERSION},
                )
            )
    return results


def _possible_duplicate_candidates(
    index: LedgerIndex | None,
    pair: ComparisonPair,
    *,
    mapping_version: str,
) -> list[FindingCandidate]:
    """Flagga endast exakta, strukturella verifikationskopior som granskningsfråga.

    Motpartstext och verifikationsbeskrivning används inte i matchningen. Identiska
    konteringar kan vara legitima återkommande betalningar, så fyndet är aldrig en
    slutsats om dubbelbokning.
    """
    if index is None or not index.has_data(pair.current):
        return []

    by_signature: dict[
        tuple[date, tuple[tuple[int, str, str, str, tuple[tuple[str, str], ...], str], ...]],
        list[tuple[Voucher, Decimal, tuple[int, ...]]],
    ] = {}
    for voucher in index.vouchers_in(pair.current):
        rows = tuple(
            sorted(
                (
                    row.account,
                    str(row.amount.quantize(Decimal("0.01"))),
                    row.trans_date.isoformat() if row.trans_date is not None else "",
                    str(row.quantity.normalize()) if row.quantity is not None else "",
                    row.objects,
                    row.status.value,
                )
                for row in voucher.effective_rows
            )
        )
        if not rows:
            continue
        signature = (voucher.date, rows)
        accounts = tuple(sorted({row[0] for row in rows}))
        expense_amount = sum(
            (row.amount for row in voucher.effective_rows if 4000 <= row.account <= 7999), Decimal("0")
        )
        if abs(expense_amount) < Decimal("100"):
            continue
        by_signature.setdefault(signature, []).append((voucher, abs(expense_amount), accounts))

    candidates = []
    for signature, matches in by_signature.items():
        if len(matches) < 2:
            continue
        vouchers = [match[0] for match in matches]
        # Each row is included as a source reference for a human to compare. The
        # text and full row payload are deliberately not returned.
        references = [
            {
                "period": pair.current.spec,
                "voucher": str(voucher.key),
                "date": voucher.date.isoformat(),
                "source_line": voucher.source_line,
                "content_hash": voucher.content_hash(),
            }
            for voucher in vouchers
        ]
        accounts = matches[0][2]
        pattern_hash = sha256(repr(signature).encode()).hexdigest()[:20]
        candidates.append(
            FindingCandidate(
                code="possible_duplicate",
                label="Möjlig dubblett – kontrollera verifikationerna",
                period_pair=(pair.current.spec, pair.previous.spec),
                amount_effect=max((match[1] for match in matches), default=Decimal("0")),
                unit="SEK",
                fact_ids=(),
                sources=(
                    {
                        "accounts": ",".join(map(str, accounts)),
                        "source_level": "account_voucher",
                        "references": references,
                    },
                ),
                source_level="account_voucher",
                warnings=("Exakt likadan kontering samma dag är en granskningssignal, inte bevis på dubbelbokning.",),
                group_key=f"possible_duplicate:{pattern_hash}",
                versions={"mapping": mapping_version, "finding_rules": RULE_VERSION},
            )
        )
    return candidates
