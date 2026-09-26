"""Kommandorad.

redovisningai analyze FIL.se [FIL2.se ...] --out rapport/    # Fas 0: SIE/CSV/Excel → Excel + PDF (ingen databas)
redovisningai convert FIL.se [FIL.csv ...] --out bokforing.json  # valfri källa → standardformat (JSON)
redovisningai compare FIL.se [...] --period 2026-09 --out rapport/  # jämförelserapport med de viktigaste skillnaderna
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
import os
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

    ledger = _load_files(args.files, getattr(args, "fiscal_year_start", None))
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


def _load_files(files: list[str], fiscal_year_start: int | None = None):  # type: ignore[no-untyped-def]
    """Läs SIE-, CSV-, Excel- eller standardformatsfiler till en gemensam bokföring och visa avvikelser."""
    from redovisningai.standard.loader import SourceFormatError, load_ledger

    try:
        ledger, issues = load_ledger(
            [(Path(f).read_bytes(), Path(f).name) for f in files], fiscal_year_start_month=fiscal_year_start
        )
    except SourceFormatError as exc:
        raise SystemExit(f"Kunde inte läsa filen: {exc}") from exc
    for name, issue in issues:
        if issue.severity != "info":
            print(f"  {name}:{issue.line or '-'} {issue.code}: {issue.message}", file=sys.stderr)
    return ledger


def cmd_convert(args: argparse.Namespace) -> int:
    """Valfri källa (SIE, CSV, Excel, standardformat) → RedovisningAI:s standardformat."""
    from redovisningai.standard.format import dumps

    ledger = _load_files(args.files, args.fiscal_year_start)
    text = dumps(ledger, source={"program": ledger.program, "files": [Path(f).name for f in args.files]})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    vouchers = sum(len(y.vouchers) for y in ledger.years)
    years = ", ".join(
        f"{y.fiscal_year.label}{'' if y.has_vouchers else ' (saldon)'}{' – IB saknas' if y.opening_status == 'missing' else ''}"
        for y in ledger.years
    )
    print(f"{ledger.company_name}: {vouchers} verifikationer, räkenskapsår {years}.")
    print(f"Skrev {out.resolve()}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Jämförelserapport för två perioder: nyckeltal, viktigaste skillnader och vald utveckling över tid."""
    from redovisningai.accounting.comparisons import comparison_pair
    from redovisningai.accounting.metrics import REGISTRY
    from redovisningai.analytics.differences import collect_differences, fmt_change, fmt_value
    from redovisningai.reports.comparison_report import (
        ReportSelectionError,
        SelectedItem,
        build_comparison_report,
    )
    from redovisningai.reports.document import to_docx, to_pdf, to_xlsx
    from redovisningai.review.analysis import PAYROLL, CompanyAnalysis, CompanyContext

    ledger = _load_files(args.files, args.fiscal_year_start)
    analysis = CompanyAnalysis(ledger, CompanyContext("local", "local", ledger.company_name))
    try:
        current = analysis.period(args.period)
        if args.compare:
            pair = comparison_pair(current, "custom", ledger, analysis.index, analysis.period(args.compare))
        else:
            pair = comparison_pair(current, args.mode, ledger, analysis.index)
    except ValueError as exc:
        raise SystemExit(f"Ogiltig period: {exc}") from exc
    client = args.audience == "client"
    differences = collect_differences(
        analysis.index,
        pair,
        mapping=analysis.ctx.statement_mapping,
        categories=analysis.ctx.category_mapping,
        rates=analysis.rates,
        hidden_accounts=PAYROLL if client else None,
        include_findings=not client,
        recommend=args.top,
    )
    print(f"{ledger.company_name}: {pair.current.label} jämfört med {pair.previous.label}")
    for warning in (*pair.warnings, *pair.notices):
        print(f"  OBS: {warning}")
    print()
    for code, explanation in differences.explanations.items():
        unit = REGISTRY[code].unit.value
        print(
            f"  {REGISTRY[code].name:48} {fmt_value(explanation.current, unit):>14} "
            f"{fmt_value(explanation.previous, unit):>14} {fmt_change(explanation.change, unit):>24}"
        )
    selection = (
        [SelectedItem(item_id.strip()) for item_id in args.items.split(",") if item_id.strip()]
        if args.items
        else [SelectedItem(item.id) for item in differences.recommended()]
    )
    for spec in args.structure or []:
        code, _, rest = spec.partition(":")
        series, _, count = rest.partition(":")
        selection.append(SelectedItem(f"structure:{code}", None, series or "months", int(count) if count else None))
    print("\nViktigaste skillnaderna (föreslagna):")
    for item in differences.recommended():
        print(f"  [{item.score:3}] {item.summary}")
    try:
        document, sheets = build_comparison_report(
            analysis.index,
            pair,
            differences,
            selection,
            company_name=ledger.company_name,
            org_number=ledger.org_number,
            audience=args.audience,
            mapping=analysis.ctx.statement_mapping,
            rates=analysis.rates,
            firm_name=args.firm,
            title=args.title,
        )
    except ReportSelectionError as exc:
        raise SystemExit(f"Rapporten kunde inte skapas: {exc}") from exc
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    kind = "kundrapport" if client else "intern"
    stem = f"{ledger.company_name} {pair.current.spec} jämförelse {kind}".replace("/", "-").replace(":", "_")
    data = to_pdf(document) if args.format == "pdf" else to_docx(document) if args.format == "docx" else to_xlsx(sheets)
    path = out / f"{stem}.{args.format}"
    path.write_bytes(data)
    print(f"\nSkrev {path.resolve()} ({len(selection)} poster)")
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

    # Källkodsträdet (utveckling) eller arbetskatalogen (container där paketet är installerat).
    root = Path(__file__).resolve().parents[2]
    if not (root / "alembic.ini").exists():
        root = Path(os.environ.get("RAI_APP_ROOT", Path.cwd()))
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    return 0


def cmd_worker_schema(args: argparse.Namespace) -> int:
    """Skapa Procrastinates jobbkö (som ägarrollen) och ge applikationsrollen rätt att använda den.

    Kön saknar radnivåskydd; därför innehåller jobbens argument bara id:n, aldrig kunddata.
    """
    import procrastinate
    import psycopg

    from redovisningai.config import get_settings

    owner = get_settings().database_url_owner.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(owner, autocommit=True) as conn:
        exists = conn.execute("select to_regclass('public.procrastinate_jobs')").fetchone()[0]
        if exists is None:
            app = procrastinate.App(connector=procrastinate.SyncPsycopgConnector(conninfo=owner))
            with app.open():
                app.schema_manager.apply_schema()
        conn.execute(
            """
            do $$
            declare r record;
            begin
              for r in select tablename from pg_tables where schemaname = 'public' and tablename like 'procrastinate%' loop
                execute format('grant select, insert, update, delete on public.%I to redovisningai_app', r.tablename);
              end loop;
              for r in select sequence_name from information_schema.sequences
                       where sequence_schema = 'public' and sequence_name like 'procrastinate%' loop
                execute format('grant usage, select, update on sequence public.%I to redovisningai_app', r.sequence_name);
              end loop;
              for r in select p.oid::regprocedure as f from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                       where n.nspname = 'public' and p.proname like 'procrastinate%' loop
                execute format('grant execute on function %s to redovisningai_app', r.f);
              end loop;
            end $$;
            """
        )
    print("Jobbkön är klar." if exists is None else "Jobbkön fanns redan; behörigheter uppdaterade.")
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
    a.add_argument("--fiscal-year-start", type=int, help="Räkenskapsårets startmånad för CSV/Excel (standard: 1)")
    a.set_defaults(fn=cmd_analyze)
    cv = sub.add_parser("convert", help="Läs SIE/CSV/Excel/standardformat och skriv standardformatet (JSON)")
    cv.add_argument("files", nargs="+")
    cv.add_argument("--out", default="bokforing.json")
    cv.add_argument("--fiscal-year-start", type=int, help="Räkenskapsårets startmånad för CSV/Excel (standard: 1)")
    cv.set_defaults(fn=cmd_convert)
    cp = sub.add_parser("compare", help="Jämför två perioder och skriv en rapport med de viktigaste skillnaderna")
    cp.add_argument("files", nargs="+")
    cp.add_argument(
        "--period", help="t.ex. 2026-09, 2026-Q3, YTD:2026-09, FY:2026-01, R12:2026-09 (standard: senaste månaden)"
    )
    cp.add_argument(
        "--mode",
        default="yoy",
        choices=["yoy", "previous"],
        help="yoy = samma period i fjol, previous = föregående period",
    )
    cp.add_argument("--compare", help="Fritt vald jämförelseperiod av samma typ, t.ex. 2026-03")
    cp.add_argument("--audience", default="internal", choices=["internal", "client"])
    cp.add_argument("--format", default="pdf", choices=["pdf", "docx", "xlsx"])
    cp.add_argument("--top", type=int, default=6, help="Antal föreslagna skillnader (standard 6)")
    cp.add_argument("--items", help="Egna poster i stället för förslagen, t.ex. metric:net_sales,line:income:personnel")
    cp.add_argument(
        "--structure",
        action="append",
        help="Utveckling över tid, t.ex. operating_margin:months:12 eller equity_ratio:fiscal_years:3 (kan upprepas)",
    )
    cp.add_argument("--title")
    cp.add_argument("--firm", default="Redovisningsbyrån")
    cp.add_argument("--out", default="rapport")
    cp.add_argument("--fiscal-year-start", type=int, help="Räkenskapsårets startmånad för CSV/Excel (standard: 1)")
    cp.set_defaults(fn=cmd_compare)
    d = sub.add_parser("demo-sie", help="Skriv SIE-filer för demobolagen")
    d.add_argument("--out", default="demo-sie")
    d.add_argument("--as-of", default="2026-10-12")
    d.set_defaults(fn=cmd_demo_sie)
    sub.add_parser("migrate", help="Kör databasmigrationer").set_defaults(fn=cmd_migrate)
    sub.add_parser("worker-schema", help="Skapa jobbkön för bakgrundsarbetaren").set_defaults(fn=cmd_worker_schema)
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
