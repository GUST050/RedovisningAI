"""Import av alla källformat genom API:t och databasen (CSV, Excel, standardformat, zip)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from redovisningai.api.app import create_app
from redovisningai.db import models as m
from redovisningai.db.bootstrap import add_user, create_company, create_organization
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.jobs.pipeline import import_sie
from redovisningai.sie.writer import write_sie4
from redovisningai.standard import loads

pytestmark = pytest.mark.usefixtures("database")

HEADER = "Testbolaget Format AB, org.nr 559900-0001\nVernr;Datum;Konto;Benämning;Text;Debet;Kredit\n"
IB_ROWS = (
    "IB;2026-01-01;1930;Företagskonto;Ingående balans;52 000,00;\n"
    "IB;2026-01-01;2081;Aktiekapital;Ingående balans;;25 000,00\n"
    "IB;2026-01-01;2099;Årets resultat;Ingående balans;;27 000,00\n"
)
JAN = (
    "A1;2026-01-05;1510;Kundfordringar;Kundfaktura 1;12 500,00;\n"
    "A1;2026-01-05;3001;Försäljning;Kundfaktura 1;;10 000,00\n"
    "A1;2026-01-05;2611;Utgående moms;Kundfaktura 1;;2 500,00\n"
    "A2;2026-01-15;1930;Företagskonto;Inbetalning;12 500,00;\n"
    "A2;2026-01-15;1510;Kundfordringar;Inbetalning;;12 500,00\n"
)
FEB = (
    "A3;2026-02-03;5010;Lokalhyra;Hyra feb;8 000,00;\n"
    "A3;2026-02-03;2641;Ingående moms;Hyra feb;2 000,00;\n"
    "A3;2026-02-03;1930;Företagskonto;Hyra feb;;10 000,00\n"
)


def H(email: str) -> dict[str, str]:
    return {"X-Dev-User": email}


@pytest.fixture(scope="module")
def env(database):  # type: ignore[no-untyped-def]
    org = create_organization("Formatbyrån", "admin@format.se", "Admin", owner_url=database)
    kim = add_user(org.org_id, "kim@format.se", "Kim", "CONSULTANT", owner_url=database)
    admin = TenantContext(org.org_id, org.admin_user_id, "ADMIN", True, True, "admin@format.se")
    csv_company = create_company(admin, "Testbolaget Format AB", "559900-0001", assign_to=[kim])
    # Utan organisationsnummer: exporten av det andra bolaget får läsas in som kopia.
    json_company = create_company(admin, "Kopia AB", None, assign_to=[kim])
    return {"client": TestClient(create_app()), "admin": admin, "csv": str(csv_company), "json": str(json_company)}


def _upload(env, cid: str, name: str, content: bytes, user: str = "kim@format.se"):  # type: ignore[no-untyped-def]
    return env["client"].post(
        f"/api/companies/{cid}/imports", headers=H(user), files={"file": (name, content, "application/octet-stream")}
    )


def _metric(env, cid: str, code: str, period: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    body = (
        env["client"]
        .get(f"/api/companies/{cid}/metric-comparisons?period={period}&mode=previous", headers=H("admin@format.se"))
        .json()
    )
    return body["metrics"][code]  # type: ignore[no-any-return]


def test_csv_upload_is_normalized_and_versioned(env) -> None:  # type: ignore[no-untyped-def]
    r = _upload(env, env["csv"], "jan.csv", (HEADER + IB_ROWS + JAN).encode("cp1252"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "CSV"
    assert body["stats"]["added"] == 2
    assert "Verifikation" in body["columns"]
    with tenant_session(env["admin"]) as s:
        runs = s.scalars(select(m.ImportRun).where(m.ImportRun.company_id == env["csv"])).all()
        assert [r.source for r in runs] == ["csv_file"]
        assert runs[0].stats["opening_status"] == "known"
        assert runs[0].stats["covered_months"] == ["2026-01"]
        sf = s.scalar(select(m.SourceFile).where(m.SourceFile.company_id == env["csv"]))
        assert sf is not None and sf.detected_format == "CSV"
    assert Decimal(_metric(env, env["csv"], "net_sales", "2026-01")["current"]) == Decimal("10000.00")
    assert Decimal(_metric(env, env["csv"], "cash", "2026-01")["current"]) == Decimal("64500.00")
    # Februari finns inte i filen: saknad, inte noll.
    assert _metric(env, env["csv"], "net_sales", "2026-02")["status"] == "INSUFFICIENT_DATA"


def test_partial_export_keeps_earlier_months_and_opening_balances(env) -> None:  # type: ignore[no-untyped-def]
    r = _upload(env, env["csv"], "feb.csv", (HEADER + FEB).encode())
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["added"] == 1
    assert r.json()["stats"]["removed"] == 0
    feb_cash = _metric(env, env["csv"], "cash", "2026-02")
    assert feb_cash["status"] == "CALCULATED"
    assert Decimal(feb_cash["current"]) == Decimal("54500.00")
    assert Decimal(feb_cash["previous"]) == Decimal("64500.00")
    with tenant_session(env["admin"]) as s:
        last = s.scalars(
            select(m.ImportRun).where(m.ImportRun.company_id == env["csv"]).order_by(m.ImportRun.seq.desc())
        ).first()
        assert last is not None
        assert last.stats["covered_months"] == ["2026-01", "2026-02"]
        assert last.stats["opening_status"] == "known"
        assert last.stats["kept"] == 2


def test_corrected_export_replaces_vouchers_only_inside_its_range(env) -> None:  # type: ignore[no-untyped-def]
    corrected_jan = JAN.replace("12 500,00;\nA1;2026-01-05;3001", "12 400,00;\nA1;2026-01-05;3001").replace(
        ";;10 000,00", ";;9 900,00"
    )
    # A2 finns inte längre i januari-exporten: den räknas som borttagen, februari berörs inte.
    corrected_jan = "\n".join(line for line in corrected_jan.splitlines() if not line.startswith("A2;")) + "\n"
    r = _upload(env, env["csv"], "jan-rattad.csv", (HEADER + IB_ROWS + corrected_jan).encode())
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]
    assert (stats["changed"], stats["removed"]) == (1, 1)
    assert Decimal(_metric(env, env["csv"], "net_sales", "2026-01")["current"]) == Decimal("9900.00")
    assert Decimal(_metric(env, env["csv"], "operating_result", "2026-02")["current"]) == Decimal("-8000.00")


def test_xlsx_upload(env) -> None:  # type: ignore[no-untyped-def]
    wb = Workbook()
    ws = wb.active
    ws.append(["Verifikationsnummer", "Verifikationsserie", "Bokföringsdatum", "Konto", "Debet", "Kredit"])
    ws.append([4, "A", date(2026, 3, 10), 6540, 1000, None])
    ws.append([4, "A", date(2026, 3, 10), 1930, None, 1000])
    buf = io.BytesIO()
    wb.save(buf)
    r = _upload(env, env["csv"], "mars.xlsx", buf.getvalue())
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "XLSX"
    assert Decimal(_metric(env, env["csv"], "operating_result", "2026-03")["current"]) == Decimal("-1000.00")


def test_unreadable_files_get_clear_errors(env) -> None:  # type: ignore[no-untyped-def]
    r = _upload(env, env["csv"], "gammal.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    assert r.status_code == 422 and ".xlsx" in r.json()["detail"]
    r = _upload(env, env["csv"], "lista.csv", b"a;b\n1;2\n")
    assert r.status_code == 422 and "rubrikrad" in r.json()["detail"]


def test_standard_export_requires_payroll_permission_and_round_trips(env) -> None:  # type: ignore[no-untyped-def]
    cl = env["client"]
    assert cl.get(f"/api/companies/{env['csv']}/export/ledger.json", headers=H("kim@format.se")).status_code == 403
    r = cl.get(f"/api/companies/{env['csv']}/export/ledger.json", headers=H("admin@format.se"))
    assert r.status_code == 200
    ledger, issues = loads(r.content)
    assert not [i for i in issues if i.code != "UNKNOWN_ACCOUNT"]
    assert sorted(str(v.key) for y in ledger.years for v in y.vouchers) == ["A1", "A3", "A4"]
    data = json.loads(r.content)
    assert data["source"]["system"] == "csv_file"
    assert {f["format"] for f in data["source"]["files"]} <= {"CSV", "XLSX"}

    # Organisationsnumret i filen skyddar mot import till fel bolag, även i standardformatet.
    wrong = create_company(env["admin"], "Annat AB", "559900-0003")
    assert _upload(env, str(wrong), "kopia.json", r.content, user="admin@format.se").status_code == 422
    imported = _upload(env, env["json"], "kopia.json", r.content)
    assert imported.status_code == 200, imported.text
    assert imported.json()["format"] == "RAI-JSON"
    for code in ("net_sales", "cash", "operating_result"):
        assert _metric(env, env["json"], code, "2026-02") == {
            **_metric(env, env["csv"], code, "2026-02"),
            "fact_ids": _metric(env, env["json"], code, "2026-02")["fact_ids"],
        }


def test_bulk_zip_matches_csv_by_org_number_in_preamble(env) -> None:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "april.csv", HEADER + "A9;2026-04-02;6540;Program;Licens;500,00;\nA9;2026-04-02;1930;Bank;Licens;;500,00\n"
        )
    r = env["client"].post(
        "/api/imports/bulk",
        headers=H("admin@format.se"),
        files={"file": ("filer.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "imported"
    assert r.json()["results"][0]["company_id"] == env["csv"]


def test_sie_import_behaviour_is_unchanged(env) -> None:  # type: ignore[no-untyped-def]
    admin = env["admin"]
    cid = create_company(admin, DEMO_PROFILES[1].name, DEMO_PROFILES[1].org_number)
    g = generate(DEMO_PROFILES[1], date(2026, 9, 30))
    for year in g.ledger.years:
        res = import_sie(admin, cid, f"{year.fiscal_year.label}.se", write_sie4(g.ledger, year), run_review=False)
        assert res.format == "SIE4"
    with tenant_session(admin) as s:
        runs = s.scalars(select(m.ImportRun).where(m.ImportRun.company_id == cid)).all()
        assert {r.source for r in runs} == {"sie_file"}
        assert all("covered_months" not in r.stats for r in runs if r.has_vouchers)
