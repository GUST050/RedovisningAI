"""Regelmotor: laddar katalogen, kör kontroller och ger fyndkandidater.

En regel är en ren funktion `(RuleContext, RuleDefinition) -> list[FindingCandidate]`.
Regler kör deterministiskt; samma bokföring ger alltid samma fynd och samma fingeravtryck.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.periods import Period
from redovisningai.domain.ledger import Ledger
from redovisningai.facts.model import Fact, FactStore, Visibility
from redovisningai.maturity.assess import Maturity
from redovisningai.rules.rates import RateTable, default_rates

log = logging.getLogger(__name__)
CATALOG = Path(__file__).parent / "catalog" / "rules.toml"


class Severity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def rank(self) -> int:
        return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}[self.value]


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    code: str
    version: str
    category: str
    title: str
    description: str
    legal_basis: str
    valid_from: date
    valid_to: date | None
    severity: Severity
    visibility: Visibility
    applies_to: tuple[str, ...]
    owner: str
    reviewed_at: str
    params: dict[str, Any]

    def valid_on(self, d: date) -> bool:
        return self.valid_from <= d and (self.valid_to is None or d <= self.valid_to)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "version": self.version,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "legal_basis": self.legal_basis,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "severity": self.severity.value,
            "visibility": self.visibility.value,
            "applies_to": list(self.applies_to),
            "owner": self.owner,
            "reviewed_at": self.reviewed_at,
            "params": self.params,
        }


def load_catalog(path: Path | None = None) -> dict[str, RuleDefinition]:
    data = tomllib.loads((path or CATALOG).read_text(encoding="utf-8"))
    out: dict[str, RuleDefinition] = {}
    for r in data.get("rule", []):
        out[r["code"]] = RuleDefinition(
            code=r["code"],
            version=str(r["version"]),
            category=r["category"],
            title=r["title"],
            description=r["description"],
            legal_basis=r["legal_basis"],
            valid_from=date.fromisoformat(r["valid_from"]),
            valid_to=date.fromisoformat(r["valid_to"]) if r.get("valid_to") else None,
            severity=Severity(r["severity"]),
            visibility=Visibility(r["visibility"]),
            applies_to=tuple(r.get("applies_to", [])),
            owner=r.get("owner", ""),
            reviewed_at=r.get("reviewed_at", ""),
            params=dict(r.get("params", {})),
        )
    return out


@lru_cache(maxsize=1)
def default_catalog() -> dict[str, RuleDefinition]:
    return load_catalog()


@dataclass(slots=True)
class CompanySettings:
    legal_form: str = "AB"
    vat_period: str = "quarter"  # month | quarter | year
    industry: str | None = None
    food_retail: bool = False
    materiality: Decimal = Decimal("5000")
    has_overdraft: bool = False
    manual_series: tuple[str, ...] | None = None
    suspense_accounts: tuple[int, ...] = ()


@dataclass(slots=True)
class RuleContext:
    ledger: Ledger
    index: LedgerIndex
    period: Period  # granskad period (normalt en månad)
    settings: CompanySettings
    maturity: Maturity
    rates: RateTable = field(default_factory=default_rates)
    param_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    store: FactStore = field(default_factory=FactStore)

    def params(self, rule: RuleDefinition) -> dict[str, Any]:
        return {**rule.params, **self.param_overrides.get(rule.code, {})}


@dataclass(slots=True)
class FindingCandidate:
    rule_code: str
    rule_version: str
    severity: Severity
    title: str
    description: str
    period: str  # period som fyndet gäller (t.ex. "2026-09")
    key: tuple[Any, ...]  # identitet (fingeravtryck)
    visibility: Visibility
    vouchers: list[str] = field(default_factory=list)
    accounts: list[int] = field(default_factory=list)
    amount: Decimal | None = None
    facts: list[Fact] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    legal_basis: str = ""
    category: str = ""

    @property
    def fingerprint(self) -> str:
        raw = json.dumps([self.rule_code, *[str(k) for k in self.key]])
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "period": self.period,
            "fingerprint": self.fingerprint,
            "visibility": self.visibility.value,
            "vouchers": self.vouchers,
            "accounts": self.accounts,
            "amount": None if self.amount is None else str(self.amount),
            "facts": [f.to_dict() for f in self.facts],
            "details": self.details,
            "legal_basis": self.legal_basis,
            "category": self.category,
        }


RuleFn = Callable[[RuleContext, RuleDefinition], list[FindingCandidate]]
_REGISTRY: dict[str, RuleFn] = {}


def rule(code: str) -> Callable[[RuleFn], RuleFn]:
    def deco(fn: RuleFn) -> RuleFn:
        _REGISTRY[code] = fn
        return fn

    return deco


def candidate(
    ctx: RuleContext,
    rd: RuleDefinition,
    *,
    title: str,
    description: str,
    key: tuple[Any, ...],
    severity: Severity | None = None,
    period: str | None = None,
    vouchers: list[str] | None = None,
    accounts: list[int] | None = None,
    amount: Decimal | None = None,
    facts: list[Fact] | None = None,
    details: dict[str, Any] | None = None,
) -> FindingCandidate:
    return FindingCandidate(
        rule_code=rd.code,
        rule_version=rd.version,
        severity=severity or rd.severity,
        title=title,
        description=description,
        period=period or ctx.period.spec,
        key=key,
        visibility=rd.visibility,
        vouchers=vouchers or [],
        accounts=accounts or [],
        amount=amount,
        facts=facts or [],
        details=details or {},
        legal_basis=rd.legal_basis,
        category=rd.category,
    )


def registered_rules() -> dict[str, RuleFn]:
    # Importera implementationerna så att de registreras.
    from redovisningai.rules import aml, controls  # noqa: F401

    return dict(_REGISTRY)


def run_rules(
    ctx: RuleContext,
    catalog: dict[str, RuleDefinition] | None = None,
    *,
    codes: set[str] | None = None,
    include_aml: bool = True,
) -> list[FindingCandidate]:
    catalog = catalog or default_catalog()
    fns = registered_rules()
    out: list[FindingCandidate] = []
    for code, rd in catalog.items():
        if codes is not None and code not in codes:
            continue
        if rd.category == "aml" and not include_aml:
            continue
        if not rd.valid_on(ctx.period.end):
            continue
        if rd.applies_to and ctx.settings.legal_form not in rd.applies_to:
            continue
        fn = fns.get(code)
        if fn is None:
            continue
        try:
            out.extend(fn(ctx, rd))
        except Exception:  # en trasig regel får inte stoppa granskningen
            log.exception("Regel %s kraschade", code)
    return _dedupe(out)


def _dedupe(cands: list[FindingCandidate]) -> list[FindingCandidate]:
    seen: dict[str, FindingCandidate] = {}
    for c in cands:
        seen.setdefault(c.fingerprint, c)
    return list(seen.values())


def parse_account_spec(items: list[str | int]) -> AccountSet:
    ranges: list[tuple[int, int] | int] = []
    for it in items:
        s = str(it)
        if "-" in s:
            a, b = s.split("-", 1)
            ranges.append((int(a), int(b)))
        else:
            ranges.append(int(s))
    return AccountSet.of(*ranges)
