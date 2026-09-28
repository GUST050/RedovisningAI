"""Rapporter: kundrapport (bara CLIENT_SAFE), internrapport och Reko-dokumentation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from redovisningai.reports.analysis import ReportAnalysis
from redovisningai.reports.document import Document, Section, Table

STATUS_SV = {
    "OPEN": "Öppet",
    "WAITING_CLIENT": "Väntar på kund",
    "CLOSED": "Avslutat",
    "NEW": "Ny",
    "IN_PROGRESS": "Under utredning",
    "ASK_CLIENT": "Fråga till kund",
    "RESOLVED": "Åtgärdat",
    "ACCEPTED_OK": "Bedömt OK",
    "SUPPRESSED": "Undertryckt",
    "AUTO_CLOSED": "Rättat i bokföringen",
}
SEVERITY_SV = {"HIGH": "Hög", "MEDIUM": "Medel", "LOW": "Låg"}
MATURITY_SV = {"COMPLETE": "Fullständig", "PRELIMINARY": "Preliminär", "NO_DATA": "Saknar data"}
METHOD_SV = {"invoice": "faktureringsmetoden", "cash": "kontantmetoden", "unknown": "okänd"}


def _metric_rows(section: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for m in section["metrics"].values():
        f = m["fact"]
        prev = m.get("previous", {})
        ch = m.get("change", {})
        rows.append([f["label"], f["display"], prev.get("display", ""), ch.get("display", "")])
    return rows


def _exact_kr(value: str | Decimal | None) -> str:
    if value is None:
        return ""
    amount = Decimal(value)
    decimals = 2 if amount != amount.quantize(Decimal("1")) else 0
    rendered = f"{abs(amount):,.{decimals}f}".replace(",", "\u00a0").replace(".", ",")
    return ("−" if amount < 0 else "") + rendered


def _statement_section(statement: dict[str, Any], heading: str) -> Section:
    rows = [
        [
            ln["label"],
            _exact_kr(ln["amount"]),
            _exact_kr(ln["compare"]),
            _exact_kr(ln["diff"]),
        ]
        for ln in statement["lines"]
        if Decimal(ln["amount"]) != 0 or (ln["compare"] is not None and Decimal(ln["compare"]) != 0)
    ]
    note = "Belopp i kronor från bokföringen. Förändring = aktuell period minus jämförelseperiod."
    if not statement.get("complete", True):
        note += " Underlaget är ofullständigt; tolka jämförelsen med försiktighet."
    return Section(
        heading,
        table=Table(
            ["Post", statement["period"], statement["compare"] or "Jämförelse", "Förändring, kr"],
            rows,
            numeric_cols={1, 2, 3},
        ),
        note=note,
    )


def _metric_section(overview: dict[str, Any], heading: str) -> Section:
    monthly = overview["sections"]["month"]
    return Section(
        heading,
        table=Table(
            ["Nyckeltal", monthly["period"]["label"], monthly["compare"]["label"], "Förändring"],
            _metric_rows(monthly),
            numeric_cols={1, 2, 3},
        ),
        note="Nyckeltal kan ha olika enheter; förändringen visas i respektive nyckeltals enhet.",
    )


def _headline_section(overview: dict[str, Any]) -> Section | None:
    monthly = overview["sections"]["month"]
    selected = {
        code: monthly["metrics"][code]
        for code in ("net_sales", "operating_result", "gross_margin", "cash")
        if code in monthly["metrics"]
    }
    if not selected:
        return None
    return Section(
        "Periodens huvudtal",
        table=Table(
            ["Nyckeltal", monthly["period"]["label"], monthly["compare"]["label"], "Förändring"],
            _metric_rows({"metrics": selected}),
            numeric_cols={1, 2, 3},
        ),
        note="Samtliga nyckeltal finns i bilaga 1.",
    )


def _balance_warnings(statements: dict[str, Any]) -> list[str]:
    balance = statements.get("balance")
    if not balance:
        return []
    totals = {line["code"]: line for line in balance["lines"]}
    assets = totals.get("total_assets")
    liabilities = totals.get("total_equity_liabilities")
    if not assets or not liabilities:
        return []
    warnings = []
    for field, label in (("amount", balance["period"]), ("compare", balance.get("compare"))):
        left, right = assets.get(field), liabilities.get(field)
        if left is None or right is None:
            continue
        difference = Decimal(left) - Decimal(right)
        if difference:
            balance_date = str(label).removeprefix("Per ")
            warnings.append(
                f"Balansräkningen per {balance_date} går inte ihop: tillgångar och eget kapital/skulder "
                f"skiljer sig med {_exact_kr(abs(difference))} kr. Stäm av bokföringen före användning."
            )
    return warnings


def _claims_text(claims: list[dict[str, Any]]) -> list[str]:
    prefix = {"HYPOTHESIS": "Möjlig förklaring: ", "QUESTION": ""}
    texts = []
    for claim in claims:
        body = claim.get("rendered", claim.get("text", ""))
        lead = prefix.get(claim["type"], "")
        texts.append(body if body.casefold().startswith(lead.casefold()) else lead + body)
    return texts


def client_report(
    overview: dict[str, Any],
    statements: dict[str, Any],
    meeting: dict[str, Any] | None,
    firm_name: str,
    *,
    approved_case_questions: list[dict[str, Any]] | None = None,
    analysis: ReportAnalysis | None = None,
    draft: bool = False,
) -> Document:
    """Kundrapport med godkänd analys först och bokföringsunderlag i bilagor."""
    company = overview["company"]["name"]
    period = overview["period"]["label"]
    compare = overview["sections"]["month"]["compare"]["label"]
    sections: list[Section] = []
    balance_warnings = _balance_warnings(statements)
    if balance_warnings:
        sections.append(Section("Avstämning av underlag", paragraphs=balance_warnings, alert=True))
    summary = _claims_text(meeting.get("summary", [])) if meeting else []
    if analysis and analysis.summary:
        sections.append(Section("Perioden i korthet", paragraphs=[analysis.summary]))
        sections.append(
            Section(
                "Vad som ligger bakom förändringen",
                paragraphs=list(analysis.drivers),
                note=analysis.limitation,
            )
        )
    else:
        sections.append(
            Section(
                "Analysens underlag",
                paragraphs=[
                    analysis.limitation
                    if analysis and analysis.limitation
                    else "En avstämd analys av periodens viktigaste förändringar saknas. Se resultat- och balansräkningen i bilagorna."
                ],
            )
        )
    if summary:
        sections.append(
            Section(
                "AI-utkast för konsultens granskning" if draft else "Konsultens bedömning",
                paragraphs=summary,
                note=(
                    "Texten är inte godkänd. Redovisningskonsulten måste granska den innan rapporten delas med kunden."
                    if draft
                    else "AI-assisterat mötesunderlag som godkänts av redovisningskonsulten."
                ),
            )
        )
    if meeting and (meeting.get("questions") or approved_case_questions):
        # Direktanrop (t.ex. CLI) får aldrig ärendefrågor utan ett separat godkännande.
        bullets = _claims_text(meeting.get("questions", [])) + [
            str(q["question"]) for q in approved_case_questions or []
        ]
        sections.append(Section("Frågor och nästa steg", bullets=bullets))
    headline = _headline_section(overview)
    if headline:
        sections.append(headline)
    metric_appendix = _metric_section(overview, "Bilaga 1 · Nyckeltal")
    sections.extend([metric_appendix, _statement_section(statements["income"], "Bilaga 2 · Resultaträkning")])
    if "balance" in statements:
        balance_appendix = _statement_section(statements["balance"], "Bilaga 3 · Balansräkning")
        balance_appendix.page_break_before = True
        sections.append(balance_appendix)
    sections.append(
        Section(
            "Om rapporten",
            paragraphs=[
                f"Rapportperiod: {period}. Jämförelseperiod: {compare}. "
                "Siffrorna kommer från importerad bokföring vid rapportens framtagande. "
                "Förklaringar om bakomliggande affärshändelser bygger på tillgängligt underlag "
                "och kan behöva bekräftas med fakturor, avtal eller kunden."
            ],
        )
    )
    return Document(
        title=f"{company} – månadsrapport",
        subtitle=f"{period} · {firm_name}",
        sections=sections,
        footer=f"{firm_name} · Framtagen {datetime.now():%Y-%m-%d}",
        classification="UTKAST – FÅR INTE LÄMNAS TILL KUND" if draft else "KUNDRAPPORT",
    )


def internal_report(
    overview: dict[str, Any],
    findings: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    commentary: dict[str, Any] | None,
    maturity: dict[str, Any],
    *,
    commentary_stale: bool = False,
    commentary_metadata: dict[str, Any] | None = None,
    statements: dict[str, Any] | None = None,
    analysis: ReportAnalysis | None = None,
) -> Document:
    company = overview["company"]["name"]
    findings = [finding for finding in findings if finding.get("visibility") != "RESTRICTED_AML"]
    cases = [case for case in cases if case.get("visibility") != "RESTRICTED_AML"]
    sections: list[Section] = []
    balance_warnings = _balance_warnings(statements) if statements else []
    if balance_warnings:
        sections.append(Section("Avstämning av underlag", paragraphs=balance_warnings, alert=True))
    if commentary_stale:
        sections.append(
            Section(
                "Analysens status",
                paragraphs=[
                    "Den sparade periodkommentaren utelämnades eftersom bokföringsdata, jämförelseperiod eller analysversion har ändrats. Skapa och granska en ny kommentar."
                ],
            )
        )
    pair_label = (
        f"Jämförelse: {commentary_metadata.get('period', '–')} mot {commentary_metadata.get('compare_period', '–')}."
        if commentary_metadata
        else f"Jämförelse: {overview['sections']['month']['period']['label']} mot "
        f"{overview['sections']['month']['compare']['label']}."
    )
    claims = _claims_text(commentary.get("claims", [])) if commentary else []
    if analysis and analysis.summary:
        sections.append(Section("Perioden i korthet", paragraphs=[pair_label, analysis.summary]))
        sections.append(
            Section("Vad som ligger bakom förändringen", paragraphs=list(analysis.drivers), note=analysis.limitation)
        )
    else:
        sections.append(
            Section(
                "Analysens underlag",
                paragraphs=[
                    pair_label,
                    analysis.limitation
                    if analysis and analysis.limitation
                    else "En avstämd analys av periodens viktigaste förändringar saknas.",
                ],
            )
        )
    if claims:
        sections.append(
            Section(
                "AI-stödd kommentar för intern granskning",
                paragraphs=claims,
                note="Hypoteser och affärsorsaker kräver fortsatt kontroll.",
            )
        )
    active_cases = [c for c in cases if c.get("status") != "CLOSED"]
    count_label = "1 öppet ärende" if len(active_cases) == 1 else f"{len(active_cases)} öppna ärenden"
    sections.append(
        Section(
            "Prioriterad uppföljning",
            bullets=[
                f"{SEVERITY_SV.get(c['severity'], c['severity'])}: {c['title']}. {c.get('suggested_action') or 'Bedöm och dokumentera åtgärd.'}"
                for c in active_cases[:5]
            ]
            or ["Inga öppna ärenden i underlaget."],
            note=f"{count_label} totalt; fullständig lista i bilaga 2.",
        )
    )
    status_code = str(maturity.get("status") or "UNKNOWN")
    method_code = str(maturity.get("accounting_method") or "unknown")
    sections.append(
        Section(
            "Underlag och periodmognad",
            bullets=maturity.get("notes", []) or ["Inga anmärkningar."],
            paragraphs=[
                f"Status: {MATURITY_SV.get(status_code, 'Okänd')}. "
                f"Bokföringsmetod: {METHOD_SV.get(method_code, 'okänd')}."
            ],
        )
    )
    metric_appendix = _metric_section(overview, "Bilaga 1 · Nyckeltal")
    sections.append(metric_appendix)
    sections.append(
        Section(
            "Bilaga 2 · Ärenden",
            table=Table(
                ["Allvar", "Ärende", "Status", "Föreslagen åtgärd"],
                [
                    [
                        SEVERITY_SV.get(c["severity"], c["severity"]),
                        c["title"],
                        STATUS_SV.get(c["status"], c["status"]),
                        c["suggested_action"],
                    ]
                    for c in cases
                ],
            ),
            paragraphs=[] if cases else ["Inga ärenden i underlaget."],
        )
    )
    sections.append(
        Section(
            "Bilaga 3 · Fynd och verifikationer",
            table=Table(
                ["Regel", "Allvar", "Fynd", "Status", "Verifikationer"],
                [
                    [
                        f["rule_code"],
                        SEVERITY_SV.get(f["severity"], f["severity"]),
                        f["title"],
                        STATUS_SV.get(f["status"], f["status"]),
                        ", ".join(f["vouchers"][:5])
                        + (f" (+{len(f['vouchers']) - 5} till i appen)" if len(f["vouchers"]) > 5 else ""),
                    ]
                    for f in findings
                ],
            ),
            paragraphs=[] if findings else ["Inga fynd i underlaget."],
        )
    )
    if statements:
        sections.append(_statement_section(statements["income"], "Bilaga 4 · Resultaträkning"))
        balance_appendix = _statement_section(statements["balance"], "Bilaga 5 · Balansräkning")
        balance_appendix.page_break_before = True
        sections.append(balance_appendix)
    return Document(
        title=f"{company} – intern granskningsrapport",
        subtitle=overview["period"]["label"],
        sections=sections,
        classification="INTERN – FÅR INTE LÄMNAS TILL KUND",
        footer=f"Framtagen {datetime.now():%Y-%m-%d} · Endast för intern granskning",
    )


def reko_documentation(
    company: dict[str, Any],
    period: str,
    review: dict[str, Any],
    findings: list[dict[str, Any]],
    audit: list[dict[str, Any]],
    rule_catalog: dict[str, Any],
) -> Document:
    """Granskningsdokumentation per kund och period (stöd för Reko 140)."""
    snap = review.get("snapshot") or {}
    sections = [
        Section(
            "Uppdrag och period",
            table=Table(
                ["Uppgift", "Värde"],
                [
                    ["Kund", company["name"]],
                    ["Organisationsnummer", company.get("org_number") or ""],
                    ["Period", period],
                    ["Status", review.get("status", "")],
                    ["Godkänd av", review.get("approved_by") or "–"],
                    ["Godkänd", review.get("approved_at") or "–"],
                    ["Beräkningsversion", snap.get("calc_version", "–")],
                    ["Mappningsversion", snap.get("mapping_version", "–")],
                ],
            ),
        ),
        Section(
            "Utförda kontroller",
            table=Table(
                ["Regel", "Version", "Lagstöd"],
                [[r["title"], r["version"], r["legal_basis"]] for r in rule_catalog.values() if r["category"] != "aml"],
            ),
        ),
        Section(
            "Fynd och bedömningar",
            table=Table(
                ["Fynd", "Allvar", "Beslut", "Motivering", "Av", "Datum"],
                [
                    [
                        f["title"],
                        SEVERITY_SV.get(f["severity"], ""),
                        STATUS_SV.get(f["status"], f["status"]),
                        f.get("resolution_note") or "",
                        f.get("resolved_by") or "",
                        (f.get("resolved_at") or "")[:10],
                    ]
                    for f in findings
                    if f["visibility"] != "RESTRICTED_AML"
                ],
            ),
        ),
        Section(
            "Händelselogg",
            table=Table(
                ["Tid", "Användare", "Händelse"],
                [[e["at"][:16].replace("T", " "), e.get("user_email") or "", e["action"]] for e in audit],
            ),
        ),
    ]
    if review.get("changes"):
        sections.append(
            Section(
                "Ändringar efter godkännande",
                bullets=[f"{c['voucher']} {c['kind']} {c['date']} {c['text']}" for c in review["changes"]],
            )
        )
    return Document(
        title=f"Granskningsdokumentation – {company['name']}",
        subtitle=f"Period {period}",
        sections=sections,
        classification="INTERN DOKUMENTATION (Reko 140)",
    )


def statements_tables(statements: dict[str, Any]) -> list[tuple[str, Table]]:
    out = []
    for key, name in (("income", "Resultaträkning"), ("balance", "Balansräkning")):
        st = statements[key]
        rows = []
        for ln in st["lines"]:
            rows.append([ln["label"], "", ln["amount"], ln["compare"] or ""])
            for a in ln["accounts"]:
                rows.append([f"   {a['account']} {a['name']}", a["account"], a["amount"], a["compare"] or ""])
        out.append((name, Table(["Rad", "Konto", st["period"], st["compare"] or ""], rows, numeric_cols={2, 3})))
    return out
