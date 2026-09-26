"""Exakta, deterministiska förändringsbryggor för registrerade nyckeltal."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metrics import REGISTRY, calculate_metric
from redovisningai.accounting.periods import Period
from redovisningai.accounting.statements import (
    BALANCE_LINES,
    Statement,
    StatementMapping,
    balance_sheet,
    income_statement,
)
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import FactStatus, FactStore, Unit
from redovisningai.rules.rates import RateTable

RatioPart = tuple[str, str, Decimal, Decimal, dict[int, Decimal], dict[int, Decimal], str | None, bool]


@dataclass(frozen=True, slots=True)
class MetricComponent:
    code: str
    label: str
    current: Decimal
    previous: Decimal
    effect: Decimal
    unit: Unit
    source_level: str
    current_accounts: dict[int, Decimal] = field(default_factory=dict)
    previous_accounts: dict[int, Decimal] = field(default_factory=dict)
    fact_id: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "current": str(self.current),
            "previous": str(self.previous),
            "effect": str(self.effect),
            "unit": self.unit.value,
            "source_level": self.source_level,
            "current_accounts": [
                {"account": account, "amount": str(amount)} for account, amount in sorted(self.current_accounts.items())
            ],
            "previous_accounts": [
                {"account": account, "amount": str(amount)}
                for account, amount in sorted(self.previous_accounts.items())
            ],
            "fact_id": self.fact_id,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class MetricExplanation:
    code: str
    label: str
    unit: Unit
    current: Decimal | None
    previous: Decimal | None
    change: Decimal | None
    status: FactStatus
    components: tuple[MetricComponent, ...]
    warnings: tuple[str, ...]
    periods: dict[str, str]
    versions: dict[str, str]
    fact_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "unit": self.unit.value,
            "current": None if self.current is None else str(self.current),
            "previous": None if self.previous is None else str(self.previous),
            "change": None if self.change is None else str(self.change),
            "status": self.status.value,
            "components": [component.to_dict() for component in self.components],
            "warnings": list(self.warnings),
            "periods": self.periods,
            "versions": self.versions,
            "fact_ids": list(self.fact_ids),
        }


def ratio_effects(n0: Decimal, d0: Decimal, n1: Decimal, d1: Decimal) -> tuple[Decimal, Decimal]:
    """Symmetrisk täljar-/nämnarbidrag för 100*N/D, före presentationsavrundning."""
    if d0 == 0 or d1 == 0:
        raise ZeroDivisionError("En kvotbrygga kräver skilda nollnämnare.")
    numerator_effect = Decimal(100) * (n1 - n0) * (Decimal(1) / d0 + Decimal(1) / d1) / Decimal(2)
    total_change = Decimal(100) * (n1 / d1 - n0 / d0)
    # Decimal division may round the final unit; close the identity in this context.
    denominator_effect = total_change - numerator_effect
    return numerator_effect, denominator_effect


def _accounts(statement: Statement, code: str) -> dict[int, Decimal]:
    return statement.line(code).accounts


def _line_component(
    code: str,
    statement: Statement,
    previous_statement: Statement,
    *,
    effect: Decimal | None = None,
    unit: Unit = Unit.SEK,
    source_level: str = "account",
    note: str | None = None,
) -> MetricComponent:
    current_line = statement.line(code)
    previous_line = previous_statement.line(code)
    current = current_line.amount
    previous = previous_line.amount
    return MetricComponent(
        code=code,
        label=current_line.label,
        current=current,
        previous=previous,
        effect=current - previous if effect is None else effect,
        unit=unit,
        source_level=source_level,
        current_accounts=current_line.accounts,
        previous_accounts=previous_line.accounts,
        note=note,
    )


def _income_components(code: str) -> tuple[str, ...]:
    if code == "net_sales":
        return ("net_sales",)
    if code == "operating_result":
        return (
            "net_sales",
            "inventory_change",
            "capitalized_work",
            "other_operating_income",
            "materials",
            "other_external",
            "personnel",
            "depreciation",
            "other_operating_expenses",
        )
    if code == "result_after_financial":
        return (
            *_income_components("operating_result"),
            "financial_assets_result",
            "interest_income",
            "interest_expense",
        )
    return ()


def _balance_component(index: LedgerIndex, code: str, period: Period, previous: Period) -> MetricComponent:
    accounts = {
        "cash": AccountSet.of((1900, 1999)),
        "receivables": AccountSet.of((1500, 1599)),
        "payables": AccountSet.of((2440, 2449)),
    }[code]
    sign = Decimal(-1) if code == "payables" else Decimal(1)
    current_accounts = {a: sign * value for a, value in index.balances_at(period.end, accounts).items()}
    previous_accounts = {a: sign * value for a, value in index.balances_at(previous.end, accounts).items()}
    current = sum(current_accounts.values(), ZERO)
    previous_value = sum(previous_accounts.values(), ZERO)
    return MetricComponent(
        code=code,
        label=REGISTRY[code].name,
        current=current,
        previous=previous_value,
        effect=current - previous_value,
        unit=Unit.SEK,
        source_level="account",
        current_accounts=current_accounts,
        previous_accounts=previous_accounts,
    )


def _balance_accounts(index: LedgerIndex, statement: Statement, code: str, at: Any) -> dict[int, Decimal]:
    """Expandera en summeringsrad till bladkonton utan att ändra balansrapportens utdata."""
    if code == "current_result":
        return index.result_by_account_at(at, include_closing_entries=True)
    definition = next(line for line in BALANCE_LINES if line.code == code)
    if not definition.sum_of:
        return dict(statement.line(code).accounts)
    accounts: dict[int, Decimal] = {}
    for child in definition.sum_of:
        for account, value in _balance_accounts(index, statement, child, at).items():
            accounts[account] = accounts.get(account, ZERO) + value
    return {account: value for account, value in accounts.items() if value != ZERO}


def _ratio_parts(
    code: str,
    index: LedgerIndex,
    current_period: Period,
    previous_period: Period,
    current_income: Statement,
    previous_income: Statement,
    current_balance: Statement,
    previous_balance: Statement,
    rates: RateTable,
) -> tuple[
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    list[RatioPart],
]:
    """Returnerar råa N/D samt deras icke överlappande redovisningsbidrag."""
    num_lines: list[RatioPart]
    den_lines: list[RatioPart]
    if code == "operating_margin":
        num_lines = [
            (
                line,
                current_income.line(line).label,
                current_income.line(line).amount,
                previous_income.line(line).amount,
                current_income.line(line).accounts,
                previous_income.line(line).accounts,
                None,
                True,
            )
            for line in _income_components("operating_result")
        ]
        n0 = sum((x[3] for x in num_lines), ZERO)
        n1 = sum((x[2] for x in num_lines), ZERO)
        d0, d1 = previous_income.line("net_sales").amount, current_income.line("net_sales").amount
        den_lines = [
            (
                "net_sales",
                "Nettoomsättning",
                d1,
                d0,
                current_income.line("net_sales").accounts,
                previous_income.line("net_sales").accounts,
                None,
                False,
            )
        ]
    elif code == "profit_margin":
        numerator_lines = [
            *_income_components("operating_result"),
            "financial_assets_result",
            "interest_income",
            "interest_expense",
        ]
        num_lines = [
            (
                line,
                current_income.line(line).label,
                current_income.line(line).amount,
                previous_income.line(line).amount,
                current_income.line(line).accounts,
                previous_income.line(line).accounts,
                None,
                True,
            )
            for line in numerator_lines
        ]
        n0 = sum((x[3] for x in num_lines), ZERO)
        n1 = sum((x[2] for x in num_lines), ZERO)
        d0, d1 = previous_income.line("net_sales").amount, current_income.line("net_sales").amount
        den_lines = [
            (
                "net_sales",
                "Nettoomsättning",
                d1,
                d0,
                current_income.line("net_sales").accounts,
                previous_income.line("net_sales").accounts,
                None,
                False,
            )
        ]
    elif code == "gross_margin":
        num_lines = []
        for line in ("net_sales", "materials"):
            cl, pl = current_income.line(line), previous_income.line(line)
            num_lines.append((line, cl.label, cl.amount, pl.amount, cl.accounts, pl.accounts, None, True))
        n0 = sum((x[3] for x in num_lines), ZERO)
        n1 = sum((x[2] for x in num_lines), ZERO)
        d0, d1 = previous_income.line("net_sales").amount, current_income.line("net_sales").amount
        den_lines = [
            (
                "net_sales",
                "Nettoomsättning",
                d1,
                d0,
                current_income.line("net_sales").accounts,
                previous_income.line("net_sales").accounts,
                "Även täljaren innehåller nettoomsättning.",
                False,
            )
        ]
    elif code == "personnel_share":
        cl, pl = current_income.line("personnel"), previous_income.line("personnel")
        num_lines = [
            (
                "personnel",
                cl.label,
                -cl.amount,
                -pl.amount,
                {account: -value for account, value in cl.accounts.items()},
                {account: -value for account, value in pl.accounts.items()},
                None,
                True,
            )
        ]
        n0, n1 = -pl.amount, -cl.amount
        d0, d1 = previous_income.line("net_sales").amount, current_income.line("net_sales").amount
        den_lines = [
            (
                "net_sales",
                "Nettoomsättning",
                d1,
                d0,
                current_income.line("net_sales").accounts,
                previous_income.line("net_sales").accounts,
                None,
                False,
            )
        ]
    elif code == "equity_ratio":
        e0, e1 = previous_balance.line("total_equity").amount, current_balance.line("total_equity").amount
        u0, u1 = previous_balance.line("untaxed_reserves").amount, current_balance.line("untaxed_reserves").amount
        t0, t1 = rates.value("corporate_tax", previous_period.end), rates.value("corporate_tax", current_period.end)
        num_lines = [
            (
                "total_equity",
                "Summa eget kapital",
                e1,
                e0,
                _balance_accounts(index, current_balance, "total_equity", current_period.end),
                _balance_accounts(index, previous_balance, "total_equity", previous_period.end),
                None,
                True,
            ),
            (
                "untaxed_reserves",
                "Förändring i obeskattade reserver efter skatt",
                u1 * (1 - t0),
                u0 * (1 - t0),
                {
                    account: value * (1 - t0)
                    for account, value in current_balance.line("untaxed_reserves").accounts.items()
                },
                {
                    account: value * (1 - t0)
                    for account, value in previous_balance.line("untaxed_reserves").accounts.items()
                },
                "Efter skatt: verifikationerna visar bokförda belopp före skattejusteringen.",
                True,
            ),
            (
                "corporate_tax",
                "Ändrad bolagsskattesats",
                u1 * ((1 - t1) - (1 - t0)),
                ZERO,
                {},
                {},
                "Parameterpåverkan; ingen bokföringsrad.",
                True,
            ),
        ]
        n0 = e0 + u0 * (1 - t0)
        n1 = e1 + u1 * (1 - t1)
        d0, d1 = previous_balance.line("total_assets").amount, current_balance.line("total_assets").amount
        den_lines = [
            (
                "total_assets",
                "Totala tillgångar",
                d1,
                d0,
                _balance_accounts(index, current_balance, "total_assets", current_period.end),
                _balance_accounts(index, previous_balance, "total_assets", previous_period.end),
                None,
                False,
            )
        ]
    elif code in ("quick_ratio", "current_ratio"):
        if code == "current_ratio":
            cnum, pnum = current_balance.line("current_assets"), previous_balance.line("current_assets")
            n1, n0 = cnum.amount, pnum.amount
            num_lines = [
                (
                    "current_assets",
                    cnum.label,
                    n1,
                    n0,
                    _balance_accounts(index, current_balance, "current_assets", current_period.end),
                    _balance_accounts(index, previous_balance, "current_assets", previous_period.end),
                    None,
                    True,
                )
            ]
        else:
            cnum, pnum = current_balance.line("current_assets"), previous_balance.line("current_assets")
            ci, pi = current_balance.line("inventory"), previous_balance.line("inventory")
            n1, n0 = cnum.amount - ci.amount, pnum.amount - pi.amount
            current_accounts = _balance_accounts(index, current_balance, "current_assets", current_period.end)
            previous_accounts = _balance_accounts(index, previous_balance, "current_assets", previous_period.end)
            for account, value in ci.accounts.items():
                current_accounts[account] = current_accounts.get(account, ZERO) - value
            for account, value in pi.accounts.items():
                previous_accounts[account] = previous_accounts.get(account, ZERO) - value
            num_lines = [
                (
                    "current_assets_ex_inventory",
                    "Omsättningstillgångar exklusive lager",
                    n1,
                    n0,
                    {account: value for account, value in current_accounts.items() if value != ZERO},
                    {account: value for account, value in previous_accounts.items() if value != ZERO},
                    None,
                    True,
                ),
            ]
        den1, den0 = current_balance.line("current_liabilities"), previous_balance.line("current_liabilities")
        d1, d0 = den1.amount, den0.amount
        den_lines = [("current_liabilities", den1.label, d1, d0, den1.accounts, den0.accounts, None, False)]
    else:
        raise KeyError(f"Saknar kvotdefinition för {code}")

    return n0, d0, n1, d1, [*num_lines, *den_lines]


def explain_metric(
    code: str,
    index: LedgerIndex,
    pair: ComparisonPair,
    *,
    mapping: StatementMapping,
    rates: RateTable,
    store: FactStore,
) -> MetricExplanation:
    if code not in REGISTRY:
        raise KeyError(f"Okänt nyckeltal: {code}")
    definition = REGISTRY[code]
    current_fact = calculate_metric(code, index, pair.current, store=store, mapping=mapping, rates=rates)
    previous_fact = calculate_metric(code, index, pair.previous, store=store, mapping=mapping, rates=rates)
    warnings = list(pair.warnings)
    if pair.status not in (FactStatus.CALCULATED, FactStatus.PARTIAL):
        return MetricExplanation(
            code,
            definition.name,
            definition.unit,
            current_fact.value,
            previous_fact.value,
            None,
            pair.status,
            (),
            tuple(warnings),
            {"current": pair.current.spec, "previous": pair.previous.spec},
            {"metric": definition.version, "mapping": mapping.version},
            (current_fact.id, previous_fact.id),
        )
    if current_fact.value is None or previous_fact.value is None:
        status = current_fact.status if current_fact.value is None else previous_fact.status
        return MetricExplanation(
            code,
            definition.name,
            definition.unit,
            current_fact.value,
            previous_fact.value,
            None,
            status,
            (),
            tuple(warnings),
            {"current": pair.current.spec, "previous": pair.previous.spec},
            {"metric": definition.version, "mapping": mapping.version},
            (current_fact.id, previous_fact.id),
        )

    unit = Unit.PP if definition.unit is Unit.PERCENT else definition.unit
    components: list[MetricComponent] = []
    if definition.unit is Unit.SEK:
        if code in ("cash", "receivables", "payables"):
            components.append(_balance_component(index, code, pair.current, pair.previous))
        else:
            cur_statement = income_statement(index, pair.current, mapping=mapping)
            prev_statement = income_statement(index, pair.previous, mapping=mapping)
            for line in _income_components(code):
                components.append(_line_component(line, cur_statement, prev_statement))
        change = sum((component.effect for component in components), ZERO)
        current_value, previous_value = current_fact.value, previous_fact.value
    else:
        cur_income = income_statement(index, pair.current, mapping=mapping)
        prev_income = income_statement(index, pair.previous, mapping=mapping)
        cur_balance = balance_sheet(index, pair.current.end, mapping=mapping)
        prev_balance = balance_sheet(index, pair.previous.end, mapping=mapping)
        n0, d0, n1, d1, parts = _ratio_parts(
            code, index, pair.current, pair.previous, cur_income, prev_income, cur_balance, prev_balance, rates
        )
        if d0 == 0 or d1 == 0:
            warnings.append("Kvoten kan inte jämföras eftersom en nämnare är noll.")
            return MetricExplanation(
                code,
                definition.name,
                definition.unit,
                current_fact.value,
                previous_fact.value,
                None,
                FactStatus.NOT_APPLICABLE,
                (),
                tuple(warnings),
                {"current": pair.current.spec, "previous": pair.previous.spec},
                {"metric": definition.version, "mapping": mapping.version},
                (current_fact.id, previous_fact.id),
            )
        with localcontext() as context:
            context.prec = 28
            numerator_effect, denominator_effect = ratio_effects(n0, d0, n1, d1)
            num_factor = numerator_effect / (n1 - n0) if n1 != n0 else ZERO
            den_factor = denominator_effect / (d1 - d0) if d1 != d0 else ZERO
            accumulated = ZERO
            for position, (
                part_code,
                label,
                now,
                before,
                now_accounts,
                before_accounts,
                note,
                is_numerator,
            ) in enumerate(parts):
                factor = num_factor if is_numerator else den_factor
                part_effect = (now - before) * factor
                if position == len(parts) - 1:
                    # Close finite Decimal precision at the final component while preserving exact bridge identity.
                    part_effect = numerator_effect + denominator_effect - accumulated
                accumulated += part_effect
                components.append(
                    MetricComponent(
                        part_code,
                        label,
                        now,
                        before,
                        part_effect,
                        unit,
                        "account" if now_accounts or before_accounts else "parameter",
                        now_accounts,
                        before_accounts,
                        note=note,
                    )
                )
            change = sum((component.effect for component in components), ZERO)
            current_value = n1 / d1 * Decimal(100)
            previous_value = n0 / d0 * Decimal(100)

    status = (
        FactStatus.PARTIAL
        if current_fact.status is FactStatus.PARTIAL or previous_fact.status is FactStatus.PARTIAL
        else FactStatus.CALCULATED
    )
    return MetricExplanation(
        code,
        definition.name,
        definition.unit,
        current_value,
        previous_value,
        change,
        status,
        tuple(components),
        tuple(dict.fromkeys(warnings)),
        {"current": pair.current.spec, "previous": pair.previous.spec},
        {"metric": definition.version, "mapping": mapping.version},
        (current_fact.id, previous_fact.id),
    )
