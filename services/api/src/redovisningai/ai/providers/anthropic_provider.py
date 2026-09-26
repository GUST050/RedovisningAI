"""Claude via Anthropics SDK – helst i EU-region hos molnleverantör.

Plattformar:
- "bedrock": Amazon Bedrock (Mantle-klienten), EU-region väljs med `region`.
- "vertex": Google Cloud Vertex AI, EU-region (t.ex. "europe-west4") eller multi-region "eu".
- "anthropic": Anthropics eget API. OBS: har i dag ingen EU-inferens (endast "us"/"global").

Strukturerad output (`output_config.format` med JSON-schema) garanterar giltig JSON.
Stabila systemprompter cachas (prompt caching). Vid vägran (`stop_reason == "refusal"`)
används reservmodell: server-side `fallbacks` på Anthropics API, klientmiddleware på Bedrock/Vertex.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from redovisningai.ai.providers.base import (
    ModelTier,
    ProviderError,
    ProviderUnavailable,
    RefusalError,
    StructuredResult,
    ToolBudget,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)

# Samma modell på alla nivåer som standard; nivån styr ansträngning (effort). Enligt
# rekommendationen mäts "starkaste modellen med lägre effort" innan billigare modeller
# införs. Per nivå kan modell och effort ändras i konfigurationen.
DEFAULT_MODELS = {
    ModelTier.SMALL: "claude-opus-5",
    ModelTier.MEDIUM: "claude-opus-5",
    ModelTier.STRONG: "claude-opus-5",
}
DEFAULT_EFFORT = {ModelTier.SMALL: "low", ModelTier.MEDIUM: "medium", ModelTier.STRONG: "high"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(slots=True)
class AnthropicConfig:
    platform: str = "bedrock"  # bedrock | vertex | anthropic
    region: str | None = "eu-north-1"
    project_id: str | None = None  # Vertex
    models: dict[ModelTier, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    effort: dict[ModelTier, str] = field(default_factory=lambda: dict(DEFAULT_EFFORT))
    refusal_fallback_model: str | None = "claude-opus-4-8"
    timeout_s: float = 120.0
    max_retries: int = 2


def _platform_model(platform: str, model: str) -> str:
    if platform == "bedrock" and not model.startswith("anthropic."):
        return f"anthropic.{model}"
    return model


class AnthropicProvider:
    def __init__(self, config: AnthropicConfig, client: Any | None = None) -> None:
        self.config = config
        self.name = f"claude-{config.platform}"
        self.region = config.region
        self._client = client if client is not None else self._make_client()
        self._fallback_state: Any | None = None

    # ------------------------------------------------------------------ klient
    def _make_client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - beror på installation
            raise ProviderError("Paketet 'anthropic' saknas – installera extras 'ai'.") from exc
        c = self.config
        middleware: list[Any] = []
        if c.platform in ("bedrock", "vertex") and c.refusal_fallback_model:
            middleware.append(
                anthropic.BetaRefusalFallbackMiddleware(
                    [{"model": _platform_model(c.platform, c.refusal_fallback_model)}]
                )
            )
        common: dict[str, Any] = {"timeout": c.timeout_s, "max_retries": c.max_retries}
        if c.platform == "bedrock":
            if not c.region:
                raise ProviderError("Bedrock kräver region (t.ex. en EU-region).")
            return anthropic.AnthropicBedrockMantle(aws_region=c.region, middleware=middleware or None, **common)
        if c.platform == "vertex":
            if not (c.project_id and c.region):
                raise ProviderError("Vertex kräver project_id och region.")
            return anthropic.AnthropicVertex(
                project_id=c.project_id, region=c.region, middleware=middleware or None, **common
            )
        if c.platform == "anthropic":
            return anthropic.Anthropic(**common)
        raise ProviderError(f"Okänd plattform {c.platform!r}")

    # ------------------------------------------------------------------ hjälp
    def _request_base(self, tier: ModelTier, system: str, schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        c = self.config
        params: dict[str, Any] = {
            "model": _platform_model(c.platform, c.models[tier]),
            "max_tokens": max_tokens,
            # Stabil systemprompt först så att prefixet kan cachas.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": {
                "effort": c.effort.get(tier, "high"),
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        if c.platform == "anthropic" and c.refusal_fallback_model:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _create(self, params: dict[str, Any]) -> Any:
        import anthropic

        try:
            if self.config.platform in ("bedrock", "vertex") and self.config.refusal_fallback_model:
                state = anthropic.BetaFallbackState()
                with state:
                    return self._client.beta.messages.create(**params)
            return self._client.beta.messages.create(**params)
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise ProviderUnavailable(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 529:
                raise ProviderUnavailable(str(exc)) from exc
            raise ProviderError(f"{exc.status_code}: {exc.message}") from exc

    @staticmethod
    def _usage(resp: Any) -> Usage:
        u = resp.usage
        return Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    # Felen nedan bär förbrukningen: ett svar som inte går att använda debiteras ändå.
    @staticmethod
    def _check_stop(resp: Any, usage: Usage) -> None:
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise RefusalError(f"Modellen avböjde (kategori: {category})", usage=usage)
        if resp.stop_reason == "max_tokens":
            raise ProviderError("Svaret avbröts vid max_tokens", usage=usage)

    @staticmethod
    def _json_text(resp: Any, usage: Usage) -> dict[str, Any]:
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise ProviderError("Svar utan textblock", usage=usage)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Ogiltig JSON från modellen: {exc}", usage=usage) from exc
        if not isinstance(data, dict):
            raise ProviderError("JSON-svaret är inte ett objekt", usage=usage)
        return data

    # ------------------------------------------------------------------ API
    def structured(
        self,
        *,
        task: str,
        tier: ModelTier,
        system: str,
        user_content: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
    ) -> StructuredResult:
        params = self._request_base(tier, system, schema, max_tokens)
        params["messages"] = [{"role": "user", "content": user_content}]
        resp = self._create(params)
        usage = self._usage(resp)
        self._check_stop(resp, usage)
        return StructuredResult(
            data=self._json_text(resp, usage),
            usage=usage,
            provider=self.name,
            model=getattr(resp, "model", params["model"]),
            region=self.region,
        )

    def run_tools(
        self,
        *,
        task: str,
        tier: ModelTier,
        system: str,
        user_content: str,
        tools: list[ToolSpec],
        schema: dict[str, Any],
        budget: ToolBudget,
        max_tokens: int = 16000,
    ) -> StructuredResult:
        """Manuell verktygsloop med budget. Verktygen är läsverktyg med servern som grind."""
        params = self._request_base(tier, system, schema, max_tokens)
        params["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
            for t in tools
        ]
        handlers = {t.name: t for t in tools}
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        usage = Usage()
        calls: list[dict[str, Any]] = []
        for iteration in range(budget.max_iterations + 1):
            exhausted = len(calls) >= budget.max_tool_calls or iteration == budget.max_iterations
            req = dict(params, messages=messages)
            if exhausted:
                req["tool_choice"] = {"type": "none"}
            resp = self._create(req)
            usage.add(self._usage(resp))
            self._check_stop(resp, usage)
            if resp.stop_reason != "tool_use":
                return StructuredResult(
                    data=self._json_text(resp, usage),
                    usage=usage,
                    provider=self.name,
                    model=getattr(resp, "model", params["model"]),
                    region=self.region,
                    tool_calls=calls,
                )
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                spec = handlers.get(block.name)
                if spec is None or len(calls) >= budget.max_tool_calls:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Verktygsbudgeten är slut eller verktyget finns inte. Svara med det du har.",
                            "is_error": True,
                        }
                    )
                    continue
                try:
                    output = spec.handler(dict(block.input))
                    content = json.dumps(output, ensure_ascii=False, default=str)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                    calls.append({"tool": block.name, "input": dict(block.input)})
                except Exception as exc:  # verktygsfel skickas tillbaka, loopen fortsätter
                    log.warning("Verktyget %s misslyckades: %s", block.name, exc)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Fel: {exc}",
                            "is_error": True,
                        }
                    )
            # Alla verktygsresultat i ett och samma användarmeddelande.
            messages.append({"role": "user", "content": results})
        raise ProviderError("Verktygsloopen avslutades inte", usage=usage)
