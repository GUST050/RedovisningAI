"""Analys av ett bolag för en period – samma kod för API, bakgrundsjobb och CLI (Fas 0).

Knyter ihop: index → periodmognad → kontroller → fyndlivscykel → kundminne → ärenden →
nyckeltal och bryggor → AI-paket. Ingen databas här; persistens sker i db-lagret.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import cached_property
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.categories import CategoryMapping, cost_tree
from redovisningai.accounting.comparisons import validate_comparison
from redovisningai.accounting.metrics import CORE_METRICS, calculate_metric, change_fact, change_pct_fact
from redovisningai.accounting.periods import (
    Period,
    month,
    month_start,
    parse_period,
    previous_period,
    rolling,
    same_period_previous_year,
    ytd,
)
from redovisningai.accounting.statements import StatementMapping, balance_sheet, income_statement
from redovisningai.accounting.variance import category_bridge, drilldown, line_accounts, result_bridge
from redovisningai.cases.builder import Case, build_cases
from redovisningai.domain.ledger import Ledger
from redovisningai.facts.model import CALC_VERSION, FactStatus, FactStore, Visibility
from redovisningai.findings.lifecycle import FindingRecord, SuppressionRule, reconcile
from redovisningai.maturity.assess import AccountingMethod, Maturity, assess
from redovisningai.memory.resolutions import Resolution, apply_memory
from redovisningai.rules.engine import CompanySettings, FindingCandidate, RuleContext, RuleDefinition, run_rules
from redovisningai.rules.rates import RateTable, default_rates

PAYROLL = AccountSet.of((7000, 7699), (2710, 2719))


@dataclass(slots=True)
class CompanyContext:
    company_id: str
    org_id: str
    name: str
    settings: CompanySettings = field(default_factory=CompanySettings)
    statement_mapping: StatementMapping = field(default_factory=StatementMapping)
    category_mapping: CategoryMapping = field(default_factory=CategoryMapping)
    method_override: AccountingMethod | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    person_names: list[str] = field(default_factory=list)
    param_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class ReviewResult:
    period: Period
    maturity: Maturity
    records: list[FindingRecord]
    cases: list[Case]
    store: FactStore
    created: int
    auto_closed: int

    @property
    def open_records(self) -> list[FindingRecord]:
        return [r for r in self.records if r.status.is_open]


class CompanyAnalysis:
    def __init__(
        self,
        ledger: Ledger,
        ctx: CompanyContext,
        *,
        rates: RateTable | None = None,
        catalog: dict[str, RuleDefinition] | None = None,
    ) -> None:
        self.ledger = ledger
        self.ctx = ctx
        self.rates = rates or default_rates()
        self.catalog = catalog

    @cached_property
    def index(self) -> LedgerIndex:
        return LedgerIndex.build(self.ledger)

    # ------------------------------------------------------------------ perioder
    def latest_month(self) -> Period | None:
        m = self.index.latest_month_with_data()
        return month(m.year, m.month) if m else None

    def period(self, spec: str | None) -> Period:
        if spec:
            return parse_period(spec, self.ledger)
        p = self.latest_month()
        if p is None:
            raise ValueError("Bokföringen saknar verifikationer")
        return p

    def maturity(self, period: Period) -> Maturity:
        return assess(self.index, month(period.end.year, period.end.month), method_override=self.ctx.method_override)

    # ------------------------------------------------------------------ granskning
    def run_controls(
        self,
        period: Period,
        *,
        include_aml: bool = True,
        store: FactStore | None = None,
        maturity: Maturity | None = None,
    ) -> list[FindingCandidate]:
        rctx = RuleContext(
            ledger=self.ledger,
            index=self.index,
            period=period,
            settings=self.ctx.settings,
            maturity=maturity or self.maturity(period),
            rates=self.rates,
            param_overrides=self.ctx.param_overrides,
            store=store or FactStore(),
        )
        return run_rules(rctx, self.catalog, include_aml=include_aml)

    def review(
        self,
        period: Period,
        existing: list[FindingRecord] | None = None,
        *,
        suppressions: list[SuppressionRule] | None = None,
        resolutions: list[Resolution] | None = None,
        extra_candidates: list[FindingCandidate] | None = None,
        now: datetime | None = None,
    ) -> ReviewResult:
        now = now or datetime.now()
        store = FactStore()
        mat = self.maturity(period)
        candidates = self.run_controls(period, store=store, maturity=mat) + list(extra_candidates or [])
        existing = list(existing or [])
        res = reconcile(existing, candidates, period.spec, suppressions=suppressions, now=now)
        records = existing + res.created
        apply_memory(records, resolutions or [], self.ledger, today=now.date())
        cases = build_cases([r for r in records if period.spec in r.seen_in_reviews or r.status.is_open])
        return ReviewResult(period, mat, records, cases, store, len(res.created), len(res.auto_closed))

    # ------------------------------------------------------------------ nyckeltal
    def metric_facts(
        self,
        period: Period,
        compare: Period | None,
        store: FactStore,
        codes: list[str] | None = None,
        low_maturity: bool = False,
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for code in codes or CORE_METRICS:
            cur = calculate_metric(
                code,
                self.index,
                period,
                store=store,
                mapping=self.ctx.statement_mapping,
                rates=self.rates,
                low_maturity=low_maturity,
            )
            entry: dict[str, Any] = {"id": cur.id, "fact": cur.to_dict()}
            if compare is not None:
                pair = validate_comparison(period, compare, self.index)
                prev = calculate_metric(
                    code, self.index, compare, store=store, mapping=self.ctx.statement_mapping, rates=self.rates
                )
                entry["previous"] = prev.to_dict()
                entry["comparison_status"] = pair.status.value
                entry["comparison_warnings"] = list(pair.warnings)
                if pair.status in (FactStatus.CALCULATED, FactStatus.PARTIAL):
                    ch = change_fact(store, cur, prev, compare.label)
                    if ch is not None:
                        entry["change_id"] = ch.id
                        entry["change"] = ch.to_dict()
                    chp = change_pct_fact(store, cur, prev)
                    if chp is not None:
                        entry["change_pct_id"] = chp.id
                        entry["change_pct"] = chp.to_dict()
            out[code] = entry
        return out

    def overview(self, period: Period, compare: Period | None = None) -> dict[str, Any]:
        store = FactStore()
        mat = self.maturity(period)
        fy = self.ledger.year_for(period.end)
        periods: dict[str, Period] = {"month": month(period.end.year, period.end.month)}
        if fy is not None:
            periods["ytd"] = ytd(fy.fiscal_year, period.end)
        periods["r12"] = rolling(period.end, 12)
        sections = {}
        for key, p in periods.items():
            cmp_ = compare if compare is not None and key == "month" else same_period_previous_year(p, self.ledger)
            sections[key] = {
                "period": {"spec": p.spec, "label": p.label},
                "compare": {"spec": cmp_.spec, "label": cmp_.label},
                "metrics": self.metric_facts(p, cmp_, store, low_maturity=mat.low_periodization and key == "month"),
            }
        return {
            "company": {"id": self.ctx.company_id, "name": self.ctx.name, "org_number": self.ledger.org_number},
            "period": {"spec": period.spec, "label": period.label},
            "maturity": mat.to_dict(),
            "recommended_view": mat.recommended_view,
            "sections": sections,
            "fiscal_years": [
                {
                    "start": y.fiscal_year.start.isoformat(),
                    "end": y.fiscal_year.end.isoformat(),
                    "has_vouchers": y.has_vouchers,
                }
                for y in self.ledger.years
            ],
            "months_with_data": [m.isoformat() for m in self.index.months_with_data()],
            "latest_month": (lm.spec if (lm := self.latest_month()) else None),
        }

    def statements(self, period: Period, compare: Period | None) -> dict[str, Any]:
        names = {a: acc.name for a, acc in self.ledger.accounts.items()}
        inc = income_statement(self.index, period, compare, self.ctx.statement_mapping)
        bs = balance_sheet(self.index, period.end, compare.end if compare else None, self.ctx.statement_mapping)
        return {"income": inc.to_dict(names), "balance": bs.to_dict(names)}

    def cost_tree(self, period: Period, compare: Period | None) -> dict[str, Any]:
        names = {a: acc.name for a, acc in self.ledger.accounts.items()}
        return cost_tree(self.index, period, compare, self.ctx.category_mapping).to_dict(names)

    # ------------------------------------------------------------------ trend
    def trend(self, months: int = 24) -> dict[str, Any]:
        """Månadsserie för diagram: omsättning, rörelsens kostnader och rörelseresultat (t.o.m. senaste månaden)."""
        last = self.index.latest_month_with_data()
        if last is None:
            return {"months": [], "net_sales": [], "costs": [], "operating_result": []}
        ms = [*self.index.history_months(last, months - 1), last]
        sales = [-x for x in self.index.monthly_series(AccountSet.of((3000, 3799)), ms)]
        costs = self.index.monthly_series(AccountSet.of((4000, 7999)), ms)
        other_income = [-x for x in self.index.monthly_series(AccountSet.of((3800, 3999)), ms)]
        return {
            "months": [m.isoformat()[:7] for m in ms],
            "net_sales": [str(x) for x in sales],
            "costs": [str(x) for x in costs],
            "operating_result": [str(a + o - c) for a, o, c in zip(sales, other_income, costs, strict=True)],
        }

    # ------------------------------------------------------------------ förklara
    def explain(
        self, target: str, period: Period, compare: Period | None = None, store: FactStore | None = None
    ) -> dict[str, Any]:
        """target: 'operating_result' | 'net_result' | 'line:<kod>' | 'category:<kod>' | 'account:<nr>'."""
        store = store if store is not None else FactStore()
        compare = compare or same_period_previous_year(period, self.ledger)
        if target in ("operating_result", "net_result"):
            b = result_bridge(
                self.index, period, compare, target=target, mapping=self.ctx.statement_mapping, store=store
            )
            return {"kind": "bridge", "bridge": b.to_dict()}
        if target == "costs":
            b = category_bridge(self.index, period, compare, mapping=self.ctx.category_mapping, store=store)
            return {"kind": "bridge", "bridge": b.to_dict()}
        kind, _, code = target.partition(":")
        if kind == "line":
            accs = line_accounts(code, self.index, self.ctx.statement_mapping)
            sign = -1 if code in ("net_sales", "other_operating_income", "interest_income") else 1
        elif kind == "category":
            accs = self.ctx.category_mapping.accounts_in(code, self.index.accounts_used)
            sign = 1
        elif kind == "account":
            accs = {int(code)}
            sign = -1 if 3000 <= int(code) <= 3999 else 1
        else:
            raise ValueError(f"Okänt förklaringsmål: {target}")
        d = drilldown(self.index, accs, period, compare, store=store, sign=sign)
        return {
            "kind": "drilldown",
            "target": target,
            "period": period.spec,
            "compare": compare.spec,
            "drilldown": d.to_dict(),
        }

    # ------------------------------------------------------------------ AI-paket
    def allowed_identifiers(self) -> set[str]:
        ids = {str(a) for a in self.ledger.accounts} | {str(a) for a in self.index.accounts_used}
        ids |= {str(v.key) for v in self.ledger.all_vouchers()}
        return ids

    def source_fingerprint(self, current: Period, previous: Period, *, prompt_version: str) -> str:
        """Stabilt analysfingeravtryck för periodpar, bokföring och beräkningsregler."""
        years = [
            {
                "fiscal_year": [year.fiscal_year.start.isoformat(), year.fiscal_year.end.isoformat()],
                "opening": sorted((account, str(value)) for account, value in year.opening.items()),
                "closing": sorted((account, str(value)) for account, value in year.closing.items()),
                "result": sorted((account, str(value)) for account, value in year.result.items()),
                "period_balances": sorted(
                    (period.isoformat(), account, str(value))
                    for (period, account), value in year.period_balances.items()
                ),
                "vouchers": sorted((str(voucher.key), voucher.content_hash()) for voucher in year.vouchers),
            }
            for year in self.ledger.years
        ]
        payload = {
            "periods": [current.spec, previous.spec],
            "years": years,
            "statement_mapping": self.ctx.statement_mapping.version,
            "category_mapping": self.ctx.category_mapping.version,
            "rates": [
                (
                    rate.code,
                    rate.value,
                    rate.valid_from.isoformat(),
                    rate.valid_to.isoformat() if rate.valid_to else None,
                )
                for rate in self.rates.rates
            ],
            "calculation": CALC_VERSION,
            "prompt": prompt_version,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def draft_is_current(self, metadata: dict[str, Any] | None, *, prompt_version: str) -> bool:
        """Validera ett sparat utkast mot aktuell bokföring, periodpar och promptversion."""
        if not metadata or metadata.get("prompt_version") != prompt_version:
            return False
        try:
            current = self.period(str(metadata["period"]))
            previous = self.period(str(metadata["compare_period"]))
        except (KeyError, TypeError, ValueError):
            return False
        return bool(
            metadata.get("source_fingerprint")
            == self.source_fingerprint(current, previous, prompt_version=prompt_version)
        )

    def commentary_package(self, review: ReviewResult, *, compare_spec: str | None = None) -> dict[str, Any]:
        p = review.period
        # Use the reviewed month and its year-ago month by default. An explicit
        # comparison opts into the selected period without changing current.
        analysis_period = p
        compare = self.period(compare_spec) if compare_spec else same_period_previous_year(analysis_period, self.ledger)
        comparison = validate_comparison(analysis_period, compare, self.index)
        store = review.store
        metrics = self.metric_facts(analysis_period, compare, store, low_maturity=review.maturity.low_periodization)
        bridge = result_bridge(self.index, analysis_period, compare, mapping=self.ctx.statement_mapping, store=store)
        open_cases = [c for c in review.cases if c.visibility is not Visibility.RESTRICTED_AML]
        return {
            "company": self.ctx.name,
            "period": {"spec": analysis_period.spec, "label": analysis_period.label},
            "compare": {"spec": compare.spec, "label": compare.label},
            "comparison_status": comparison.status.value,
            "comparison_warnings": list(comparison.warnings),
            "metrics": {
                k: {
                    "id": v["id"],
                    "label": v["fact"]["label"],
                    "display": v["fact"]["display"],
                    "change_id": v.get("change_id"),
                    "change_pct_id": v.get("change_pct_id"),
                }
                for k, v in metrics.items()
            },
            "bridge": {
                "components": [
                    {
                        "code": c.code,
                        "label": c.label,
                        "effect": str(c.effect),
                        "fact_id": c.fact_id,
                        "display": (
                            fact.to_dict()["display"]
                            if c.fact_id and (fact := store.get(c.fact_id)) is not None
                            else None
                        ),
                    }
                    for c in bridge.components
                ]
            },
            "maturity": review.maturity.to_dict(),
            "open_cases": {
                "high": sum(1 for c in open_cases if c.severity == "HIGH" and c.status != "CLOSED"),
                "titles": [c.title for c in open_cases if c.status != "CLOSED"][:10],
            },
            "facts": [f.to_dict() for f in store if f.visibility is not Visibility.RESTRICTED_AML][:200],
        }

    def client_package(self, review: ReviewResult, *, compare_spec: str | None = None) -> dict[str, Any]:
        """Paket för kundmötesagenten: bara CLIENT_SAFE-fakta, inga PTL-uppgifter."""
        base = self.commentary_package(review, compare_spec=compare_spec)
        safe_ids = {f.id for f in review.store if f.visibility is Visibility.CLIENT_SAFE}
        base["facts"] = [f for f in base["facts"] if f["id"] in safe_ids]
        base.pop("open_cases", None)
        base["ask_client"] = [
            {"key": c.key, "title": c.title, "question_hint": None}
            for c in review.cases
            if c.ask_client_suggested and c.visibility is Visibility.CLIENT_SAFE and c.status != "CLOSED"
        ]
        return base

    def case_package(self, review: ReviewResult) -> dict[str, Any]:
        """Paket för ärendebyggaren. PTL-ärenden skickas aldrig till AI."""
        cases = []
        for c in review.cases:
            if c.visibility is Visibility.RESTRICTED_AML or c.status == "CLOSED":
                continue
            d = c.to_dict()
            for f in d["findings"]:
                f["evidence"] = self._evidence(f["vouchers"])
            cases.append(
                {
                    k: d[k]
                    for k in (
                        "key",
                        "title",
                        "root_cause",
                        "suggested_action",
                        "ask_client_suggested",
                        "severity",
                        "memory_hint",
                        "findings",
                    )
                }
            )
        return {"company": self.ctx.name, "period": review.period.spec, "cases": cases}

    def _evidence(self, voucher_keys: list[str], limit: int = 4) -> list[dict[str, Any]]:
        wanted = set(voucher_keys[:limit])
        out = []
        for v in self.ledger.all_vouchers():
            if str(v.key) in wanted:
                out.append(voucher_view(v, self.ledger, include_payroll_rows=False))
        return out


def voucher_view(v: Any, ledger: Ledger, *, include_payroll_rows: bool) -> dict[str, Any]:
    rows = []
    payroll_total = Decimal(0)
    for r in v.rows:
        if not include_payroll_rows and r.account in PAYROLL:
            payroll_total += r.amount if r.is_effective else 0
            continue
        rows.append(
            {
                "account": r.account,
                "account_name": ledger.account_name(r.account),
                "amount": str(r.amount),
                "text": r.text,
                "status": r.status.value,
                "objects": [list(o) for o in r.objects],
                "source_line": r.source_line,
            }
        )
    view = {
        "key": str(v.key),
        "series": v.series,
        "number": v.number,
        "date": v.date.isoformat(),
        "reg_date": v.reg_date.isoformat() if v.reg_date else None,
        "text": v.text,
        "rows": rows,
        "balance": str(v.balance),
        "source_line": v.source_line,
    }
    if payroll_total:
        view["payroll_rows_masked"] = True
        view["payroll_total"] = str(payroll_total)
    return view


def previous(period: Period) -> Period:
    return previous_period(period)


def month_of(d: date) -> Period:
    m = month_start(d)
    return month(m.year, m.month)
