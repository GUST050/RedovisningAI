"""OpenAI Responses adapter contracts; no network calls or real API key."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from redovisningai.ai.providers.base import ModelTier, ProviderError, RefusalError, ToolBudget, ToolSpec
from redovisningai.ai.providers.openai_provider import OpenAIConfig, OpenAIProvider


class Responses:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(deepcopy(kwargs))
        return self.replies.pop(0)


def reply(*, output_text: str = '{"claims": []}', output: list[Any] | None = None, status: str = "completed") -> Any:
    return SimpleNamespace(
        output_text=output_text,
        output=output if output is not None else [],
        status=status,
        incomplete_details=None,
        usage=SimpleNamespace(input_tokens=12, output_tokens=5, input_tokens_details=SimpleNamespace(cached_tokens=2)),
        model="gpt-6-luna",
    )


def provider(replies: list[Any]) -> tuple[OpenAIProvider, Responses]:
    responses = Responses(replies)
    client = SimpleNamespace(responses=responses)
    cfg = OpenAIConfig(api_key="test-only", models={tier: "gpt-6-luna" for tier in ModelTier})
    return OpenAIProvider(cfg, client=client), responses


def test_structured_output_is_stateless_schema_bound_and_metered() -> None:
    api, responses = provider([reply()])
    result = api.structured(
        task="A3",
        tier=ModelTier.STRONG,
        system="rules",
        user_content="facts",
        schema={"type": "object"},
        max_tokens=700,
    )
    call = responses.calls[0]
    assert call["model"] == "gpt-6-luna"
    assert call["store"] is False
    assert call["max_output_tokens"] == 700
    assert call["text"]["format"] == {
        "type": "json_schema",
        "name": "rai_a3",
        "strict": True,
        "schema": {"type": "object"},
    }
    assert call["input"] == [{"role": "system", "content": "rules"}, {"role": "user", "content": "facts"}]
    assert result.data == {"claims": []}
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cache_read_tokens) == (10, 5, 2)
    assert result.usage.total == 17


def test_refusal_and_incomplete_result_cannot_be_used_as_accounting_text() -> None:
    refusal = reply(output_text="", output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])])
    api, _ = provider([refusal])
    with pytest.raises(RefusalError):
        api.structured(task="A3", tier=ModelTier.STRONG, system="", user_content="", schema={})

    api, _ = provider([reply(output_text="{}", status="incomplete")])
    with pytest.raises(ProviderError, match="ofullständigt"):
        api.structured(task="A3", tier=ModelTier.STRONG, system="", user_content="", schema={})


def test_tool_loop_returns_results_and_enforces_call_limit() -> None:
    handled: list[dict[str, Any]] = []
    tool = ToolSpec(
        "lookup",
        "Read one voucher",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        lambda args: handled.append(args) or {"voucher": "A1"},
    )
    first = reply(
        output_text="", output=[SimpleNamespace(type="function_call", call_id="c1", name="lookup", arguments="{}")]
    )
    second = reply(
        output_text="", output=[SimpleNamespace(type="function_call", call_id="c2", name="lookup", arguments="{}")]
    )
    api, responses = provider([first, second])
    with pytest.raises(ProviderError, match="verktygsgränsen"):
        api.run_tools(
            task="A5",
            tier=ModelTier.STRONG,
            system="rules",
            user_content="question",
            tools=[tool],
            schema={"type": "object"},
            budget=ToolBudget(max_tool_calls=1, max_iterations=3),
            max_tokens=900,
        )
    assert handled == [{}]
    assert responses.calls[0]["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Read one voucher",
            "parameters": tool.input_schema,
            "strict": True,
        }
    ]
    assert responses.calls[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "c1",
        "output": json.dumps({"voucher": "A1"}, ensure_ascii=False),
    }
    assert responses.calls[1]["tool_choice"] == "none"
    assert len(responses.calls) == 2


def test_unsupported_json_schema_limits_are_not_sent_to_openai() -> None:
    api, responses = provider([reply()])
    schema = {
        "type": "object",
        "properties": {"claims": {"type": "array", "maxItems": 12, "items": {"type": "string"}}},
        "required": ["claims"],
        "additionalProperties": False,
    }
    api.structured(task="A3", tier=ModelTier.STRONG, system="", user_content="", schema=schema)
    assert "maxItems" not in responses.calls[0]["text"]["format"]["schema"]["properties"]["claims"]
    assert schema["properties"]["claims"]["maxItems"] == 12


def test_openai_requires_key_before_constructing_client() -> None:
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider(OpenAIConfig(api_key=None))
