"""Helpers for recovery-plan tests: a completed investigation, plan calls and decisions."""

import uuid
from typing import Any

import httpx
from fastapi import FastAPI

from app.llm.provider import LlmProvider
from tests.helpers import csrf_headers, run_jobs
from tests.investigation.support import files_of, finish, ingest, investigate, options


async def completed(
    app: FastAPI,
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    scenario: str = "return_spike",
    seed: int = 7,
    **overrides: Any,
) -> dict[str, Any]:
    """A completed investigation of uploaded generated data ("Not sure" to every question)."""
    files, _ = files_of(options(seed, scenario, **overrides))
    dataset_id = await ingest(app, client, business_id, files)
    body = await investigate(app, client, business_id, dataset_id)
    return await finish(app, client, business_id, body)


async def create(
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    investigation_id: str,
    constraints: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = await csrf_headers(client)
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    body: dict[str, Any] = {} if constraints is None else {"constraints": constraints}
    return await client.post(
        f"/api/v1/businesses/{business_id}/investigations/{investigation_id}/recovery-plans",
        json=body,
        headers=headers,
    )


async def detail(client: httpx.AsyncClient, business_id: uuid.UUID, plan_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/businesses/{business_id}/recovery-plans/{plan_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def planned(
    app: FastAPI,
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    investigation_id: str,
    constraints: dict[str, Any] | None = None,
    provider: LlmProvider | None = None,
) -> dict[str, Any]:
    """Create a plan and run it to its decision checkpoint."""
    response = await create(client, business_id, investigation_id, constraints)
    assert response.status_code == 202, response.text
    app.state.recovery.provider = provider
    try:
        await run_jobs(app)
    finally:
        app.state.recovery.provider = None
    return await detail(client, business_id, response.json()["id"])


def binding(body: dict[str, Any]) -> dict[str, Any]:
    awaiting = body["awaiting"]
    assert awaiting is not None, body["status"]
    return {
        "version": awaiting["version"],
        "content_hash": awaiting["content_hash"],
        "checkpoint_id": awaiting["checkpoint_id"],
    }


async def decide(
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    plan_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = await csrf_headers(client)
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        f"/api/v1/businesses/{business_id}/recovery-plans/{plan_id}/{kind}",
        json=payload,
        headers=headers,
    )


async def decided(
    app: FastAPI,
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    body: dict[str, Any],
    kind: str,
    **extra: Any,
) -> dict[str, Any]:
    """Decide the waiting version, run the resume and return the plan."""
    response = await decide(client, business_id, body["id"], kind, binding(body) | extra)
    assert response.status_code == 202, response.text
    await run_jobs(app)
    return await detail(client, business_id, body["id"])


def strategy(body: dict[str, Any], code: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        s for s in body["current"]["content"]["strategies"] if s["strategy"] == code
    )
    return found
