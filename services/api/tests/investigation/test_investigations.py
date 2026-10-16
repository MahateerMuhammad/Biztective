"""Investigations end to end on uploaded data: planted incidents, clean data, insufficient
history, evidence references, LLM mode, determinism across phrasing, and the API."""

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests.helpers import Tenant, create_dataset, create_tenant, csrf_headers, login, run_jobs
from tests.investigation.support import (
    detail,
    events,
    files_of,
    finish,
    ingest,
    investigate,
    options,
    planted,
    signature,
    start,
)
from tests.llm_fakes import CooperativeModel, ScriptedProvider


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


def value_segments(segments_list: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [s for segments in segments_list for s in segments if "value" in s]


async def assert_evidence_references_resolve(
    client: httpx.AsyncClient, tenant: Tenant, body: dict[str, Any]
) -> None:
    """Every number shown cites evidence of the same incident, and the evidence is intact."""
    linked = {e["id"] for i in body["incidents"] for e in i["evidence"]}
    checked: dict[str, dict[str, Any]] = {}

    async def fetch(evidence_id: str) -> dict[str, Any]:
        if evidence_id not in checked:
            response = await client.get(
                f"/api/v1/businesses/{tenant.business_id}/evidence/{evidence_id}"
            )
            assert response.status_code == 200
            checked[evidence_id] = response.json()
            assert checked[evidence_id]["intact"] is True
        return checked[evidence_id]

    for incident in body["incidents"]:
        own = {e["id"] for e in incident["evidence"]}
        for segment in value_segments(incident["observations"]):
            assert segment["evidence_id"] in own
            await fetch(segment["evidence_id"])
        for h in incident["hypotheses"]:
            for criterion in h["criteria"]:
                for basis in criterion["basis"]:
                    assert basis["evidence_id"] in own, (h["template"], criterion["key"])
                    stored = await fetch(basis["evidence_id"])
                    assert basis["field"] in stored["result"]["facts"]
    findings = body["findings"]["rendered"] if body["findings"] else None
    if findings:
        texts = [findings["summary"], *(c["statement"] for c in findings["root_causes"])]
        texts += [a["why_not"] for a in findings["alternatives_considered"]]
        for segment in value_segments(texts):
            assert segment["evidence_id"] in linked
            await fetch(segment["evidence_id"])


@pytest.mark.parametrize("seed", [7, 8, 9])
async def test_planted_return_spike_is_explained_by_evidence(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, seed: int
) -> None:
    files, manifest = files_of(options(seed, "return_spike"))
    dataset_id = await ingest(app, client, owner.business_id, files)
    body = await investigate(app, client, owner.business_id, dataset_id)
    # Missing context pauses the run; "Not sure" adds none, so the data verdicts must stand.
    body = await finish(app, client, owner.business_id, body)

    assert body["status"] == "completed", body["error_code"]
    assert body["llm_mode"] == "deterministic"
    assert body["llm_disabled_reason"] == "not_configured"
    incident = planted(body, manifest, "refund_rate")
    statuses = {h["template"]: h["status"] for h in incident["hypotheses"]}
    assert statuses["H_RETURNS_QUALITY"] == "supported"
    cause = next(
        h for h in incident["hypotheses"] if h["id"] == incident["root_cause_hypothesis_id"]
    )
    assert cause["template"] == "H_RETURNS_QUALITY"
    assert {c["key"] for c in cause["criteria"] if c["satisfied"]} >= {"C1", "C2"}
    # Competing explanations were investigated and not supported.
    assert len(incident["hypotheses"]) >= 3
    assert statuses["H_FULFILLMENT"] != "supported"
    assert statuses["H_DATA_QUALITY"] != "supported"
    assert statuses["H_EXTERNAL_CONTEXT"] == "needs_human_context"
    assert incident["observations"]
    await assert_evidence_references_resolve(client, owner, body)

    findings = body["findings"]
    assert findings["source"] == "deterministic"
    causes = {c["hypothesis_id"] for c in findings["rendered"]["root_causes"]}
    assert incident["root_cause_hypothesis_id"] in causes
    supported = {
        h["id"] for i in body["incidents"] for h in i["hypotheses"] if h["status"] == "supported"
    }
    assert causes <= supported  # no unsupported cause is ever stated


async def test_planted_discount_spike_is_explained_by_evidence(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    files, manifest = files_of(options(7, "discount_spike"))
    dataset_id = await ingest(app, client, owner.business_id, files)
    body = await investigate(app, client, owner.business_id, dataset_id)
    body = await finish(app, client, owner.business_id, body)
    assert body["status"] == "completed"
    incident = planted(body, manifest, "discount_rate")
    statuses = {h["template"]: h["status"] for h in incident["hypotheses"]}
    assert statuses["H_PRICE_DISCOUNT"] == "supported"
    assert "H_RETURNS_QUALITY" not in statuses  # not applicable to discount incidents
    await assert_evidence_references_resolve(client, owner, body)


@pytest.mark.parametrize("seed", [101, 202])
async def test_clean_data_produces_no_significant_incident(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, seed: int
) -> None:
    files, _ = files_of(options(seed))
    dataset_id = await ingest(app, client, owner.business_id, files)
    provider = ScriptedProvider(CooperativeModel())
    body = await investigate(app, client, owner.business_id, dataset_id, provider)
    assert body["status"] == "completed_no_incidents"
    assert body["incidents"] == []
    assert body["findings"] is None
    assert provider.requests == []  # nothing to explain, nothing asked
    types = [e["type"] for e in await events(client, owner.business_id, body["id"])]
    assert types == [
        "investigation.created",
        "run.started",
        "incidents.detected",
        "investigation.completed_no_incidents",
    ]


async def test_insufficient_history(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> None:
    files, _ = files_of(options(5, weeks=8, customers=300, products=30, orders_per_day=10))
    dataset_id = await ingest(app, client, owner.business_id, files)
    body = await investigate(app, client, owner.business_id, dataset_id)
    assert body["status"] == "insufficient_data"
    assert body["incidents"] == []
    assert any(r["code"] == "insufficient_history" for r in body["restrictions"])


async def test_llm_mode_selects_extra_tools_and_cites_only_valid_evidence(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    files, manifest = files_of(options(7, "return_spike"))
    dataset_id = await ingest(app, client, owner.business_id, files)
    provider = ScriptedProvider(CooperativeModel())
    body = await investigate(app, client, owner.business_id, dataset_id, provider)

    assert body["status"] == "completed"
    assert body["llm_mode"] == "llm"
    assert body["findings"]["source"] == "llm"
    incident = planted(body, manifest, "refund_rate")
    requested = {e["requested_by"] for e in incident["evidence"]}
    assert requested == {"detection", "system", "llm"}
    assert {h["source"] for h in incident["hypotheses"]} == {"llm_prioritized"}
    assert all(h["rationale"] for h in incident["hypotheses"])
    await assert_evidence_references_resolve(client, owner, body)

    usage = body["usage"]
    assert 0 < usage["llm_calls"] <= 40
    assert usage["llm_fallbacks"] == 0
    tasks = provider.tasks()
    assert tasks[0] == "prioritize_hypotheses"
    assert tasks[-1] == "synthesize_findings"
    assert tasks.count("select_tools") <= 2 * len(
        [i for i in body["incidents"] if i["investigated"]]
    )
    timeline = await events(client, owner.business_id, body["id"])
    lookups = [e for e in timeline if e["type"] == "evidence.looked_up"]
    assert lookups
    assert all(e["payload"]["refused"] == 0 for e in lookups)
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT task, validation, tokens_in > 0, latency_ms >= 0, input_hash, model, "
                "prompt_version FROM llm_calls WHERE investigation_id = :i"
            ),
            {"i": body["id"]},
        ).all()
    assert len(rows) == usage["llm_calls"]
    assert {r.validation for r in rows} == {"accepted"}
    assert all(r[2] and r[3] and len(r.input_hash) == 64 for r in rows)
    assert {r.prompt_version for r in rows} == {
        "prioritize_hypotheses.v1",
        "select_tools.v1",
        "synthesize_findings.v2",  # AMD-15: synthesis receives human-context codes
    }


async def test_analytical_facts_do_not_depend_on_llm_phrasing(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    files, _ = files_of(options(8, "return_spike"))
    dataset_id = await ingest(app, client, owner.business_id, files)
    runs = [
        await finish(
            app,
            client,
            owner.business_id,
            await investigate(app, client, owner.business_id, dataset_id),
        )
    ]
    for style in (0, 1, 2):
        provider = ScriptedProvider(CooperativeModel(style))
        body = await investigate(app, client, owner.business_id, dataset_id, provider)
        app.state.investigations.provider = provider  # the same model words the final findings
        try:
            runs.append(await finish(app, client, owner.business_id, body))
        finally:
            app.state.investigations.provider = None
    baseline = signature(runs[0])
    assert baseline
    for body in runs:
        assert body["status"] == "completed"
    for body in runs[1:]:
        assert signature(body) == baseline
        assert signature(body) == baseline
    summaries = {
        "".join(s.get("text", "") for s in body["findings"]["rendered"]["summary"]) for body in runs
    }
    assert len(summaries) == 4  # four different wordings of the same facts
    # Required tool results were computed once and reused (immutable revision cache).
    with owner_engine.connect() as conn:
        duplicates = conn.scalar(
            text(
                "SELECT count(*) FROM (SELECT revision_id, kind, params_hash FROM evidence_items "
                "WHERE params_hash IS NOT NULL GROUP BY 1, 2, 3 HAVING count(*) > 1) d"
            )
        )
    assert duplicates == 0
    later = await events(client, owner.business_id, runs[1]["id"])
    cached = [
        item["cached"]
        for e in later
        if e["type"] == "evidence.collected"
        for item in e["payload"]["items"]
        if item["requested_by"] == "system"
    ]
    assert cached
    assert all(cached)


async def test_missing_costs_are_reported_not_estimated(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    files, manifest = files_of(options(7, "return_spike"))
    lines = files["products"].decode().splitlines()
    header, rows = lines[0], lines[1:]
    stripped = []
    for row in rows:  # blank the unit cost of every product in the planted category
        cells = row.split(",")
        if cells[2] == manifest["planted_category"]:
            cells[3] = ""
        stripped.append(",".join(cells))
    files["products"] = ("\n".join([header, *stripped]) + "\n").encode()
    dataset_id = await ingest(app, client, owner.business_id, files)
    body = await investigate(app, client, owner.business_id, dataset_id)
    assert body["status"] == "completed"
    assert any(r["code"] == "missing_costs" for r in body["restrictions"])
    incident = planted(body, manifest, "refund_rate")
    dq = next(h for h in incident["hypotheses"] if h["template"] == "H_DATA_QUALITY")
    assert dq["status"] in {"refuted", "inconclusive", "needs_human_context"}
    caveats = [
        "".join(s.get("text", "") for s in c) for c in body["findings"]["rendered"]["caveats"]
    ]
    assert any("unit cost" in c for c in caveats)


async def test_api_contract(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    base = f"/api/v1/businesses/{owner.business_id}"
    # A ready dataset without a finished analysis cannot be investigated yet.
    bare = create_dataset(owner_engine, owner.business_id, owner.user_id, 9)
    response = await start(client, owner.business_id, str(bare))
    assert response.status_code == 409
    assert response.json()["code"] == "analysis_not_ready"
    response = await start(client, owner.business_id, str(uuid.uuid4()))
    assert response.status_code == 404
    response = await client.post(
        f"{base}/investigations",
        json={"dataset_id": str(bare), "business_id": str(uuid.uuid4())},
        headers={**await csrf_headers(client), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 422  # unknown fields are refused

    files, _ = files_of(options(101))
    dataset_id = await ingest(app, client, owner.business_id, files)
    first = await start(client, owner.business_id, dataset_id)
    again = await start(client, owner.business_id, dataset_id)
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]  # one active investigation per dataset
    assert first.json()["status"] == "queued"
    listed = (await client.get(f"{base}/investigations", params={"dataset_id": dataset_id})).json()
    assert [i["id"] for i in listed["items"]] == [first.json()["id"]]
    retry = await client.post(
        f"{base}/investigations/{first.json()['id']}/retry", headers=await csrf_headers(client)
    )
    assert retry.status_code == 409
    assert retry.json()["code"] == "not_retryable"
    await run_jobs(app)
    done = await detail(client, owner.business_id, first.json()["id"])
    assert done["status"] == "completed_no_incidents"
    assert done["completed_at"] is not None
    with owner_engine.connect() as conn:
        audit = conn.scalar(
            text("SELECT count(*) FROM audit_log WHERE action = 'investigation.started'")
        )
    assert audit == 1

    replay_headers = {
        **await csrf_headers(client),
        "Idempotency-Key": first.request.headers["Idempotency-Key"],
    }
    replay = await client.post(
        f"{base}/investigations", json={"dataset_id": dataset_id}, headers=replay_headers
    )
    assert replay.status_code == 202
    assert replay.json() == first.json()  # completion cannot turn a replay into another run
    mismatched = await client.post(
        f"{base}/investigations", json={"dataset_id": str(bare)}, headers=replay_headers
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["code"] == "idempotency_key_reuse"
    missing_key = await client.post(
        f"{base}/investigations",
        json={"dataset_id": dataset_id},
        headers=await csrf_headers(client),
    )
    assert missing_key.status_code == 422

    # Rate limit (SEC-20).
    runtime = app.state.investigations
    original = runtime.settings
    runtime.settings = original.model_copy(update={"investigation_starts_per_hour": 1})
    try:
        limited = await start(client, owner.business_id, dataset_id)
    finally:
        runtime.settings = original
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "600"

    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE idempotency_keys SET expires_at = now() - interval '1 second' "
                "WHERE key = :k"
            ),
            {"k": replay_headers["Idempotency-Key"]},
        )
    after_expiry = await client.post(
        f"{base}/investigations", json={"dataset_id": dataset_id}, headers=replay_headers
    )
    assert after_expiry.status_code == 202
    assert after_expiry.json()["id"] != first.json()["id"]


async def test_events_are_ordered_gap_free_and_paginated(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    files, _ = files_of(options(7, "return_spike"))
    dataset_id = await ingest(app, client, owner.business_id, files)
    body = await investigate(
        app, client, owner.business_id, dataset_id, ScriptedProvider(CooperativeModel())
    )
    timeline = await events(client, owner.business_id, body["id"])
    assert [e["seq"] for e in timeline] == list(range(1, len(timeline) + 1))
    assert body["last_seq"] == len(timeline)
    assert timeline[0]["type"] == "investigation.created"
    assert timeline[0]["actor_kind"] == "human"
    assert timeline[-1]["type"] == "investigation.completed"
    url = f"/api/v1/businesses/{owner.business_id}/investigations/{body['id']}/events"
    page = (await client.get(url, params={"after_seq": 5, "limit": 3})).json()
    assert [e["seq"] for e in page["items"]] == [6, 7, 8]
    assert page["last_seq"] == len(timeline)
    kinds = {e["type"] for e in timeline}
    assert {
        "incidents.detected",
        "incident.selected",
        "hypotheses.generated",
        "tools.selected",
        "evidence.collected",
        "hypothesis.evaluated",
        "findings.drafted",
        "llm.call_completed",
    } <= kinds
