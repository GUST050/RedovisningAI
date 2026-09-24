"""Rapporter: kundrapport (bara CLIENT_SAFE), internrapport och Reko-dokumentation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from redovisningai.facts.model import format_percent, format_sek
from redovisningai.reports.document import Document, Section, Table

STATUS_SV = {
    "NEW": "Ny",
    "IN_PROGRESS": "Under utredning",
    "ASK_CLIENT": "Fråga till kund",
    "RESOLVED": "Åtgärdat",
    "ACCEPTED_OK": "Bedömt OK",
    "SUPPRESSED": "Undertryckt",
    "AUTO_CLOSED": "Rättat i bokföringen",
}
SEVERITY_SV = {"HIGH": "Hög", "MEDIUM": "Medel", "LOW": "Låg"}


def _metric_rows(section: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for m in section["metrics"].values():
        f = m["fact"]
        prev = m.get("previous", {})
        ch = m.get("change", {})
        rows.append([f["label"], f["display"], prev.get("display", ""), ch.get("display", "")])
    return rows


def _claims_text(claims: list[dict[str, Any]]) -> list[str]:
    prefix = {"HYPOTHESIS": "Möjlig förklaring: ", "QUESTION": ""}
    return [prefix.get(c["type"], "") + c.get("rendered", c.get("text", "")) for c in claims]


def client_report(
    overview: dict[str, Any], statements: dict[str, Any], meeting: dict[str, Any] | None, firm_name: str
) -> Document:
    """Kundrapport. Byggs bara av nyckeltal, resultaträkning och godkända AI-texter (CLIENT_SAFE)."""
    company = overview["company"]["name"]
    period = overview["period"]["label"]
    sections: list[Section] = []
    if meeting:
        sections.append(
            Section(
                "Sammanfattning",
                paragraphs=_claims_text(meeting.get("summary", [])),
                note="Texten är AI-assisterad och granskad av er redovisningskonsult.",
            )
        )
    ytd = overview["sections"].get("ytd") or overview["sections"]["month"]
    sections.append(
        Section(
            f"Nyckeltal – {ytd['period']['label']}",
            table=Table(
                ["Nyckeltal", "Utfall", f"Jämförelse ({ytd['compare']['label']})", "Förändring"],
                _metric_rows(ytd),
                numeric_cols={1, 2, 3},
            ),
        )
    )
    inc = statements["income"]
    rows = [
        [
            ln["label"],
            format_sek(Decimal(ln["amount"])),
            "" if ln["compare"] is None else format_sek(Decimal(ln["compare"])),
            "" if ln["diff_pct"] is None else format_percent(Decimal(ln["diff_pct"]), signed=True),
        ]
        for ln in inc["lines"]
        if Decimal(ln["amount"]) != 0 or (ln["compare"] and Decimal(ln["compare"]) != 0)
    ]
    sections.append(
        Section(
            "Resultaträkning i sammandrag",
            table=Table(["", inc["period"], inc["compare"] or "", "Förändring"], rows, numeric_cols={1, 2, 3}),
        )
    )
    if meeting and (meeting.get("questions") or meeting.get("case_questions")):
        bullets = _claims_text(meeting.get("questions", []))
        sections.append(Section("Att diskutera på mötet", bullets=bullets))
    return Document(
        title=f"{company} – månadsrapport",
        subtitle=f"{period} · {firm_name}",
        sections=sections,
        footer=f"Framtagen {datetime.now():%Y-%m-%d}. Siffrorna bygger på bokföringen vid framtagandet.",
        classification="KUNDRAPPORT",
    )


def internal_report(
    overview: dict[str, Any],
    findings: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    commentary: dict[str, Any] | None,
    maturity: dict[str, Any],
) -> Document:
    company = overview["company"]["name"]
    sections = []
    if commentary:
        sections.append(Section("Periodkommentar (intern)", paragraphs=_claims_text(commentary.get("claims", []))))
    sections.append(
        Section(
            "Periodmognad",
            bullets=maturity.get("notes", []) or ["Inga anmärkningar."],
            paragraphs=[f"Status: {maturity.get('status')}, bokföringsmetod: {maturity.get('accounting_method')}"],
        )
    )
    sections.append(
        Section(
            "Ärenden",
            table=Table(
                ["Allvar", "Ärende", "Status", "Föreslagen åtgärd"],
                [
                    [SEVERITY_SV.get(c["severity"], c["severity"]), c["title"], c["status"], c["suggested_action"]]
                    for c in cases
                ],
            ),
        )
    )
    sections.append(
        Section(
            "Fynd",
            table=Table(
                ["Regel", "Allvar", "Fynd", "Status", "Verifikationer"],
                [
                    [
                        f["rule_code"],
                        SEVERITY_SV.get(f["severity"], f["severity"]),
                        f["title"],
                        STATUS_SV.get(f["status"], f["status"]),
                        ", ".join(f["vouchers"][:5]),
                    ]
                    for f in findings
                ],
            ),
        )
    )
    return Document(
        title=f"{company} – intern granskningsrapport",
        subtitle=overview["period"]["label"],
        sections=sections,
        classification="INTERN – FÅR INTE LÄMNAS TILL KUND",
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
