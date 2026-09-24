"""Databas, RLS och pipeline. Kör mot riktig PostgreSQL med applikationsrollen."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.bootstrap import add_user, create_company, create_organization
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.findings.lifecycle import FindingStatus
from redovisningai.jobs.pipeline import ImportError_, import_sie, review_company
from redovisningai.review.workflow import (
    WorkflowError,
    answer_question,
    approve_period,
    create_question,
    decide_findings,
    token_hash,
)
from redovisningai.sie.writer import write_sie4

pytestmark = pytest.mark.usefixtures("database")


@pytest.fixture(scope="module")
def world(database):  # type: ignore[no-untyped-def]
    """Två byråer. Byrå A har två konsulter med var sin kund, en läsare och en admin."""
    a = create_organization("Byrå A", "admin@a.se", "Admin A", owner_url=database)
    b = create_organization("Byrå B", "admin@b.se", "Admin B", owner_url=database)
    anna = add_user(a.org_id, "anna@a.se", "Anna", "CONSULTANT", owner_url=database)
    bo = add_user(a.org_id, "bo@a.se", "Bo", "CONSULTANT", can_payroll=True, can_aml=True, owner_url=database)
    lisa = add_user(a.org_id, "lisa@a.se", "Lisa", "VIEWER", owner_url=database)
    admin_a = TenantContext(a.org_id, a.admin_user_id, "ADMIN", payroll=True, aml=True, user_email="admin@a.se")
    admin_b = TenantContext(b.org_id, b.admin_user_id, "ADMIN", payroll=True, aml=True, user_email="admin@b.se")
    p = DEMO_PROFILES[0]
    bygg = create_company(admin_a, p.name, p.org_number, assign_to=[anna, lisa], vat_period=p.vat_period)
    nord = create_company(admin_a, "Nord Frakt AB", DEMO_PROFILES[3].org_number, assign_to=[bo])
    other = create_company(admin_b, "Annan Byrås Kund AB", "556111-2222")
    g = generate(p, date(2026, 9, 30))  # augustiexport – sen augustifaktura saknas ännu
    for y in g.ledger.years:
        import_sie(admin_a, bygg, f"bygg-{y.fiscal_year.label}.se", write_sie4(g.ledger, y), run_review=False)
    review_company(TenantContext.worker(a.org_id), bygg)
    return {
        "a": a,
        "b": b,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "bygg": bygg,
        "nord": nord,
        "other": other,
        "anna": TenantContext(a.org_id, anna, "CONSULTANT", user_email="anna@a.se"),
        "bo": TenantContext(a.org_id, bo, "CONSULTANT", payroll=True, aml=True, user_email="bo@a.se"),
        "lisa": TenantContext(a.org_id, lisa, "VIEWER", user_email="lisa@a.se"),
    }


# ---------------------------------------------------------------------------- isolering


def test_firm_isolation(world) -> None:  # type: ignore[no-untyped-def]
    with tenant_session(world["admin_b"]) as s:
        names = {c.name for c in s.scalars(select(m.Company)).all()}
        assert names == {"Annan Byrås Kund AB"}
        assert s.scalar(select(func.count()).select_from(m.TransactionRow)) == 0
        assert s.scalar(select(func.count()).select_from(m.FindingRow)) == 0
        assert s.get(m.Company, world["bygg"]) is None


def test_consultant_sees_only_assigned_clients(world) -> None:  # type: ignore[no-untyped-def]
    with tenant_session(world["anna"]) as s:
        assert {c.name for c in s.scalars(select(m.Company)).all()} == {"Bygg & Co AB"}
    with tenant_session(world["bo"]) as s:
        assert {c.name for c in s.scalars(select(m.Company)).all()} == {"Nord Frakt AB"}
        assert s.scalar(select(func.count()).select_from(m.VoucherVersion)) == 0


def test_payroll_rows_hidden_without_permission(world) -> None:  # type: ignore[no-untyped-def]
    payroll = select(func.count()).select_from(m.TransactionRow).where(m.TransactionRow.account.between(7000, 7699))
    with tenant_session(world["anna"]) as s:
        assert s.scalar(payroll) == 0
        assert s.scalar(select(func.count()).select_from(m.TransactionRow)) > 0
        # Aggregat (månadsrörelser) är synliga – de avslöjar inga enskilda lönerader.
        assert s.scalar(select(func.count()).select_from(m.AccountPeriodBalance)) > 0
    with tenant_session(world["admin_a"]) as s:
        assert s.scalar(payroll) > 0


def test_aml_findings_hidden_without_permission(world) -> None:  # type: ignore[no-untyped-def]
    aml = select(func.count()).select_from(m.FindingRow).where(m.FindingRow.visibility == "RESTRICTED_AML")
    with tenant_session(world["anna"]) as s:
        assert s.scalar(aml) == 0
        assert s.scalar(select(func.count()).select_from(m.FindingRow)) > 0
    with tenant_session(world["admin_a"]) as s:
        assert s.scalar(aml) > 0


def test_viewer_cannot_write(world) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises((ProgrammingError, DBAPIError)), tenant_session(world["lisa"]) as s:
        s.execute(text("UPDATE finding SET status = 'RESOLVED'"))
        s.add(
            m.FindingRow(
                org_id=world["a"].org_id,
                company_id=world["bygg"],
                fingerprint="x",
                rule_code="X",
                rule_version="1",
                severity="LOW",
                title="t",
                description="d",
                period="2026-09",
                visibility="INTERNAL",
                status="NEW",
            )
        )
        s.flush()
    with tenant_session(world["lisa"]) as s:
        res = s.execute(text("UPDATE finding SET status = 'RESOLVED'"))
        assert res.rowcount == 0  # RLS: inga rader matchar för en läsare


def test_cross_tenant_insert_rejected(world) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises((ProgrammingError, DBAPIError)), tenant_session(world["admin_b"]) as s:
        s.add(m.Account(org_id=world["a"].org_id, company_id=world["bygg"], number=9999, name="Intrång"))
        s.flush()


def test_no_context_sees_nothing(world) -> None:  # type: ignore[no-untyped-def]
    from redovisningai.db.session import anonymous_session

    with anonymous_session() as s:
        assert s.scalar(select(func.count()).select_from(m.Company)) == 0
        assert s.scalar(select(func.count()).select_from(m.Organization)) == 0


def test_context_does_not_leak_between_transactions(world) -> None:  # type: ignore[no-untyped-def]
    """set_config(..., true) gäller bara transaktionen – nästa transaktion på samma anslutning ser inget."""
    from redovisningai.db.session import apply_context, get_engine

    engine = get_engine()
    with engine.connect() as conn:
        with conn.begin():
            from sqlalchemy.orm import Session

            s = Session(bind=conn)
            apply_context(s, world["admin_a"])
            assert s.scalar(select(func.count()).select_from(m.Company)) > 0
        with conn.begin():
            assert conn.execute(text("select count(*) from company")).scalar() == 0
            assert conn.execute(text("select current_setting('app.org_id', true)")).scalar() in ("", None)


def test_audit_log_is_append_only(world) -> None:  # type: ignore[no-untyped-def]
    with tenant_session(world["admin_a"]) as s:
        assert s.scalar(select(func.count()).select_from(m.AuditEvent)) > 0
    with pytest.raises((ProgrammingError, DBAPIError)), tenant_session(world["admin_a"]) as s:
        s.execute(text("DELETE FROM audit_event"))


# ---------------------------------------------------------------------------- import och versioner


def test_import_versioning_and_duplicate(world) -> None:  # type: ignore[no-untyped-def]
    g = generate(DEMO_PROFILES[0], date(2026, 10, 12))  # nyare export: fler verifikationer
    raw = write_sie4(g.ledger, g.ledger.current)
    res = import_sie(world["admin_a"], world["bygg"], "bygg-2026-okt.se", raw, run_review=False)
    assert res.stats["added"] > 0 and res.stats["unchanged"] > 0 and res.stats["removed"] == 0
    again = import_sie(world["admin_a"], world["bygg"], "kopia.se", raw, run_review=False)
    assert again.skipped_duplicate
    with tenant_session(TenantContext.worker(world["a"].org_id)) as s:
        company = s.get(m.Company, world["bygg"])
        ledger, _ = repo.load_ledger(s, company)
        assert len(ledger.current.vouchers) == len(g.ledger.current.vouchers)
        for a, b in zip(
            sorted(ledger.current.vouchers, key=lambda v: str(v.key)),
            sorted(g.ledger.current.vouchers, key=lambda v: str(v.key)),
            strict=True,
        ):
            assert a.content_hash() == b.content_hash()


def test_orgnr_mismatch_rejected(world) -> None:  # type: ignore[no-untyped-def]
    g = generate(DEMO_PROFILES[1], date(2026, 9, 30))
    with pytest.raises(ImportError_):
        import_sie(world["admin_a"], world["bygg"], "fel.se", write_sie4(g.ledger, g.ledger.current), run_review=False)


def test_source_file_is_encrypted_at_rest(world) -> None:  # type: ignore[no-untyped-def]
    from redovisningai.storage.objects import get_object_store

    with tenant_session(world["admin_a"]) as s:
        sf = s.scalars(select(m.SourceFile).where(m.SourceFile.company_id == world["bygg"])).first()
        raw = get_object_store().get(sf.object_key)
        assert not raw.startswith(b"#FLAGGA")


# ---------------------------------------------------------------------------- arbetsflöde


def test_review_decide_approve_and_change_after_approval(world) -> None:  # type: ignore[no-untyped-def]
    bygg = world["bygg"]
    worker = TenantContext.worker(world["a"].org_id)
    review_company(worker, bygg, periods=["2026-08"])
    with tenant_session(world["admin_a"]) as s:
        company = s.get(m.Company, bygg)
        records = [r for r in repo.load_findings(s, bygg) if "2026-08" in r.seen_in_reviews and r.status.is_open]
        assert records
        with pytest.raises(WorkflowError):
            approve_period(s, world["admin_a"], company, "2026-08")  # öppna High-fynd
        decide_findings(
            s,
            world["admin_a"],
            company,
            [uuid.UUID(r.id) for r in records],
            FindingStatus.ACCEPTED_OK,
            "Genomgånget med kunden",
        )
        pr = approve_period(s, world["admin_a"], company, "2026-08", override_note="Test")
        assert pr.status == "APPROVED" and pr.snapshot["fy_seqs"]
    # Ny export där en augustifaktura registrerats i efterhand (efter godkännandet).
    from decimal import Decimal

    from redovisningai.domain.ledger import Row, Voucher

    g = generate(DEMO_PROFILES[0], date(2026, 10, 20))
    late = Voucher(
        "D",
        "9001",
        date(2026, 8, 29),
        "Leverantörsfaktura Ahlsell 99120",
        (Row(4010, Decimal("8000.00")), Row(2641, Decimal("2000.00")), Row(2440, Decimal("-10000.00"))),
        reg_date=date(2026, 10, 15),
    )
    g.ledger.current.vouchers.append(late)
    import_sie(world["admin_a"], bygg, "bygg-2026-sen.se", write_sie4(g.ledger, g.ledger.current))
    with tenant_session(world["admin_a"]) as s:
        pr = s.scalar(
            select(m.PeriodReview).where(m.PeriodReview.company_id == bygg, m.PeriodReview.period == "2026-08")
        )
        assert pr.status == "CHANGED_AFTER_APPROVAL"
        assert any(c["kind"] == "ADDED" and "Ahlsell" in c["text"] for c in pr.changes)
        caa = [r for r in repo.load_findings(s, bygg) if r.rule_code == "CHANGED_AFTER_APPROVAL"]
        assert caa and caa[0].status.is_open
        # Kundminnet sparade besluten
        assert s.scalar(select(func.count()).select_from(m.ResolutionRow)) > 0


def test_client_question_flow_and_aml_block(world) -> None:  # type: ignore[no-untyped-def]
    bygg = world["bygg"]
    with tenant_session(world["admin_a"]) as s:
        company = s.get(m.Company, bygg)
        cases = s.scalars(select(m.CaseRow).where(m.CaseRow.company_id == bygg)).all()
        safe = next(c for c in cases if c.visibility != "RESTRICTED_AML")
        aml = next(c for c in cases if c.visibility == "RESTRICTED_AML")
        with pytest.raises(WorkflowError):
            create_question(s, world["admin_a"], company, aml.case_key, "Varifrån kom pengarna?")
        created = create_question(s, world["admin_a"], company, safe.case_key, "Kan du förklara posten?")
        token = created.token
        assert created.link.endswith(token)
    # Kunden svarar via publik länk – ser bara sin fråga.
    with tenant_session(TenantContext(world["a"].org_id, None, "NONE")) as s:
        row = s.execute(text("select * from question_lookup(:h)"), {"h": token_hash(token)}).one()
    ctx = TenantContext(row.org_id, None, "PUBLIC_QUESTION", question_id=row.id, user_email="kund")
    with tenant_session(ctx) as s:
        assert s.scalar(select(func.count()).select_from(m.Company)) == 0
        assert s.scalar(select(func.count()).select_from(m.FindingRow)) == 0
        q = s.get(m.ClientQuestion, row.id)
        assert q.display["company_name"] == "Bygg & Co AB"
        answer_question(s, ctx, q, "Det var ett förskott.", [])
    with tenant_session(world["admin_a"]) as s:
        q = s.get(m.ClientQuestion, row.id)
        assert q.status == "ANSWERED" and q.answer_text == "Det var ett förskott."


def test_seed_demo_is_idempotent(database) -> None:  # type: ignore[no-untyped-def]
    from redovisningai.db.bootstrap import seed_demo

    first = seed_demo(owner_url=database)
    second = seed_demo(owner_url=database)
    assert first.org_id == second.org_id and first.admin_user_id == second.admin_user_id
