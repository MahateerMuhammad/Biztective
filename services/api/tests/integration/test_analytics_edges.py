"""Edge cases through the real upload → ingest → analyse path."""

import pathlib
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from tests import analytics_reference as ref
from tests.helpers import Tenant, create_tenant, ingest_files, login


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


def files(orders: str, returns: str = ref.RETURNS.splitlines()[0] + "\n") -> dict[str, bytes]:
    return {
        "products": ref.PRODUCTS.encode(),
        "customers": ref.CUSTOMERS.encode(),
        "orders": orders.encode(),
        "returns": returns.encode(),
    }


HEADER = ref.ORDERS.splitlines()[0]


async def test_empty_business_has_no_analytics(client: httpx.AsyncClient, owner: Tenant) -> None:
    datasets = (await client.get(f"/api/v1/businesses/{owner.business_id}/datasets")).json()
    assert datasets["items"] == []
    missing = await client.get(
        f"/api/v1/businesses/{owner.business_id}/datasets/{uuid.uuid4()}/metrics"
    )
    assert missing.status_code == 404


async def test_only_cancelled_lines(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> None:
    orders = f"{HEADER}\nL1,O1,2026-01-05,C1,P1,1,25.00,0,web,north,cancelled\n"
    detail = await ingest_files(app, client, owner.business_id, files(orders))
    assert detail["status"] == "ready"
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}"
    assert (await client.get(f"{base}/metrics")).json()["code"] == "no_completed_orders"
    analysis = (await client.get(f"{base}/analysis")).json()
    assert analysis["status"] == "insufficient_data"
    assert {r["code"] for r in analysis["restrictions"]} >= {
        "cancelled_excluded",
        "insufficient_history",
    }


async def test_duplicate_order_lines_are_counted_once(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    line = "L1,O1,2026-01-05,C1,P1,2,25.00,0,web,north,completed"
    padding = [f"L{i},O{i},2026-01-06,C2,P2,1,12.00,0,app,south,completed" for i in range(100, 160)]
    orders = "\n".join([HEADER, line, line, *padding]) + "\n"  # exact duplicate of L1
    detail = await ingest_files(app, client, owner.business_id, files(orders))
    assert detail["status"] == "ready"
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}"
    body = (await client.get(f"{base}/products")).json()
    p1 = next(p for p in body["data"] if p["product_id"] == "P1")["metrics"]
    assert (p1["units_sold"], p1["gross_sales"]) == (2, "50.0000")  # not 4 / 100


async def test_period_boundaries_are_inclusive_calendar_days(
    client: httpx.AsyncClient, app: FastAPI, owner: Tenant
) -> None:
    detail = await ingest_files(app, client, owner.business_id, ref.files())
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/metrics"
    sunday = (await client.get(base, params={"from": "2026-01-11", "to": "2026-01-11"})).json()
    assert sunday["data"] == [] or sunday["data"][0]["metrics"]["orders"] == 0
    monday = (await client.get(base, params={"from": "2026-01-12", "to": "2026-01-12"})).json()
    assert monday["data"][0]["metrics"]["orders"] == 1  # O3 only (O4 is cancelled, 01-13)
    span = (await client.get(base, params={"from": "2026-01-05", "to": "2026-01-12"})).json()
    assert span["data"][0]["metrics"]["orders"] == 3  # both ends included


async def test_extreme_valid_values_are_exact(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    products = (
        "product_id,product_name,category,unit_cost,list_price\n"
        "PX,Big,bulk,999999999.9999,1000000000\n"
    )
    customers = "customer_id\nC1\n"
    orders = (
        "order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price,discount_amount\n"
        "L1,O1,2026-01-05,C1,PX,100000,1000000000,0.0001\n"
    )
    detail = await ingest_files(
        app,
        client,
        owner.business_id,
        {
            "products": products.encode(),
            "customers": customers.encode(),
            "orders": orders.encode(),
            "returns": (
                b"return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code\n"
            ),
        },
    )
    assert detail["status"] == "ready", detail
    body = (
        await client.get(f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}/metrics")
    ).json()
    m = body["data"][0]["metrics"]
    assert m["gross_sales"] == "100000000000000.0000"
    assert m["net_sales"] == "99999999999999.9999"
    # 100,000 × 999,999,999.9999 = 1e14 − 10 exactly (a float would not keep the 4 dp).
    assert m["cogs_sold"] == "99999999999990.0000"
    assert m["gross_profit"] == "9.9999"  # 99,999,999,999,999.9999 − 99,999,999,999,990
    assert m["gross_margin_pct"] == "0.000000"  # 9.9999 / ~1e14 rounds to zero at 6 dp


def test_runtime_analytics_has_no_demo_literals() -> None:
    """Detection must never be steered by the demo or generator vocabulary."""
    from scripts.generate_dataset import ADJECTIVES, CATEGORIES, NOUNS

    vocabulary = {*CATEGORIES, *ADJECTIVES, *NOUNS}
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    for path in app_dir.rglob("*.py"):
        source = path.read_text().lower()
        for word in vocabulary:
            assert f'"{word}"' not in source, (path, word)
            assert f"'{word}'" not in source, (path, word)
