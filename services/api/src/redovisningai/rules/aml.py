"""PTL-signaler (penningtvättslagen).

Visas bara för behörigheten PTL-ansvarig (synlighet RESTRICTED_AML). Får aldrig hamna i
kundrapport, kundfråga eller AI-paket – byrån omfattas av meddelandeförbud. Signalerna är
underlag för byråns egen bedömning; programmet bedömer inte misstanke och rapporterar inte.
Ingen AI används här.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from redovisningai.accounting.balances import AccountSet
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import Unit, Visibility
from redovisningai.rules.engine import FindingCandidate, RuleContext, RuleDefinition, candidate, rule
from redovisningai.rules.patterns import Pattern, classify

D = Decimal
CASH = AccountSet.of((1910, 1919))
BANK = AccountSet.of((1920, 1949))
RELATED = AccountSet.of((1360, 1369), (1680, 1689), (2390, 2399), (2890, 2899))
REVENUE_OR_CUSTOMER = AccountSet.of((1510, 1519), (3000, 3999))
SUPPLIER_OR_COST = AccountSet.of((2440, 2449), (4000, 7999), (2710, 2739), (1630, 1639))


def _restricted(ctx: RuleContext, facts_subject: str, label: str, value: Decimal):  # type: ignore[no-untyped-def]
    return ctx.store.new(
        "amount", facts_subject, label, value, Unit.SEK, period=ctx.period.spec, visibility=Visibility.RESTRICTED_AML
    )


@rule("AML_LARGE_CASH")
def aml_large_cash(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    threshold = D(str(ctx.params(rd)["threshold"]))
    out = []
    for v in ctx.index.vouchers_in(ctx.period):
        for r in v.effective_rows:
            if r.account in CASH and abs(r.amount) >= threshold:
                f = _restricted(ctx, f"aml:cash:{v.key}", "Kontant belopp", r.amount)
                out.append(
                    candidate(
                        ctx,
                        rd,
                        title=f"Stor kontantpost i {v.key}",
                        description=f"Kontant {'insättning' if r.amount > 0 else 'uttag'} {{f:{f.id}}} ({v.text}, {v.date}).",
                        key=(str(v.key), r.account),
                        vouchers=[str(v.key)],
                        accounts=[r.account],
                        amount=r.amount,
                        facts=[f],
                    )
                )
    return out


@rule("AML_ROUND_AMOUNTS_RELATED")
def aml_round_amounts_related(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    min_amount, round_to = D(str(p["min_amount"])), D(str(p["round_to"]))
    out = []
    for v in ctx.index.vouchers_in(ctx.period):
        for r in v.effective_rows:
            if r.account in RELATED and abs(r.amount) >= min_amount and abs(r.amount) % round_to == 0:
                f = _restricted(ctx, f"aml:round:{v.key}", "Belopp mot närstående", r.amount)
                out.append(
                    candidate(
                        ctx,
                        rd,
                        title=f"Jämnt belopp mot närstående i {v.key}",
                        description=f"{{f:{f.id}}} bokat mot {r.account} {ctx.ledger.account_name(r.account)} ({v.text}).",
                        key=(str(v.key), r.account),
                        vouchers=[str(v.key)],
                        accounts=[r.account],
                        amount=r.amount,
                        facts=[f],
                    )
                )
    return out


@rule("AML_RAPID_IN_OUT")
def aml_rapid_in_out(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    min_amount, max_days, tol = D(str(p["min_amount"])), int(p["max_days"]), D(str(p["tolerance_share"]))
    vs = [
        v
        for v in ctx.ledger.all_vouchers()
        if ctx.period.start - timedelta(days=max_days) <= v.date <= ctx.period.end + timedelta(days=max_days)
    ]
    inflows = []
    outflows = []
    for v in vs:
        if classify(v) & {Pattern.CUSTOMER_PAYMENT, Pattern.PAYROLL, Pattern.TAX_ACCOUNT}:
            continue
        bank = sum((r.amount for r in v.effective_rows if r.account in BANK), ZERO)
        others = [r for r in v.effective_rows if r.account not in BANK]
        if bank >= min_amount and not any(r.account in REVENUE_OR_CUSTOMER for r in others):
            inflows.append((v, bank))
        elif bank <= -min_amount and not any(r.account in SUPPLIER_OR_COST for r in others):
            outflows.append((v, -bank))
    out = []
    for vin, a_in in inflows:
        if not ctx.period.contains(vin.date):
            continue
        for vout, a_out in outflows:
            days = (vout.date - vin.date).days
            if 0 <= days <= max_days and abs(a_out - a_in) <= a_in * tol:
                f = _restricted(ctx, f"aml:inout:{vin.key}:{vout.key}", "Belopp in/ut", a_in)
                out.append(
                    candidate(
                        ctx,
                        rd,
                        title=f"Snabbt in- och utflöde: {vin.key} → {vout.key}",
                        description=(
                            f"Inbetalning {{f:{f.id}}} ('{vin.text}') följs efter {days} dagar av en utbetalning "
                            f"av liknande belopp ('{vout.text}') utan koppling till kunder eller leverantörer."
                        ),
                        key=(str(vin.key), str(vout.key)),
                        vouchers=[str(vin.key), str(vout.key)],
                        amount=a_in,
                        facts=[f],
                    )
                )
    return out
