"""Typed application settings, validated at startup (fail fast)."""

import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]
TRACING_VARIABLES = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    environment: Environment
    database_url: SecretStr
    session_secret: SecretStr = Field(min_length=32)
    frontend_origin: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)

    # LLM gateway (AMD-12). "disabled" runs every investigation in deterministic mode.
    llm_provider: Literal["disabled", "anthropic"] = "disabled"
    anthropic_api_key: SecretStr | None = None
    llm_model: str = Field(default="claude-opus-5-5", min_length=1, max_length=100)
    llm_effort: Literal["low", "medium", "high"] = "low"
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=8000, ge=1024, le=64000)
    llm_max_calls: int = Field(default=40, ge=1, le=500)  # NFR-10, per investigation
    llm_max_tokens: int = Field(default=200_000, ge=1000, le=10_000_000)
    llm_deadline_seconds: float = Field(default=300.0, gt=0, le=3600)

    # Investigation workflow limits (AMD-12).
    investigation_llm_tools_per_incident: int = Field(default=6, ge=0, le=20)
    investigation_evidence_per_incident: int = Field(default=40, ge=10, le=200)
    investigation_tool_rounds: int = Field(default=2, ge=1, le=5)
    investigation_recursion_limit: int = Field(default=120, ge=10, le=1000)
    investigation_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    investigation_starts_per_hour: int = Field(default=20, ge=1, le=1000)  # SEC-20

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the postgresql+psycopg:// scheme")
        return value

    @field_validator("frontend_origin")
    @classmethod
    def _origin_only(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("FRONTEND_ORIGIN must be an absolute http(s) origin")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("FRONTEND_ORIGIN must not contain a path, query or fragment")
        return f"{parts.scheme}://{parts.netloc}"

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.environment == "production" and not self.frontend_origin.startswith("https://"):
            raise ValueError("FRONTEND_ORIGIN must use https in production")
        return self

    @model_validator(mode="after")
    def _llm_rules(self) -> Settings:
        if self.llm_provider == "anthropic" and self.anthropic_api_key is None:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        # LangGraph ships a tracing client; investigation data must never leave that way.
        enabled = [
            name
            for name in TRACING_VARIABLES
            if os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}
        ]
        if enabled:
            raise ValueError(f"tracing must stay disabled: unset {', '.join(enabled)}")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def session_cookie_name(self) -> str:
        # The __Host- prefix requires Secure, which browsers only honour over https.
        return "__Host-root_session" if self.is_production else "root_session"

    @property
    def csrf_cookie_name(self) -> str:
        return "__Host-root_csrf" if self.is_production else "root_csrf"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
