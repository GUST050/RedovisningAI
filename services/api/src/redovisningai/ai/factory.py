"""Bygg AIService från konfiguration, med databasbaserad budget och spårning."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from redovisningai.ai.providers.anthropic_provider import AnthropicConfig, AnthropicProvider
from redovisningai.ai.providers.base import (
    FailoverProvider,
    ModelProvider,
    ModelTier,
    ProviderError,
    StructuredResult,
    ToolBudget,
    ToolSpec,
    Usage,
)
from redovisningai.ai.providers.openai_provider import OpenAIConfig, OpenAIProvider
from redovisningai.ai.service import AIService, AITrace, FakeProvider
from redovisningai.config import Settings, get_settings
from redovisningai.db import models as m
from redovisningai.db.session import TenantContext, tenant_session

log = logging.getLogger(__name__)


PLATFORM_LABELS = {
    "anthropic": "Claude (Anthropic)",
    "openai": "OpenAI",
    "bedrock": "Claude via AWS Bedrock",
    "vertex": "Claude via Google Vertex AI",
    "fake": "Testleverantör",
}


def platform_models(platform: str | None, s: Settings) -> dict[str, str]:
    if platform == "openai":
        return {"strong": s.openai_model_strong, "medium": s.openai_model_medium, "small": s.openai_model_small}
    if platform in {"anthropic", "bedrock", "vertex"}:
        return {"strong": s.ai_model_strong, "medium": s.ai_model_medium, "small": s.ai_model_small}
    return {}


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
                timeout_s=s.ai_timeout_s,
                max_retries=0 if s.ai_test_mode else 2,
            )
        )
    if platform not in {"bedrock", "vertex", "anthropic"}:
        raise ProviderError(f"Okänd AI-plattform: {platform}")
    if platform == "anthropic" and not s.anthropic_api_key:
        raise ProviderError("Claude (Anthropic) kräver ANTHROPIC_API_KEY i API-serverns miljö.")
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
        timeout_s=s.ai_timeout_s,
        max_retries=0 if s.ai_test_mode else 2,
        api_key=s.anthropic_api_key if platform == "anthropic" else None,
        base_url=s.anthropic_base_url if platform == "anthropic" else None,
    )
    return AnthropicProvider(cfg)


@dataclass(slots=True)
class ConfiguredProvider:
    role: str  # "primär" | "reserv"
    platform: str
    provider: ModelProvider | None
    problem: str | None = None


def configured_providers(s: Settings | None = None) -> list[ConfiguredProvider]:
    """Leverantörerna i den ordning de används. En som inte går att konfigurera (t.ex. saknad
    nyckel) får en felorsak men stoppar inte de andra."""
    s = s or get_settings()
    primary, secondary = s.ai_platforms()
    wanted = [("primär", primary, s.ai_region)]
    if not s.ai_test_mode:  # testläget använder aldrig en andra betald leverantör
        wanted.append(("reserv", secondary, s.ai_secondary_region or s.ai_region))
    out: list[ConfiguredProvider] = []
    for role, platform, region in wanted:
        if not platform:
            continue
        try:
            out.append(ConfiguredProvider(role, platform, _provider(platform, region, s)))
        except ProviderError as exc:
            log.error("AI-leverantören %s kunde inte konfigureras: %s", platform, exc)
            out.append(ConfiguredProvider(role, platform, None, str(exc)))
    return out


def build_provider(s: Settings | None = None) -> ModelProvider | None:
    s = s or get_settings()
    if not s.ai_requested:
        return None
    providers = [c.provider for c in configured_providers(s) if c.provider is not None]
    if not providers:
        return None
    return providers[0] if len(providers) == 1 else FailoverProvider(providers)


# ------------------------------------------------------------------ anslutningstest
CHECK_VALUE = "RAI-4711"
CHECK_SYSTEM = "Det här är ett anslutningstest för RedovisningAI. Svara kort på svenska och följ JSON-schemat."
CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "svar": {"type": "string"}},
    "required": ["ok", "svar"],
    "additionalProperties": False,
}
_CHECK_TOOL = ToolSpec(
    name="hamta_kontrollvarde",
    description="Hämtar kontrollvärdet för anslutningstestet.",
    input_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    handler=lambda _args: {"kontrollvarde": CHECK_VALUE},
)


def _run_check(
    name: str, call: Callable[[], StructuredResult], passed: Callable[[StructuredResult], bool]
) -> dict[str, Any]:
    start = time.monotonic()
    try:
        res = call()
    except Exception as exc:  # visas för administratören; anropet innehåller inga kunddata
        return {
            "name": name,
            "ok": False,
            "error": (str(exc) or type(exc).__name__)[:300],
            "tokens": 0,
            "seconds": round(time.monotonic() - start, 1),
        }
    return {
        "name": name,
        "ok": passed(res),
        "model": res.model,
        "answer": str(res.data.get("svar", ""))[:200],
        "tokens": res.usage.total,
        "tool_calls": len(res.tool_calls),
        "seconds": round(time.monotonic() - start, 1),
    }


def _checks(provider: ModelProvider, *, tools: bool) -> list[dict[str, Any]]:
    if isinstance(provider, FakeProvider):
        provider.responses.setdefault("CHECK", lambda _c: {"ok": True, "svar": "Testleverantören svarar."})
        provider.responses.setdefault("CHECK_TOOLS", lambda _c: {"ok": True, "svar": CHECK_VALUE})
    checks = [
        _run_check(
            "Svar i JSON-format",
            lambda: provider.structured(
                task="CHECK",
                tier=ModelTier.SMALL,
                system=CHECK_SYSTEM,
                user_content="Bekräfta att du fungerar: sätt ok till true och svara med en kort mening.",
                schema=CHECK_SCHEMA,
                max_tokens=4000,
            ),
            lambda r: r.data.get("ok") is True,
        )
    ]
    if tools:
        checks.append(
            _run_check(
                "Läsverktyg (som AI-analytikern)",
                lambda: provider.run_tools(
                    task="CHECK_TOOLS",
                    tier=ModelTier.SMALL,
                    system=CHECK_SYSTEM,
                    user_content="Anropa verktyget hamta_kontrollvarde en gång och svara sedan med "
                    "kontrollvärdet i fältet svar. Sätt ok till true.",
                    tools=[_CHECK_TOOL],
                    schema=CHECK_SCHEMA,
                    budget=ToolBudget(max_tool_calls=2, max_iterations=3),
                    max_tokens=4000,
                ),
                lambda r: CHECK_VALUE in str(r.data.get("svar", "")) and len(r.tool_calls) >= 1,
            )
        )
    return checks


def check_providers(s: Settings | None = None, *, tools: bool = True) -> list[dict[str, Any]]:
    """Kort provanrop till varje konfigurerad leverantör utan kunddata: visar om nyckel, modell
    och nätverk fungerar och – med tools=True – verktygsloopen som AI-analytikern använder."""
    s = s or get_settings()
    results: list[dict[str, Any]] = []
    for item in configured_providers(s):
        checks = _checks(item.provider, tools=tools) if item.provider is not None else []
        results.append(
            {
                "role": item.role,
                "platform": item.platform,
                "label": PLATFORM_LABELS.get(item.platform, item.platform),
                "models": platform_models(item.platform, s),
                "checks": checks,
                "error": item.problem,
                "ok": item.provider is not None and all(c["ok"] for c in checks),
            }
        )
    return results


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
