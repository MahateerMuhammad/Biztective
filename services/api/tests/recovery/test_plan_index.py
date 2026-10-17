"""Decision Center index and investigation pagination (Phase 9): newest first, keyset cursors,
tenant isolation."""

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine

from tests.helpers import create_tenant, login
from tests.recovery.support import completed, planned


async def test_plans_and_investigations_are_paginated_per_business(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    owner = create_tenant(owner_engine, "alice")
    assert (await login(client, owner.email)).status_code == 200
    base = f"/api/v1/businesses/{owner.business_id}"
    first = await completed(app, client, owner.business_id, seed=7)
    second = await completed(app, client, owner.business_id, seed=8)
    plan_a = await planned(app, client, owner.business_id, first["id"])
    plan_b = await planned(app, client, owner.business_id, second["id"])

    page = (await client.get(f"{base}/investigations?limit=1")).json()
    assert [i["id"] for i in page["items"]] == [second["id"]]
    assert page["next_cursor"]
    rest = (await client.get(f"{base}/investigations?limit=1&cursor={page['next_cursor']}")).json()
    assert [i["id"] for i in rest["items"]] == [first["id"]]
    assert rest["next_cursor"] is None

    plans = (await client.get(f"{base}/recovery-plans?limit=1")).json()
    assert [p["id"] for p in plans["items"]] == [plan_b["id"]]
    assert plans["items"][0]["status"] == "awaiting_decision"
    assert plans["items"][0]["execution_status"] is None
    more = (await client.get(f"{base}/recovery-plans?limit=1&cursor={plans['next_cursor']}")).json()
    assert [p["id"] for p in more["items"]] == [plan_a["id"]]
    assert more["next_cursor"] is None
    bad = await client.get(f"{base}/recovery-plans?cursor=%%%")
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_cursor")

    other = create_tenant(owner_engine, "mallory")
    transport = httpx.ASGITransport(app=app, client=("203.0.113.80", 50080))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        assert (await login(c, other.email)).status_code == 200
        theirs = f"/api/v1/businesses/{other.business_id}"
        assert (await c.get(f"{theirs}/recovery-plans")).json()["items"] == []
        assert (await c.get(f"{theirs}/investigations")).json()["items"] == []
        assert (await c.get(f"{base}/recovery-plans")).status_code in (403, 404)
