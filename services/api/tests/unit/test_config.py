import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID = {
    "ENVIRONMENT": "local",
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/root",
    "SESSION_SECRET": "s" * 32,
    "FRONTEND_ORIGIN": "http://localhost:3000",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> pytest.MonkeyPatch:
    monkeypatch.chdir(str(tmp_path))  # no stray .env file is read
    for key in (*VALID, "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _load(env: pytest.MonkeyPatch, **values: str) -> Settings:
    for key, value in {**VALID, **values}.items():
        env.setenv(key, value)
    return Settings()


def test_valid_local_settings(env: pytest.MonkeyPatch) -> None:
    settings = _load(env)
    assert settings.frontend_origin == "http://localhost:3000"
    assert settings.session_cookie_name == "root_session"
    assert settings.csrf_cookie_name == "root_csrf"
    assert not settings.is_production


@pytest.mark.parametrize(
    "missing", ["ENVIRONMENT", "DATABASE_URL", "SESSION_SECRET", "FRONTEND_ORIGIN"]
)
def test_missing_required_variable_fails_fast(env: pytest.MonkeyPatch, missing: str) -> None:
    for key, value in VALID.items():
        if key != missing:
            env.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", "postgresql://u:p@localhost/root"),
        ("SESSION_SECRET", "too-short"),
        ("FRONTEND_ORIGIN", "http://localhost:3000/app"),
        ("FRONTEND_ORIGIN", "localhost:3000"),
        ("ENVIRONMENT", "staging"),
    ],
)
def test_invalid_values_rejected(env: pytest.MonkeyPatch, key: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _load(env, **{key: value})


def test_production_requires_https_origin(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _load(env, ENVIRONMENT="production", FRONTEND_ORIGIN="http://app.example.com")


def test_production_uses_host_prefixed_cookies(env: pytest.MonkeyPatch) -> None:
    settings = _load(env, ENVIRONMENT="production", FRONTEND_ORIGIN="https://app.example.com/")
    assert settings.frontend_origin == "https://app.example.com"
    assert settings.session_cookie_name == "__Host-root_session"
    assert settings.csrf_cookie_name == "__Host-root_csrf"


def test_database_url_is_secret_in_repr(env: pytest.MonkeyPatch) -> None:
    settings = _load(env)
    assert "u:p@" not in repr(settings)
    assert "s" * 32 not in repr(settings)
