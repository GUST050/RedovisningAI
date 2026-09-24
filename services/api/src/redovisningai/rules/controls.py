"""Svenska granskningskontroller (MVP). Se catalog/rules.toml för lagstöd och parametrar."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from statistics import median

from redovisningai.accounting.balances import AccountSet
from redovisningai.accounting.periods import Period, add_months, month, month_end, month_start, rolling
from redovisningai.accounting.statements import balance_sheet
from redovisningai.domain.ledger import ZERO, Voucher, YearData
from redovisningai.facts.model import Unit
from redovisningai.rules.engine import (
    FindingCandidate,
    RuleContext,
    RuleDefinition,
    Severity,
    candidate,
    parse_account_spec,
    rule,
)
from redovisningai.rules.patterns import Pattern, classify, is_routine

D = Decimal
CASH = AccountSet.of((1910, 1919))
BANK = AccountSet.of((1920, 1949))
INPUT_VAT = AccountSet.of((2640, 2649))
OUTPUT_VAT = AccountSet.of((2610, 2639))
OUTPUT_VAT_25 = AccountSet.of((2610, 2619))
OUTPUT_VAT_12 = AccountSet.of((2620, 2629))
OUTPUT_VAT_6 = AccountSet.of((2630, 2639))
SALES = AccountSet.of((3000, 3799))
COSTS = AccountSet.of((4000, 7999))
SALARY_BASE = AccountSet.of((7010, 7289), (7380, 7389))
EMPLOYER_CONTRIB = AccountSet.of((7510, 7518))
PAYABLES = AccountSet.of((2440, 2449))


def _vouchers(ctx: RuleContext) -> list[Voucher]:
    return ctx.index.vouchers_in(ctx.period)


def _year(ctx: RuleContext) -> YearData | None:
    return ctx.ledger.year_for(ctx.period.end)


def _fact_amount(ctx: RuleContext, subject: str, label: str, value: Decimal, **lineage: object):  # type: ignore[no-untyped-def]
    return ctx.store.new("amount", subject, label, value, Unit.SEK, period=ctx.period.spec, lineage=dict(lineage))


# ---------------------------------------------------------------------------- integritet


@rule("UNBALANCED_VOUCHER")
def unbalanced_voucher(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    tol = D(str(ctx.params(rd)["tolerance"]))
    out = []
    for v in _vouchers(ctx):
        diff = v.balance
        if abs(diff) > tol:
            f = _fact_amount(
                ctx, f"voucher:{v.key}:imbalance", f"Differens i verifikation {v.key}", diff, voucher=str(v.key)
            )
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Verifikation {v.key} balanserar inte",
                    description=f"Debet och kredit skiljer sig med {{f:{f.id}}} i verifikation {v.key} ({v.text}).",
                    key=(v.date.year, str(v.key)),
                    vouchers=[str(v.key)],
                    accounts=sorted({r.account for r in v.effective_rows}),
                    amount=diff,
                    facts=[f],
                )
            )
    return out


@rule("IB_NE_PREV_UB")
def ib_ne_prev_ub(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    year = _year(ctx)
    if year is None:
        return []
    prev = ctx.ledger.previous_year(year)
    if prev is None or not year.opening:
        return []
    tol = D(str(ctx.params(rd)["tolerance"]))
    prev_end = prev.fiscal_year.end
    prev_ub = ctx.index.balances_at(prev_end) if prev.has_vouchers else dict(prev.closing)
    unclosed = ctx.index.result_to_date(prev_end, include_closing_entries=True) if prev.has_vouchers else ZERO
    out = []
    accounts = {a for a in set(prev_ub) | set(year.opening) if a < 3000}
    equity_group = {a for a in accounts if 2090 <= a <= 2099}
    for acc in sorted(accounts - equity_group):
        ib = year.opening.get(acc, ZERO)
        ub = prev_ub.get(acc, ZERO)
        if abs(ib - ub) > tol:
            f = _fact_amount(
                ctx,
                f"ib_ub:{acc}:{year.fiscal_year.start}",
                f"Differens IB/UB konto {acc}",
                ib - ub,
                ib=str(ib),
                ub=str(ub),
            )
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"IB konto {acc} stämmer inte med föregående års UB",
                    description=(
                        f"IB {year.fiscal_year.label} för {acc} {ctx.ledger.account_name(acc)} avviker "
                        f"{{f:{f.id}}} från UB {prev.fiscal_year.label}."
                    ),
                    key=(year.fiscal_year.start.isoformat(), acc),
                    period=f"{year.fiscal_year.start:%Y-%m}",
                    accounts=[acc],
                    amount=ib - ub,
                    facts=[f],
                )
            )
    ib_eq = sum((year.opening.get(a, ZERO) for a in equity_group), ZERO)
    ub_eq = sum((prev_ub.get(a, ZERO) for a in equity_group), ZERO) - unclosed
    if abs(ib_eq - ub_eq) > tol:
        f = _fact_amount(
            ctx, f"ib_ub:equity:{year.fiscal_year.start}", "Differens IB/UB eget kapital (2090–2099)", ib_eq - ub_eq
        )
        out.append(
            candidate(
                ctx,
                rd,
                title="IB för balanserat resultat stämmer inte med föregående år",
                description=f"Konton 2090–2099 (inkl. föregående års resultat) avviker {{f:{f.id}}}.",
                key=(year.fiscal_year.start.isoformat(), "2090-2099"),
                period=f"{year.fiscal_year.start:%Y-%m}",
                accounts=sorted(equity_group),
                amount=ib_eq - ub_eq,
                facts=[f],
            )
        )
    return out


@rule("VOUCHER_NUMBER_GAP")
def voucher_number_gap(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    year = _year(ctx)
    if year is None or not year.has_vouchers:
        return []
    by_series: dict[str, list[tuple[int, Voucher]]] = defaultdict(list)
    for v in year.vouchers:
        if v.number.isdigit():
            by_series[v.series].append((int(v.number), v))
    out = []
    for series, items in by_series.items():
        nums = sorted(n for n, _ in items)
        seen: set[int] = set()
        for n in nums:
            if n in seen:
                out.append(
                    candidate(
                        ctx,
                        rd,
                        title=f"Dubblett i nummerserie {series}: {series}{n}",
                        description=f"Verifikationsnummer {series}{n} förekommer flera gånger {year.fiscal_year.label}.",
                        key=(year.fiscal_year.start.isoformat(), series, n, "dup"),
                        vouchers=[f"{series}{n}"],
                    )
                )
            seen.add(n)
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - seen)
        # Rapportera bara luckor som ligger t.o.m. granskad period.
        by_no = {n: v for n, v in items}
        for n in missing:
            nxt = next((by_no[k] for k in range(n + 1, nums[-1] + 1) if k in by_no), None)
            if nxt is not None and nxt.date > ctx.period.end:
                continue
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Lucka i nummerserie {series}: {series}{n} saknas",
                    description=(
                        f"Verifikation {series}{n} saknas i räkenskapsåret {year.fiscal_year.label}. "
                        "Verifikationsnummer ska löpa i obruten följd."
                    ),
                    key=(year.fiscal_year.start.isoformat(), series, n),
                    period=f"{nxt.date:%Y-%m}" if nxt else ctx.period.spec,
                    vouchers=[f"{series}{n}"],
                )
            )
    return out


def _business_days_between(a: date, b: date) -> int:
    days, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


@rule("LATE_BOOKING")
def late_booking(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    out = []
    # Sent registrerade verifikationer syns när de registreras – granska registreringsdatum i perioden.
    for v in ctx.ledger.all_vouchers():
        if v.reg_date is None or not ctx.period.contains(v.reg_date):
            continue
        is_cash = any(r.account in CASH for r in v.effective_rows)
        if is_cash:
            lag = _business_days_between(v.date, v.reg_date)
            limit = int(p["cash_max_days"])
        else:
            lag = (v.reg_date - v.date).days
            limit = int(p["other_max_days"])
        if lag > limit:
            f = ctx.store.new(
                "count",
                f"voucher:{v.key}:lag",
                f"Bokföringsfördröjning {v.key}",
                D(lag),
                Unit.DAYS,
                period=ctx.period.spec,
                lineage={"date": v.date.isoformat(), "reg_date": v.reg_date.isoformat()},
            )
            kind = "kontant betalning (ska bokföras senast påföljande arbetsdag)" if is_cash else "affärshändelse"
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Sent bokförd verifikation {v.key}",
                    description=(
                        f"{v.text}: {kind} daterad {v.date} registrerades {v.reg_date} – "
                        f"{{f:{f.id}}} {'arbetsdagar' if is_cash else ''} efter affärshändelsen."
                    ).replace("  ", " "),
                    key=(str(v.key), v.date.isoformat()),
                    severity=Severity.MEDIUM if is_cash else Severity.LOW,
                    vouchers=[str(v.key)],
                    facts=[f],
                    details={"cash": is_cash, "lag": lag},
                )
            )
    return out


_BAS_VALID = AccountSet.of((1000, 8999))


@rule("ACCOUNT_NOT_IN_CHART")
def account_not_in_chart(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    used: dict[int, list[str]] = defaultdict(list)
    for v in _vouchers(ctx):
        for r in v.effective_rows:
            used[r.account].append(str(v.key))
    out = []
    for acc, keys in sorted(used.items()):
        a = ctx.ledger.accounts.get(acc)
        missing = a is None or a.name == f"Konto {acc}"
        outside = acc not in _BAS_VALID
        if missing or outside:
            reason = "finns inte i kontoplanen" if missing else "ligger utanför BAS kontoklasser 1–8"
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Konto {acc} {reason}",
                    description=f"Konto {acc} används i {len(keys)} verifikation(er) men {reason}.",
                    key=(acc,),
                    vouchers=sorted(set(keys))[:10],
                    accounts=[acc],
                )
            )
    return out


@rule("ABNORMAL_SIGN")
def abnormal_sign(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    out = []
    balances = ctx.index.balances_at(ctx.period.end, AccountSet.of((1000, 1999)))
    mat = ctx.settings.materiality
    for acc, bal in sorted(balances.items()):
        if acc in CASH and bal < 0:
            f = _fact_amount(ctx, f"balance:{acc}", f"Saldo konto {acc}", bal)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Negativ kassa ({acc})",
                    description=f"Kassan {acc} {ctx.ledger.account_name(acc)} har negativt saldo {{f:{f.id}}} "
                    "– en kassa kan inte vara negativ.",
                    key=(acc, "negative_cash"),
                    severity=Severity.HIGH,
                    accounts=[acc],
                    amount=bal,
                    facts=[f],
                )
            )
        elif acc not in CASH and acc not in BANK and acc % 10 not in (8, 9) and bal < -mat:
            f = _fact_amount(ctx, f"balance:{acc}", f"Saldo konto {acc}", bal)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Tillgångskonto {acc} har kreditsaldo",
                    description=f"{acc} {ctx.ledger.account_name(acc)} har saldo {{f:{f.id}}} (kredit) vid periodens slut.",
                    key=(acc, "credit_asset"),
                    accounts=[acc],
                    amount=bal,
                    facts=[f],
                )
            )
    for acc, amt in sorted(ctx.index.movement_by_account(SALES, ctx.period).items()):
        if amt > mat:
            f = _fact_amount(ctx, f"movement:{acc}", f"Nettorörelse konto {acc}", amt)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Intäktskonto {acc} har debetsaldo för perioden",
                    description=f"{acc} {ctx.ledger.account_name(acc)} har netto debet {{f:{f.id}}} – kontrollera "
                    "krediteringar och felkonteringar.",
                    key=(acc, "debit_revenue", ctx.period.spec),
                    severity=Severity.LOW,
                    accounts=[acc],
                    amount=amt,
                    facts=[f],
                )
            )
    return out


# ---------------------------------------------------------------------------- moms


def _vat_rates(ctx: RuleContext, d: date) -> list[Decimal]:
    return [r for r in ctx.rates.values("vat_rates_allowed", d) if r > 0]


def _nearest(rate: Decimal, allowed: list[Decimal]) -> Decimal:
    return min(allowed, key=lambda a: abs(a - rate))


@rule("INPUT_VAT_RATIO")
def input_vat_ratio(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    exempt = parse_account_spec(p["exempt_accounts"])
    tol = D(str(p["tolerance_pp"])) / 100
    out = []
    for v in _vouchers(ctx):
        vat = sum((r.amount for r in v.effective_rows if r.account in INPUT_VAT), ZERO)
        if vat <= 0:
            continue
        cost_rows = [r for r in v.effective_rows if (r.account in COSTS or 1100 <= r.account <= 1499) and r.amount > 0]
        base = sum((r.amount for r in cost_rows), ZERO)
        if base <= 0:
            continue
        exempt_only = all(r.account in exempt for r in cost_rows)
        rate = vat / base
        allowed = _vat_rates(ctx, v.date)
        max_rate = max(allowed)
        problem: str | None = None
        severity = rd.severity
        if exempt_only:
            problem = "ingående moms på en kostnad som normalt är momsfri (t.ex. försäkring, bankavgift, lön, ränta)"
        elif rate > max_rate + tol:
            problem = f"momsen motsvarar {rate * 100:.1f} % av kostnaden – högre än högsta tillåtna sats"
            severity = Severity.HIGH
        elif rate < min(allowed) - tol and rate > D("0.005"):
            problem = f"momsen motsvarar {rate * 100:.1f} % av kostnaden – lägre än lägsta sats"
        if problem:
            f = _fact_amount(ctx, f"voucher:{v.key}:input_vat", f"Ingående moms {v.key}", vat, base=str(base))
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Ingående moms avviker i {v.key}",
                    description=f"{v.text}: {problem}. Moms {{f:{f.id}}}.",
                    key=(str(v.key), v.date.isoformat()),
                    severity=severity,
                    vouchers=[str(v.key)],
                    accounts=sorted(
                        {r.account for r in cost_rows} | {r.account for r in v.effective_rows if r.account in INPUT_VAT}
                    ),
                    amount=vat,
                    facts=[f],
                    details={"implied_rate": str(rate.quantize(D("0.0001")))},
                )
            )
    return out


def _vat_period_ends(ctx: RuleContext, until: date) -> list[tuple[date, date]]:
    months = {"month": 1, "quarter": 3, "year": 12}.get(ctx.settings.vat_period, 3)
    out = []
    for y in ctx.ledger.years:
        start = y.fiscal_year.start
        cur = start
        while cur <= y.fiscal_year.end:
            end = month_end(add_months(cur, months - 1))
            if end <= until:
                out.append((cur, end))
            cur = add_months(cur, months)
    return out


@rule("VAT_ACCOUNTS_NOT_CLEARED")
def vat_accounts_not_cleared(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    tol = D(str(p["tolerance"]))
    grace = int(p["grace_days"])
    vat_accounts = AccountSet.of((2610, 2649))
    out = []
    for start, end in _vat_period_ends(ctx, ctx.period.end - timedelta(days=grace))[-4:]:
        if ctx.ledger.year_for(end) is None:
            continue
        per_acc = {a: b for a, b in ctx.index.balances_at(end, vat_accounts).items() if abs(b) > tol}
        if not per_acc:
            continue
        total = sum(per_acc.values(), ZERO)
        f = _fact_amount(
            ctx,
            f"vat_open:{end}",
            f"Kvarstående moms {start:%Y-%m}–{end:%Y-%m}",
            total,
            accounts={str(k): str(v) for k, v in per_acc.items()},
        )
        out.append(
            candidate(
                ctx,
                rd,
                title=f"Momskonton inte nollställda för {start:%Y-%m} – {end:%Y-%m}",
                description=(
                    "Efter momsperiodens slut finns saldo på "
                    + ", ".join(str(a) for a in sorted(per_acc))
                    + f" (netto {{f:{f.id}}}). Momsredovisningen kan sakna konton eller vara ofullständig."
                ),
                key=(end.isoformat(),),
                period=f"{end:%Y-%m}",
                accounts=sorted(per_acc),
                amount=total,
                facts=[f],
            )
        )
    return out


@rule("OUTPUT_VAT_RATIO")
def output_vat_ratio(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    tol = D(str(ctx.params(rd)["tolerance_pp"])) / 100
    out = []
    food_12_after_cut: list[Voucher] = []
    for v in _vouchers(ctx):
        vat = -sum((r.amount for r in v.effective_rows if r.account in OUTPUT_VAT), ZERO)
        base = -sum((r.amount for r in v.effective_rows if r.account in SALES), ZERO)
        if vat <= 0 or base <= 0:
            continue
        allowed = _vat_rates(ctx, v.date)
        rate = vat / base
        if rate > max(allowed) + tol:
            f = _fact_amount(ctx, f"voucher:{v.key}:output_vat", f"Utgående moms {v.key}", vat, base=str(base))
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Utgående moms för hög i {v.key}",
                    description=f"{v.text}: momsen motsvarar {rate * 100:.1f} % av försäljningen (moms {{f:{f.id}}}).",
                    key=(str(v.key),),
                    severity=Severity.HIGH,
                    vouchers=[str(v.key)],
                    amount=vat,
                    facts=[f],
                )
            )
        if ctx.settings.food_retail:
            try:
                food_rate = ctx.rates.value("vat_food", v.date)
            except KeyError:
                continue
            if food_rate == D("0.06") and any(r.account in OUTPUT_VAT_12 for r in v.effective_rows):
                food_12_after_cut.append(v)
    if food_12_after_cut:
        vat12 = -sum(
            (r.amount for v in food_12_after_cut for r in v.effective_rows if r.account in OUTPUT_VAT_12), ZERO
        )
        f = _fact_amount(ctx, f"food_vat12:{ctx.period.spec}", "Utgående moms 12 % efter sänkningen", vat12)
        out.append(
            candidate(
                ctx,
                rd,
                title="12 % moms på försäljning efter att livsmedelsmomsen sänkts till 6 %",
                description=(
                    f"{len(food_12_after_cut)} försäljningsverifikationer har 12 % moms (totalt {{f:{f.id}}}) "
                    "trots att livsmedelsmomsen är 6 % från 2026-04-01 till 2027-12-31. Kontrollera om "
                    "försäljningen avser livsmedel eller t.ex. restaurangtjänst."
                ),
                key=("food_vat", ctx.period.spec),
                vouchers=[str(v.key) for v in food_12_after_cut[:20]],
                accounts=sorted(
                    {r.account for v in food_12_after_cut for r in v.effective_rows if r.account in OUTPUT_VAT_12}
                ),
                amount=vat12,
                facts=[f],
            )
        )
    return out


# ---------------------------------------------------------------------------- lön


@rule("PAYROLL_TAX_NOT_CLEARED")
def payroll_tax_not_cleared(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    share, minimum = D(str(p["tolerance_share"])), D(str(p["tolerance_min"]))
    out = []
    for label, accs in (
        ("Personalskatt", AccountSet.of((2710, 2719))),
        ("Arbetsgivaravgifter", AccountSet.of((2730, 2739))),
    ):
        balance = -ctx.index.balance_at(accs, ctx.period.end)  # skuld som positivt tal
        month_credits = -sum(
            (r.amount for v in _vouchers(ctx) for r in v.effective_rows if r.account in accs and r.amount < 0), ZERO
        )
        allowed = month_credits * (1 + share) + minimum
        if balance > allowed:
            excess = balance - month_credits
            f = _fact_amount(
                ctx,
                f"payroll_open:{label}",
                f"{label} från tidigare månader",
                excess,
                balance=str(balance),
                month_credits=str(month_credits),
            )
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"{label} från tidigare månader har inte nollställts",
                    description=(
                        f"Skulden för {label.lower()} är större än månadens avdrag – ca {{f:{f.id}}} avser "
                        "tidigare perioder. Kontrollera betalning till skattekontot och bokning av dragningar."
                    ),
                    key=(label, ctx.period.spec),
                    accounts=[a for a in range(2710, 2740) if a in ctx.index.accounts_used and a in accs],
                    amount=excess,
                    facts=[f],
                )
            )
    return out


@rule("EMPLOYER_CONTRIBUTION_RATIO")
def employer_contribution_ratio(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    max_dev = D(str(ctx.params(rd)["max_deviation_pp"])) / 100
    base = ctx.index.movement(SALARY_BASE, ctx.period)
    contrib = ctx.index.movement(EMPLOYER_CONTRIB, ctx.period)
    if base <= 0:
        return []
    full = ctx.rates.value("employer_contribution_full", ctx.period.end)
    ratio = contrib / base
    history = []
    for m in ctx.index.history_months(ctx.period.start, 6):
        b = ctx.index.movement(SALARY_BASE, month(m.year, m.month))
        c = ctx.index.movement(EMPLOYER_CONTRIB, month(m.year, m.month))
        if b > 0:
            history.append(c / b)
    expected = D(str(median(history))) if len(history) >= 3 else full
    youth = ctx.rates.all_valid("employer_contribution_youth", ctx.period.end)
    problem = None
    if contrib == 0:
        problem = "inga arbetsgivaravgifter är bokade trots löner"
    elif ratio > full + D("0.01"):
        problem = "avgifterna är högre än full arbetsgivaravgift"
    elif abs(ratio - expected) > max_dev:
        problem = "avgifterna avviker från kundens normala nivå"
    if problem is None:
        return []
    fr = ctx.store.new(
        "metric",
        "ratio:employer_contribution",
        "Arbetsgivaravgift i % av lön",
        (ratio * 100).quantize(D("0.1")),
        Unit.PERCENT,
        period=ctx.period.spec,
        lineage={"base": str(base), "contributions": str(contrib)},
    )
    fe = ctx.store.new(
        "metric",
        "ratio:employer_contribution_expected",
        "Förväntad nivå",
        (expected * 100).quantize(D("0.1")),
        Unit.PERCENT,
        period=ctx.period.spec,
        lineage={"history_months": len(history), "full_rate": str(full)},
    )
    note = " Observera att tillfälligt sänkt avgift för unga (20,81 %) gäller 2026-04-01–2027-09-30." if youth else ""
    return [
        candidate(
            ctx,
            rd,
            title="Arbetsgivaravgifter stämmer inte mot lönerna",
            description=(f"{problem.capitalize()}: {{f:{fr.id}}} av lönesumman mot normalt {{f:{fe.id}}}.{note}"),
            key=(ctx.period.spec,),
            severity=Severity.HIGH if contrib == 0 else rd.severity,
            accounts=[7510],
            amount=contrib,
            facts=[fr, fe],
        )
    ]


# ---------------------------------------------------------------------------- balans


@rule("TAX_ACCOUNT_BALANCE")
def tax_account_balance(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    tol = D(str(ctx.params(rd)["tolerance"]))
    bal = ctx.index.balance_at(AccountSet.of((1630, 1639)), ctx.period.end)
    if abs(bal) <= tol:
        return []
    f = _fact_amount(ctx, "balance:1630", "Saldo skattekonto (1630)", bal)
    return [
        candidate(
            ctx,
            rd,
            title="Skattekontot (1630) har saldo – stäm av mot Skatteverket",
            description=f"Avräkningskontot för skattekontot har saldo {{f:{f.id}}} vid periodens slut.",
            key=(ctx.period.spec,),
            accounts=[1630],
            amount=bal,
            facts=[f],
        )
    ]


@rule("SUSPENSE_ACCOUNT_BALANCE")
def suspense_account_balance(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    pattern = re.compile(p["name_pattern"], re.I)
    tol = D(str(p["tolerance"]))
    configured = set(ctx.settings.suspense_accounts) | {int(a) for a in p.get("accounts", [])}
    candidates = configured | {a.number for a in ctx.ledger.accounts.values() if pattern.search(a.name)}
    out = []
    for acc in sorted(candidates):
        bal = ctx.index.balance_at(acc, ctx.period.end)
        if abs(bal) > tol:
            f = _fact_amount(ctx, f"balance:{acc}", f"Saldo {acc}", bal)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Saldo på {acc} {ctx.ledger.account_name(acc)}",
                    description=f"Kontot för oklara poster har saldo {{f:{f.id}}} som behöver utredas och bokas om.",
                    key=(acc, ctx.period.spec),
                    accounts=[acc],
                    amount=bal,
                    facts=[f],
                )
            )
    return out


@rule("ASSETS_WITHOUT_DEPRECIATION")
def assets_without_depreciation(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    assets = AccountSet.of((1110, 1299), exclude=[(1130, 1139), (1180, 1189), (1280, 1289)])
    gross = sum((b for a, b in ctx.index.balances_at(ctx.period.end, assets).items() if a % 10 not in (8, 9)), ZERO)
    if gross <= ctx.settings.materiality:
        return []
    dep = AccountSet.of((7700, 7899))
    year = _year(ctx)
    if year is None:
        return []
    n = int(ctx.params(rd)["months_missing"])
    if ctx.maturity.monthly_depreciation:
        recent = [m for m in ctx.index.history_months(add_months(ctx.period.start, 1), n)]
        if len(recent) < n or any(v != 0 for v in ctx.index.monthly_series(dep, recent)):
            return []
        msg = f"Inga avskrivningar de senaste {n} månaderna trots att kunden normalt skriver av månadsvis."
    else:
        if month_start(year.fiscal_year.end) != month_start(ctx.period.end):
            return []
        fy = Period(year.fiscal_year.start, year.fiscal_year.end, ctx.period.kind, year.fiscal_year.label)
        if ctx.index.movement(dep, fy) != 0:
            return []
        msg = "Inga avskrivningar är bokade under räkenskapsåret."
    f = _fact_amount(ctx, "balance:fixed_assets", "Bokfört värde inventarier (brutto)", gross)
    return [
        candidate(
            ctx,
            rd,
            title="Inventarier utan avskrivningar",
            description=f"{msg} Anläggningstillgångar {{f:{f.id}}}.",
            key=(year.fiscal_year.start.isoformat(),),
            accounts=sorted(a for a in ctx.index.accounts_used if a in assets),
            amount=gross,
            facts=[f],
        )
    ]


@rule("VACATION_LIABILITY_STATIC")
def vacation_liability_static(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    n = int(ctx.params(rd)["months"])
    months = ctx.index.history_months(add_months(ctx.period.start, 1), n)
    if len(months) < n:
        return []
    salaries = ctx.index.monthly_series(SALARY_BASE, months)
    if not all(s > 0 for s in salaries):
        return []
    if not ctx.maturity.monthly_vacation_accrual:
        return []  # bolaget bokar semesterlöneskuld vid bokslut – inget fel
    vac = ctx.index.monthly_series(AccountSet.of((2920, 2929)), months)
    if any(v != 0 for v in vac):
        return []
    return [
        candidate(
            ctx,
            rd,
            title="Semesterlöneskulden har inte ändrats",
            description=f"Semesterlöneskulden (2920) har inte förändrats på {n} månader trots löneutbetalningar.",
            key=(ctx.period.spec,),
            accounts=[2920],
        )
    ]


_TAX_YEAR = re.compile(r"(?:tax|taxering|tax\.)\s*(\d{4})|(\d{4})", re.I)


@rule("TAX_ALLOCATION_RESERVE_DUE")
def tax_allocation_reserve_due(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    year = _year(ctx)
    if year is None:
        return []
    years_limit = int(ctx.rates.value("tax_allocation_reserve_years", ctx.period.end))
    out = []
    for acc, bal in ctx.index.balances_at(ctx.period.end, AccountSet.of((2110, 2149))).items():
        if bal >= 0:
            continue
        name = ctx.ledger.account_name(acc)
        m = _TAX_YEAR.search(name)
        if not m:
            continue
        if m.group(1):
            alloc_year = int(m.group(1)) - 1  # "Tax 2020" = taxeringsår → räkenskapsår 2019
        else:
            alloc_year = int(m.group(2))
        due_year = alloc_year + years_limit
        if year.fiscal_year.end.year >= due_year:
            f = _fact_amount(ctx, f"balance:{acc}", f"Periodiseringsfond {acc}", -bal)
            overdue = year.fiscal_year.end.year > due_year
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"{name} ska återföras" + (" (förfallen)" if overdue else ""),
                    description=(
                        f"Fonden avsattes för räkenskapsår {alloc_year} och ska återföras senast "
                        f"räkenskapsår {due_year}. Kvarvarande belopp {{f:{f.id}}}."
                    ),
                    key=(acc, alloc_year),
                    severity=Severity.HIGH if overdue else rd.severity,
                    accounts=[acc],
                    amount=-bal,
                    facts=[f],
                )
            )
    return out


@rule("NEGATIVE_BANK")
def negative_bank(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    if ctx.settings.has_overdraft:
        return []
    out = []
    for acc, bal in ctx.index.balances_at(ctx.period.end, BANK).items():
        if bal < 0:
            f = _fact_amount(ctx, f"balance:{acc}", f"Saldo {acc}", bal)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Negativt saldo på {acc} {ctx.ledger.account_name(acc)}",
                    description=f"Bankkontot har saldo {{f:{f.id}}} vid periodens slut. Stäm av mot kontoutdrag.",
                    key=(acc, ctx.period.spec),
                    accounts=[acc],
                    amount=bal,
                    facts=[f],
                )
            )
    return out


# ---------------------------------------------------------------------------- aktiebolagsrätt

_RELATED_NAME = re.compile(r"(delägare|närstående|aktieägare|styrelse|\bvd\b|ägare)", re.I)


@rule("RELATED_PARTY_RECEIVABLE")
def related_party_receivable(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    min_amount = D(str(ctx.params(rd)["min_amount"]))
    out = []
    candidates: set[int] = {1685, 1360, 1369}
    for a in ctx.ledger.accounts.values():
        if (1300 <= a.number <= 1399 or 1600 <= a.number <= 1699) and _RELATED_NAME.search(a.name):
            candidates.add(a.number)
    balances = ctx.index.balances_at(ctx.period.end)
    for acc in sorted(candidates):
        bal = balances.get(acc, ZERO)
        if bal >= min_amount:
            f = _fact_amount(ctx, f"balance:{acc}", f"Fordran {acc}", bal)
            vouchers = [
                str(v.key)
                for v in ctx.ledger.all_vouchers()
                if v.date <= ctx.period.end and any(r.account == acc and r.amount > 0 for r in v.effective_rows)
            ]
            out.append(
                candidate(
                    ctx,
                    rd,
                    title="Fordran på delägare eller närstående – kontrollera låneförbudet",
                    description=(
                        f"{acc} {ctx.ledger.account_name(acc)} har saldo {{f:{f.id}}}. Lån till aktieägare, "
                        "styrelse, VD eller närstående kan vara förbjudet enligt ABL 21 kap. Kontrollera om "
                        "undantag gäller eller om beloppet ska regleras."
                    ),
                    key=(acc,),
                    accounts=[acc],
                    vouchers=vouchers[-10:],
                    amount=bal,
                    facts=[f],
                )
            )
    # Skuld till närstående med debetsaldo = fordran
    for acc in range(2890, 2900):
        bal = balances.get(acc, ZERO)
        if bal >= min_amount:
            f = _fact_amount(ctx, f"balance:{acc}", f"Saldo {acc}", bal)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Skuld till närstående ({acc}) har debetsaldo",
                    description=f"{acc} har debetsaldo {{f:{f.id}}} – bolaget har i praktiken en fordran på närstående.",
                    key=(acc, "debit"),
                    accounts=[acc],
                    amount=bal,
                    facts=[f],
                )
            )
    return out


@rule("EQUITY_BELOW_HALF_SHARE_CAPITAL")
def equity_below_half(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    year = _year(ctx)
    if year is None:
        return []
    share_capital = -ctx.index.balance_at(AccountSet.of((2081, 2081)), ctx.period.end)
    if share_capital <= 0:
        return []
    bs = balance_sheet(ctx.index, ctx.period.end)
    tax = ctx.rates.value("corporate_tax", ctx.period.end)
    equity = bs.line("total_equity").amount + bs.line("untaxed_reserves").amount * (1 - tax)
    if equity >= share_capital / 2:
        return []
    fe = _fact_amount(ctx, "equity:adjusted", "Justerat eget kapital", equity)
    fs = _fact_amount(ctx, "equity:share_capital", "Registrerat aktiekapital (2081)", share_capital)
    return [
        candidate(
            ctx,
            rd,
            title="Eget kapital under halva aktiekapitalet",
            description=(
                f"Justerat eget kapital {{f:{fe.id}}} understiger hälften av aktiekapitalet {{f:{fs.id}}}. "
                "Styrelsen kan vara skyldig att upprätta kontrollbalansräkning (ABL 25 kap. 13 §). "
                "Indikator baserad på bokföringen – inte en juridisk bedömning."
            ),
            key=(year.fiscal_year.start.isoformat(),),
            accounts=[2081],
            amount=equity,
            facts=[fe, fs],
        )
    ]


# ---------------------------------------------------------------------------- mönster


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


@rule("DUPLICATE_CANDIDATE")
def duplicate_candidate(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    min_amount, max_days = D(str(p["min_amount"])), int(p["max_days"])
    min_sim = float(p["min_text_similarity"])
    window = Period(ctx.period.start - timedelta(days=max_days), ctx.period.end, ctx.period.kind, "")
    vouchers = [v for v in ctx.ledger.all_vouchers() if window.contains(v.date)]
    by_amount: dict[tuple[int, Decimal], list[Voucher]] = defaultdict(list)
    for v in vouchers:
        tags = classify(v)
        if tags & {
            Pattern.VAT_SETTLEMENT,
            Pattern.PAYROLL,
            Pattern.PAYROLL_TAX,
            Pattern.TAX_ACCOUNT,
            Pattern.DEPRECIATION,
            Pattern.ACCRUAL,
        }:
            continue
        for r in v.effective_rows:
            if r.account in PAYABLES and r.amount < 0 and -r.amount >= min_amount:
                by_amount[(r.account, -r.amount)].append(v)
            elif (
                r.account in COSTS
                and r.amount >= min_amount
                and not any(x.account in PAYABLES for x in v.effective_rows)
            ):
                by_amount[(r.account, r.amount)].append(v)
    out = []
    for (acc, amount), vs in by_amount.items():
        vs = sorted({v.key: v for v in vs}.values(), key=lambda v: v.date)
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                a, b = vs[i], vs[j]
                days = (b.date - a.date).days
                if days > max_days:
                    break
                if not (ctx.period.contains(a.date) or ctx.period.contains(b.date)):
                    continue
                sim = SequenceMatcher(None, _norm_text(a.text), _norm_text(b.text)).ratio()
                if sim < min_sim:
                    continue
                f = _fact_amount(ctx, f"dup:{a.key}:{b.key}", "Belopp per verifikation", amount)
                exact = _norm_text(a.text) == _norm_text(b.text)
                out.append(
                    candidate(
                        ctx,
                        rd,
                        title=f"Möjlig dubbelbokning: {a.key} och {b.key}",
                        description=(
                            f"Samma belopp ({{f:{f.id}}}) på konto {acc}, "
                            f"{'identisk' if exact else 'liknande'} text och {days} dagar mellan "
                            f"verifikationerna ('{a.text}')."
                        ),
                        key=tuple(sorted([str(a.key), str(b.key)])),
                        severity=Severity.HIGH if exact else Severity.MEDIUM,
                        vouchers=[str(a.key), str(b.key)],
                        accounts=[acc],
                        amount=amount,
                        facts=[f],
                        details={"text_similarity": round(sim, 2), "days_apart": days},
                    )
                )
    return out


@rule("LARGE_MANUAL_POSTING")
def large_manual_posting(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    series = set(ctx.settings.manual_series or p["manual_series"])
    days = int(p["days_before_end"])
    threshold = max(D(str(p["min_amount"])), ctx.settings.materiality * 5)
    out = []
    for v in _vouchers(ctx):
        if v.series not in series or (ctx.period.end - v.date).days > days:
            continue
        if is_routine(v) or v.debit_total < threshold:
            continue
        f = _fact_amount(ctx, f"voucher:{v.key}:total", f"Belopp {v.key}", v.debit_total)
        out.append(
            candidate(
                ctx,
                rd,
                title=f"Stor manuell post nära periodslut: {v.key}",
                description=(
                    f"'{v.text}' ({v.date}) på {{f:{f.id}}} är en manuell verifikation nära periodens slut "
                    "som inte liknar en rutinpost. Kontrollera underlag."
                ),
                key=(str(v.key), v.date.isoformat()),
                vouchers=[str(v.key)],
                accounts=sorted({r.account for r in v.effective_rows}),
                amount=v.debit_total,
                facts=[f],
            )
        )
    return out


def _signature(v: Voucher) -> tuple[tuple[int, Decimal], ...]:
    agg: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for r in v.effective_rows:
        agg[r.account] += r.amount
    return tuple(sorted((a, x) for a, x in agg.items() if x != 0))


@rule("RAPID_REVERSAL")
def rapid_reversal(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    max_days, min_amount = int(p["max_days"]), D(str(p["min_amount"]))
    window_start = ctx.period.start - timedelta(days=max_days)
    vs = [v for v in ctx.ledger.all_vouchers() if window_start <= v.date <= ctx.period.end]
    sigs: dict[tuple[tuple[int, Decimal], ...], list[Voucher]] = defaultdict(list)
    for v in vs:
        sigs[_signature(v)].append(v)
    out = []
    for v in vs:
        if not ctx.period.contains(v.date) or v.debit_total < min_amount:
            continue
        if Pattern.ACCRUAL in classify(v):
            continue  # periodiseringar återförs normalt nästa månad
        neg = tuple(sorted((a, -x) for a, x in _signature(v)))
        for w in sigs.get(neg, []):
            if w.key == v.key or not (0 < (v.date - w.date).days <= max_days):
                continue
            f = _fact_amount(ctx, f"reversal:{w.key}:{v.key}", "Återfört belopp", v.debit_total)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"{w.key} återförs av {v.key} inom {(v.date - w.date).days} dagar",
                    description=(
                        f"'{w.text}' ({w.date}) motbokas helt av '{v.text}' ({v.date}), belopp {{f:{f.id}}}. "
                        "Kontrollera syftet med bokningen."
                    ),
                    key=tuple(sorted([str(w.key), str(v.key)])),
                    vouchers=[str(w.key), str(v.key)],
                    accounts=sorted({a for a, _ in _signature(v)}),
                    amount=v.debit_total,
                    facts=[f],
                )
            )
    return out


def _pair(v: Voucher) -> tuple[int, int] | None:
    rows = v.effective_rows
    if not rows:
        return None
    debit = max(rows, key=lambda r: r.amount)
    credit = min(rows, key=lambda r: r.amount)
    if debit.amount <= 0 or credit.amount >= 0:
        return None
    return (debit.account, credit.account)


@rule("UNUSUAL_ACCOUNT_COMBINATION")
def unusual_account_combination(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    min_months, min_amount = int(p["min_history_months"]), max(D(str(p["min_amount"])), ctx.settings.materiality)
    history_months = ctx.index.history_months(ctx.period.start, 24)
    if len(history_months) < min_months:
        return []
    seen: set[tuple[int, int]] = set()
    known_accounts: set[int] = set()
    for v in ctx.ledger.all_vouchers():
        if v.date < ctx.period.start:
            pr = _pair(v)
            if pr:
                seen.add(pr)
            known_accounts |= {r.account for r in v.effective_rows}
    out = []
    for v in _vouchers(ctx):
        pr = _pair(v)
        if pr is None or pr in seen or v.debit_total < min_amount or is_routine(v):
            continue
        if pr[0] not in known_accounts or pr[1] not in known_accounts:
            continue  # nya konton fångas av andra kontroller
        out.append(
            candidate(
                ctx,
                rd,
                title=f"Ovanlig kontering {pr[0]} mot {pr[1]} i {v.key}",
                description=(
                    f"Kombinationen debet {pr[0]} {ctx.ledger.account_name(pr[0])} / kredit {pr[1]} "
                    f"{ctx.ledger.account_name(pr[1])} har inte förekommit tidigare ('{v.text}')."
                ),
                key=(str(v.key), pr[0], pr[1]),
                vouchers=[str(v.key)],
                accounts=list(pr),
                amount=v.debit_total,
            )
        )
    return out


# ---------------------------------------------------------------------------- trender


@rule("COST_DEVIATION")
def cost_deviation(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    p = ctx.params(rd)
    min_hist = int(p["min_history_months"])
    zlim, rel = D(str(p["robust_z"])), D(str(p["min_relative_change"]))
    min_amount = max(D(str(p["min_amount"])), ctx.settings.materiality)
    history = ctx.index.history_months(ctx.period.start, 12)
    if len(history) < min_hist:
        return []
    skip = AccountSet.of((7290, 7299), (7700, 7899), (7510, 7599))  # periodiseringsberoende
    out = []
    current = ctx.index.movement_by_account(AccountSet.of((4000, 7999)), ctx.period)
    candidates_accounts = set(current)
    for m in history:
        candidates_accounts |= {a for a in ctx.index.movements.get(m, {}) if 4000 <= a <= 7999}
    for acc in sorted(candidates_accounts):
        if acc in skip and ctx.maturity.low_periodization:
            continue
        x = current.get(acc, ZERO)
        series = ctx.index.monthly_series(acc, history)
        nonzero = [s for s in series if s != 0]
        med = D(str(median(series)))
        if not nonzero:
            if x >= min_amount:
                f = _fact_amount(ctx, f"movement:{acc}", f"Kostnad konto {acc}", x)
                out.append(
                    candidate(
                        ctx,
                        rd,
                        title=f"Ny kostnad: {acc} {ctx.ledger.account_name(acc)}",
                        description=f"Kontot har inte använts de senaste {len(history)} månaderna. Periodens kostnad {{f:{f.id}}}.",
                        key=(acc, ctx.period.spec, "new"),
                        accounts=[acc],
                        amount=x,
                        facts=[f],
                    )
                )
            continue
        if x == 0 and len(nonzero) >= len(series) - 1 and med >= min_amount:
            f = _fact_amount(ctx, f"median:{acc}", f"Normal månadskostnad {acc}", med)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Förväntad kostnad saknas: {acc} {ctx.ledger.account_name(acc)}",
                    description=f"Kontot brukar ha ca {{f:{f.id}}} per månad men saknar kostnad i perioden.",
                    key=(acc, ctx.period.spec, "missing"),
                    accounts=[acc],
                    amount=med,
                    facts=[f],
                )
            )
            continue
        mad = D(str(median([abs(s - med) for s in series])))
        scale = mad * D("1.4826") if mad > 0 else max(abs(med) * D("0.1"), D(1))
        z = (x - med) / scale
        if abs(x - med) >= min_amount and abs(z) >= zlim and (med == 0 or abs(x - med) / abs(med) >= rel):
            fx = _fact_amount(ctx, f"movement:{acc}", f"Kostnad konto {acc}", x)
            fm = _fact_amount(ctx, f"median:{acc}", f"Normal månadskostnad {acc}", med)
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Kostnad avviker: {acc} {ctx.ledger.account_name(acc)}",
                    description=f"Periodens kostnad {{f:{fx.id}}} mot normalt {{f:{fm.id}}} per månad.",
                    key=(acc, ctx.period.spec, "deviation"),
                    severity=Severity.MEDIUM if abs(x - med) >= ctx.settings.materiality * 10 else rd.severity,
                    accounts=[acc],
                    amount=x - med,
                    facts=[fx, fm],
                    details={"robust_z": str(z.quantize(D("0.1")))},
                )
            )
    return out


@rule("REVENUE_COST_TREND")
def revenue_cost_trend(ctx: RuleContext, rd: RuleDefinition) -> list[FindingCandidate]:
    threshold = D(str(ctx.params(rd)["threshold_pct"]))
    cur = rolling(ctx.period.end, 12)
    prev = rolling(add_months(ctx.period.end, -12), 12)
    if not (ctx.index.has_data(cur) and ctx.index.has_data(prev)):
        return []
    out = []
    for label, accs, sign in (("Omsättning", SALES, -1), ("Kostnader", AccountSet.of((4000, 7999)), 1)):
        c = ctx.index.movement(accs, cur) * sign
        pv = ctx.index.movement(accs, prev) * sign
        if pv <= 0:
            continue
        pct = (c - pv) / pv * 100
        if abs(pct) >= threshold:
            fc = ctx.store.new("amount", f"r12:{label}", f"{label} R12", c, Unit.SEK, period=cur.spec)
            fp = ctx.store.new(
                "change",
                f"r12:{label}:pct",
                f"Förändring {label.lower()} R12",
                pct.quantize(D("0.1")),
                Unit.PERCENT,
                period=cur.spec,
                compare_period=prev.spec,
            )
            out.append(
                candidate(
                    ctx,
                    rd,
                    title=f"Trendbrott: {label.lower()} rullande 12 månader",
                    description=f"{label} R12 {{f:{fc.id}}}, förändring {{f:{fp.id}}} mot föregående 12 månader.",
                    key=(label, ctx.period.spec),
                    amount=c,
                    facts=[fc, fp],
                )
            )
    return out
