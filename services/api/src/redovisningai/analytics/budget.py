"""Budget mot utfall (från #PBUDGET i SIE eller budget i ekonomisystemet)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from redovisningai.accounting.balances import LedgerIndex
from redovisningai.accounting.periods import Period
from redovisningai.accounting.statements import INCOME_LINES, StatementMapping, income_statement
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import pct_change


@dataclass(slots=True)
class BudgetLine:
    code: str
    label: str
    actual: Decimal
    budget: Decimal

    @property
    def diff(self) -> Decimal:
        return self.actual - self.budget

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "actual": str(self.actual),
            "budget": str(self.budget),
            "diff": str(self.diff),
            "diff_pct": None if self.budget == 0 else str(pct_change(self.actual, self.budget)),
        }


def budget_vs_actual(index: LedgerIndex, period: Period, mapping: StatementMapping | None = None) -> list[BudgetLine]:
    mapping = mapping or StatementMapping()
    budget: dict[str, Decimal] = defaultdict(lambda: ZERO)
    has_budget = False
    for year in index.ledger.years:
        for (m, acc), amt in year.budget.items():
            if period.contains(m) and 3000 <= acc <= 8989:
                code = mapping.line_for(acc)
                if code:
                    budget[code] -= amt  # presentationstecken
                    has_budget = True
    if not has_budget:
        return []
    st = income_statement(index, period, mapping=mapping)
    totals: dict[str, Decimal] = {}
    out = []
    for ln in INCOME_LINES:
        if ln.sum_of:
            b = sum((totals.get(c, ZERO) for c in ln.sum_of), ZERO)
        else:
            b = budget.get(ln.code, ZERO)
        totals[ln.code] = b
        actual = st.line(ln.code).amount
        if actual or b:
            out.append(BudgetLine(ln.code, ln.label, actual, b))
    return out
