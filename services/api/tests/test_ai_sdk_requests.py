"""De riktiga SDK:erna (anthropic, openai) mot en simulerad HTTP-server: exakt det som skulle
skickas till Anthropic och OpenAI, och att svaren tolkas rätt. Inga nätverksanrop, inga nycklar."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from redovisningai.ai.providers.anthropic_provider import AnthropicConfig, AnthropicProvider
from redovisningai.ai.providers.base import ModelTier, ProviderError, ToolBudget, ToolSpec
from redovisningai.ai.providers.openai_provider import OpenAIConfig, OpenAIProvider
from redovisningai.ai.tasks import TASKS

anthropic = pytest.importorskip("anthropic")
openai = pytest.importorskip("openai")


def _server(responses: list[dict[str, Any]], status: int = 200) -> tuple[list[httpx2.Request], httpx2.MockTransport]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=responses[min(len(seen), len(responses)) - 1])

    return seen, httpx2.MockTransport(handler)


def _claude(transport: httpx2.MockTransport, **config: Any) -> AnthropicProvider:
    client = anthropic.Anthropic(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=transport),
    )
    return AnthropicProvider(AnthropicConfig(platform="anthropic", **config), client=client)


def _message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 40, "cache_read_input_tokens": 0},
    }


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _claims(n: int) -> str:
    return json.dumps({"claims": [{"type": "OBSERVATION", "text": f"Påstående {i}", "fact_ids": []} for i in range(n)]})


def test_claude_request_uses_supported_schema_and_trims_to_max_items() -> None:
    seen, transport = _server(
        [_message([{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": _claims(12)}])]
    )
    provider = _claude(transport)
    task = TASKS["A3"]
    result = provider.structured(
        task="A3", tier=task.spec.tier, system=task.spec.system, user_content="data", schema=task.spec.schema
    )

    (request,) = seen
    body = json.loads(request.content)
    assert request.url.host == "api.anthropic.com" and request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert body["model"] == "claude-opus-5"
    assert body["fallbacks"] == "default"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    # maxItems avvisas av API:t – det får inte skickas som villkor (bara som text i beskrivningen),
    # men gränsen gäller ändå för svaret.
    assert "maxItems" not in _keys(body["output_config"]["format"]["schema"])
    assert len(result.data["claims"]) == 10
    assert result.usage.input_tokens == 120 and result.model == "claude-opus-5"


def test_claude_tool_loop_sends_results_back_in_one_message() -> None:
    seen, transport = _server(
        [
            _message(
                [
                    {"type": "thinking", "thinking": "", "signature": "s"},
                    {"type": "tool_use", "id": "tu_1", "name": "hamta", "input": {}},
                ],
                stop_reason="tool_use",
            ),
            _message([{"type": "text", "text": json.dumps({"ok": True, "svar": "RAI-4711"})}]),
        ]
    )
    tool = ToolSpec(
        "hamta",
        "Hämtar värdet.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        lambda _args: {"varde": "RAI-4711"},
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "svar": {"type": "string"}},
        "required": ["ok", "svar"],
        "additionalProperties": False,
    }
    result = _claude(transport).run_tools(
        task="CHECK",
        tier=ModelTier.SMALL,
        system="test",
        user_content="Anropa verktyget.",
        tools=[tool],
        schema=schema,
        budget=ToolBudget(max_tool_calls=2, max_iterations=3),
    )

    first, second = (json.loads(r.content) for r in seen)
    assert first["tools"][0]["strict"] is True and first["output_config"]["effort"] == "low"
    assistant, results = second["messages"][1], second["messages"][2]
    assert assistant["role"] == "assistant" and assistant["content"][0]["type"] == "thinking"
    assert results["content"] == [{"type": "tool_result", "tool_use_id": "tu_1", "content": '{"varde": "RAI-4711"}'}]
    assert result.data["svar"] == "RAI-4711" and result.tool_calls == [{"tool": "hamta", "input": {}}]


def test_claude_error_message_is_readable() -> None:
    _, transport = _server(
        [{"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}], status=401
    )
    with pytest.raises(ProviderError, match=r"^Claude HTTP 401: invalid x-api-key$"):
        _claude(transport).structured(
            task="A1", tier=ModelTier.SMALL, system="s", user_content="u", schema=TASKS["A1"].spec.schema
        )


def test_claude_client_uses_configured_key_and_address_not_environment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fel-nyckel-fran-miljon")
    provider = AnthropicProvider(
        AnthropicConfig(platform="anthropic", api_key="ratt-nyckel", base_url="https://api.anthropic.com")
    )
    assert provider._client.api_key == "ratt-nyckel"
    assert str(provider._client.base_url).rstrip("/") == "https://api.anthropic.com"


def _response(output: list[dict[str, Any]], status: str = "completed") -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "gpt-6-luna",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 50,
            "output_tokens": 10,
            "total_tokens": 60,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _openai(transport: httpx2.MockTransport) -> OpenAIProvider:
    client = openai.OpenAI(
        api_key="test-key", max_retries=0, http_client=openai.DefaultHttpxClient(transport=transport)
    )
    provider = OpenAIProvider(OpenAIConfig(api_key="test-key"), client=client)
    provider._sdk = openai  # som i drift: SDK:ns fel översätts till leverantörsfel
    return provider


def test_openai_tool_loop_keeps_encrypted_reasoning_with_store_false() -> None:
    seen, transport = _server(
        [
            _response(
                [
                    {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "krypterat"},
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "hamta",
                        "arguments": "{}",
                        "status": "completed",
                    },
                ]
            ),
            _response(
                [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"ok": True, "svar": "RAI-4711"}),
                                "annotations": [],
                            }
                        ],
                    }
                ]
            ),
        ]
    )
    tool = ToolSpec(
        "hamta",
        "Hämtar värdet.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        lambda _args: {"varde": "RAI-4711"},
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "svar": {"type": "string"}},
        "required": ["ok", "svar"],
        "additionalProperties": False,
    }
    result = _openai(transport).run_tools(
        task="CHECK",
        tier=ModelTier.SMALL,
        system="test",
        user_content="Anropa verktyget.",
        tools=[tool],
        schema=schema,
        budget=ToolBudget(max_tool_calls=2, max_iterations=3),
    )

    first, second = (json.loads(r.content) for r in seen)
    assert seen[0].url.path == "/v1/responses"
    assert first["store"] is False and first["include"] == ["reasoning.encrypted_content"]
    assert first["text"]["format"]["strict"] is True and first["tools"][0]["strict"] is True
    replay = second["input"]
    assert {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "krypterat"} in replay
    assert {"type": "function_call_output", "call_id": "call_1", "output": '{"varde": "RAI-4711"}'} in replay
    assert result.data == {"ok": True, "svar": "RAI-4711"} and len(result.tool_calls) == 1


def test_openai_errors_explain_the_cause() -> None:
    _, transport = _server(
        [{"error": {"message": "The model `gpt-6-luna` does not exist.", "type": "invalid_request_error"}}],
        status=404,
    )
    with pytest.raises(ProviderError, match="OpenAI HTTP 404: The model `gpt-6-luna` does not exist"):
        _openai(transport).structured(
            task="A1", tier=ModelTier.SMALL, system="s", user_content="u", schema=TASKS["A1"].spec.schema
        )
    _, transport = _server(
        [{**_response([], status="incomplete"), "incomplete_details": {"reason": "max_output_tokens"}}]
    )
    with pytest.raises(ProviderError, match=r"ofullständigt svar \(max_output_tokens\)"):
        _openai(transport).structured(
            task="A1", tier=ModelTier.SMALL, system="s", user_content="u", schema=TASKS["A1"].spec.schema
        )
