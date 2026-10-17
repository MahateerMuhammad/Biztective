import httpx
from alembic import command
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import create_engine, text

from app.main import create_app
from tests.conftest import (
    DatabaseUrls,
    alembic_config,
    create_disposable_database,
    drop_database,
    make_settings,
)

EXPECTED_TABLES = {
    "alembic_version",
    "analysis_configs",
    "analysis_revisions",
    "audit_log",
    "auth_attempts",
    "businesses",
    "clarifications",
    "customer_segments",
    "customers",
    "dataset_files",
    "datasets",
    "evidence_items",
    "findings",
    "hypotheses",
    "human_inputs",
    "recovery_plans",
    "recovery_plan_versions",
    "plan_decisions",
    "integrations",
    "plan_actions",
    "action_executions",
    "hypothesis_evaluations",
    "idempotency_keys",
    "incidents",
    "investigation_events",
    "investigation_evidence",
    "investigation_versions",
    "investigations",
    "jobs",
    "llm_calls",
    "memberships",
    "order_lines",
    "products",
    "returns",
    "sessions",
    "users",
    "validation_issues",
}
CHECKPOINT_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}
RLS_TABLES = EXPECTED_TABLES - {
    "alembic_version",
    "analysis_configs",
    "auth_attempts",
    "jobs",
    "sessions",
    "users",
}


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_when_database_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def _readyz(database_url: str) -> httpx.Response:
    app = create_app(make_settings(database_url))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        return await c.get("/api/v1/readyz")


async def test_readyz_503_when_database_unreachable() -> None:
    response = await _readyz("postgresql+psycopg://root_app:x@127.0.0.1:1/none")
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "unavailable"
    assert "127.0.0.1" not in response.text  # no connection details leak


async def test_readyz_503_when_connected_as_owner_role(db_urls: DatabaseUrls) -> None:
    """AMD-3: a role that bypasses RLS must never be accepted as the runtime role."""
    response = await _readyz(db_urls.owner)
    assert response.status_code == 503


def test_migrations_upgrade_downgrade_upgrade(admin_url: str, app_password: str) -> None:
    name, urls = create_disposable_database(admin_url, app_password)
    engine = create_engine(urls.owner)
    try:
        config = alembic_config(urls.owner)

        command.upgrade(config, "head")
        with engine.connect() as conn:
            tables: dict[str, bool] = dict(
                conn.execute(
                    text("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public'")
                ).all()
            )
        assert set(tables) == EXPECTED_TABLES
        assert {t for t, rls in tables.items() if rls} == RLS_TABLES
        with engine.connect() as conn:
            checkpoint_tables = set(
                conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='langgraph'"))
            )
            checkpoint_migrations = list(
                conn.scalars(text("SELECT v FROM langgraph.checkpoint_migrations ORDER BY v"))
            )
        assert checkpoint_tables == CHECKPOINT_TABLES
        assert checkpoint_migrations == list(range(len(AsyncPostgresSaver.MIGRATIONS)))

        command.downgrade(config, "base")
        with engine.connect() as conn:
            remaining = set(
                conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
            )
            functions = conn.scalar(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'audit_log_append_only'")
            )
            schemas = conn.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = 'langgraph'")
            )
        assert remaining == {"alembic_version"}
        assert functions == 0
        assert schemas == 0

        command.upgrade(config, "head")
        for target in (
            "0006",
            "0005",
            "0004",
            "0003",
            "0002",
            "0001",
        ):  # each revision downgrades on its own
            command.downgrade(config, target)
            command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
            assert conn.scalar(text("SELECT count(*) FROM analysis_configs")) == 1
    finally:
        engine.dispose()
        drop_database(admin_url, name)
