"""Källspårbar evidens för nyckeltalskomponenter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metric_explanations import MetricComponent
from redovisningai.domain.ledger import ZERO, Row, Voucher

BALANCE_COMPONENTS = frozenset(
    {
        "cash",
        "receivables",
        "payables",
        "inventory",
        "short_investments",
        "total_equity",
        "untaxed_reserves",
        "total_assets",
        "current_assets",
        "current_assets_ex_inventory",
        "current_liabilities",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    period: str
    account: int
    amount: Decimal
    voucher: str | None
    date: date | None
    text: str
    source_line: int | None
    content_hash: str | None
    source: str
    row_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "account": self.account,
            "amount": str(self.amount),
            "voucher": self.voucher,
            "date": self.date.isoformat() if self.date else None,
            "text": self.text,
            "source_line": self.source_line,
            "content_hash": self.content_hash,
            "source": self.source,
            "row_status": self.row_status,
        }


@dataclass(frozen=True, slots=True)
class AccountEvidence:
    account: int
    name: str
    current: Decimal
    previous: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "name": self.name,
            "current": str(self.current),
            "previous": str(self.previous),
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    accounts: tuple[AccountEvidence, ...]
    current_rows: tuple[EvidenceRow, ...]
    previous_rows: tuple[EvidenceRow, ...]
    current_total: Decimal
    previous_total: Decimal
    other_current: Decimal
    other_previous: Decimal
    source_level: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "accounts": [account.to_dict() for account in self.accounts],
            "current_rows": [row.to_dict() for row in self.current_rows],
            "previous_rows": [row.to_dict() for row in self.previous_rows],
            "current_total": str(self.current_total),
            "previous_total": str(self.previous_total),
            "other_current": str(self.other_current),
            "other_previous": str(self.other_previous),
            "source_level": self.source_level,
            "warnings": list(self.warnings),
        }


def _sort_key(row: EvidenceRow) -> tuple[Decimal, str, str]:
    return (-abs(row.amount), row.period, row.voucher or "")


def _row_evidence(voucher: Voucher, row: Row, period_spec: str, amount: Decimal) -> EvidenceRow:
    return EvidenceRow(
        period=period_spec,
        account=row.account,
        amount=amount,
        voucher=str(voucher.key),
        date=voucher.row_date(row),
        text=row.text or voucher.text,
        source_line=row.source_line or voucher.source_line,
        content_hash=voucher.content_hash(),
        source="voucher",
        row_status=row.status.value,
    )


def _income_rows(index: LedgerIndex, component: MetricComponent, spec: str, period: Any) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    targets = component.current_accounts if spec == "current" else component.previous_accounts
    for account, target in targets.items():
        source_rows: list[tuple[Voucher, Row]] = []
        raw_total = ZERO
        for voucher in index.vouchers_in(period):
            for row in voucher.effective_rows:
                if row.account == account:
                    source_rows.append((voucher, row))
                    raw_total += row.amount
        if raw_total == 0:
            continue
        # A voucher row must retain its booked amount. Any gap to a calculated
        # component is reported in the remaining amount, never spread over rows.
        sign = Decimal(-1) if target * raw_total < 0 else Decimal(1)
        for voucher, row in source_rows:
            rows.append(_row_evidence(voucher, row, period.spec, row.amount * sign))
    return rows


def _balance_rows(
    index: LedgerIndex, component: MetricComponent, period: Any, spec: str
) -> tuple[list[EvidenceRow], list[str]]:
    rows: list[EvidenceRow] = []
    warnings: list[str] = []
    year = index.ledger.year_for(period.end)
    if year is None:
        return rows, [f"Räkenskapsår saknas för {period.spec}; endast periodsaldo kan visas."]
    months = [m for m in year.fiscal_year.months() if m <= period.end.replace(day=1)]
    if any(index.coverage.get(m) != "vouchers" for m in months):
        return rows, [f"Verifikationskedjan för {period.spec} är inte fullständig; endast periodsaldo visas."]

    targets = component.current_accounts if spec == "current" else component.previous_accounts
    raw_balances = index.balances_at(period.end)
    for account, target in targets.items():
        raw_balance = raw_balances.get(account, ZERO)
        if account >= 3000:
            sign = Decimal(-1)
        elif target and raw_balance:
            sign = Decimal(1) if target * raw_balance > 0 else Decimal(-1)
        else:
            sign = Decimal(1) if account < 2000 else Decimal(-1)
        opening = index.opening_balance(year, account) * sign if account < 3000 else ZERO
        if opening:
            rows.append(
                EvidenceRow(
                    period.spec,
                    account,
                    opening,
                    None,
                    year.fiscal_year.start,
                    "Ingående saldo",
                    None,
                    None,
                    "opening_balance",
                )
            )
        for voucher in index.ledger.all_vouchers():
            if voucher.date < year.fiscal_year.start or voucher.date > period.end:
                continue
            for row in voucher.effective_rows:
                if row.account == account:
                    rows.append(_row_evidence(voucher, row, period.spec, row.amount * sign))
        if not any(row.account == account for row in rows) and target:
            warnings.append(f"Konto {account}: saldot kan inte följas till ingående saldo och verifikationer.")
    return rows, warnings


def evidence_for_component(
    index: LedgerIndex,
    component: MetricComponent,
    pair: ComparisonPair,
    *,
    limit: int = 8,
) -> Evidence:
    """Returnera verifierbara konton/verifikationsrader och avstämmande övrigtbelopp."""
    if limit < 1 or limit > 100:
        raise ValueError("Evidensgränsen måste vara mellan 1 och 100 rader per period.")
    accounts = sorted(set(component.current_accounts) | set(component.previous_accounts))
    account_rows = tuple(
        AccountEvidence(
            account,
            index.ledger.account_name(account),
            component.current_accounts.get(account, ZERO),
            component.previous_accounts.get(account, ZERO),
        )
        for account in accounts
    )
    warnings: list[str] = []
    if pair.status.value not in ("CALCULATED", "PARTIAL"):
        warnings.extend(pair.warnings)
        return Evidence(
            account_rows,
            (),
            (),
            component.current,
            component.previous,
            component.current,
            component.previous,
            "insufficient_data",
            tuple(warnings),
        )

    if not accounts:
        return Evidence(
            (),
            (),
            (),
            component.current,
            component.previous,
            component.current,
            component.previous,
            "parameter",
            (),
        )

    if component.code in BALANCE_COMPONENTS:
        current_all, current_warnings = _balance_rows(index, component, pair.current, "current")
        previous_all, previous_warnings = _balance_rows(index, component, pair.previous, "previous")
        warnings.extend(current_warnings)
        warnings.extend(previous_warnings)
        current_all = sorted(current_all, key=_sort_key)
        previous_all = sorted(previous_all, key=_sort_key)
        current_rows, previous_rows = current_all[:limit], previous_all[:limit]
        source_level = "account_voucher" if current_all or previous_all else "period_balance"
        if component.code == "untaxed_reserves":
            warnings.append("Beloppet efter skatt innehåller en beräknad justering som inte är en verifikationsrad.")
    else:
        current_all = sorted(_income_rows(index, component, "current", pair.current), key=_sort_key)
        previous_all = sorted(_income_rows(index, component, "previous", pair.previous), key=_sort_key)
        current_rows, previous_rows = current_all[:limit], previous_all[:limit]
        has_period_balance = any(
            index.coverage.get(m) in ("psaldo", "annual")
            for period in (pair.current, pair.previous)
            for m in period.months()
        )
        source_level = (
            "account_voucher" if current_all or previous_all else "period_balance" if has_period_balance else "account"
        )
        if has_period_balance:
            warnings.append(
                "Källan innehåller period- eller årssaldon utan verifikationer; endast befintliga verifikationsrader visas."
            )
        elif not (current_all or previous_all):
            warnings.append("Inga verifikationsrader finns för komponentens konton i perioderna.")

    other_current = component.current - sum((row.amount for row in current_rows), ZERO)
    other_previous = component.previous - sum((row.amount for row in previous_rows), ZERO)
    if source_level == "period_balance":
        other_current = component.current
        other_previous = component.previous
    return Evidence(
        account_rows,
        tuple(current_rows),
        tuple(previous_rows),
        component.current,
        component.previous,
        other_current,
        other_previous,
        source_level,
        tuple(dict.fromkeys(warnings)),
    )
