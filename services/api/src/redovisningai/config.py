"""Konfiguration via miljövariabler (prefix RAI_)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_MASTER_KEY = "dev-master-key-change-me-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAI_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    # Migrationer körs som ägarrollen; applikationen som en roll utan BYPASSRLS och utan tabellägarskap.
    database_url_owner: str = "postgresql+psycopg://postgres@127.0.0.1:54329/redovisningai"
    database_url: str = "postgresql+psycopg://redovisningai_app:app@127.0.0.1:54329/redovisningai"
    app_db_password: str = "app"

    # Objektlagring
    storage_backend: str = "local"  # local | s3
    storage_path: str = "var/storage"
    s3_endpoint: str | None = None
    s3_bucket: str = "redovisningai"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "eu-north-1"
    # Huvudnyckel (KEK) för att kryptera byråernas datanycklar. I produktion: Key Vault/KMS.
    master_key: str = Field(default=DEV_MASTER_KEY)

    # Autentisering
    auth_mode: str = "dev"  # dev | oidc
    # Utvecklingsläge: användaren man automatiskt är inloggad som (ingen inloggningssida).
    # Tom sträng = kräv att användaren väljs. Används aldrig med auth_mode=oidc.
    dev_default_user: str = "anna@demobyran.se"
    # Utvecklingsläge: skapa demobyrån när API:t startar om den saknas (så att standardanvändaren
    # alltid har en byrå). Görs bara när RAI_ENV=dev och RAI_AUTH_MODE=dev.
    dev_auto_seed: bool = True
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None

    # AI. Standard: på så fort en nyckel finns – Claude (ANTHROPIC_API_KEY) först och OpenAI
    # (OPENAI_API_KEY) som reserv vid tillfälliga fel. RAI_AI_ENABLED: auto | true | false.
    ai_enabled: bool | None = None  # None = auto
    ai_platform: str = "auto"  # auto | anthropic | openai | bedrock | vertex | fake
    ai_region: str | None = "eu-north-1"
    ai_project_id: str | None = None
    ai_secondary_platform: str | None = "auto"  # failover; auto = den andra leverantören med nyckel
    ai_secondary_region: str | None = None
    ai_model_strong: str = "claude-opus-5"
    ai_model_medium: str = "claude-opus-5"
    ai_model_small: str = "claude-opus-5"
    ai_refusal_fallback_model: str | None = "claude-opus-4-8"
    ai_timeout_s: float = Field(default=300.0, gt=0)
    ai_trace_retention_days: int = 30
    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "RAI_ANTHROPIC_API_KEY")
    )
    # Nyckeln skickas bara hit – inte till en ANTHROPIC_BASE_URL som råkar finnas i miljön.
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "RAI_OPENAI_API_KEY")
    )
    openai_base_url: str | None = None
    openai_model_strong: str = "gpt-6-luna"
    openai_model_medium: str = "gpt-6-luna"
    openai_model_small: str = "gpt-6-luna"
    # Testläge för en försiktig provkörning: lågt tak, korta svar, ingen reservleverantör. I full
    # drift (standard) begränsas kostnaden av byråns månadsbudget för tokens.
    ai_test_mode: bool = False
    ai_test_monthly_token_cap: int = Field(default=20_000, ge=1)
    ai_test_max_output_tokens: int = Field(default=1_500, ge=1)
    ai_test_max_tool_calls: int = Field(default=3, ge=0)

    @field_validator("ai_enabled", mode="before")
    @classmethod
    def _auto_enabled(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and value.strip().lower() in {"", "auto"}):
            return None
        return value

    @field_validator("ai_platform", mode="before")
    @classmethod
    def _platform(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "auto"
        return value

    @field_validator("ai_secondary_platform", mode="before")
    @classmethod
    def _secondary(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            return None if value in {"", "none", "off", "false"} else value
        return value

    @field_validator("anthropic_api_key", "openai_api_key", mode="before")
    @classmethod
    def _key(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    def ai_platforms(self) -> tuple[str | None, str | None]:
        """Primär och reserv efter "auto". Claude går före OpenAI; None = ingen."""
        keys = [p for p, key in (("anthropic", self.anthropic_api_key), ("openai", self.openai_api_key)) if key]
        primary: str | None = self.ai_platform
        if primary == "auto":
            primary = keys[0] if keys else None
        secondary = self.ai_secondary_platform
        if secondary == "auto":
            secondary = next((p for p in keys if p != primary), None)
        return primary, secondary if secondary != primary else None

    @property
    def ai_requested(self) -> bool:
        """AI ska användas: uttryckligen påslaget, eller "auto" och det finns en leverantör."""
        if self.ai_enabled is False:
            return False
        return self.ai_enabled is True or self.ai_platforms()[0] is not None

    # Fortnox
    fortnox_client_id: str | None = None
    fortnox_client_secret: str | None = None
    fortnox_redirect_uri: str = "http://localhost:8000/api/connections/fortnox/callback"
    fortnox_api_base: str = "https://api.fortnox.se"
    fortnox_auth_base: str = "https://apps.fortnox.se"

    # Webb
    web_base_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]
    question_link_days: int = 14
    source_file_retention_months: int = 24

    def production_problems(self) -> list[str]:
        """Inställningar som aldrig får användas i produktion."""
        if self.env != "prod":
            return []
        problems = []
        if self.auth_mode == "dev":
            problems.append("RAI_AUTH_MODE=dev (utvecklingsinloggning) är inte tillåtet i produktion")
        if self.master_key == DEV_MASTER_KEY or len(self.master_key) < 32:
            problems.append("RAI_MASTER_KEY saknas eller är för kort")
        if self.app_db_password == "app" or ":app@" in self.database_url:
            problems.append("Databaslösenordet för applikationsrollen är standardvärdet")
        if self.auth_mode == "oidc" and not (self.oidc_issuer and self.oidc_audience and self.oidc_jwks_url):
            problems.append("OIDC kräver RAI_OIDC_ISSUER, RAI_OIDC_AUDIENCE och RAI_OIDC_JWKS_URL")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
