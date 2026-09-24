"""Nyckeltalsregister.

Alla nyckeltal definieras här med kod, namn, version, enhet och formel i klartext
("Så räknas detta"). Resultatet är alltid ett Fact med status – aldrig `None = 0`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.periods import Period
from redovisningai.accounting.statements import StatementMapping, balance_sheet, income_statement
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import Fact, FactStatus, FactStore, Unit
from redovisningai.rules.rates import RateTable, default_rates


@dataclass(slots=True)
class MetricContext:
    index: LedgerIndex
    period: Period
    mapping: StatementMapping
    rates: RateTable
    low_maturity: bool = False


@dataclass(frozen=True, slots=True)
class MetricResult:
    value: Decimal | None
    status: FactStatus
    inputs: dict[str, str]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    code: str
    name: str
    version: str
    unit: Unit
    formula: str  # förklaring på svenska
    compute: Callable[[MetricContext], MetricResult]
    period_based: bool = True  # False = mäts vid periodens slut (balansnyckeltal)
    needs_maturity: bool = False  # påverkas av periodiseringsgrad


def _q(x: Decimal, places: str = "0.1") -> Decimal:
    return x.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _ratio_pct(num: Decimal, den: Decimal) -> Decimal | None:
    if den == 0:
        return None
    return _q(num / den * 100)


def _is(ctx: MetricContext):  # type: ignore[no-untyped-def]
    return income_statement(ctx.index, ctx.period, mapping=ctx.mapping)


def _bs(ctx: MetricContext):  # type: ignore[no-untyped-def]
    return balance_sheet(ctx.index, ctx.period.end, mapping=ctx.mapping)


def _status(ctx: MetricContext, value: Decimal | None, needs_maturity: bool = False) -> FactStatus:
    if not ctx.index.has_data(ctx.period):
        return FactStatus.INSUFFICIENT_DATA
    if value is None:
        return FactStatus.NOT_APPLICABLE
    if needs_maturity and ctx.low_maturity:
        return FactStatus.PARTIAL
    return FactStatus.CALCULATED


def _net_sales(ctx: MetricContext) -> MetricResult:
    v = _is(ctx).line("net_sales").amount
    return MetricResult(v, _status(ctx, v), {"net_sales": str(v)})


def _operating_result(ctx: MetricContext) -> MetricResult:
    v = _is(ctx).line("operating_result").amount
    return MetricResult(v, _status(ctx, v, True), {"operating_result": str(v)})


def _operating_margin(ctx: MetricContext) -> MetricResult:
    st = _is(ctx)
    ns, orr = st.line("net_sales").amount, st.line("operating_result").amount
    v = _ratio_pct(orr, ns)
    return MetricResult(v, _status(ctx, v, True), {"operating_result": str(orr), "net_sales": str(ns)})


def _result_after_fin(ctx: MetricContext) -> MetricResult:
    v = _is(ctx).line("result_after_financial").amount
    return MetricResult(v, _status(ctx, v, True), {"result_after_financial": str(v)})


def _profit_margin(ctx: MetricContext) -> MetricResult:
    st = _is(ctx)
    ns, r = st.line("net_sales").amount, st.line("result_after_financial").amount
    v = _ratio_pct(r, ns)
    return MetricResult(v, _status(ctx, v, True), {"result_after_financial": str(r), "net_sales": str(ns)})


def _gross_margin(ctx: MetricContext) -> MetricResult:
    st = _is(ctx)
    ns, mat = st.line("net_sales").amount, st.line("materials").amount
    v = _ratio_pct(ns + mat, ns)
    return MetricResult(v, _status(ctx, v), {"net_sales": str(ns), "materials": str(mat)})


def _personnel_share(ctx: MetricContext) -> MetricResult:
    st = _is(ctx)
    ns, p = st.line("net_sales").amount, st.line("personnel").amount
    v = _ratio_pct(-p, ns)
    return MetricResult(v, _status(ctx, v, True), {"personnel": str(p), "net_sales": str(ns)})


def _equity_ratio(ctx: MetricContext) -> MetricResult:
    bs = _bs(ctx)
    tax = ctx.rates.value("corporate_tax", ctx.period.end)
    eq = bs.line("total_equity").amount
    untaxed = bs.line("untaxed_reserves").amount
    adj = eq + untaxed * (1 - tax)
    total = bs.line("total_assets").amount
    v = _ratio_pct(adj, total)
    return MetricResult(
        v,
        FactStatus.NOT_APPLICABLE if v is None else FactStatus.CALCULATED,
        {"adjusted_equity": str(_q(adj, "0.01")), "total_assets": str(total), "corporate_tax": str(tax)},
    )


def _quick_ratio(ctx: MetricContext) -> MetricResult:
    bs = _bs(ctx)
    ca = bs.line("current_assets").amount - bs.line("inventory").amount
    cl = bs.line("current_liabilities").amount
    v = _ratio_pct(ca, cl)
    return MetricResult(
        v,
        FactStatus.NOT_APPLICABLE if v is None else FactStatus.CALCULATED,
        {"current_assets_excl_inventory": str(ca), "current_liabilities": str(cl)},
    )


def _current_ratio(ctx: MetricContext) -> MetricResult:
    bs = _bs(ctx)
    ca, cl = bs.line("current_assets").amount, bs.line("current_liabilities").amount
    v = _ratio_pct(ca, cl)
    return MetricResult(
        v,
        FactStatus.NOT_APPLICABLE if v is None else FactStatus.CALCULATED,
        {"current_assets": str(ca), "current_liabilities": str(cl)},
    )


def _cash(ctx: MetricContext) -> MetricResult:
    v = ctx.index.balance_at(AccountSet.of((1900, 1999)), ctx.period.end)
    return MetricResult(v, FactStatus.CALCULATED, {"accounts": "1900–1999"})


def _receivables(ctx: MetricContext) -> MetricResult:
    v = ctx.index.balance_at(AccountSet.of((1500, 1599)), ctx.period.end)
    return MetricResult(v, FactStatus.CALCULATED, {"accounts": "1500–1599"})


def _payables(ctx: MetricContext) -> MetricResult:
    v = -ctx.index.balance_at(AccountSet.of((2440, 2449)), ctx.period.end)
    return MetricResult(v, FactStatus.CALCULATED, {"accounts": "2440–2449"})


REGISTRY: dict[str, MetricDefinition] = {
    m.code: m
    for m in [
        MetricDefinition("net_sales", "Nettoomsättning", "1", Unit.SEK, "Summa konton 3000–3799.", _net_sales),
        MetricDefinition(
            "operating_result",
            "Rörelseresultat",
            "1",
            Unit.SEK,
            "Rörelseintäkter minus rörelsekostnader (konton 3000–7999).",
            _operating_result,
            needs_maturity=True,
        ),
        MetricDefinition(
            "operating_margin",
            "Rörelsemarginal",
            "1",
            Unit.PERCENT,
            "Rörelseresultat / nettoomsättning × 100.",
            _operating_margin,
            needs_maturity=True,
        ),
        MetricDefinition(
            "result_after_financial",
            "Resultat efter finansiella poster",
            "1",
            Unit.SEK,
            "Rörelseresultat plus finansiella intäkter minus finansiella kostnader (3000–8499).",
            _result_after_fin,
            needs_maturity=True,
        ),
        MetricDefinition(
            "profit_margin",
            "Vinstmarginal",
            "1",
            Unit.PERCENT,
            "Resultat efter finansiella poster / nettoomsättning × 100.",
            _profit_margin,
            needs_maturity=True,
        ),
        MetricDefinition(
            "gross_margin",
            "Bruttomarginal",
            "1",
            Unit.PERCENT,
            "(Nettoomsättning − material och varor) / nettoomsättning × 100.",
            _gross_margin,
        ),
        MetricDefinition(
            "personnel_share",
            "Personalkostnader i % av omsättningen",
            "1",
            Unit.PERCENT,
            "Personalkostnader (7000–7699) / nettoomsättning × 100.",
            _personnel_share,
            needs_maturity=True,
        ),
        MetricDefinition(
            "equity_ratio",
            "Soliditet",
            "1",
            Unit.PERCENT,
            "Justerat eget kapital / totalt kapital × 100. Justerat eget kapital = eget kapital "
            "(inkl. periodens resultat) + obeskattade reserver × (1 − bolagsskattesats).",
            _equity_ratio,
            period_based=False,
        ),
        MetricDefinition(
            "quick_ratio",
            "Kassalikviditet",
            "1",
            Unit.PERCENT,
            "(Omsättningstillgångar − varulager) / kortfristiga skulder × 100.",
            _quick_ratio,
            period_based=False,
        ),
        MetricDefinition(
            "current_ratio",
            "Balanslikviditet",
            "1",
            Unit.PERCENT,
            "Omsättningstillgångar / kortfristiga skulder × 100.",
            _current_ratio,
            period_based=False,
        ),
        MetricDefinition(
            "cash",
            "Kassa och bank",
            "1",
            Unit.SEK,
            "Saldo konton 1900–1999 vid periodens slut.",
            _cash,
            period_based=False,
        ),
        MetricDefinition(
            "receivables", "Kundfordringar", "1", Unit.SEK, "Saldo konton 1500–1599.", _receivables, period_based=False
        ),
        MetricDefinition(
            "payables", "Leverantörsskulder", "1", Unit.SEK, "Saldo konton 2440–2449.", _payables, period_based=False
        ),
    ]
}

CORE_METRICS = [
    "net_sales",
    "operating_result",
    "operating_margin",
    "result_after_financial",
    "gross_margin",
    "personnel_share",
    "equity_ratio",
    "quick_ratio",
    "cash",
]


def calculate_metric(
    code: str,
    index: LedgerIndex,
    period: Period,
    *,
    store: FactStore | None = None,
    mapping: StatementMapping | None = None,
    rates: RateTable | None = None,
    low_maturity: bool = False,
) -> Fact:
    definition = REGISTRY[code]
    ctx = MetricContext(index, period, mapping or StatementMapping(), rates or default_rates(), low_maturity)
    try:
        res = definition.compute(ctx)
    except Exception as exc:  # beräkningsfel ska synas, inte krascha analysen
        res = MetricResult(None, FactStatus.ERROR, {}, note=str(exc))
    store = store or FactStore()
    return store.new(
        "metric",
        f"metric:{code}",
        definition.name,
        res.value,
        definition.unit,
        period=period.spec,
        status=res.status,
        lineage={
            "metric": code,
            "metric_version": definition.version,
            "formula": definition.formula,
            "inputs": res.inputs,
            "mapping_version": ctx.mapping.version,
            "period_start": period.start.isoformat(),
            "period_end": period.end.isoformat(),
            **({"note": res.note} if res.note else {}),
        },
    )


def change_fact(store: FactStore, current: Fact, previous: Fact, compare_label: str) -> Fact | None:
    """Förändring mellan två fakta (kr eller procentenheter)."""
    if current.value is None or previous.value is None:
        return None
    unit = Unit.PP if current.unit is Unit.PERCENT else current.unit
    diff = current.value - previous.value
    return store.new(
        "change",
        current.subject + ":change",
        f"Förändring {current.label.lower()}",
        diff,
        unit,
        period=current.period,
        compare_period=previous.period,
        lineage={"current": current.id, "previous": previous.id, "compare_label": compare_label},
    )


def change_pct_fact(store: FactStore, current: Fact, previous: Fact) -> Fact | None:
    if current.value is None or previous.value is None or previous.value == 0 or current.unit is not Unit.SEK:
        return None
    pct = _q((current.value - previous.value) / abs(previous.value) * 100)
    return store.new(
        "change",
        current.subject + ":change_pct",
        f"Förändring {current.label.lower()} i procent",
        pct,
        Unit.PERCENT,
        period=current.period,
        compare_period=previous.period,
        lineage={"current": current.id, "previous": previous.id},
    )


__all__ = ["CORE_METRICS", "REGISTRY", "ZERO", "MetricDefinition", "calculate_metric", "change_fact", "change_pct_fact"]
