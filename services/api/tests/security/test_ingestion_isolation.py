"""Tenant isolation of ingested records at the database layer (T-18 for Phase 3 tables)."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, text

from tests import ingestion_fixtures as fx
from tests.conftest import DatabaseUrls
from tests.helpers import create_tenant, login, run_jobs, upload

RECORD_TABLES = (
    "dataset_files",
    "products",
    "customers",
    "order_lines",
    "returns",
    "validation_issues",
    "idempotency_keys",
    "analysis_revisions",
    "customer_segments",
    "incidents",
    "evidence_items",
)


@pytest.fixture
def app_engine(db_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(db_urls.app)
    yield engine
    engine.dispose()


async def test_records_are_invisible_outside_their_business(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine, app_engine: Engine
) -> None:
    alice = create_tenant(owner_engine, "alice")
    bob = create_tenant(owner_engine, "bob")
    await login(client, alice.email)
    response = await upload(client, alice.business_id, fx.dataset())
    assert response.status_code == 202
    await run_jobs(app)

    with app_engine.connect() as conn:
        for table in RECORD_TABLES:  # no tenant context at all
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table
    with app_engine.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.current_business_id', :b, true)"),
            {"b": str(bob.business_id)},
        )
        for table in RECORD_TABLES:  # another tenant's context
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table
        with pytest.raises(Exception, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO products (business_id, dataset_id, product_id, product_name, "
                    "category,"
                    " unit_cost, list_price, source_row) VALUES (:b, :d, 'X', 'x', 'x', 1, 1, 2)"
                ),
                {"b": str(alice.business_id), "d": response.json()["id"]},
            )
    with app_engine.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.current_business_id', :b, true)"),
            {"b": str(alice.business_id)},
        )
        assert conn.scalar(text("SELECT count(*) FROM order_lines")) == 4
