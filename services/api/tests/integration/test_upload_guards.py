"""Upload-time file checks, column mapping checkpoint, PII handling and duplicate semantics."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.ingestion import contract
from tests import ingestion_fixtures as fx
from tests.helpers import Tenant, create_tenant, csrf_headers, login, run_jobs, upload


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


async def post_files(
    client: httpx.AsyncClient, tenant: Tenant, files: dict[str, tuple[str, bytes, str]]
) -> httpx.Response:
    headers = {**await csrf_headers(client), "Idempotency-Key": uuid.uuid4().hex}
    return await client.post(
        f"/api/v1/businesses/{tenant.business_id}/datasets", headers=headers, files=files
    )


def as_parts(**replace: tuple[str, bytes, str]) -> dict[str, tuple[str, bytes, str]]:
    parts = {k: (f"{k}.csv", v, "text/csv") for k, v in fx.dataset().items()}
    parts.update(replace)
    return parts


@pytest.mark.parametrize(
    ("part", "status", "code"),
    [
        (("orders.csv", b"", "text/csv"), 422, "empty_file"),
        (("orders.csv", b"   \n", "text/csv"), 422, "empty_file"),
        (("orders.csv", fx.ORDERS.splitlines()[0].encode() + b"\n", "text/csv"), 422, "empty_file"),
        (("orders.xlsx", fx.ORDERS.encode(), "text/csv"), 415, "invalid_file_type"),
        (("orders.csv", fx.ORDERS.encode(), "application/pdf"), 415, "invalid_file_type"),
        (("orders.csv", b"PK\x03\x04" + b"\x00" * 64, "text/csv"), 422, "corrupt_file"),
        (("orders.csv", b"%PDF-1.7\n", "text/csv"), 422, "corrupt_file"),
        (("orders.csv", fx.ORDERS.encode("utf-16"), "text/csv"), 422, "corrupt_file"),
        (
            ("orders.csv", fx.ORDERS.replace("Web", "Wéb").encode("latin-1"), "text/csv"),
            422,
            "invalid_encoding",
        ),
        (("orders.csv", b"order_line_id\x00,x\n1,2\n", "text/csv"), 422, "corrupt_file"),
        (("orders.csv", b'order_line_id,"unterminated\n1,2\n', "text/csv"), 422, "invalid_csv"),
        (("orders.csv", ("," * 120 + "\n1\n").encode(), "text/csv"), 422, "invalid_csv"),
        (
            ("orders.csv", b"order_line_id,order_id\nL1,O1\n", "text/csv"),
            422,
            "missing_required_columns",
        ),
        (("orders.csv", b"order_id,ORDER ID,x\n1,2,3\n", "text/csv"), 422, "duplicate_header"),
    ],
)
async def test_unsafe_or_malformed_files_are_rejected(
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    part: tuple[str, bytes, str],
    status: int,
    code: str,
) -> None:
    response = await post_files(client, owner, as_parts(orders=part))
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert body["detail"]
    assert "Traceback" not in response.text
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM datasets")) == 0


async def test_missing_required_columns_explain_accepted_headers(
    client: httpx.AsyncClient, owner: Tenant
) -> None:
    response = await post_files(
        client, owner, as_parts(products=("products.csv", b"product_id,name\nP1,x\n", "text/csv"))
    )
    body = response.json()
    assert body["code"] == "missing_required_columns"
    fields = {e["field"]: e["message"] for e in body["errors"]}
    assert "products.category" in fields
    assert (
        "accepted headers: category, product_category, category_name" in fields["products.category"]
    )


async def test_missing_or_unexpected_parts(client: httpx.AsyncClient, owner: Tenant) -> None:
    parts = as_parts()
    del parts["returns"]
    response = await post_files(client, owner, parts)
    assert response.status_code == 422
    assert response.json()["code"] == "missing_files"
    parts = as_parts()
    parts["extra"] = ("extra.csv", b"a\n1\n", "text/csv")
    assert (await post_files(client, owner, parts)).status_code == 422
    headers = {**await csrf_headers(client), "Idempotency-Key": uuid.uuid4().hex}
    raw = await client.post(
        f"/api/v1/businesses/{owner.business_id}/datasets", headers=headers, json={"x": 1}
    )
    assert raw.status_code == 415


async def test_oversized_files_are_rejected(client: httpx.AsyncClient, owner: Tenant) -> None:
    big = fx.ORDERS.encode() + b"x" * (contract.MAX_FILE_BYTES + 1)
    response = await post_files(client, owner, as_parts(orders=("orders.csv", big, "text/csv")))
    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"
    headers = {
        **await csrf_headers(client),
        "Idempotency-Key": uuid.uuid4().hex,
        "Content-Type": "multipart/form-data; boundary=x",
        "Content-Length": str(contract.MAX_UPLOAD_BYTES + 1),
    }
    response = await client.post(
        f"/api/v1/businesses/{owner.business_id}/datasets", headers=headers, content=b"--x--"
    )
    assert response.status_code == 413


async def test_ambiguous_mapping_requires_human_confirmation(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    orders = (
        fx.ORDERS.replace("quantity,", "Qty,", 1)
        .replace("order_date,", "date,", 1)
        .replace("region\n", "region,created_at,Customer Email,Notes Internal\n", 1)
    )
    lines = orders.splitlines()
    orders = "\n".join([lines[0], *(line + ",2026-01-01,a@b.test,n" for line in lines[1:])]) + "\n"
    response = await upload(client, owner.business_id, fx.dataset(orders=orders))
    assert response.status_code == 202
    detail = response.json()
    assert detail["status"] == "awaiting_mapping"
    mapping = {f["kind"]: f["mapping"] for f in detail["files"]}["orders"]
    assert mapping["columns"]["quantity"] == {
        "source": "qty",
        "status": "proposed",
        "candidates": ["qty"],
        "required": True,
    }
    assert mapping["columns"]["order_date"]["status"] == "ambiguous"
    assert mapping["columns"]["order_date"]["candidates"] == ["date", "created_at"]
    assert mapping["pii_dropped"] == ["customer_email"]
    assert mapping["ignored"] == ["notes_internal"]
    assert await run_jobs(app) == 0  # nothing is ingested before confirmation

    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/mapping"
    incomplete = await client.post(
        base, headers=await csrf_headers(client), json={"files": {"orders": {"quantity": "qty"}}}
    )
    assert incomplete.status_code == 422
    assert any("decision required" in e["message"] for e in incomplete.json()["errors"])
    invalid = await client.post(
        base,
        headers=await csrf_headers(client),
        json={"files": {"orders": {"quantity": "qty", "order_date": "customer_email"}}},
    )
    assert invalid.status_code == 422

    confirmed = await client.post(
        base,
        headers=await csrf_headers(client),
        json={"files": {"orders": {"quantity": "qty", "order_date": "date"}}},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["status"] == "queued"
    again = await client.post(
        base,
        headers=await csrf_headers(client),
        json={"files": {"orders": {"quantity": "qty", "order_date": "date"}}},
    )
    assert again.status_code == 409

    await run_jobs(app)
    final = (
        await client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}")
    ).json()
    assert final["status"] == "ready", final
    issues = {(i["file"], i["code"]) for i in final["issues"]}
    assert {("orders", "V-040"), ("orders", "V-041")} <= issues
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT order_date FROM order_lines WHERE order_line_id='L3'")
            ).isoformat()
            == "2026-03-03"
        )
        # The PII column never reached the database, not even in the stored raw file.
        for table, column in (
            ("dataset_files", "convert_from(raw, 'UTF8')"),
            ("dataset_files", "headers::text"),
            ("validation_issues", "coalesce(excerpt, '')"),
            ("order_lines", "order_line_id"),
        ):
            leaked = conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column} LIKE '%a@b.test%'")
            )
            assert leaked == 0, table


async def test_pii_column_in_customers_is_dropped(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    customers = (
        "customer_id,full_name,email,region\n"
        "C1,Ada L,ada@example.test,north\nC2,B,b@example.test,south\nC3,C,c@example.test,east\n"
    )
    response = await upload(client, owner.business_id, fx.dataset(customers=customers))
    assert response.json()["status"] == "queued"  # exact matches only; PII needs no decision
    await run_jobs(app)
    with owner_engine.connect() as conn:
        for pattern in ("%ada@example.test%", "%Ada L%"):
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM dataset_files WHERE convert_from(raw, 'UTF8') LIKE :p"
                    ),
                    {"p": pattern},
                )
                == 0
            )
        headers = conn.scalar(text("SELECT headers FROM dataset_files WHERE kind='customers'"))
    assert headers == ["customer_id", "region"]


async def test_idempotent_retries_and_duplicate_content(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    first = await upload(client, owner.business_id, fx.dataset(), key="retry-key-0001")
    replay = await upload(client, owner.business_id, fx.dataset(), key="retry-key-0001")
    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    reuse = await upload(
        client,
        owner.business_id,
        fx.dataset(orders=fx.ORDERS.replace("L4", "L9")),
        key="retry-key-0001",
    )
    assert reuse.status_code == 409
    assert reuse.json()["code"] == "idempotency_key_reuse"
    duplicate = await upload(client, owner.business_id, fx.dataset())
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_dataset"
    assert "version 1" in duplicate.json()["detail"]
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM datasets")) == 1
        assert conn.scalar(text("SELECT count(*) FROM jobs")) == 1


async def test_concurrent_uploads_get_distinct_versions(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    variants = [fx.dataset(orders=fx.ORDERS.replace("L4,", f"L{i}4,")) for i in range(4)]
    responses = await asyncio.gather(*(upload(client, owner.business_id, v) for v in variants))
    assert sorted(r.status_code for r in responses) == [202] * 4
    assert sorted(r.json()["version"] for r in responses) == [1, 2, 3, 4]
    same = await asyncio.gather(
        *(upload(client, owner.business_id, fx.dataset()) for _ in range(3))
    )
    assert sorted(r.status_code for r in same) == [202, 409, 409]
    await run_jobs(app)
    with owner_engine.connect() as conn:
        statuses = conn.scalars(text("SELECT status FROM datasets ORDER BY version")).all()
    assert statuses == ["ready"] * 5


async def test_failure_rolls_back_every_write(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion import pipeline

    async def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated crash after staging and issue writes")

    monkeypatch.setattr(pipeline, "_price_warnings", explode)
    orders = fx.with_rows(fx.ORDERS, "B1,OB1,bad-date,C1,P1,1,25.00,0,web,north")
    response = await upload(client, owner.business_id, fx.dataset(orders=orders))
    await run_jobs(app)
    detail = (
        await client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{response.json()['id']}")
    ).json()
    assert detail["status"] == "failed"
    assert detail["error_code"] == "unexpected_error"
    assert "simulated" not in str(detail)
    with owner_engine.connect() as conn:
        written = conn.scalar(
            text(
                "SELECT (SELECT count(*) FROM products) + (SELECT count(*) FROM order_lines) + "
                "(SELECT count(*) FROM validation_issues)"
            )
        )
        job = conn.execute(text("SELECT status, attempts, last_error_code FROM jobs")).one()
    assert written == 0
    assert tuple(job) == ("failed", 1, "unexpected_error")
    # A failed version does not block re-uploading the same files.
    monkeypatch.undo()
    retry = await upload(client, owner.business_id, fx.dataset(orders=orders))
    assert retry.status_code == 202


async def test_transient_errors_are_retried(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.ingestion import pipeline

    real = pipeline._price_warnings
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))
        await real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_price_warnings", flaky)
    response = await upload(client, owner.business_id, fx.dataset())
    await run_jobs(app)
    with owner_engine.begin() as conn:  # make the backoff due now
        conn.execute(text("UPDATE jobs SET run_after = now()"))
    await run_jobs(app)
    detail = (
        await client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{response.json()['id']}")
    ).json()
    assert detail["status"] == "ready"
    with owner_engine.connect() as conn:
        job = conn.execute(text("SELECT status, attempts FROM jobs")).one()
    assert tuple(job) == ("succeeded", 2)


async def test_expired_lease_is_recovered(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    response = await upload(client, owner.business_id, fx.dataset())
    with owner_engine.begin() as conn:  # simulate a worker that died mid-job
        conn.execute(
            text(
                "UPDATE jobs SET status='running', attempts=1, "
                "lease_expires_at = now() - interval '1 minute'"
            )
        )
    assert await run_jobs(app) == 1
    detail = (
        await client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{response.json()['id']}")
    ).json()
    assert detail["status"] == "ready"


async def test_ready_dataset_is_immutable(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, db_urls: Any
) -> None:
    from sqlalchemy import create_engine

    response = await upload(client, owner.business_id, fx.dataset())
    await run_jobs(app)
    engine = create_engine(db_urls.app)
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(
                text("SELECT set_config('app.current_business_id', :b, true)"),
                {"b": str(owner.business_id)},
            )
            with pytest.raises(Exception, match="immutable"):
                conn.execute(
                    text("UPDATE datasets SET status='queued' WHERE id=:id"),
                    {"id": response.json()["id"]},
                )
        for statement in (
            "UPDATE order_lines SET quantity = 9",
            "DELETE FROM products",
            "UPDATE validation_issues SET code = 'V-000'",
        ):
            with engine.connect() as conn, conn.begin():
                conn.execute(
                    text("SELECT set_config('app.current_business_id', :b, true)"),
                    {"b": str(owner.business_id)},
                )
                with pytest.raises(Exception, match="permission denied"):
                    conn.execute(text(statement))
    finally:
        engine.dispose()


async def test_upload_quota(
    client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.datasets import router

    monkeypatch.setattr(router, "UPLOADS_PER_HOUR", 2)
    for i in range(2):
        r = await upload(
            client, owner.business_id, fx.dataset(orders=fx.ORDERS.replace("L4,", f"L{i}4,"))
        )
        assert r.status_code == 202
    limited = await upload(
        client, owner.business_id, fx.dataset(orders=fx.ORDERS.replace("L4,", "L94,"))
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "3600"


async def test_business_currency_and_timezone_settings(
    client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    base = f"/api/v1/businesses/{owner.business_id}"
    changed = await client.patch(
        base,
        headers=await csrf_headers(client),
        json={"currency": "EUR", "timezone": "Europe/Berlin"},
    )
    assert changed.status_code == 200
    assert (changed.json()["currency"], changed.json()["timezone"]) == ("EUR", "Europe/Berlin")
    bad = await client.patch(
        base, headers=await csrf_headers(client), json={"timezone": "Nowhere/X"}
    )
    assert bad.status_code == 422
    empty = await client.patch(base, headers=await csrf_headers(client), json={})
    assert empty.status_code == 422
    await upload(client, owner.business_id, fx.dataset(), currency="EUR")
    locked = await client.patch(base, headers=await csrf_headers(client), json={"currency": "USD"})
    assert locked.status_code == 409
    assert locked.json()["code"] == "currency_locked"
    tz_only = await client.patch(base, headers=await csrf_headers(client), json={"timezone": "UTC"})
    assert tz_only.status_code == 200
