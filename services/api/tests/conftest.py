"""Test harness: a disposable database per test session, migrated as the owner role.

Required environment (see README.md):
  TEST_DATABASE_ADMIN_URL  owner/superuser URL of a PostgreSQL server, e.g.
                           postgresql+psycopg://root_owner:...@127.0.0.1:5432/postgres
  TEST_APP_DB_PASSWORD     password of the pre-provisioned `root_app` role
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app
from tests.helpers import FRONTEND_ORIGIN, TEST_SESSION_SECRET

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.exit(f"{name} must be set to run the test suite (see README.md)", returncode=2)
    return value


@dataclass(frozen=True)
class DatabaseUrls:
    owner: str
    app: str


def alembic_config(url: str) -> Config:
    config = Config(os.path.join(API_DIR, "alembic.ini"))
    config.attributes["database_url"] = url
    return config


def create_disposable_database(admin_url: str, app_password: str) -> tuple[str, DatabaseUrls]:
    name = f"root_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    owner = make_url(admin_url).set(database=name)
    app = owner.set(username="root_app", password=app_password)
    return name, DatabaseUrls(
        owner=owner.render_as_string(hide_password=False),
        app=app.render_as_string(hide_password=False),
    )


def drop_database(admin_url: str, name: str) -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def admin_url() -> str:
    return _required_env("TEST_DATABASE_ADMIN_URL")


@pytest.fixture(scope="session")
def app_password() -> str:
    return _required_env("TEST_APP_DB_PASSWORD")


@pytest.fixture(scope="session")
def db_urls(admin_url: str, app_password: str) -> Iterator[DatabaseUrls]:
    name, urls = create_disposable_database(admin_url, app_password)
    try:
        command.upgrade(alembic_config(urls.owner), "head")
        yield urls
    finally:
        drop_database(admin_url, name)


@pytest.fixture(scope="session")
def owner_engine(db_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(db_urls.owner)
    yield engine
    engine.dispose()


def make_settings(database_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": SecretStr(database_url),
        "session_secret": SecretStr(TEST_SESSION_SECRET),
        "frontend_origin": FRONTEND_ORIGIN,
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture(scope="session")
def settings(db_urls: DatabaseUrls) -> Settings:
    return make_settings(db_urls.app)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):  # disposes the engine
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def clean_db(owner_engine: Engine) -> Iterator[None]:
    yield
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, sessions, auth_attempts, businesses, memberships, "
                "datasets, audit_log CASCADE"
            )
        )
