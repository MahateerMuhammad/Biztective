"""Typed application settings, validated at startup (fail fast)."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    environment: Environment
    database_url: SecretStr
    session_secret: SecretStr = Field(min_length=32)
    frontend_origin: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    db_pool_size: int = Field(default=5, ge=1, le=50)

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
