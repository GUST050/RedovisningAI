"""Syntetiska men realistiska svenska bolag för test, demo och golden tests.

Bokföringen följer BAS och vanliga flöden (kundfakturor, leverantörsfakturor, löner,
skattekonto, moms, avskrivningar). Med `plant_anomalies=True` planteras kända fel som
kontrollerna ska hitta – de listas i `GeneratedCompany.planted`.

Allt är deterministiskt för ett givet `seed`.
"""

from __future__ import annotations

import calendar
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from redovisningai.domain.ledger import Account, FiscalYear, Ledger, Row, RowStatus, Voucher, YearData

D = Decimal
CENT = D("0.01")


def q(x: float | Decimal) -> Decimal:
    return D(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


ACCOUNTS: dict[int, str] = {
    1220: "Inventarier och verktyg",
    1229: "Ackumulerade avskrivningar på inventarier och verktyg",
    1510: "Kundfordringar",
    1630: "Avräkning för skatter och avgifter (skattekonto)",
    1685: "Kortfristiga fordringar hos delägare eller närstående",
    1790: "Övriga förutbetalda kostnader och upplupna intäkter",
    1910: "Kassa",
    1930: "Företagskonto",
    2081: "Aktiekapital",
    2091: "Balanserad vinst eller förlust",
    2099: "Årets resultat",
    2120: "Periodiseringsfond Tax 2020",
    2440: "Leverantörsskulder",
    2611: "Utgående moms på försäljning inom Sverige, 25 %",
    2621: "Utgående moms på försäljning inom Sverige, 12 %",
    2631: "Utgående moms på försäljning inom Sverige, 6 %",
    2641: "Debiterad ingående moms",
    2650: "Redovisningskonto för moms",
    2710: "Personalskatt",
    2731: "Avräkning lagstadgade sociala avgifter",
    2893: "Skulder till närstående personer, kortfristig del",
    2920: "Upplupna semesterlöner",
    2999: "OBS-konto",
    3001: "Försäljning inom Sverige, 25 % moms",
    3002: "Försäljning inom Sverige, 12 % moms",
    3003: "Försäljning inom Sverige, 6 % moms",
    4010: "Inköp material och varor",
    5010: "Lokalhyra",
    5460: "Förbrukningsmaterial",
    6110: "Kontorsmateriel",
    6212: "Mobiltelefon",
    6310: "Företagsförsäkringar",
    6540: "IT-tjänster",
    6550: "Konsultarvoden",
    6570: "Bankkostnader",
    7210: "Löner till tjänstemän",
    7290: "Förändring av semesterlöneskuld",
    7510: "Arbetsgivaravgifter 31,42 %",
    7832: "Avskrivningar på inventarier och verktyg",
    8410: "Räntekostnader för långfristiga skulder",
}

MATERIAL_VENDORS = ["Byggvaruhuset", "Ahlsell", "Beijer Bygg", "Bauhaus Proffs"]
CUSTOMERS = ["Brf Solgläntan", "Kommunfastigheter", "Villa Andersson", "Hotell Kronan", "Skolverket Norr"]


@dataclass
class CompanyProfile:
    name: str
    org_number: str
    seed: int
    industry: str = "bygg"
    legal_form: str = "AB"
    vat_period: str = "quarter"  # month | quarter | year
    fiscal_start_month: int = 1
    first_year: int = 2024
    monthly_revenue: float = 900_000.0
    annual_growth: float = 0.12
    employees: int = 6
    monthly_salary: float = 38_000.0
    rent: float = 42_000.0
    monthly_accruals: bool = True  # avskrivningar & semesterlöneskuld varje månad
    cash_method: bool = False
    sales_vat_rates: tuple[tuple[int, float], ...] = ((3001, 0.25),)
    food_retail: bool = False
    # Månader (år, månad) då kassan felaktigt fortsatte med 12 % livsmedelsmoms efter sänkningen.
    food_vat_error_months: tuple[tuple[int, int], ...] = ()
    plant_anomalies: bool = False
    recurring_insurance: bool = False


@dataclass
class GeneratedCompany:
    profile: CompanyProfile
    ledger: Ledger
    planted: dict[str, str] = field(default_factory=dict)


@dataclass
class _Draft:
    series: str
    date: date
    text: str
    rows: list[Row]
    reg_date: date
    seq: int


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


class _Builder:
    def __init__(self, profile: CompanyProfile, as_of: date) -> None:
        self.p = profile
        self.as_of = as_of
        self.rng = random.Random(profile.seed)
        self.drafts: list[_Draft] = []
        self.seq = 0
        self.planted: dict[str, str] = {}
        self.pending: list[tuple[date, str, str, list[tuple[int, Decimal, str | None]]]] = []

    # ------------------------------------------------------------------ primitives
    def ver(
        self,
        series: str,
        d: date,
        text: str,
        rows: list[tuple[int, Decimal, str | None]] | list[tuple[int, Decimal]],
        reg_date: date | None = None,
        statuses: list[RowStatus] | None = None,
    ) -> None:
        self.seq += 1
        built: list[Row] = []
        for i, r in enumerate(rows):
            acc, amt = r[0], r[1]
            rtext = r[2] if len(r) > 2 else None  # type: ignore[misc]
            st = statuses[i] if statuses else RowStatus.NORMAL
            built.append(Row(account=acc, amount=q(amt), text=rtext, status=st))
        reg = reg_date or (d + timedelta(days=self.rng.randint(0, 3)))
        self.drafts.append(_Draft(series, d, text, built, reg, self.seq))

    # ------------------------------------------------------------------ flows
    def revenue_for(self, month_index: int, d: date) -> float:
        season = 1.0 + 0.15 * (1 if d.month in (4, 5, 6, 9, 10) else -0.5 if d.month in (7, 12) else 0)
        growth = (1 + self.p.annual_growth) ** (month_index / 12)
        noise = self.rng.uniform(0.9, 1.1)
        return self.p.monthly_revenue * season * growth * noise

    def sales(self, month_index: int, y: int, m: int) -> None:
        total = self.revenue_for(month_index, date(y, m, 1))
        n = self.rng.randint(3, 6)
        for i in range(n):
            d = date(y, m, min(28, 3 + i * 5 + self.rng.randint(0, 3)))
            net_total = q(total / n)
            customer = self.rng.choice(CUSTOMERS)
            rows: list[tuple[int, Decimal, str | None]] = []
            gross = D(0)
            for acc, rate in self._sales_rates(d):
                share = q(net_total / len(self._sales_rates(d)))
                vat = q(share * D(str(rate)))
                vat_acc = {0.25: 2611, 0.12: 2621, 0.06: 2631}[rate]
                rows += [(acc, -share, None), (vat_acc, -vat, None)]
                gross += share + vat
            if self.p.cash_method:
                self.ver("C", d, f"Kontantförsäljning {customer}", [(1930, gross, None), *rows])
            else:
                self.ver("B", d, f"Kundfaktura {customer}", [(1510, gross, None), *rows])
                pay = d + timedelta(days=self.rng.randint(20, 40))
                self.ver("C", pay, f"Inbetalning {customer}", [(1930, gross), (1510, -gross)])

    def _sales_rates(self, d: date) -> list[tuple[int, float]]:
        if self.p.food_retail:
            food_rate = 0.06 if d >= date(2026, 4, 1) else 0.12
            if (d.year, d.month) in self.p.food_vat_error_months:
                food_rate = 0.12
                self.planted["OUTPUT_VAT_RATIO"] = "12 % livsmedelsmoms i april–maj 2026 efter sänkningen till 6 %"
            food_acc = 3003 if food_rate == 0.06 else 3002
            return [(food_acc, food_rate), (3001, 0.25)]
        return list(self.p.sales_vat_rates)

    def supplier(self, d: date, vendor: str, cost_acc: int, net: float, pay_days: int = 30) -> None:
        net_d = q(net)
        vat = q(net_d * D("0.25"))
        gross = net_d + vat
        if self.p.cash_method:
            self.ver("E", d, f"{vendor}", [(cost_acc, net_d), (2641, vat), (1930, -gross)])
            return
        self.ver("D", d, f"Leverantörsfaktura {vendor}", [(cost_acc, net_d), (2641, vat), (2440, -gross)])
        self.ver("E", d + timedelta(days=pay_days), f"Betalning {vendor}", [(2440, gross), (1930, -gross)])

    def purchases(self, month_index: int, y: int, m: int) -> None:
        rev = self.revenue_for(month_index, date(y, m, 1))
        for _ in range(self.rng.randint(2, 4)):
            vendor = self.rng.choice(MATERIAL_VENDORS)
            self.supplier(date(y, m, self.rng.randint(2, 26)), vendor, 4010, rev * 0.35 / 3)
        self.supplier(date(y, m, 1), "Fastighets AB Kvarnen", 5010, self.p.rent, pay_days=0)
        ms = 24_900 if date(y, m, 1) < date(2026, 4, 1) else 31_200
        self.supplier(date(y, m, 3), "Microsoft Ireland", 6540, ms * self.p.employees / 6)
        self.supplier(date(y, m, 5), "Fortnox AB", 6540, 1_500)
        self.supplier(date(y, m, 8), "Telia Sverige AB", 6212, 450 * self.p.employees)
        self.supplier(date(y, m, 12), "Staples", 6110, self.rng.uniform(800, 2500))
        self.supplier(date(y, m, 14), "Amazon Web Services", 6540, self.rng.uniform(4_000, 9_000))
        if self.p.plant_anomalies and date(y, m, 1) >= date(2026, 4, 1):
            self.supplier(date(y, m, 20), "Konsultgruppen Nord AB", 6550, self.rng.uniform(55_000, 75_000))
        if self.p.recurring_insurance and m in (3, 6, 9, 12):
            self.supplier(date(y, m, 15), "Trygg Försäkring AB", 6310, 18_600)
        fee = q(self.rng.uniform(250, 450))
        self.ver("A", _month_end(y, m), "Bankavgifter", [(6570, fee), (1930, -fee)])

    def payroll(self, y: int, m: int, aga_rate: Decimal = D("0.3142")) -> tuple[Decimal, Decimal]:
        gross = q(self.p.monthly_salary * self.p.employees * self.rng.uniform(0.98, 1.03))
        tax = q(gross * D("0.30"))
        net = gross - tax
        aga = q(gross * aga_rate)
        d = date(y, m, 25)
        self.ver("L", d, f"Lön {y}-{m:02d}", [(7210, gross), (2710, -tax), (1930, -net)])
        self.ver("L", _month_end(y, m), f"Arbetsgivaravgifter {y}-{m:02d}", [(7510, aga), (2731, -aga)])
        # Dragning på skattekontot och inbetalning den 12:e nästa månad.
        nd = _add_months(date(y, m, 12), 1)
        self.ver("A", nd, "Inbetalning skattekonto", [(1630, tax + aga), (1930, -(tax + aga))])
        self.ver(
            "A",
            nd,
            "Skattekonto: skatter och avgifter",
            [(2710, tax), (2731, aga), (1630, -(tax + aga))],
        )
        return gross, aga

    def accruals(self, y: int, m: int, gross: Decimal, depreciation: Decimal, fy_end_month: int) -> None:
        vac = q(gross * D("0.12") * D("0.30"))
        if self.p.monthly_accruals:
            self.ver("A", _month_end(y, m), "Semesterlöneskuld", [(7290, vac), (2920, -vac)])
            if depreciation > 0:
                self.ver(
                    "A", _month_end(y, m), "Avskrivning inventarier", [(7832, depreciation), (1229, -depreciation)]
                )
        elif m == fy_end_month:
            self.ver("A", _month_end(y, m), "Semesterlöneskuld bokslut", [(7290, vac * 4), (2920, -vac * 4)])
            if depreciation > 0:
                total = depreciation * 12
                self.ver("A", _month_end(y, m), "Avskrivningar bokslut", [(7832, total), (1229, -total)])

    # ------------------------------------------------------------------ build
    def build(self) -> GeneratedCompany:
        p = self.p
        start = date(p.first_year, p.fiscal_start_month, 1)
        fiscal_years: list[FiscalYear] = []
        fy_start = start
        while fy_start <= self.as_of:
            fy_end = _add_months(fy_start, 12) - timedelta(days=1)
            fiscal_years.append(FiscalYear(fy_start, fy_end))
            fy_start = _add_months(fy_start, 12)
        fy_end_month = fiscal_years[0].end.month

        depreciation = q(300_000 / 60)
        month = start
        idx = 0
        vat_months = {"month": 1, "quarter": 3, "year": 12}[p.vat_period]
        while month <= self.as_of:
            y, m = month.year, month.month
            self.sales(idx, y, m)
            self.purchases(idx, y, m)
            aga_rate = D("0.3142")
            if p.plant_anomalies and (y, m) == (2026, 8):
                aga_rate = D("0.15")
                self.planted["EMPLOYER_CONTRIBUTION_RATIO"] = "Arbetsgivaravgifter bokade med 15 % i augusti 2026"
            gross, _ = self.payroll(y, m, aga_rate)
            dep = depreciation
            if p.plant_anomalies and month >= date(2026, 7, 1):
                dep = D(0)
            self.accruals(y, m, gross, dep, fy_end_month)
            if ((m - p.fiscal_start_month) % 12 + 1) % vat_months == 0:
                self._vat_settlement(y, m, vat_months)
            if m in (3, 6, 9, 12):
                interest = q(self.rng.uniform(2_000, 3_500))
                self.ver("A", _month_end(y, m), "Räntekostnad lån", [(8410, interest), (1930, -interest)])
            month = _add_months(month, 1)
            idx += 1
        if p.plant_anomalies:
            self._plant()
        return self._assemble(fiscal_years)

    def _vat_settlement(self, y: int, m: int, months: int) -> None:
        """Flytta periodens moms till 2650. Beloppen räknas i _assemble (behöver saldon)."""
        period_end = _month_end(y, m)
        period_start = _add_months(date(y, m, 1), -(months - 1))
        skip_input = self.p.plant_anomalies and (y, m) == (2026, 6)
        if skip_input:
            self.planted["VAT_ACCOUNTS_NOT_CLEARED"] = "Ingående moms (2641) för Q2 2026 flyttades inte till 2650"
        self.pending.append((period_end, "vat", "", [(0, D(0), f"{period_start}|{period_end}|{int(skip_input)}")]))

    def _plant(self) -> None:
        # 1. Dubbelbokad leverantörsfaktura
        self.ver(
            "D",
            date(2026, 9, 10),
            "Leverantörsfaktura Byggvaruhuset 88213",
            [(4010, D("48200.00")), (2641, D("12050.00")), (2440, D("-60250.00"))],
        )
        self.ver(
            "D",
            date(2026, 9, 12),
            "Leverantörsfaktura Byggvaruhuset 88213",
            [(4010, D("48200.00")), (2641, D("12050.00")), (2440, D("-60250.00"))],
        )
        self.planted["DUPLICATE_CANDIDATE"] = "Byggvaruhuset faktura 88213 bokförd två gånger (sept 2026)"
        # 2. Lån till delägare
        self.ver("A", date(2026, 6, 15), "Utlägg ägare Erik", [(1685, D("150000.00")), (1930, D("-150000.00"))])
        self.planted["RELATED_PARTY_RECEIVABLE"] = "150 000 kr fordran på delägare (1685) juni 2026"
        # 3. Obalanserad verifikation
        self.ver("A", date(2026, 5, 20), "Diverse korrigering", [(6110, D("1250.00")), (1930, D("-1249.00"))])
        self.planted["UNBALANCED_VOUCHER"] = "Verifikation med 1 kr differens (maj 2026)"
        # 4. Sent bokförd
        self.ver(
            "A",
            date(2026, 3, 2),
            "Kontantköp förbrukningsmaterial",
            [(5460, D("2400.00")), (2641, D("600.00")), (1910, D("-3000.00"))],
            reg_date=date(2026, 5, 10),
        )
        self.planted["LATE_BOOKING"] = "Kontant utbetalning 2 mars bokförd 10 maj 2026"
        # 5. Negativ kassa (kassan har aldrig haft insättningar) – samma verifikation som ovan
        self.planted["ABNORMAL_SIGN"] = "Kassa (1910) negativ"
        # 6. Stor manuell post vid månadsslut
        self.ver(
            "A",
            date(2026, 7, 30),
            "Justering enligt ägare",
            [(6550, D("250000.00")), (2893, D("-250000.00"))],
        )
        self.planted["LARGE_MANUAL_POSTING"] = "250 000 kr manuell post 30 juli 2026"
        # 7. Snabb återföring
        self.ver("A", date(2026, 8, 5), "Omföring", [(6540, D("85000.00")), (1930, D("-85000.00"))])
        self.ver("A", date(2026, 8, 7), "Återföring omföring", [(6540, D("-85000.00")), (1930, D("85000.00"))])
        self.planted["RAPID_REVERSAL"] = "85 000 kr bokat och återfört inom 2 dagar (aug 2026)"
        # 8. OBS-konto
        self.ver("A", date(2026, 8, 18), "Okänd inbetalning", [(1930, D("12400.00")), (2999, D("-12400.00"))])
        self.planted["SUSPENSE_ACCOUNT_BALANCE"] = "12 400 kr på OBS-konto 2999"
        # 9. Konto utanför kontoplanen
        self.ver("A", date(2026, 4, 9), "Diverse", [(6993, D("3200.00")), (1930, D("-3200.00"))])
        self.planted["ACCOUNT_NOT_IN_CHART"] = "Konto 6993 saknas i kontoplanen"
        # 10. Sent registrerad augustiverifikation (dyker upp först i septemberexporten)
        self.ver(
            "D",
            date(2026, 8, 28),
            "Leverantörsfaktura Ahlsell 55120",
            [(4010, D("18400.00")), (2641, D("4600.00")), (2440, D("-23000.00"))],
            reg_date=date(2026, 9, 15),
        )
        self.planted["CHANGED_AFTER_APPROVAL"] = "Augustifaktura från Ahlsell registrerad 15 sept"
        # 11. Rättad rad (#BTRANS/#RTRANS)
        self.ver(
            "A",
            date(2026, 2, 27),
            "Kontorsmaterial (rättad)",
            [(6110, D("900.00")), (5460, D("900.00")), (1930, D("-900.00"))],
            statuses=[RowStatus.REMOVED, RowStatus.ADDED, RowStatus.NORMAL],
        )
        self.planted["TAX_ALLOCATION_RESERVE_DUE"] = "Periodiseringsfond Tax 2020 ska återföras"

    def _assemble(self, fiscal_years: list[FiscalYear]) -> GeneratedCompany:
        p = self.p
        ledger = Ledger(company_name=p.name, org_number=p.org_number, program="RedovisningAI testdata")
        for no, name in ACCOUNTS.items():
            ledger.accounts[no] = Account(no, name)
        if not p.plant_anomalies:
            ledger.accounts[2120] = Account(2120, f"Periodiseringsfond Tax {p.first_year + 1}")
        # Konto 6993 ska inte finnas i kontoplanen.
        # Ingående balans första året (summerar till 0).
        opening: dict[int, Decimal] = {
            1220: D("300000.00"),
            1229: D("-60000.00"),
            1930: D("450000.00"),
            1510: D("0.00"),
            2081: D("-50000.00"),
            2120: D("-80000.00"),
            2440: D("0.00"),
        }
        opening[2091] = -sum(opening.values(), D(0))
        # Momsomföringar beräknas nu när alla andra verifikationer finns.
        drafts = sorted(self.drafts, key=lambda d: (d.date, d.seq))
        for _period_end, _kind, _t, meta in sorted(self.pending, key=lambda x: x[0]):
            start_s, end_s, skip_s = str(meta[0][2]).split("|")
            ps, pe = date.fromisoformat(start_s), date.fromisoformat(end_s)
            sums: dict[int, Decimal] = defaultdict(lambda: D(0))
            for dr in drafts:
                if ps <= dr.date <= pe and "Momsredovisning" not in dr.text:
                    for r in dr.rows:
                        if r.account in (2611, 2621, 2631, 2641) and r.is_effective:
                            sums[r.account] += r.amount
            rows: list[tuple[int, Decimal, str | None]] = []
            total = D(0)
            for acc in (2611, 2621, 2631, 2641):
                if acc == 2641 and skip_s == "1":
                    continue
                if sums[acc] != 0:
                    rows.append((acc, -sums[acc], None))
                    total += sums[acc]
            if rows:
                rows.append((2650, total, None))
                self.seq += 1
                v = _Draft("A", pe, f"Momsredovisning {ps:%Y-%m} - {pe:%Y-%m}", [], pe, self.seq)
                v.rows = [Row(a, q(x)) for a, x, _ in rows]
                drafts.append(v)
                # Betalning/återbetalning via skattekontot ca 42 dagar efter periodens slut.
                pay = pe + timedelta(days=42)
                amount = -total  # skuld (positivt belopp = att betala)
                self.seq += 1
                drafts.append(
                    _Draft(
                        "A",
                        pay,
                        "Skattekonto: moms",
                        [Row(2650, q(amount)), Row(1630, q(-amount))],
                        pay,
                        self.seq,
                    )
                )
                self.seq += 1
                drafts.append(
                    _Draft(
                        "A",
                        pay,
                        "Inbetalning skattekonto moms",
                        [Row(1630, q(amount)), Row(1930, q(-amount))],
                        pay,
                        self.seq,
                    )
                )

        prev_closing: dict[int, Decimal] | None = None
        prev_result = D(0)
        for fy in fiscal_years:
            yd = YearData(fiscal_year=fy, source_ref=f"generated:{fy.label}")
            if prev_closing is None:
                yd.opening = dict(opening)
            else:
                ib = {a: v for a, v in prev_closing.items() if a < 3000}
                ib[2091] = ib.get(2091, D(0)) + ib.get(2099, D(0))
                ib[2099] = -prev_result
                if p.plant_anomalies and fy.start.year == 2026:
                    ib[1510] = ib.get(1510, D(0)) + D("500.00")
                    ib[2091] = ib.get(2091, D(0)) - D("500.00")
                    self.planted["IB_NE_PREV_UB"] = "IB 2026 för 1510 avviker 500 kr från UB 2025"
                yd.opening = {a: v for a, v in ib.items() if v != 0}
            in_year = [d for d in drafts if fy.contains(d.date) and d.reg_date <= self.as_of and d.date <= self.as_of]
            # Numrering per serie i registreringsordning (stabil mellan exporter).
            in_year.sort(key=lambda d: (d.reg_date, d.date, d.seq))
            counters: dict[str, int] = defaultdict(int)
            vouchers: list[Voucher] = []
            gap_done = False
            for dr in in_year:
                counters[dr.series] += 1
                if p.plant_anomalies and dr.series == "A" and fy.start.year == 2026 and not gap_done:
                    if counters["A"] == 40:
                        counters["A"] += 1  # lucka i nummerserien
                        gap_done = True
                        self.planted["VOUCHER_NUMBER_GAP"] = "Verifikation A40 saknas 2026"
                vouchers.append(
                    Voucher(
                        series=dr.series,
                        number=str(counters[dr.series]),
                        date=dr.date,
                        text=dr.text,
                        rows=tuple(dr.rows),
                        reg_date=dr.reg_date,
                    )
                )
            vouchers.sort(key=lambda v: (v.date, v.series, int(v.number)))
            yd.vouchers = vouchers
            # Saldon
            mov: dict[int, Decimal] = defaultdict(lambda: D(0))
            for v in vouchers:
                for r in v.effective_rows:
                    mov[r.account] += r.amount
            closing = dict(yd.opening)
            for a, x in mov.items():
                if a < 3000:
                    closing[a] = closing.get(a, D(0)) + x
            yd.closing = {a: v for a, v in closing.items() if v != 0}
            yd.result = {a: x for a, x in mov.items() if a >= 3000 and x != 0}
            # Budget (#PBUDGET) för omsättning
            for mo in fy.months():
                yd.budget[(mo, 3001)] = q(-p.monthly_revenue * 1.1)
            prev_result = -sum((x for a, x in mov.items() if a >= 3000), D(0))
            prev_closing = closing
            ledger.years.append(yd)
        ledger.sort()
        return GeneratedCompany(profile=p, ledger=ledger, planted=self.planted)


def generate(profile: CompanyProfile, as_of: date) -> GeneratedCompany:
    return _Builder(profile, as_of).build()


# ---------------------------------------------------------------------- demoportfölj

DEMO_PROFILES: list[CompanyProfile] = [
    CompanyProfile(name="Bygg & Co AB", org_number="556677-8899", seed=11, industry="bygg", plant_anomalies=True),
    CompanyProfile(
        name="Lilja Livs AB",
        org_number="559012-3456",
        seed=22,
        industry="livsmedelshandel",
        vat_period="month",
        monthly_revenue=650_000,
        employees=4,
        monthly_salary=29_000,
        rent=28_000,
        food_retail=True,
        food_vat_error_months=((2026, 4), (2026, 5)),
    ),
    CompanyProfile(
        name="Konsult X AB",
        org_number="559876-5432",
        seed=33,
        industry="konsult",
        fiscal_start_month=7,
        first_year=2024,
        monthly_revenue=320_000,
        employees=2,
        monthly_salary=52_000,
        rent=9_000,
        monthly_accruals=False,
        cash_method=True,
    ),
    CompanyProfile(
        name="Nord Frakt AB",
        org_number="556432-1098",
        seed=44,
        industry="transport",
        monthly_revenue=1_400_000,
        employees=9,
        recurring_insurance=True,
    ),
]
