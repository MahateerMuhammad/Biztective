"""Integration: a read-only PostgreSQL source feeds the same ingestion path as CSV uploads."""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from typing import Any

import httpx
import psycopg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.main import create_app
from app.sources.postgres import Target, connect
from scripts.generate_dataset import Options, generate_with_manifest
from tests.conftest import DatabaseUrls, make_settings
from tests.helpers import Tenant, create_tenant, csrf_headers, ingest_files, login, run_jobs

pytestmark = pytest.mark.usefixtures("clean_db")
READER = "root_src_reader"
READER_PASSWORD = uuid.uuid4().hex
SCHEMA = "shop"


def _options() -> Options:
    return Options(
        seed=7,
        weeks=26,
        customers=600,
        products=60,
        orders_per_day=20,
        start=date(2026, 1, 5),
        noise=0.0,
        pii_column=False,
        scenarios=("discount_spike",),
        spike_return_rate=0.6,
    )


@pytest.fixture(scope="session")
def generated() -> dict[str, bytes]:
    files, _ = generate_with_manifest(_options())
    return {name.removesuffix(".csv"): body for name, body in files.items()}


@pytest.fixture(scope="session")
def source_db(admin_url: str, generated: dict[str, bytes]) -> Iterator[dict[str, Any]]:
    name = f"root_src_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
        conn.execute(text(f"DROP ROLE IF EXISTS {READER}"))
        conn.execute(text(f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PASSWORD}'"))
    url = make_url(admin_url).set(database=name)
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
        for kind, body in generated.items():
            header = body.decode().splitlines()[0].split(",")
            conn.execute(
                sql.SQL("CREATE TABLE {}.{} ({})").format(
                    sql.Identifier(SCHEMA),
                    sql.Identifier(kind),
                    sql.SQL(", ").join(
                        sql.SQL("{} text").format(sql.Identifier(c)) for c in header
                    ),
                )
            )
            copy_sql = sql.SQL("COPY {}.{} FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                sql.Identifier(SCHEMA), sql.Identifier(kind)
            )
            with conn.cursor().copy(copy_sql) as copy:
                copy.write(body)
        # Generous grants: read-only enforcement must come from the session, not privileges.
        conn.execute(f"GRANT ALL ON SCHEMA {SCHEMA} TO {READER}")
        conn.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA {SCHEMA} TO {READER}")
    try:
        yield {"host": url.host, "port": url.port or 5432, "database": name}
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f"DROP ROLE IF EXISTS {READER}"))
        admin.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def source_app(db_urls: DatabaseUrls, source_db: dict[str, Any]) -> AsyncIterator[FastAPI]:
    settings = make_settings(
        db_urls.app, source_db_allowed_hosts=source_db["host"], source_db_sslmode="disable"
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


async def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _body(source_db: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "host": source_db["host"],
        "port": source_db["port"],
        "database": source_db["database"],
        "username": READER,
        "password": READER_PASSWORD,
        "schema": SCHEMA,
        **extra,
    }


TABLES = {
    "orders": "orders",
    "products": "products",
    "customers": "customers",
    "returns": "returns",
}


async def _owner(client: httpx.AsyncClient, engine: Engine, label: str) -> Tenant:
    tenant = create_tenant(engine, label)
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


async def _weekly(client: httpx.AsyncClient, tenant: Tenant, dataset_id: str) -> Any:
    response = await client.get(
        f"/api/v1/businesses/{tenant.business_id}/datasets/{dataset_id}/metrics",
        params={"granularity": "week"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_sync_matches_csv_upload(
    source_app: FastAPI,
    owner_engine: Engine,
    source_db: dict[str, Any],
    generated: dict[str, bytes],
) -> None:
    async with await _client(source_app) as up, await _client(source_app) as db_client:
        uploader = await _owner(up, owner_engine, "csv")
        uploaded = await ingest_files(source_app, up, uploader.business_id, generated)
        assert uploaded["status"] == "ready", uploaded

        owner = await _owner(db_client, owner_engine, "pg")
        base = f"/api/v1/businesses/{owner.business_id}/sources"
        headers = await csrf_headers(db_client)
        tested = await db_client.post(f"{base}/test", json=_body(source_db), headers=headers)
        assert tested.status_code == 200, tested.text
        assert set(TABLES.values()) <= set(tested.json()["tables"])

        created = await db_client.post(base, json=_body(source_db, tables=TABLES), headers=headers)
        assert created.status_code == 201, created.text
        assert "password" not in created.text
        assert READER_PASSWORD not in created.text
        source_id = created.json()["id"]

        synced = await db_client.post(
            f"{base}/{source_id}/sync", json={"password": READER_PASSWORD}, headers=headers
        )
        assert synced.status_code == 200, synced.text
        assert synced.json()["status"] == "created"
        dataset_id = synced.json()["dataset_id"]
        await run_jobs(source_app)
        detail = (
            await db_client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{dataset_id}")
        ).json()
        assert detail["status"] == "ready", detail

        # DUAL-SOURCE EQUIVALENCE: identical weekly metrics from both sources.
        assert await _weekly(db_client, owner, dataset_id) == await _weekly(
            up, uploader, str(uploaded["id"])
        )

        again = await db_client.post(
            f"{base}/{source_id}/sync", json={"password": READER_PASSWORD}, headers=headers
        )
        assert again.status_code == 200
        assert again.json() == {
            "dataset_id": dataset_id,
            "status": "unchanged",
        }
        listed = (await db_client.get(base)).json()
        assert listed[0]["last_sync_status"] == "succeeded"
        assert listed[0]["last_dataset_id"] == dataset_id
        with owner_engine.connect() as conn:
            source_type = conn.execute(
                text("SELECT source_type FROM datasets WHERE id = :i"), {"i": dataset_id}
            ).scalar_one()
        assert source_type == "postgres"

        # Cross-business: the uploader cannot see or sync this connection.
        foreign = await up.post(
            f"/api/v1/businesses/{uploader.business_id}/sources/{source_id}/sync",
            json={"password": READER_PASSWORD},
            headers=await csrf_headers(up),
        )
        assert foreign.status_code == 404


async def test_wrong_password_and_missing_table(
    source_app: FastAPI, owner_engine: Engine, source_db: dict[str, Any]
) -> None:
    async with await _client(source_app) as client:
        owner = await _owner(client, owner_engine, "errs")
        base = f"/api/v1/businesses/{owner.business_id}/sources"
        headers = await csrf_headers(client)
        bad = _body(source_db, password="wrong-secret-value")
        response = await client.post(f"{base}/test", json=bad, headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "source_auth_failed"
        assert "wrong-secret-value" not in response.text

        missing = await client.post(
            base, json=_body(source_db, tables={**TABLES, "returns": "refunds"}), headers=headers
        )
        assert missing.status_code == 422
        assert missing.json()["code"] == "source_missing_table"


async def test_private_host_blocked_without_allowlist(
    client: httpx.AsyncClient, owner_engine: Engine, source_db: dict[str, Any]
) -> None:
    owner = await _owner(client, owner_engine, "ssrf")
    response = await client.post(
        f"/api/v1/businesses/{owner.business_id}/sources/test",
        json=_body(source_db),
        headers=await csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "source_blocked_address"


def test_session_is_read_only(source_db: dict[str, Any]) -> None:
    target = Target(
        host=source_db["host"],
        port=source_db["port"],
        database=source_db["database"],
        username=READER,
        password=READER_PASSWORD,
        sslmode="disable",
        allowed_hosts=frozenset({source_db["host"]}),
    )
    with connect(target) as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("INSERT INTO shop.products (product_id) VALUES ('x')")
