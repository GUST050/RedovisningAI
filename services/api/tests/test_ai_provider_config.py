"""Provider switching and safe defaults, without external requests."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from redovisningai.ai import factory
from redovisningai.ai.factory import build_provider
from redovisningai.ai.providers.base import FailoverProvider, ModelTier, ProviderError
from redovisningai.config import Settings


def _stub_optional_adapters(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class OpenAIStub:
        name = "openai"
        region = None

        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            if not config.api_key:
                raise ProviderError("OpenAI kräver OPENAI_API_KEY")
            self.config = config

    class AnthropicStub:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            self.config = config
            self.name = f"claude-{config.platform}"
            self.region = config.region

    monkeypatch.setattr(factory, "OpenAIProvider", OpenAIStub)
    monkeypatch.setattr(factory, "AnthropicProvider", AnthropicStub)


def test_openai_can_be_primary_with_claude_secondary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    settings = Settings(
        ai_enabled=True,
        ai_platform="openai",
        ai_secondary_platform="anthropic",
        ai_test_mode=False,
    )
    provider = build_provider(settings)
    assert isinstance(provider, FailoverProvider)
    assert [p.name for p in provider.providers] == ["openai", "claude-anthropic"]


def test_claude_can_be_primary_with_openai_secondary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    settings = Settings(
        ai_enabled=True,
        ai_platform="anthropic",
        ai_secondary_platform="openai",
        ai_test_mode=False,
    )
    provider = build_provider(settings)
    assert isinstance(provider, FailoverProvider)
    assert [p.name for p in provider.providers] == ["claude-anthropic", "openai"]


def test_test_mode_prevents_second_paid_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    settings = Settings(ai_enabled=True, ai_platform="openai", ai_secondary_platform="fake", ai_test_mode=True)
    provider = build_provider(settings)
    assert provider is not None and provider.name == "openai"


def test_test_mode_disables_claude_retries_and_refusal_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    provider = build_provider(Settings(ai_enabled=True, ai_platform="anthropic", ai_test_mode=True))
    assert provider is not None and provider.config.max_retries == 0
    assert provider.config.refusal_fallback_model is None


@pytest.mark.parametrize(("platform", "key"), [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
def test_test_mode_runs_every_tier_on_the_cheap_test_model(monkeypatch, platform: str, key: str) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv(key, "test-only")
    settings = Settings(
        ai_enabled=True,
        ai_platform=platform,
        ai_test_mode=True,
        ai_test_model="cheap-test-model",
        openai_model_strong="expensive-openai",
        ai_model_strong="expensive-claude",
    )
    provider = build_provider(settings)
    assert provider is not None
    assert {provider.config.models[tier] for tier in ModelTier} == {"cheap-test-model"}


def test_full_mode_ignores_the_test_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    settings = Settings(
        ai_enabled=True,
        ai_platform="openai",
        ai_test_mode=False,
        ai_test_model="cheap-test-model",
        openai_model_strong="expensive-openai",
    )
    provider = build_provider(settings)
    assert provider is not None and provider.config.models[ModelTier.STRONG] == "expensive-openai"


def test_missing_key_or_unknown_provider_fails_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert build_provider(Settings(ai_enabled=True, ai_platform="openai")) is None
    assert build_provider(Settings(ai_enabled=True, ai_platform="mystery")) is None


def test_test_monthly_cap_is_lower_than_org_budget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    org_id = uuid.uuid4()
    state = SimpleNamespace(used=19_999)

    class Session:
        def get(self, *args):  # type: ignore[no-untyped-def]
            return SimpleNamespace(ai_monthly_token_budget=5_000_000)

        def scalar(self, *args):  # type: ignore[no-untyped-def]
            return state.used

    @contextmanager
    def session(*args):  # type: ignore[no-untyped-def]
        yield Session()

    monkeypatch.setattr(factory, "tenant_session", session)
    budget = factory.DbBudget(org_id, monthly_cap=20_000)
    assert budget.allow(str(org_id), "A3")
    state.used = 20_000
    assert not budget.allow(str(org_id), "A3")
