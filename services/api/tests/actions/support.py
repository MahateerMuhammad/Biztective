"""Helpers for action-execution tests: an approved plan, a verified spreadsheet binding and
execution requests."""

import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests.actions.fake_google import SID, FakeGoogle
from tests.helpers import Tenant, csrf_headers
from tests.recovery.support import completed, decided, planned

COST = {"low": "1500", "mid": "2000", "high": "3000"}
CONTACT = {"low": "1", "mid": "1", "high": "1"}
QUEUE_TARGETS = 3


def base(tenant: Tenant) -> str:
    return f"/api/v1/businesses/{tenant.business_id}"


async def approved_plan(
    app: FastAPI, client: httpx.AsyncClient, tenant: Tenant, *, queue: bool = False
) -> dict[str, Any]:
    """An approved plan (version 2): the top quality review, plus, with `queue`, goodwill
    outreach to three customers (review rows and service-queue rows)."""
    investigation = await completed(app, client, tenant.business_id)
    proposed = await planned(app, client, tenant.business_id, investigation["id"])
    strategies = proposed["current"]["content"]["strategies"]
    review = str(proposed["current"]["content"]["selected"][0])
    edits: dict[str, Any] = {
        "assumptions": {review: {"review_cost": COST}},
        "budget_amount": "100000",
    }
    if queue:
        outreach = next(
            s
            for s in strategies
            if s["strategy"] == "S_GOODWILL_OUTREACH" and s["eligibility"]["eligible"]
        )
        edits["selected_strategies"] = [review, outreach["id"]]
        edits["allowed_action_types"] = ["review_task", "customer_contact", "customer_offer"]
        edits["assumptions"][outreach["id"]] = {"contact_cost": CONTACT}
        edits["targets"] = {outreach["id"]: outreach["targeting"]["selected"][:QUEUE_TARGETS]}
    valid = await decided(app, client, tenant.business_id, proposed, "request-changes", edits=edits)
    assert valid["current"]["valid"] is True, valid["current"]["validation"]
    approved = await decided(app, client, tenant.business_id, valid, "approve")
    assert approved["status"] == "approved"
    return approved


async def bind(client: httpx.AsyncClient, tenant: Tenant, spreadsheet: str = SID) -> httpx.Response:
    return await client.post(
        f"{base(tenant)}/integrations/google-sheets",
        json={"spreadsheet": spreadsheet},
        headers=await csrf_headers(client),
    )


async def verify(client: httpx.AsyncClient, tenant: Tenant, integration_id: str) -> httpx.Response:
    return await client.post(
        f"{base(tenant)}/integrations/{integration_id}/verify", headers=await csrf_headers(client)
    )


async def connect(
    client: httpx.AsyncClient, tenant: Tenant, google: FakeGoogle, sid: str = SID
) -> dict[str, Any]:
    """Bind and prove ownership of a shared spreadsheet, as an owner would."""
    book = google.sheet(sid)
    created = await bind(client, tenant, sid)
    assert created.status_code == 201, created.text
    book["ROOT_VERIFY"] = [[created.json()["verification_code"]]]
    verified = await verify(client, tenant, created.json()["id"])
    assert verified.status_code == 200, verified.text
    body: dict[str, Any] = verified.json()
    return body


def binding(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": plan["approved"]["version"],
        "content_hash": plan["approved"]["content_hash"],
    }


async def execute(
    client: httpx.AsyncClient,
    tenant: Tenant,
    plan_id: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = await csrf_headers(client)
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        f"{base(tenant)}/recovery-plans/{plan_id}/actions", json=payload, headers=headers
    )


async def action(client: httpx.AsyncClient, tenant: Tenant, action_id: str) -> dict[str, Any]:
    response = await client.get(f"{base(tenant)}/actions/{action_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def retry(client: httpx.AsyncClient, tenant: Tenant, action_id: str) -> httpx.Response:
    return await client.post(
        f"{base(tenant)}/actions/{action_id}/retry", headers=await csrf_headers(client)
    )


def make_due(engine: Engine) -> None:
    """Skip queue backoff and expire leases, as time passing (or a restart) would."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE jobs SET run_after = now() - interval '1 second',
                    lease_expires_at = CASE WHEN status = 'running'
                        THEN now() - interval '1 second' END
                WHERE status IN ('queued', 'running')
            """)
        )


def rows_of(google: FakeGoogle, title: str, sid: str = SID) -> list[list[str]]:
    return google.spreadsheets[sid].get(title, [])[1:]


def table_count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        found: int = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    return found


def new_id() -> str:
    return str(uuid.uuid4())
