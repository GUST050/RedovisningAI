"""OpenAI Responses adapter for the existing, read-only AI task interface."""

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


def _openai_schema(value: Any) -> Any:
    """Remove JSON Schema keywords unsupported by OpenAI's strict subset."""
    if isinstance(value, dict):
        return {key: _openai_schema(item) for key, item in value.items() if key not in {"maxItems", "minItems"}}
    if isinstance(value, list):
        return [_openai_schema(item) for item in value]
    return value


@dataclass(slots=True)
class OpenAIConfig:
    api_key: str | None
    base_url: str | None = None
    models: dict[ModelTier, str] = field(default_factory=lambda: {tier: "gpt-6-luna" for tier in ModelTier})
    timeout_s: float = 90.0
    max_retries: int = 0


class OpenAIProvider:
    def __init__(self, config: OpenAIConfig, client: Any | None = None) -> None:
        if not config.api_key:
            raise ProviderError("OpenAI kräver OPENAI_API_KEY i API-serverns miljö.")
        self.config = config
        self.name = "openai"
        self.region = (
            "eu-endpoint" if config.base_url and config.base_url.startswith("https://eu.api.openai.com/") else None
        )
        self._sdk: Any | None = None
        self._client = client if client is not None else self._make_client()

    def _make_client(self) -> Any:
        try:
            import openai  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - installationsberoende
            raise ProviderError("Paketet 'openai' saknas – installera extras 'ai'.") from exc
        kwargs: dict[str, Any] = {
            "api_key": self.config.api_key,
            "timeout": self.config.timeout_s,
            "max_retries": self.config.max_retries,
        }
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        self._sdk = openai
        return openai.OpenAI(**kwargs)

    def _create(self, **kwargs: Any) -> Any:
        try:
            return self._client.responses.create(**kwargs)
        except Exception as exc:
            # An injected client (for tests or a compatible proxy) does not need
            # the optional SDK just to exercise the provider's request contract.
            if self._sdk is None:
                raise
            if isinstance(exc, (self._sdk.APIConnectionError, self._sdk.RateLimitError, self._sdk.InternalServerError)):
                raise ProviderUnavailable(f"OpenAI tillfälligt otillgängligt ({type(exc).__name__})") from exc
            if isinstance(exc, self._sdk.APIStatusError):
                if exc.status_code >= 500:
                    raise ProviderUnavailable(f"OpenAI HTTP {exc.status_code}") from exc
                raise ProviderError(f"OpenAI HTTP {exc.status_code}") from exc
            if isinstance(exc, self._sdk.APIError):
                raise ProviderError(f"OpenAI-anropet misslyckades ({type(exc).__name__})") from exc
            raise

    def _request(
        self, task: str, tier: ModelTier, system: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        return {
            "model": self.config.models[tier],
            "input": [{"role": "system", "content": system}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"rai_{task.lower()}",
                    "strict": True,
                    "schema": _openai_schema(schema),
                }
            },
            "max_output_tokens": max_tokens,
            "store": False,
        }

    @staticmethod
    def _usage(response: Any) -> Usage:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        cached_tokens = getattr(details, "cached_tokens", 0) or 0
        return Usage(
            input_tokens=max(0, input_tokens - cached_tokens),
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=cached_tokens,
        )

    @staticmethod
    def _data(response: Any, usage: Usage) -> dict[str, Any]:
        """Svarets JSON; felen bär förbrukningen eftersom även oanvändbara svar debiteras."""
        if any(
            getattr(part, "type", None) == "refusal"
            for item in (getattr(response, "output", None) or [])
            for part in (getattr(item, "content", None) or [])
        ):
            raise RefusalError("OpenAI avböjde svaret", usage=usage)
        if getattr(response, "status", None) != "completed":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            detail = f" ({reason})" if reason else ""
            raise ProviderError(f"OpenAI gav ett ofullständigt svar{detail}", usage=usage)
        raw = getattr(response, "output_text", None)
        if not raw:
            raise ProviderError("OpenAI gav inget textsvar", usage=usage)
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("OpenAI gav ogiltig JSON", usage=usage) from exc
        if not isinstance(data, dict):
            raise ProviderError("OpenAI-svaret är inte ett objekt", usage=usage)
        return data

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
        req = self._request(task, tier, system, schema, max_tokens)
        req["input"].append({"role": "user", "content": user_content})
        response = self._create(**req)
        usage = self._usage(response)
        return StructuredResult(self._data(response, usage), usage, self.name, response.model, self.region)

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
        req = self._request(task, tier, system, schema, max_tokens)
        req["input"].append({"role": "user", "content": user_content})
        req["tools"] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
                "strict": True,
            }
            for t in tools
        ]
        handlers = {t.name: t for t in tools}
        usage = Usage()
        calls: list[dict[str, Any]] = []
        for iteration in range(budget.max_iterations + 1):
            request = dict(req)
            if len(calls) >= budget.max_tool_calls or iteration == budget.max_iterations:
                request["tool_choice"] = "none"
            response = self._create(**request)
            usage.add(self._usage(response))
            if getattr(response, "status", None) != "completed":
                self._data(response, usage)  # ger tydligt fel även för vägran
            functions = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not functions:
                return StructuredResult(
                    self._data(response, usage), usage, self.name, response.model, self.region, calls
                )
            if len(calls) >= budget.max_tool_calls or iteration == budget.max_iterations:
                raise ProviderError("OpenAI nådde verktygsgränsen utan ett slutligt svar", usage=usage)
            req["input"].extend(response.output)
            for item in functions:
                spec = handlers.get(item.name)
                if spec is None or len(calls) >= budget.max_tool_calls:
                    output = {"error": "Verktyget saknas eller anropsgränsen är nådd"}
                else:
                    try:
                        arguments = json.loads(item.arguments)
                        if not isinstance(arguments, dict):
                            raise ValueError("Verktygsargument är inte ett objekt")
                        output = spec.handler(arguments)
                        calls.append({"tool": item.name, "input": arguments})
                    except (ValueError, TypeError) as exc:
                        output = {"error": f"Ogiltiga verktygsargument: {exc}"}
                    except Exception:  # verktygsfel får inte exponera data till klienten eller loggen
                        log.exception("OpenAI-läsverktyget %s misslyckades", item.name)
                        output = {"error": "Läsverktyget misslyckades"}
                req["input"].append(
                    {
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": json.dumps(output, ensure_ascii=False, default=str),
                    }
                )
        raise ProviderError("OpenAI nådde verktygsgränsen utan ett slutligt svar", usage=usage)
