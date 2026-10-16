"""Phase 3 ingestion: upload → mapping → validation → persistence → retrieval."""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests import ingestion_fixtures as fx
from tests.helpers import Tenant, create_tenant, csrf_headers, login, run_jobs, upload


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


async def ingest(
    app: FastAPI, client: httpx.AsyncClient, tenant: Tenant, files: dict[str, bytes], **fields: str
) -> dict[str, Any]:
    response = await upload(client, tenant.business_id, files, **fields)
    assert response.status_code == 202, response.text
    await run_jobs(app)
    detail = await client.get(
        f"/api/v1/businesses/{tenant.business_id}/datasets/{response.json()['id']}"
    )
    body: dict[str, Any] = detail.json()
    return body


def counts(detail: dict[str, Any]) -> dict[tuple[str, str], int]:
    return {(i["file"], i["code"]): i["count"] for i in detail["issues"]}


async def test_valid_dataset_is_persisted_exactly(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    detail = await ingest(app, client, owner, fx.dataset())
    assert detail["status"] == "ready", detail
    assert (
        detail["products_rows"],
        detail["customers_rows"],
        detail["orders_rows"],
        detail["returns_rows"],
    ) == (3, 3, 4, 2)
    assert detail["date_min"] == "2026-03-02"
    assert detail["date_max"] == "2026-03-05"  # L4 23:30 at -05:00 is 04:30 UTC next day
    # The only finding on a short, clean dataset is the history warning.
    assert counts(detail) == {("dataset", "V-042"): 1}

    with owner_engine.connect() as conn:
        lines = {r.order_line_id: r for r in conn.execute(text("SELECT * FROM order_lines"))}
        returns = conn.execute(text("SELECT * FROM returns ORDER BY return_id")).all()
        customers = {r.customer_id: r for r in conn.execute(text("SELECT * FROM customers"))}
        products = {r.product_id: r for r in conn.execute(text("SELECT * FROM products"))}
        orphans = conn.scalar(
            text("""
            SELECT count(*) FROM order_lines o
            LEFT JOIN customers c USING (dataset_id, customer_id)
            LEFT JOIN products p USING (dataset_id, product_id)
            WHERE c.customer_id IS NULL OR p.product_id IS NULL
        """)
        )
    assert orphans == 0
    l1 = lines["L1"]
    assert (l1.order_id, l1.order_date, l1.customer_id, l1.product_id) == (
        "O1",
        date(2026, 3, 2),
        "C1",
        "P1",
    )
    assert (l1.quantity, l1.unit_price, l1.discount_amount) == (
        2,
        Decimal("25.0000"),
        Decimal("5.0000"),
    )
    assert (l1.channel, l1.region, l1.source_row) == ("web", "north", 2)
    assert l1.ordered_at == datetime(2026, 3, 2, 10, 15, tzinfo=UTC)  # business tz is UTC
    l3 = lines["L3"]
    assert (l3.discount_amount, l3.region, l3.ordered_at) == (Decimal(0), "unknown", None)
    l4 = lines["L4"]
    assert (l4.order_date, l4.ordered_at) == (
        date(2026, 3, 5),
        datetime(2026, 3, 5, 4, 30, tzinfo=UTC),
    )
    assert l4.channel == "unknown"
    assert [
        (r.return_id, r.quantity_returned, r.refund_amount, r.reason_code, r.reason_text)
        for r in returns
    ] == [
        ("R1", 1, Decimal("22.5000"), "damaged", "Box crushed"),
        ("R2", 2, Decimal("13.0000"), "changed_mind", None),
    ]
    assert (customers["C2"].acquisition_channel, customers["C3"].signup_date) == (None, None)
    assert customers["C1"].region == "north"
    assert (products["P2"].unit_cost, products["P1"].category) == (Decimal("4.5000"), "home decor")

    files = {f["kind"]: f for f in detail["files"]}
    assert files["orders"]["rows_total"] == 4
    assert files["orders"]["rows_accepted"] == 4
    profile = files["orders"]["profile"]
    assert profile["rows"] == 4
    assert profile["columns"]["quantity"]["sum"] == 7
    assert profile["columns"]["customer_id"]["distinct"] == 3
    assert files["products"]["profile"]["columns"]["list_price"]["min"] == "6.5000"


async def test_records_are_retrievable_with_pagination(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    detail = await ingest(app, client, owner, fx.dataset())
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/records/orders"
    first = (await client.get(base, params={"limit": 3})).json()
    assert [r["order_line_id"] for r in first["items"]] == ["L1", "L2", "L3"]
    assert first["items"][0]["unit_price"] == "25.0000"
    assert first["items"][0]["source_row"] == 2
    rest = (await client.get(base, params={"limit": 3, "cursor": first["next_cursor"]})).json()
    assert [r["order_line_id"] for r in rest["items"]] == ["L4"]
    assert rest["next_cursor"] is None


async def test_optional_columns_may_be_absent(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    files = fx.dataset(
        customers="customer_id\nC1\nC2\nC3\n",
        orders=(
            "order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price\n"
            "L1,O1,2026-03-02,C1,P1,2,25.00\nL2,O1,2026-03-02,C1,P2,1,12.00\n"
            "L3,O2,2026-03-03,C2,P3,3,6.50\nL4,O3,2026-03-04,C3,P1,1,24.00\n"
        ),
        returns="return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code\n",
    )
    detail = await ingest(app, client, owner, files)
    assert detail["status"] == "ready", detail
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT discount_amount, channel, region FROM order_lines "
                "WHERE order_line_id = 'L1'"
            )
        ).one()
    assert tuple(row) == (Decimal(0), "unknown", "unknown")


@pytest.mark.parametrize(
    ("overrides", "file", "code"),
    [
        (
            {"orders": fx.with_rows(fx.ORDERS, "L5,O4,2026-03-05,C404,P1,1,25.00,0,web,north")},
            "orders",
            "V-030",
        ),
        (
            {"orders": fx.with_rows(fx.ORDERS, "L5,O4,2026-03-05,C1,P404,1,25.00,0,web,north")},
            "orders",
            "V-031",
        ),
        (
            {"returns": fx.with_rows(fx.RETURNS, "R3,L404,2026-03-06,1,1.00,other,")},
            "returns",
            "V-032",
        ),
        (
            {"orders": fx.with_rows(fx.ORDERS, "L5,O4,2026-03-05,C1,P1,1,25.00,30.00,web,north")},
            "orders",
            "V-033",
        ),
        (
            {"returns": fx.with_rows(fx.RETURNS, "R3,L2,2026-03-01,1,1.00,other,")},
            "returns",
            "V-034",
        ),
        (
            {"returns": fx.with_rows(fx.RETURNS, "R3,L1,2026-03-06,2,1.00,defective,")},
            "returns",
            "V-035",
        ),
        (
            {"returns": fx.with_rows(fx.RETURNS, "R3,L1,2026-03-06,1,30.00,defective,")},
            "returns",
            "V-036",
        ),
        (
            {"orders": fx.with_rows(fx.ORDERS, "L5,O1,2026-03-03,C1,P3,1,6.50,0,web,north")},
            "orders",
            "V-037",
        ),
    ],
)
async def test_relational_rules_reject_rows(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    overrides: dict[str, str],
    file: str,
    code: str,
) -> None:
    # Pad with clean rows so one bad row stays under the 5% rejection threshold.
    padding = [f"L{9000 + i},O{9000 + i},2026-03-06,C2,P2,1,12.00,0,web,south" for i in range(60)]
    overrides.setdefault("orders", fx.ORDERS)
    overrides["orders"] = fx.with_rows(overrides["orders"], *padding)
    overrides.setdefault("returns", fx.RETURNS)
    overrides["returns"] = fx.with_rows(
        overrides["returns"], *[f"RP{i},L{9000 + i},2026-03-07,1,1.00,other," for i in range(60)]
    )
    detail = await ingest(app, client, owner, fx.dataset(**overrides))
    assert detail["status"] == "ready", detail
    assert counts(detail)[(file, code)] >= 1
    issues = (
        await client.get(
            f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/issues",
            params={"code": code},
        )
    ).json()["items"]
    assert issues
    assert issues[0]["row_number"] >= 2
    with owner_engine.connect() as conn:
        orphan_returns = conn.scalar(
            text(
                "SELECT count(*) FROM returns r "
                "LEFT JOIN order_lines o USING (dataset_id, order_line_id) "
                "WHERE o.order_line_id IS NULL"
            )
        )
        over_returned = conn.scalar(
            text("""
            SELECT count(*) FROM (
                SELECT r.order_line_id, sum(r.quantity_returned) q, max(o.quantity) oq
                FROM returns r JOIN order_lines o USING (dataset_id, order_line_id)
                                  GROUP BY r.order_line_id) t WHERE q > oq""")
        )
    assert (orphan_returns, over_returned) == (0, 0)


async def test_cascade_rejection_follows_references(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    """An invalid customer row makes its order lines and their returns fail too."""
    padding = [f"L{9000 + i},O{9000 + i},2026-03-06,C2,P2,1,12.00,0,web,south" for i in range(80)]
    files = fx.dataset(
        customers=fx.CUSTOMERS.replace("C1,2025-12-01", "C1,2025-13-01"),
        orders=fx.with_rows(fx.ORDERS, *padding),
    )
    detail = await ingest(app, client, owner, files)
    c = counts(detail)
    assert c[("customers", "V-013")] == 1
    assert c[("orders", "V-030")] == 2  # L1 and L2 belong to C1
    assert c[("returns", "V-032")] == 1  # R1 refers to L1
    assert detail["status"] == "rejected"  # 1 of 3 customers rejected > 5%
    assert c[("customers", "V-099")] == 1


async def test_duplicate_keys(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> None:
    products = fx.with_rows(
        fx.PRODUCTS,
        "P3,Echo Tray,Kitchen,2.25,6.50",  # exact duplicate → warning, kept once
        *[f"P{100 + i},Item {i},Kitchen,1.00,2.00" for i in range(40)],
        "P100,Item 0 changed,Kitchen,1.00,2.00",  # conflicting duplicate → both rejected
    )
    detail = await ingest(app, client, owner, fx.dataset(products=products))
    c = counts(detail)
    assert c[("products", "V-021")] == 1
    assert c[("products", "V-020")] == 2
    files = {f["kind"]: f for f in detail["files"]}
    assert files["products"]["rows_duplicate"] == 1
    assert files["products"]["rows_rejected"] == 2
    assert files["products"]["rows_accepted"] == 3 + 40 - 1


async def test_value_validation_and_time_interpretation(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    bad_rows = [
        "B1,OB1,03/04/2026,C1,P1,1,25.00,0,web,north",  # ambiguous format
        "B2,OB2,2026-02-30,C1,P1,1,25.00,0,web,north",  # impossible date
        "B3,OB3,2026-03-08T02:30:00,C1,P1,1,25.00,0,web,north",  # nonexistent in New York
        "B4,OB4,2030-01-01,C1,P1,1,25.00,0,web,north",  # future
        "B5,OB5,2026-03-02,C1,P1,0,25.00,0,web,north",  # impossible quantity
        "B6,OB6,2026-03-02,C1,P1,-2,25.00,0,web,north",  # negative quantity
        "B7,OB7,2026-03-02,C1,P1,1,-5.00,0,web,north",  # negative price
        'B8,OB8,2026-03-02,C1,P1,1,"1,250.00",0,web,north',  # thousands separator
        "B9,OB9,2026-03-02,C1,P1,1,25.123456,0,web,north",  # too many decimals
        "bad id!,OB10,2026-03-02,C1,P1,1,25.00,0,web,north",  # identifier format
        ",OB11,2026-03-02,C1,P1,1,25.00,0,web,north",  # required value empty
        "B12,OB12,2026-03-02,C1,P1",  # wrong field count
    ]
    padding = [f"L{9000 + i},O{9000 + i},2026-03-06,C2,P2,1,12.00,0,web,south" for i in range(300)]
    orders = fx.with_rows(
        fx.ORDERS, "A1,OA1,2025-11-02T01:30:00,C1,P1,1,25.00,0,web,north", *bad_rows, *padding
    )
    detail = await ingest(
        app, client, owner, fx.dataset(orders=orders), source_timezone="America/New_York"
    )
    assert detail["status"] == "ready", detail
    assert detail["source_timezone"] == "America/New_York"
    c = counts(detail)
    assert c[("orders", "V-013")] == 4
    assert c[("orders", "V-014")] == 5
    assert c[("orders", "V-012")] == 1
    assert c[("orders", "V-011")] == 1
    assert c[("orders", "V-010")] == 1
    assert c[("orders", "V-044")] == 1  # ambiguous fall-back hour, first occurrence used
    with owner_engine.connect() as conn:
        a1 = conn.execute(
            text("SELECT order_date, ordered_at FROM order_lines WHERE order_line_id='A1'")
        ).one()
        l1 = conn.scalar(text("SELECT ordered_at FROM order_lines WHERE order_line_id='L1'"))
    assert a1.ordered_at == datetime(2025, 11, 2, 5, 30, tzinfo=UTC)  # 01:30 EDT (UTC-4)
    assert l1 == datetime(2026, 3, 2, 15, 15, tzinfo=UTC)  # 10:15 EST (UTC-5)
    issues = (
        await client.get(
            f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/issues",
            params={"code": "V-013", "file": "orders"},
        )
    ).json()["items"]
    assert any("YYYY-MM-DD" in i["message"] for i in issues)
    assert any(i["excerpt"] == "03/04/2026" for i in issues)


async def test_currency_values(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> None:
    orders = fx.ORDERS.replace(
        "discount_amount,channel,region", "discount_amount,channel,region,currency"
    )
    lines = orders.splitlines()
    orders = "\n".join([lines[0], *(line + ",usd" for line in lines[1:])]) + "\n"
    orders = fx.with_rows(
        orders,
        "X1,OX1,2026-03-02,C1,P1,1,25.00,0,web,north,EUR",
        "X2,OX2,2026-03-02,C1,P1,1,25.00,0,web,north,US",
        *[f"L{9000 + i},O{9000 + i},2026-03-06,C2,P2,1,12.00,0,web,south,USD" for i in range(60)],
    )
    detail = await ingest(app, client, owner, fx.dataset(orders=orders), currency="usd")
    assert detail["status"] == "ready", detail
    assert counts(detail)[("orders", "V-017")] == 2
    assert detail["currency"] == "USD"

    for currency, code in (("EUR", "currency_mismatch"), ("X1Y", "invalid_currency")):
        response = await upload(client, owner.business_id, fx.dataset(), currency=currency)
        assert response.status_code == 422
        assert response.json()["code"] == code


async def test_invalid_source_timezone_is_rejected(
    client: httpx.AsyncClient, owner: Tenant
) -> None:
    response = await upload(client, owner.business_id, fx.dataset(), source_timezone="Mars/Olympus")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_timezone"


async def test_too_many_rejected_rows_rejects_dataset_without_persisting(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    orders = fx.with_rows(fx.ORDERS, "B1,OB1,not-a-date,C1,P1,1,25.00,0,web,north")
    detail = await ingest(app, client, owner, fx.dataset(orders=orders))
    assert detail["status"] == "rejected"
    assert detail["error_code"] == "validation_failed"
    assert counts(detail)[("orders", "V-099")] == 1
    with owner_engine.connect() as conn:
        stored = conn.scalar(
            text(
                "SELECT (SELECT count(*) FROM products) + (SELECT count(*) FROM customers) + "
                "(SELECT count(*) FROM order_lines) + (SELECT count(*) FROM returns)"
            )
        )
    assert stored == 0
    records = await client.get(
        f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/records/orders"
    )
    assert records.status_code == 409


async def test_reupload_of_corrected_dataset_creates_new_version(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    broken = await ingest(
        app,
        client,
        owner,
        fx.dataset(orders=fx.with_rows(fx.ORDERS, "B1,OB1,not-a-date,C1,P1,1,25.00,0,web,north")),
    )
    fixed = await ingest(
        app,
        client,
        owner,
        fx.dataset(orders=fx.with_rows(fx.ORDERS, "B1,OB1,2026-03-05,C1,P1,1,25.00,0,web,north")),
    )
    assert (broken["version"], broken["status"]) == (1, "rejected")
    assert (fixed["version"], fixed["status"]) == (2, "ready")
    assert fixed["orders_rows"] == 5
    again = await client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{broken['id']}")
    assert again.json()["status"] == "rejected"  # earlier versions are immutable
    with owner_engine.connect() as conn:
        versions = conn.execute(
            text(
                "SELECT d.version, count(o.*) FROM datasets d "
                "LEFT JOIN order_lines o ON o.dataset_id = d.id "
                "GROUP BY d.version ORDER BY 1"
            )
        ).all()
    assert [tuple(v) for v in versions] == [(1, 0), (2, 5)]


async def test_safe_export_escapes_formulas(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    products = fx.with_rows(
        fx.PRODUCTS.replace("Aero Lamp", '"=HYPERLINK(""http://evil.example"",""x"")"'),
    )
    returns = fx.RETURNS.replace("Box crushed", "+cmd|' /C calc'!A0")
    detail = await ingest(app, client, owner, fx.dataset(products=products, returns=returns))
    assert detail["status"] == "ready", detail
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/export"

    exported = await client.get(f"{base}/products")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert 'filename="root-dataset-v1-products.csv"' in exported.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert rows[0] == [
        "source_row",
        "product_id",
        "product_name",
        "category",
        "unit_cost",
        "list_price",
    ]
    names = {r[1]: r[2] for r in rows[1:]}
    assert names["P1"] == '\'=HYPERLINK("http://evil.example","x")'
    returns_rows = list(csv.reader(io.StringIO((await client.get(f"{base}/returns")).text)))
    assert returns_rows[1][-1].startswith("'+cmd")
    issues = await client.get(f"{base}/issues")
    assert issues.status_code == 200
    assert "V-042" in issues.text
    for row in rows[1:] + returns_rows[1:]:
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r"))


async def test_upload_requires_permission_and_csrf(
    client: httpx.AsyncClient, owner_engine: Engine, owner: Tenant
) -> None:
    from tests.helpers import add_member, create_user

    viewer = create_user(owner_engine, "viewer@example.test")
    add_member(owner_engine, owner.business_id, viewer, "viewer")
    await login(client, "viewer@example.test")
    assert (await upload(client, owner.business_id, fx.dataset())).status_code == 403
    response = await client.post(
        f"/api/v1/businesses/{owner.business_id}/datasets",
        headers={"Idempotency-Key": "k" * 20},
        files={"orders": ("orders.csv", b"x", "text/csv")},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


async def test_upload_requires_idempotency_key(client: httpx.AsyncClient, owner: Tenant) -> None:
    response = await client.post(
        f"/api/v1/businesses/{owner.business_id}/datasets",
        headers=await csrf_headers(client),
        files={k: (f"{k}.csv", v, "text/csv") for k, v in fx.dataset().items()},
    )
    assert response.status_code == 422
    assert any(e["field"] == "Idempotency-Key" for e in response.json()["errors"])


async def test_file_level_rules_reject_dataset(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ingestion import contract

    monkeypatch.setitem(contract.MAX_ROWS, "orders", 3)  # the pipeline reads this mapping
    detail = await ingest(app, client, owner, fx.dataset())
    assert detail["status"] == "rejected"
    assert counts(detail)[("orders", "V-003")] == 1


async def test_dimension_cardinality_and_no_valid_orders(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    products = fx.with_rows(fx.PRODUCTS, *[f"X{i},Item {i},cat {i},1.00,2.00" for i in range(101)])
    detail = await ingest(app, client, owner, fx.dataset(products=products))
    assert detail["status"] == "rejected"
    assert counts(detail)[("products", "V-006")] == 1

    orders = fx.ORDERS.replace(",C1,", ",C404,").replace(",C2,", ",C404,").replace(",C3,", ",C404,")
    detail = await ingest(app, client, owner, fx.dataset(orders=orders))
    assert counts(detail)[("dataset", "V-007")] == 1
    assert counts(detail)[("orders", "V-030")] == 4


async def test_structurally_broken_csv_inside_file_is_rejected(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    # The header is valid (checked at upload); a later unterminated quote breaks the file.
    orders = fx.with_rows(fx.ORDERS, 'L5,O5,2026-03-05,C1,P1,1,"25.00,0,web,north')
    detail = await ingest(app, client, owner, fx.dataset(orders=orders))
    assert detail["status"] == "rejected"
    assert counts(detail)[("orders", "V-002")] == 1


async def test_worker_loop_processes_jobs_and_stops_cleanly(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    import asyncio

    from app.ingestion.pipeline import ingest_dataset
    from app.jobs.worker import run_worker

    response = await upload(client, owner.business_id, fx.dataset())
    task = asyncio.create_task(
        run_worker(
            app.state.sessionmaker,
            {"ingest_dataset": ingest_dataset},
            app.state.on_job_failure,
            poll_seconds=0.05,
        )
    )
    url = f"/api/v1/businesses/{owner.business_id}/datasets/{response.json()['id']}"
    for _ in range(100):
        if (await client.get(url)).json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await client.get(url)).json()["status"] == "ready"


async def test_text_enum_and_price_rules(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    long_name = "x" * 201
    products = fx.with_rows(
        fx.PRODUCTS,
        f"P9,{long_name},kitchen,1.00,2.00",
        *[f"Q{i},Item {i},kitchen,1.00,2.00" for i in range(30)],
    )
    padding = [f"L{9000 + i},O{9000 + i},2026-03-06,C2,P2,1,12.00,0,web,south" for i in range(60)]
    orders = fx.with_rows(fx.ORDERS, "L8,O8,2026-03-05,C1,P1,1,90.00,0,web,north", *padding)
    returns = fx.with_rows(
        fx.RETURNS,
        "R9,L2,2026-03-06,1,1.00,broken,",
        f"R10,L3,2026-03-06,1,1.00,other,{'y' * 501}",
        *[f"RP{i},L{9000 + i},2026-03-07,1,1.00,other," for i in range(60)],
    )
    detail = await ingest(
        app, client, owner, fx.dataset(products=products, orders=orders, returns=returns)
    )
    assert detail["status"] == "ready", detail
    c = counts(detail)
    assert c[("products", "V-015")] == 1
    assert c[("returns", "V-016")] == 1
    assert c[("returns", "V-015")] == 1
    assert c[("orders", "V-043")] == 1  # 90.00 is above 3x the 25.00 list price
