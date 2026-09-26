"""Provider switching and safe defaults, without external requests."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

from redovisningai.ai import factory
from redovisningai.ai.factory import build_provider
from redovisningai.ai.providers.base import FailoverProvider, ProviderError
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


_AI_ENV = (
    "RAI_AI_ENABLED",
    "RAI_AI_PLATFORM",
    "RAI_AI_SECONDARY_PLATFORM",
    "RAI_AI_TEST_MODE",
    "ANTHROPIC_API_KEY",
    "RAI_ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "RAI_OPENAI_API_KEY",
)


def _clean_env(monkeypatch, **values: str) -> Settings:  # type: ignore[no-untyped-def]
    """Settings som i en ny installation: bara de angivna miljövariablerna, ingen .env."""
    for name in _AI_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_ai_is_on_by_default_with_claude_first_and_openai_as_reserve(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.invalid")
    s = _clean_env(monkeypatch, ANTHROPIC_API_KEY="test-only", OPENAI_API_KEY="test-only")
    assert s.ai_requested and not s.ai_test_mode
    assert s.ai_platforms() == ("anthropic", "openai")
    provider = build_provider(s)
    assert isinstance(provider, FailoverProvider)
    assert [p.name for p in provider.providers] == ["claude-anthropic", "openai"]
    claude = provider.providers[0].config
    # Nyckeln från .env/miljön och Anthropics egen adress – aldrig ANTHROPIC_BASE_URL från miljön.
    assert claude.api_key == "test-only" and claude.base_url == "https://api.anthropic.com"
    assert claude.max_retries == 2 and claude.refusal_fallback_model == "claude-opus-4-8"
    assert claude.timeout_s == 300


def test_only_openai_key_uses_openai_alone(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    s = _clean_env(monkeypatch, OPENAI_API_KEY="test-only")
    assert s.ai_platforms() == ("openai", None)
    provider = build_provider(s)
    assert provider is not None and provider.name == "openai"


def test_no_key_or_switched_off_uses_rules(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    s = _clean_env(monkeypatch)
    assert not s.ai_requested and build_provider(s) is None
    s = _clean_env(monkeypatch, ANTHROPIC_API_KEY="test-only", RAI_AI_ENABLED="false")
    assert not s.ai_requested and build_provider(s) is None
    assert _clean_env(monkeypatch, RAI_AI_ENABLED="auto").ai_enabled is None
    assert _clean_env(monkeypatch, RAI_AI_ENABLED="true").ai_enabled is True


def test_reserve_can_be_turned_off_and_a_missing_key_does_not_stop_the_other(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_optional_adapters(monkeypatch)
    s = _clean_env(
        monkeypatch, ANTHROPIC_API_KEY="test-only", OPENAI_API_KEY="test-only", RAI_AI_SECONDARY_PLATFORM="none"
    )
    assert s.ai_platforms() == ("anthropic", None)
    # OpenAI vald som primär men nyckeln saknas: Claude (reserv) används och felet redovisas.
    s = _clean_env(monkeypatch, ANTHROPIC_API_KEY="test-only", RAI_AI_PLATFORM="openai")
    items = factory.configured_providers(s)
    assert [(i.role, i.platform, i.provider is not None) for i in items] == [
        ("primär", "openai", False),
        ("reserv", "anthropic", True),
    ]
    assert "OPENAI_API_KEY" in (items[0].problem or "")
    provider = build_provider(s)
    assert provider is not None and provider.name == "claude-anthropic"


def test_check_providers_runs_json_and_tool_checks_without_customer_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = _clean_env(monkeypatch, RAI_AI_ENABLED="true", RAI_AI_PLATFORM="fake")
    (result,) = factory.check_providers(s)
    assert result["ok"] and result["role"] == "primär" and result["label"] == "Testleverantör"
    assert [c["name"] for c in result["checks"]] == ["Svar i JSON-format", "Läsverktyg (som AI-analytikern)"]
    assert all(c["ok"] for c in result["checks"]) and result["checks"][1]["tool_calls"] == 1


def test_check_reports_provider_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Broken:
        name = "claude-anthropic"
        region = None

        def structured(self, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderError("Claude HTTP 401: invalid x-api-key")

        run_tools = structured

    monkeypatch.setattr(factory, "_provider", lambda platform, region, s: Broken())
    s = _clean_env(monkeypatch, ANTHROPIC_API_KEY="test-only", RAI_AI_SECONDARY_PLATFORM="none")
    (result,) = factory.check_providers(s, tools=False)
    assert not result["ok"]
    assert result["checks"][0]["error"] == "Claude HTTP 401: invalid x-api-key"
