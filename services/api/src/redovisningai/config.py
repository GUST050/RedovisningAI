"""Konfiguration via miljövariabler (prefix RAI_)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None

    # AI
    ai_enabled: bool = False
    ai_platform: str = "bedrock"  # bedrock | vertex | anthropic | fake
    ai_region: str | None = "eu-north-1"
    ai_project_id: str | None = None
    ai_secondary_platform: str | None = None  # failover
    ai_secondary_region: str | None = None
    ai_model_strong: str = "claude-opus-5"
    ai_model_medium: str = "claude-opus-5"
    ai_model_small: str = "claude-opus-5"
    ai_refusal_fallback_model: str | None = "claude-opus-4-8"
    ai_trace_retention_days: int = 30

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
