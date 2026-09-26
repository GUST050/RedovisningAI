"""Bygg AIService från konfiguration, med databasbaserad budget och spårning."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from redovisningai.ai.providers.anthropic_provider import AnthropicConfig, AnthropicProvider
from redovisningai.ai.providers.base import FailoverProvider, ModelProvider, ModelTier, ProviderError, Usage
from redovisningai.ai.providers.openai_provider import OpenAIConfig, OpenAIProvider
from redovisningai.ai.service import AIService, AITrace, FakeProvider
from redovisningai.config import Settings, get_settings
from redovisningai.db import models as m
from redovisningai.db.session import TenantContext, tenant_session

log = logging.getLogger(__name__)


def _provider(platform: str, region: str | None, s: Settings) -> ModelProvider:
    if platform == "fake":
        return FakeProvider()
    if platform == "openai":
        return OpenAIProvider(
            OpenAIConfig(
                api_key=s.openai_api_key,
                base_url=s.openai_base_url,
                models={
                    ModelTier.STRONG: s.openai_model_strong,
                    ModelTier.MEDIUM: s.openai_model_medium,
                    ModelTier.SMALL: s.openai_model_small,
                },
            )
        )
    if platform not in {"bedrock", "vertex", "anthropic"}:
        raise ProviderError(f"Okänd AI-plattform: {platform}")
    cfg = AnthropicConfig(
        platform=platform,
        region=region,
        project_id=s.ai_project_id,
        models={
            ModelTier.STRONG: s.ai_model_strong,
            ModelTier.MEDIUM: s.ai_model_medium,
            ModelTier.SMALL: s.ai_model_small,
        },
        refusal_fallback_model=None if s.ai_test_mode else s.ai_refusal_fallback_model,
        max_retries=0 if s.ai_test_mode else 2,
    )
    return AnthropicProvider(cfg)


def build_provider(s: Settings | None = None) -> ModelProvider | None:
    s = s or get_settings()
    if not s.ai_enabled:
        return None
    try:
        providers = [_provider(s.ai_platform, s.ai_region, s)]
        if s.ai_secondary_platform and not s.ai_test_mode:
            providers.append(_provider(s.ai_secondary_platform, s.ai_secondary_region, s))
    except ProviderError as exc:
        log.error("AI kunde inte konfigureras: %s", exc)
        return None
    return providers[0] if len(providers) == 1 else FailoverProvider(providers)


class DbBudget:
    """Tokenbudget per byrå och månad, lagrad i ai_usage."""

    def __init__(self, org_id: uuid.UUID, monthly_cap: int | None = None) -> None:
        self.org_id = org_id
        self.monthly_cap = monthly_cap

    def allow(self, org_id: str, task: str) -> bool:
        month = datetime.now().strftime("%Y-%m")
        with tenant_session(TenantContext.worker(self.org_id)) as s:
            org = s.get(m.Organization, self.org_id)
            used = s.scalar(select(m.AIUsage.tokens).where(m.AIUsage.org_id == self.org_id, m.AIUsage.month == month))
            limit = org.ai_monthly_token_budget if org else 0
            if self.monthly_cap is not None:
                limit = min(limit, self.monthly_cap)
            return (used or 0) < limit

    def record(self, org_id: str, task: str, usage: Usage) -> None:
        month = datetime.now().strftime("%Y-%m")
        with tenant_session(TenantContext.worker(self.org_id)) as s:
            row = s.get(m.AIUsage, (self.org_id, month))
            if row is None:
                s.add(m.AIUsage(org_id=self.org_id, month=month, tokens=usage.total))
            else:
                row.tokens += usage.total


def db_trace_sink(org_id: uuid.UUID):  # type: ignore[no-untyped-def]
    days = get_settings().ai_trace_retention_days

    def sink(trace: AITrace) -> None:
        with tenant_session(TenantContext.worker(org_id)) as s:
            s.add(
                m.AITraceRow(
                    id=uuid.UUID(trace.id),
                    org_id=org_id,
                    company_id=uuid.UUID(trace.company_id) if trace.company_id else None,
                    task=trace.task,
                    source=trace.source,
                    provider=trace.provider,
                    model=trace.model,
                    region=trace.region,
                    input_hash=trace.input_hash,
                    usage=trace.usage,
                    attempts=trace.attempts,
                    rejected=trace.rejected,
                    downgraded=trace.downgraded,
                    error=trace.error,
                    package=trace.package,
                    output=trace.output,
                    tool_calls=trace.tool_calls,
                    expires_at=datetime.now().astimezone() + timedelta(days=days),
                )
            )

    return sink


def build_ai_service(org_id: uuid.UUID, s: Settings | None = None) -> AIService:
    s = s or get_settings()
    provider = build_provider(s)
    return AIService(
        provider,
        budget=DbBudget(org_id, s.ai_test_monthly_token_cap if s.ai_test_mode else None),
        trace_sink=db_trace_sink(org_id),
        # Accounting traces keep hashes, model/usage metadata and decisions,
        # never raw customer payloads or generated drafts. Drafts are stored
        # only in their explicitly permissioned review workflow.
        keep_payloads=False,
        max_output_tokens=s.ai_test_max_output_tokens if s.ai_test_mode else None,
        max_tool_calls=s.ai_test_max_tool_calls if s.ai_test_mode else None,
        max_attempts=1 if s.ai_test_mode else 2,
    )
