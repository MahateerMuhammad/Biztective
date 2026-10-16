"""Shared helpers for investigation tests: generated datasets, starting and running
investigations, and a comparable signature of the analytical facts."""

import uuid
from datetime import date
from typing import Any

import httpx
from fastapi import FastAPI

from app.llm.provider import LlmProvider
from scripts.generate_dataset import Options, generate_with_manifest
from tests.helpers import csrf_headers, ingest_files, run_jobs


def options(seed: int = 7, *scenarios: str, **overrides: Any) -> Options:
    values: dict[str, Any] = {
        "seed": seed,
        "weeks": 26,
        "customers": 1500,
        "products": 120,
        "orders_per_day": 45,
        "start": date(2026, 1, 5),
        "noise": 0.0,
        "pii_column": False,
        "scenarios": scenarios,
        "spike_return_rate": 0.6,
    }
    values.update(overrides)
    return Options(**values)


def files_of(opts: Options) -> tuple[dict[str, bytes], dict[str, str]]:
    files, manifest = generate_with_manifest(opts)
    return {name.removesuffix(".csv"): body for name, body in files.items()}, manifest


async def ingest(
    app: FastAPI, client: httpx.AsyncClient, business_id: uuid.UUID, files: dict[str, bytes]
) -> str:
    detail = await ingest_files(app, client, business_id, files)
    assert detail["status"] == "ready", detail
    return str(detail["id"])


async def start(
    client: httpx.AsyncClient, business_id: uuid.UUID, dataset_id: str
) -> httpx.Response:
    return await client.post(
        f"/api/v1/businesses/{business_id}/investigations",
        json={"dataset_id": dataset_id},
        headers={**await csrf_headers(client), "Idempotency-Key": uuid.uuid4().hex},
    )


async def detail(
    client: httpx.AsyncClient, business_id: uuid.UUID, investigation_id: str
) -> dict[str, Any]:
    response = await client.get(
        f"/api/v1/businesses/{business_id}/investigations/{investigation_id}"
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def events(
    client: httpx.AsyncClient, business_id: uuid.UUID, investigation_id: str
) -> list[dict[str, Any]]:
    response = await client.get(
        f"/api/v1/businesses/{business_id}/investigations/{investigation_id}/events",
        params={"limit": 500},
    )
    assert response.status_code == 200
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def investigate(
    app: FastAPI,
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    dataset_id: str,
    provider: LlmProvider | None = None,
) -> dict[str, Any]:
    """Start with `provider` (None: deterministic mode), run every job, return the detail."""
    app.state.investigations.provider = provider
    try:
        response = await start(client, business_id, dataset_id)
        assert response.status_code == 202, response.text
        await run_jobs(app, limit=50)
    finally:
        app.state.investigations.provider = None
    return await detail(client, business_id, response.json()["id"])


def signature(body: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Everything analytical: incidents, statuses, confidences, criteria and the exact fact
    values they read. Excludes IDs, wording, priorities and LLM-only extras."""
    out: list[tuple[Any, ...]] = []
    for incident in body["incidents"]:
        for h in sorted(incident["hypotheses"], key=lambda h: h["template"]):
            out.append(
                (
                    incident["key"],
                    h["template"],
                    h["status"],
                    h["confidence"],
                    tuple(
                        (
                            c["key"],
                            c["satisfied"],
                            tuple((b["field"], b["value"]) for b in c["basis"]),
                        )
                        for c in h["criteria"]
                    ),
                )
            )
        root = next(
            (
                h["template"]
                for h in incident["hypotheses"]
                if h["id"] == incident["root_cause_hypothesis_id"]
            ),
            None,
        )
        out.append((incident["key"], "root_cause", root))
    return out


def planted(body: dict[str, Any], manifest: dict[str, str], metric: str) -> dict[str, Any]:
    """The investigated incident on the planted product (or its category) for `metric`."""
    targets = {("product", manifest["planted_product"]), ("category", manifest["planted_category"])}
    matches: list[dict[str, Any]] = [
        i
        for i in body["incidents"]
        if i["investigated"] and i["metric"] == metric and (i["dimension"], i["value"]) in targets
    ]
    assert matches, [(i["metric"], i["dimension"], i["value"]) for i in body["incidents"]]
    return matches[0]


async def answer(
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    investigation_id: str,
    clarification_id: str,
    body: dict[str, Any],
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = await csrf_headers(client)
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        f"/api/v1/businesses/{business_id}/investigations/{investigation_id}"
        f"/clarifications/{clarification_id}/answer",
        json=body,
        headers=headers,
    )


async def respond(
    app: FastAPI,
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    body: dict[str, Any],
    option: str | None = None,
    free_text: str | None = None,
) -> dict[str, Any]:
    """Answer the question the investigation is waiting for, run the resume, return detail."""
    awaiting = body["awaiting"]
    assert awaiting is not None, body["status"]
    payload: dict[str, Any] = {"checkpoint_id": awaiting["checkpoint_id"]}
    if option is not None:
        payload["option_id"] = option
    if free_text is not None:
        payload["free_text"] = free_text
    response = await answer(client, business_id, body["id"], awaiting["id"], payload)
    assert response.status_code == 202, response.text
    await run_jobs(app, limit=20)
    return await detail(client, business_id, body["id"])


async def finish(
    app: FastAPI, client: httpx.AsyncClient, business_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    """Answer every remaining question "Not sure" (it changes no verdict) until done."""
    while body["status"] == "awaiting_human":
        body = await respond(app, client, business_id, body, option="unsure")
    return body


def incident_by_key(body: dict[str, Any], key: str) -> dict[str, Any]:
    found: dict[str, Any] = next(i for i in body["incidents"] if i["key"] == key)
    return found


def hypothesis(incident: dict[str, Any], template: str) -> dict[str, Any] | None:
    return next((h for h in incident["hypotheses"] if h["template"] == template), None)


def root_template(incident: dict[str, Any]) -> str | None:
    root = next(
        (h for h in incident["hypotheses"] if h["id"] == incident["root_cause_hypothesis_id"]),
        None,
    )
    return root["template"] if root else None
