"""Kommandorad.

redovisningai analyze FIL.se [FIL2.se ...] --out rapport/    # Fas 0: SIE → Excel + PDF (ingen databas)
redovisningai demo-sie --out demo/                           # skriv demobolagens SIE-filer
redovisningai migrate                                        # kör databasmigrationer (ägarroll)
redovisningai create-org "Byrån AB" --admin-email a@b.se --admin-name "Anna"
redovisningai seed-demo                                      # demobyrå med fyra kunder
redovisningai eval --provider fake|bedrock|vertex|anthropic  # AI-evals
redovisningai review-all --org <uuid>                        # granska alla kunder i en byrå
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from pathlib import Path


def cmd_analyze(args: argparse.Namespace) -> int:
    from redovisningai.accounting.periods import same_period_previous_year
    from redovisningai.ai.service import AIService
    from redovisningai.reports.builders import client_report, internal_report, statements_tables
    from redovisningai.reports.document import Table, to_pdf, to_xlsx
    from redovisningai.review.analysis import CompanyAnalysis, CompanyContext
    from redovisningai.rules.engine import CompanySettings
    from redovisningai.sie.convert import ledger_from_documents
    from redovisningai.sie.parser import parse_sie

    docs = []
    for f in args.files:
        doc = parse_sie(Path(f).read_bytes())
        for issue in doc.issues:
            if issue.severity != "info":
                print(f"  {Path(f).name}:{issue.line or '-'} {issue.code}: {issue.message}", file=sys.stderr)
        docs.append((doc, Path(f).name))
    ledger = ledger_from_documents(docs)
    ctx = CompanyContext(
        "local",
        "local",
        ledger.company_name,
        CompanySettings(vat_period=args.vat_period, food_retail=args.food_retail, legal_form=args.legal_form),
    )
    analysis = CompanyAnalysis(ledger, ctx)
    period = analysis.period(args.period)
    review = analysis.review(period)
    ai = AIService(_provider(args.ai)) if args.ai else AIService(None)
    commentary = ai.run(
        "A3",
        analysis.commentary_package(review),
        review.store,
        org_id="local",
        allowed_identifiers=analysis.allowed_identifiers(),
    )
    meeting = ai.run(
        "A4",
        analysis.client_package(review),
        review.store,
        org_id="local",
        allowed_identifiers=analysis.allowed_identifiers(),
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{ledger.company_name} {period.spec}".replace("/", "-")
    compare = same_period_previous_year(period, ledger)
    stmts = analysis.statements(period, compare)
    findings = [r.to_dict() for r in review.records if r.visibility.value != "RESTRICTED_AML" or args.include_aml]
    cases = [c.to_dict() for c in review.cases if c.visibility.value != "RESTRICTED_AML" or args.include_aml]
    sheets = [
        *statements_tables(stmts),
        (
            "Ärenden",
            Table(
                ["Allvar", "Ärende", "Trolig orsak", "Åtgärd", "Verifikationer"],
                [
                    [c["severity"], c["title"], c["root_cause"], c["suggested_action"], ", ".join(c["vouchers"])]
                    for c in cases
                ],
            ),
        ),
        (
            "Fynd",
            Table(
                ["Regel", "Allvar", "Fynd", "Beskrivning", "Verifikationer", "Belopp", "Lagstöd"],
                [
                    [
                        f["rule_code"],
                        f["severity"],
                        f["title"],
                        _render(f),
                        ", ".join(f["vouchers"]),
                        f["amount"],
                        f["legal_basis"],
                    ]
                    for f in findings
                ],
                numeric_cols={5},
            ),
        ),
    ]
    (out / f"{stem} granskning.xlsx").write_bytes(to_xlsx(sheets))
    overview = analysis.overview(period)
    (out / f"{stem} intern.pdf").write_bytes(
        to_pdf(internal_report(overview, findings, cases, commentary.data, review.maturity.to_dict()))
    )
    (out / f"{stem} kundrapport (utkast).pdf").write_bytes(
        to_pdf(client_report(overview, stmts, meeting.data, args.firm))
    )
    print(f"{ledger.company_name}: {len(cases)} ärenden, {len(findings)} fynd för {period.label}.")
    print(f"Periodmognad: {review.maturity.status.value}; {'; '.join(review.maturity.notes) or 'inga anmärkningar'}")
    for c in cases[:10]:
        print(f"  [{c['severity']}] {c['title']}")
    print(f"Skrev rapporter till {out.resolve()}  (AI: {commentary.source})")
    return 0


def _render(f: dict[str, object]) -> str:
    import re

    facts = {x["id"]: x["display"] for x in f.get("facts", [])}  # type: ignore[union-attr, index]
    return re.sub(r"\{f:([^}\s]+)\}", lambda m: str(facts.get(m.group(1), "")), str(f["description"]))


def _provider(name: str):  # type: ignore[no-untyped-def]
    from redovisningai.ai.factory import _provider as make
    from redovisningai.config import get_settings

    s = get_settings()
    return make(name, s.ai_region, s)


def cmd_demo_sie(args: argparse.Namespace) -> int:
    from redovisningai.devdata.generator import DEMO_PROFILES, generate
    from redovisningai.sie.writer import write_sie4

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    as_of = date.fromisoformat(args.as_of)
    for p in DEMO_PROFILES:
        g = generate(p, as_of)
        for y in g.ledger.years:
            path = out / f"{p.name.replace(' ', '_').replace('&', 'och')}_{y.fiscal_year.label}.se"
            path.write_bytes(write_sie4(g.ledger, y))
            print(path)
        if g.planted:
            print(f"  planterade fel i {p.name}: {', '.join(sorted(g.planted))}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    return 0


def cmd_create_org(args: argparse.Namespace) -> int:
    from redovisningai.db.bootstrap import create_organization

    res = create_organization(
        args.name, args.admin_email, args.admin_name, admin_subject=args.admin_subject, org_number=args.org_number
    )
    print(json.dumps({"org_id": str(res.org_id), "admin_user_id": str(res.admin_user_id)}))
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from redovisningai.db.bootstrap import seed_demo

    res = seed_demo(date.fromisoformat(args.as_of))
    print(json.dumps({"org_id": str(res.org_id), "login": "anna@demobyran.se (admin), lisa@demobyran.se (läsare)"}))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from redovisningai.ai.evals import run_evals
    from redovisningai.ai.service import AIService

    results = run_evals(AIService(_provider(args.provider)))
    for r in results:
        print(f"{'OK ' if r.passed else 'FEL'} {r.task} {r.company:15} {r.source:5} {r.metrics} {r.failures or ''}")
    return 0 if all(r.passed for r in results) else 1


def cmd_review_all(args: argparse.Namespace) -> int:
    from redovisningai.ai.factory import build_ai_service
    from redovisningai.jobs.pipeline import review_all

    org = uuid.UUID(args.org)
    print(json.dumps(review_all(org, build_ai_service(org)), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="redovisningai", description="RedovisningAI – granskning för redovisningsbyråer")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="Analysera SIE-filer och skriv rapporter (Fas 0)")
    a.add_argument("files", nargs="+")
    a.add_argument("--period", help="t.ex. 2026-09 (standard: senaste månaden)")
    a.add_argument("--out", default="rapport")
    a.add_argument("--vat-period", default="quarter", choices=["month", "quarter", "year"])
    a.add_argument("--legal-form", default="AB")
    a.add_argument("--food-retail", action="store_true")
    a.add_argument("--firm", default="Redovisningsbyrån")
    a.add_argument("--ai", choices=["fake", "bedrock", "vertex", "anthropic"])
    a.add_argument("--include-aml", action="store_true", help="Ta med PTL-signaler (bara för PTL-ansvarig)")
    a.set_defaults(fn=cmd_analyze)
    d = sub.add_parser("demo-sie", help="Skriv SIE-filer för demobolagen")
    d.add_argument("--out", default="demo-sie")
    d.add_argument("--as-of", default="2026-10-12")
    d.set_defaults(fn=cmd_demo_sie)
    sub.add_parser("migrate", help="Kör databasmigrationer").set_defaults(fn=cmd_migrate)
    o = sub.add_parser("create-org", help="Skapa byrå och första admin")
    o.add_argument("name")
    o.add_argument("--admin-email", required=True)
    o.add_argument("--admin-name", required=True)
    o.add_argument("--admin-subject")
    o.add_argument("--org-number")
    o.set_defaults(fn=cmd_create_org)
    sd = sub.add_parser("seed-demo", help="Skapa demobyrå med kunder")
    sd.add_argument("--as-of", default="2026-10-12")
    sd.set_defaults(fn=cmd_seed_demo)
    e = sub.add_parser("eval", help="Kör AI-evals")
    e.add_argument("--provider", default="fake", choices=["fake", "bedrock", "vertex", "anthropic"])
    e.set_defaults(fn=cmd_eval)
    r = sub.add_parser("review-all", help="Granska alla kunder i en byrå")
    r.add_argument("--org", required=True)
    r.set_defaults(fn=cmd_review_all)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
