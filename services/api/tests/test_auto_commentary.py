"""A3 körs automatiskt efter granskningen (plan §9.8, Task 8).

En analys av senaste granskade månaden, bara när AI är på, högst en gång per dataversion, och ett
AI-fel får aldrig stoppa importen. Kör mot den isolerade testdatabasen rai_test.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy import select

from redovisningai.ai.service import AIService, FakeProvider, InMemoryBudget
from redovisningai.db import models as m
from redovisningai.db.bootstrap import create_company, create_organization
from redovisningai.db.session import TenantContext, tenant_session
from redovisningai.jobs.pipeline import import_sie, review_company
from sie_samples import minimal_sie_with_missing_rent

pytestmark = pytest.mark.usefixtures("database")

LATEST = "2026-09"


def _company(database: str, slug: str) -> tuple[TenantContext, Any]:
    org = create_organization(f"Byrå {slug}", f"admin@{slug}.se", "Admin", owner_url=database)
    admin = TenantContext(org.org_id, org.admin_user_id, "ADMIN", payroll=True, aml=True, user_email=f"admin@{slug}.se")
    return admin, create_company(admin, "Påhittat Hyresbolag AB", "556000-0001")


def _commentary(ctx: TenantContext, company: Any, period: str = LATEST) -> dict[str, Any] | None:
    with tenant_session(ctx) as s:
        pr = s.scalar(
            select(m.PeriodReview).where(m.PeriodReview.company_id == company, m.PeriodReview.period == period)
        )
        return dict(pr.commentary) if pr is not None and pr.commentary else None


def _a3_calls(provider: FakeProvider) -> int:
    return sum(1 for call in provider.calls if call["task"] == "A3")


def test_import_writes_one_ai_analysis_of_the_latest_month_and_does_not_repeat_it(database: str) -> None:
    admin, company = _company(database, "auto-en")
    provider = FakeProvider()

    import_sie(admin, company, "minimal.se", minimal_sie_with_missing_rent(), ai=AIService(provider))

    saved = _commentary(admin, company)
    assert saved is not None and saved["source"] == "ai"
    assert saved["analysis_metadata"]["source_fingerprint"] and saved["by"] == "automatisk analys"
    assert _commentary(admin, company, "2026-08") is None  # bara senaste månaden
    assert _a3_calls(provider) == 1

    review_company(TenantContext.worker(admin.org_id), company, ai=AIService(provider))
    assert _a3_calls(provider) == 1  # oförändrad bokföring: utkastet är aktuellt


def test_ai_failure_is_logged_and_does_not_stop_the_import(database: str, caplog: pytest.LogCaptureFixture) -> None:
    admin, company = _company(database, "auto-fel")

    class Broken(FakeProvider):
        def structured(self, **kwargs: Any) -> Any:
            if kwargs["task"] == "A3":
                raise RuntimeError("modellen föll")
            return super().structured(**kwargs)

    with caplog.at_level(logging.ERROR, logger="redovisningai.jobs.pipeline"):
        result = import_sie(admin, company, "minimal.se", minimal_sie_with_missing_rent(), ai=AIService(Broken()))

    assert result.reviewed_periods  # importen och granskningen gick igenom
    assert _commentary(admin, company) is None
    assert "Automatisk AI-analys" in caplog.text


@pytest.mark.parametrize(
    "ai",
    [None, AIService(FakeProvider(), budget=InMemoryBudget(monthly_tokens=0))],
    ids=["ai-avstangt", "budget-slut"],
)
def test_no_automatic_analysis_without_real_ai_text(database: str, ai: AIService | None) -> None:
    admin, company = _company(database, f"auto-{'utan' if ai is None else 'budget'}")
    import_sie(admin, company, "minimal.se", minimal_sie_with_missing_rent(), ai=ai)
    assert _commentary(admin, company) is None  # regeltext låser inte perioden
