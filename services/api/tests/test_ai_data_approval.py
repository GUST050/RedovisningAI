from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from redovisningai.ai.service import AIService, FakeProvider
from redovisningai.api.app import create_app
from redovisningai.db import models as m
from redovisningai.db import repo
from redovisningai.db.bootstrap import create_company, create_organization
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.jobs.pipeline import import_sie
from sie_samples import minimal_sie_with_missing_rent

pytestmark = pytest.mark.usefixtures("database")


def test_extended_ai_data_needs_a_valid_unrevoked_approval(database: str) -> None:
    org = create_organization("Byrå godk", "admin@godk.se", "Admin", owner_url=database)
    other = create_organization("Annan byrå godk", "admin@annan-godk.se", "Admin", owner_url=database)
    admin = TenantContext(org.org_id, org.admin_user_id, "ADMIN", payroll=True, aml=True, user_email="admin@godk.se")
    stranger = TenantContext(
        other.org_id, other.admin_user_id, "ADMIN", payroll=True, aml=True, user_email="admin@annan-godk.se"
    )
    company = create_company(admin, "Påhittat Hyresbolag AB", "556000-0001")
    today = date(2026, 9, 27)

    with tenant_session(admin) as s:
        assert repo.approved_ai_providers(s, company, "transaction_bridge", today) == set()
        approval = repo.approve_ai_data(
            s, admin, company, ["transaction_bridge"], "openai", date(2026, 9, 1), date(2026, 12, 31)
        )
        approval_id = approval.id
        assert repo.approved_ai_providers(s, company, "transaction_bridge", today) == {"openai"}
        assert repo.approved_ai_providers(s, company, "transaction_bridge", date(2027, 1, 2)) == set()
        with pytest.raises(ValueError):
            repo.approve_ai_data(s, admin, company, ["voucher_text"], "openai", today, today)
    with tenant_session(stranger) as s:  # RLS: en annan byrå ser inte godkännandet
        assert repo.approved_ai_providers(s, company, "transaction_bridge", today) == set()
    with tenant_session(admin) as s:
        repo.revoke_ai_data(s, admin, approval_id)
        assert repo.approved_ai_providers(s, company, "transaction_bridge", today) == set()


def _company(database: str, slug: str) -> tuple[TenantContext, Any]:
    org = create_organization(f"Byrå {slug}", f"admin@{slug}.se", "Admin", owner_url=database)
    admin = TenantContext(org.org_id, org.admin_user_id, "ADMIN", payroll=True, aml=True, user_email=f"admin@{slug}.se")
    return admin, create_company(admin, "Påhittat Hyresbolag AB", "556000-0001")


def test_every_provider_in_a_failover_chain_needs_its_own_approval(database: str) -> None:
    admin, company = _company(database, "godk-kedja")
    today = date(2026, 9, 27)
    with tenant_session(admin) as s:
        repo.approve_ai_data(s, admin, company, ["transaction_bridge"], "openai", today, today)

        assert repo.extended_ai_data_allowed(s, company, frozenset({"openai"}), today)
        assert not repo.extended_ai_data_allowed(s, company, frozenset({"openai", "claude-anthropic"}), today)
        assert not repo.extended_ai_data_allowed(s, company, frozenset(), today)  # utan AI inget utökat underlag
        # Ett godkännande som ännu inte börjat gälla räknas inte.
        assert repo.approved_ai_providers(s, company, "transaction_bridge", date(2026, 9, 26)) == set()
        with pytest.raises(ValueError):  # omvänt datumintervall
            repo.approve_ai_data(s, admin, company, ["transaction_bridge"], "openai", today, date(2026, 9, 1))
        actions = s.scalars(select(m.AuditEvent.action).where(m.AuditEvent.company_id == company)).all()
        assert "ai.data_approval.created" in actions


def _a3_contents(provider: FakeProvider) -> list[str]:
    return [str(call["user_content"]) for call in provider.calls if call["task"] == "A3"]


def test_the_background_analysis_sends_the_bridge_only_with_an_approval(database: str) -> None:
    admin, company = _company(database, "godk-jobb")
    provider = FakeProvider()  # heter "fake": det är leverantören som ska godkännas
    with tenant_session(admin) as s:
        repo.approve_ai_data(s, admin, company, ["transaction_bridge"], "fake", date(2020, 1, 1), date(2099, 12, 31))

    import_sie(admin, company, "minimal.se", minimal_sie_with_missing_rent(), ai=AIService(provider))

    with tenant_session(admin) as s:
        pr = s.scalar(
            select(m.PeriodReview).where(m.PeriodReview.company_id == company, m.PeriodReview.period == "2026-09")
        )
        assert pr is not None and pr.commentary is not None
        assert pr.commentary["analysis_metadata"]["extended"] is True
    assert all('"transactions"' in content for content in _a3_contents(provider))

    other_admin, other_company = _company(database, "godk-jobb-utan")
    unapproved = FakeProvider()
    import_sie(other_admin, other_company, "minimal.se", minimal_sie_with_missing_rent(), ai=AIService(unapproved))
    assert _a3_contents(unapproved) and not any('"transactions"' in c for c in _a3_contents(unapproved))


def test_the_commentary_route_turns_extended_data_off_when_the_approval_is_revoked(
    database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin, company = _company(database, "godk-route")
    import_sie(admin, company, "minimal.se", minimal_sie_with_missing_rent())
    provider = FakeProvider()
    monkeypatch.setattr("redovisningai.api.routes_review._ai", lambda _principal: AIService(provider))
    client, headers = TestClient(create_app()), {"X-Dev-User": "admin@godk-route.se"}
    approvals = f"/api/companies/{company}/ai-approvals"
    commentary = f"/api/companies/{company}/periods/2026-09/commentary"

    assert client.post(commentary, headers=headers).json()["analysis_metadata"]["extended"] is False
    body = {
        "data_types": ["transaction_bridge"],
        "provider": "fake",
        "valid_from": "2020-01-01",
        "valid_to": "2099-12-31",
    }
    created = client.post(approvals, json=body, headers=headers).json()
    assert created["provider"] == "fake" and created["revoked_at"] is None
    assert client.post(commentary, headers=headers).json()["analysis_metadata"]["extended"] is True
    assert '"transactions"' in _a3_contents(provider)[-1]

    assert client.post(f"{approvals}/{created['id']}/revoke", headers=headers).status_code == 200
    assert client.post(commentary, headers=headers).json()["analysis_metadata"]["extended"] is False
    assert '"transactions"' not in _a3_contents(provider)[-1]
    bad = {**body, "valid_to": "2019-12-31"}
    assert client.post(approvals, json=bad, headers=headers).status_code == 422
    assert client.post(f"{approvals}/00000000-0000-0000-0000-000000000000/revoke", headers=headers).status_code == 404
