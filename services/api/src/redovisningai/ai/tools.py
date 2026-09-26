"""Läsverktyg för AI-analytikern (A5).

- Verktygen är bundna till en kund på serversidan; AI:n kan inte ange kund- eller byrå-id.
- Ingen fri SQL, inga skriv-, mejl- eller webbverktyg.
- Alla siffror registreras som fakta i samma FactStore och returneras med id.
- Lönerader returneras bara aggregerade; PTL-fynd returneras aldrig.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from redovisningai.accounting.metrics import REGISTRY
from redovisningai.accounting.periods import Period, same_period_previous_year
from redovisningai.ai.metric_change import metric_change_evidence
from redovisningai.ai.providers.base import ToolSpec
from redovisningai.analytics.spend import spend_report
from redovisningai.facts.model import Fact, FactStore, Unit, Visibility
from redovisningai.findings.lifecycle import FindingRecord
from redovisningai.review.analysis import PAYROLL, CompanyAnalysis, voucher_view

PERIOD_PROP = {"type": "string", "description": "Period, t.ex. '2026-09', '2026-Q3', 'YTD:2026-09', 'R12:2026-09'"}


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _fact_out(f: Fact) -> dict[str, Any]:
    return {"fact_id": f.id, "label": f.label, "display": f.to_dict()["display"], "status": f.status.value}


def analyst_tools(
    analysis: CompanyAnalysis,
    store: FactStore,
    findings: list[FindingRecord],
    default_period: Period,
) -> list[ToolSpec]:
    def per(inp: dict[str, Any], key: str = "period") -> Period:
        return analysis.period(inp.get(key) or default_period.spec)

    def compare_periods(inp: dict[str, Any]) -> Any:
        p = per(inp)
        c = analysis.period(inp["compare"]) if inp.get("compare") else same_period_previous_year(p, analysis.ledger)
        m = analysis.metric_facts(p, c, store)
        return {
            k: {
                "current": _fact_out(store.get(v["id"])),  # type: ignore[arg-type]
                "change": v.get("change", {}).get("display"),
                "change_fact_id": v.get("change_id"),
                "change_pct_fact_id": v.get("change_pct_id"),
            }
            for k, v in m.items()
        }

    def explain_metric_change(inp: dict[str, Any]) -> Any:
        current = per(inp)
        previous = (
            analysis.period(inp["compare"])
            if inp.get("compare")
            else same_period_previous_year(current, analysis.ledger)
        )
        return metric_change_evidence(analysis, inp["metric"], current, previous, store)

    def variance_bridge(inp: dict[str, Any]) -> Any:
        p = per(inp)
        res = analysis.explain(inp.get("target") or "operating_result", p, None, store)
        return _strip_numbers(res)

    def cost_breakdown(inp: dict[str, Any]) -> Any:
        p = per(inp)
        return _strip_numbers(analysis.explain("costs", p, None, store))

    def account_movements(inp: dict[str, Any]) -> Any:
        p = per(inp)
        acc = int(inp["account"])
        res = analysis.explain(f"account:{acc}", p, None, store)
        if acc in PAYROLL:
            res["drilldown"]["top_vouchers"] = []  # inga lönerader på radnivå till AI
            res["drilldown"]["counterparties"] = []
        return _strip_numbers(res)

    def counterparty_spend(inp: dict[str, Any]) -> Any:
        p = per(inp)
        rep = spend_report(
            analysis.index, p, same_period_previous_year(p, analysis.ledger), aliases=analysis.ctx.aliases, limit=10
        )
        out = []
        for row in rep.counterparties:
            f = store.new(
                "amount",
                f"counterparty:{row['key']}",
                f"Kostnad {row['name']}",
                Decimal(row["amount"]),
                Unit.SEK,
                period=p.spec,
            )
            d = store.new(
                "change",
                f"counterparty:{row['key']}:diff",
                f"Förändring {row['name']}",
                Decimal(row["diff"]),
                Unit.SEK,
                period=p.spec,
            )
            out.append(
                {
                    "name": row["name"],
                    "recurrence": row["recurrence_sv"],
                    "amount": _fact_out(f),
                    "change": _fact_out(d),
                }
            )
        new = [n["name"] for n in rep.new_costs[:10]]
        return {
            "counterparties": out,
            "new_costs": new,
            "level_shifts": [{"name": s["name"], "month": s["month"]} for s in rep.level_shifts],
        }

    def get_voucher(inp: dict[str, Any]) -> Any:
        key = str(inp["voucher"]).strip()
        for v in analysis.ledger.all_vouchers():
            if str(v.key) == key:
                view = voucher_view(v, analysis.ledger, include_payroll_rows=False)
                for r in view["rows"]:
                    f = store.new(
                        "amount",
                        f"voucher:{key}:row:{r['account']}",
                        f"Belopp {key} konto {r['account']}",
                        Decimal(r["amount"]),
                        Unit.SEK,
                    )
                    r["amount_fact_id"] = f.id
                    r.pop("amount")
                view.pop("balance", None)
                view.pop("payroll_total", None)
                return view
        return {"error": f"Verifikation {key} finns inte"}

    def list_findings(inp: dict[str, Any]) -> Any:
        only_open = inp.get("only_open", True)
        out = []
        for r in findings:
            if r.visibility is Visibility.RESTRICTED_AML:
                continue
            if only_open and not r.status.is_open:
                continue
            for fd in r.facts:
                if fd["id"] not in store:
                    store.add(
                        Fact(
                            fd["id"],
                            fd["kind"],
                            fd["subject"],
                            fd["label"],
                            None if fd["value"] is None else Decimal(fd["value"]),
                            Unit(fd["unit"]),
                            fd.get("period"),
                        )
                    )
            out.append(
                {
                    "id": r.id,
                    "rule": r.rule_code,
                    "severity": r.severity.value,
                    "title": r.title,
                    "description": r.description,
                    "status": r.status.value,
                    "period": r.period,
                    "vouchers": r.vouchers[:5],
                }
            )
        return {"findings": out[:30]}

    def get_maturity(inp: dict[str, Any]) -> Any:
        return analysis.maturity(per(inp)).to_dict()

    def list_changes_since(inp: dict[str, Any]) -> Any:
        since = date.fromisoformat(inp["since"])
        vs = [v for v in analysis.ledger.all_vouchers() if v.reg_date and v.reg_date >= since and v.date < since]
        return {
            "vouchers_registered_late": [
                {
                    "key": str(v.key),
                    "date": v.date.isoformat(),
                    "reg_date": v.reg_date.isoformat() if v.reg_date else None,
                    "text": v.text,
                }
                for v in vs[:30]
            ]
        }

    return [
        ToolSpec(
            "compare_periods",
            "Nyckeltal för en period jämfört med en annan (standard: samma period i fjol).",
            _schema({"period": PERIOD_PROP, "compare": {"type": "string"}}, ["period", "compare"]),
            compare_periods,
        ),
        ToolSpec(
            "explain_metric_change",
            "Varför ett nyckeltal ändrats: exakta bidrag per resultatrad eller kvotdel och de största "
            "kontoförändringarna i båda perioderna, som fakta-id. Tom compare = samma period i fjol.",
            _schema(
                {
                    "metric": {"type": "string", "enum": sorted(REGISTRY)},
                    "period": PERIOD_PROP,
                    "compare": {"type": "string", "description": "Jämförelseperiod, eller tom sträng"},
                },
                ["metric", "period", "compare"],
            ),
            explain_metric_change,
        ),
        ToolSpec(
            "get_variance_bridge",
            "Resultatbrygga: vilka rader förklarar förändringen i rörelseresultatet.",
            _schema(
                {"period": PERIOD_PROP, "target": {"type": "string", "enum": ["operating_result", "net_result"]}},
                ["period", "target"],
            ),
            variance_bridge,
        ),
        ToolSpec(
            "get_cost_breakdown",
            "Kostnadsförändring per kategori jämfört med i fjol.",
            _schema({"period": PERIOD_PROP}, ["period"]),
            cost_breakdown,
        ),
        ToolSpec(
            "get_account_movements",
            "Förändring för ett konto: motparter och största verifikationer.",
            _schema({"period": PERIOD_PROP, "account": {"type": "integer"}}, ["period", "account"]),
            account_movements,
        ),
        ToolSpec(
            "get_counterparty_spend",
            "Kostnad per leverantör, nya kostnader och nivåskiften.",
            _schema({"period": PERIOD_PROP}, ["period"]),
            counterparty_spend,
        ),
        ToolSpec(
            "get_voucher",
            "Visa en verifikation, t.ex. 'A122'.",
            _schema({"voucher": {"type": "string"}}, ["voucher"]),
            get_voucher,
        ),
        ToolSpec(
            "list_findings",
            "Lista granskningsfynd.",
            _schema({"only_open": {"type": "boolean"}}, ["only_open"]),
            list_findings,
        ),
        ToolSpec(
            "get_maturity",
            "Periodmognad: bokföringsmetod, periodiseringar, fullständighet.",
            _schema({"period": PERIOD_PROP}, ["period"]),
            get_maturity,
        ),
        ToolSpec(
            "list_changes_since",
            "Verifikationer registrerade efter ett datum men daterade före det.",
            _schema({"since": {"type": "string", "description": "ÅÅÅÅ-MM-DD"}}, ["since"]),
            list_changes_since,
        ),
    ]


def _strip_numbers(obj: Any) -> Any:
    """Ta bort råa belopp ur verktygssvar – AI:n ska använda fakta-id och visningsvärden."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in (
                "current",
                "previous",
                "effect",
                "diff",
                "amount",
                "compare",
                "current_total",
                "previous_total",
                "change",
                "value",
                "lineage",
            ):
                continue
            out[k] = _strip_numbers(v)
        return out
    if isinstance(obj, list):
        return [_strip_numbers(x) for x in obj]
    return obj
