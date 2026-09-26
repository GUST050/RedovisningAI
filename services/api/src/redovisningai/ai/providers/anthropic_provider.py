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
    # Bara för plattformen "anthropic". Anges alltid uttryckligen av fabriken, så att klienten inte
    # läser nyckel eller adress (ANTHROPIC_BASE_URL) från processens miljö.
    api_key: str | None = None
    base_url: str | None = None


def _platform_model(platform: str, model: str) -> str:
    if platform == "bedrock" and not model.startswith("anthropic."):
        return f"anthropic.{model}"
    return model


def _api_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON-schema i den delmängd som strukturerad output och strikta verktyg stöder.

    T.ex. `maxItems` avvisas av API:t; SDK:ns transform_schema flyttar sådana villkor till
    beskrivningen. Gränsen upprätthålls i stället av `_trim_to_schema` efter svaret.
    """
    from anthropic import transform_schema

    try:
        return transform_schema(schema)
    except ValueError:  # t.ex. ett tomt schema: skickas oförändrat, som tidigare
        return schema


def _error_message(exc: Any) -> str:
    """API:ts eget felmeddelande (t.ex. "API key is invalid."), utan kringliggande JSON."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return str(message or getattr(exc, "message", "") or type(exc).__name__)[:300]


def _trim_to_schema(value: Any, schema: dict[str, Any]) -> Any:
    """Korta listor till schemats `maxItems` (som API:t inte själv kan tvinga fram)."""
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        props = schema["properties"]
        return {k: _trim_to_schema(v, props[k]) if k in props else v for k, v in value.items()}
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        limit = schema.get("maxItems")
        trimmed = value[:limit] if isinstance(limit, int) else value
        return [_trim_to_schema(v, items) for v in trimmed]
    return value


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
            return anthropic.Anthropic(api_key=c.api_key, base_url=c.base_url, **common)
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
                "format": {"type": "json_schema", "schema": _api_schema(schema)},
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
            raise ProviderError(f"Claude HTTP {exc.status_code}: {_error_message(exc)}") from exc

    @staticmethod
    def _usage(resp: Any) -> Usage:
        u = resp.usage
        return Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    @staticmethod
    def _check_stop(resp: Any) -> None:
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise RefusalError(f"Modellen avböjde (kategori: {category})")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("Svaret avbröts vid max_tokens")

    @staticmethod
    def _json_text(resp: Any) -> dict[str, Any]:
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise ProviderError("Svar utan textblock")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Ogiltig JSON från modellen: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError("JSON-svaret är inte ett objekt")
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
        self._check_stop(resp)
        return StructuredResult(
            data=_trim_to_schema(self._json_text(resp), schema),
            usage=self._usage(resp),
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
            {"name": t.name, "description": t.description, "input_schema": _api_schema(t.input_schema), "strict": True}
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
            self._check_stop(resp)
            if resp.stop_reason != "tool_use":
                return StructuredResult(
                    data=_trim_to_schema(self._json_text(resp), schema),
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
        raise ProviderError("Verktygsloopen avslutades inte")
