"""Checkpoint persistence, restart and recovery (T-25 mechanics, AC-7.2): a worker killed
mid-node resumes from the last PostgreSQL checkpoint in a new process, completed nodes are not
re-run, a node re-executed after it committed adds nothing twice, and checkpoints hold IDs only."""

import asyncio
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.investigation.graph import Nodes, State, build
from app.investigation.service import checkpointer
from app.jobs.queue import LeasedJob
from app.llm.provider import ProviderError
from app.main import create_app
from tests.helpers import Tenant, create_tenant, csrf_headers, login, run_jobs
from tests.investigation.support import (
    detail,
    events,
    files_of,
    ingest,
    investigate,
    options,
    signature,
    start,
)
from tests.llm_fakes import CooperativeModel, ScriptedProvider

NodeFn = Callable[[Nodes, State], Awaitable[dict[str, Any]]]


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


@pytest.fixture
async def spike(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> tuple[str, Any]:
    files, manifest = files_of(options(7, "return_spike"))
    return await ingest(app, client, owner.business_id, files), manifest


def fail_once(
    monkeypatch: pytest.MonkeyPatch, node: str, error: BaseException, *, after: bool = False
) -> list[int]:
    """Make `Nodes.<node>` raise once, before (or after) doing its work. Returns call counts."""
    original: NodeFn = getattr(Nodes, node)
    calls: list[int] = []

    async def wrapper(self: Nodes, state: State) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1 and not after:
            raise error
        result = await original(self, state)
        if len(calls) == 1 and after:
            raise error  # its transaction has committed; the checkpoint has not been written
        return result

    monkeypatch.setattr(Nodes, node, wrapper)
    return calls


@pytest.fixture
async def restarted(settings: Settings) -> AsyncIterator[FastAPI]:
    """A second application instance: a new process after a crash (fresh pools, no memory)."""
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


def expire_leases(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second', "
                "run_after = now() - interval '1 second' WHERE status IN ('running', 'queued')"
            )
        )


async def test_checkpoints_are_persisted_with_ids_only(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    settings: Settings,
    spike: tuple[str, Any],
) -> None:
    dataset_id, manifest = spike
    body = await investigate(app, client, owner.business_id, dataset_id)
    with owner_engine.connect() as conn:
        checkpoints = conn.scalar(
            text("SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = :t"),
            {"t": body["id"]},
        )
        blobs = conn.execute(
            text("SELECT blob FROM langgraph.checkpoint_blobs WHERE thread_id = :t"),
            {"t": body["id"]},
        ).all()
        customer = conn.scalar(text("SELECT customer_id FROM customers LIMIT 1"))
        product_name = conn.scalar(
            text("SELECT product_name FROM products WHERE product_id = :p"),
            {"p": manifest["planted_product"]},
        )
    assert checkpoints
    assert checkpoints > 10  # one per node execution (durability="sync")
    raw = b"".join(bytes(b.blob) for b in blobs if b.blob is not None)
    for needle in (
        customer,
        product_name,
        manifest["planted_product"],
        manifest["planted_category"],
    ):
        assert needle.encode() not in raw, needle

    runtime = app.state.investigations
    async with checkpointer(settings) as saver:
        compiled = build(Nodes(app.state.sessionmaker, settings, None)).compile(checkpointer=saver)
        snapshot = await compiled.aget_state(runtime.config(body["id"]))
    assert snapshot.next == ()
    assert set(snapshot.values) == set(State.__annotations__)
    assert snapshot.values["terminal_status"] == "completed"
    assert len(repr(snapshot.values)) < 2000  # small: IDs and counters, never business data


async def test_killed_worker_resumes_from_checkpoint_in_a_new_process(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    restarted: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    expected = signature(await investigate(app, client, owner.business_id, dataset_id))

    calls = fail_once(monkeypatch, "verify_evidence", asyncio.CancelledError())
    response = await start(client, owner.business_id, dataset_id)
    investigation_id = response.json()["id"]
    with pytest.raises(asyncio.CancelledError):  # the worker dies mid-node
        await run_jobs(app)
    assert (await detail(client, owner.business_id, investigation_id))["status"] == "running"

    expire_leases(owner_engine)  # the dead worker's lease runs out
    assert await run_jobs(restarted) == 1  # another process picks the job up
    body = await detail(client, owner.business_id, investigation_id)
    assert body["status"] == "completed"
    assert signature(body) == expected

    timeline = await events(client, owner.business_id, investigation_id)
    types = Counter(e["type"] for e in timeline)
    investigated = len([i for i in body["incidents"] if i["investigated"]])
    assert types["run.started"] == 1  # initialize was not executed again
    assert types["run.resumed"] == 1
    assert types["hypotheses.generated"] == investigated  # completed nodes did not re-run
    assert types["tools.selected"] == investigated
    resumed = next(e for e in timeline if e["type"] == "run.resumed")
    assert resumed["payload"]["next"] == ["verify_evidence"]
    assert len(calls) == investigated + 1  # the crashed attempt, then once per incident
    assert [e["seq"] for e in timeline] == list(range(1, len(timeline) + 1))


async def test_node_reexecuted_after_commit_writes_nothing_twice(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    calls = fail_once(monkeypatch, "retrieve_evidence", OSError("connection reset"), after=True)
    response = await start(client, owner.business_id, dataset_id)
    investigation_id = response.json()["id"]
    await run_jobs(app)  # transient failure: the job is re-queued with a backoff
    assert (await detail(client, owner.business_id, investigation_id))["status"] == "running"
    expire_leases(owner_engine)
    await run_jobs(app)
    body = await detail(client, owner.business_id, investigation_id)
    assert body["status"] == "completed"
    assert len(calls) >= 2

    with owner_engine.connect() as conn:
        duplicates = conn.scalar(
            text(
                "SELECT count(*) FROM (SELECT evidence_id FROM investigation_evidence "
                "WHERE investigation_id = :i GROUP BY 1 HAVING count(*) > 1) d"
            ),
            {"i": investigation_id},
        )
        evaluations = conn.scalar(
            text(
                "SELECT count(*) FROM hypothesis_evaluations e JOIN hypotheses h "
                "ON h.id = e.hypothesis_id WHERE h.investigation_id = :i"
            ),
            {"i": investigation_id},
        )
        hypotheses = conn.scalar(
            text("SELECT count(*) FROM hypotheses WHERE investigation_id = :i"),
            {"i": investigation_id},
        )
    assert duplicates == 0
    assert evaluations == hypotheses
    timeline = await events(client, owner.business_id, investigation_id)
    collected = [
        (e["payload"]["incident_id"], e["payload"]["round"])
        for e in timeline
        if e["type"] == "evidence.collected"
    ]
    assert len(collected) == len(set(collected))  # the re-executed node's event was not repeated


async def test_failed_investigation_is_retried_from_its_checkpoint(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    fail_once(monkeypatch, "synthesize_output", ValueError("unexpected defect"))
    response = await start(client, owner.business_id, dataset_id)
    investigation_id = response.json()["id"]
    await run_jobs(app)
    failed = await detail(client, owner.business_id, investigation_id)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "unexpected_error"
    assert failed["findings"] is None

    retry = await client.post(
        f"/api/v1/businesses/{owner.business_id}/investigations/{investigation_id}/retry",
        headers=await csrf_headers(client),
    )
    assert retry.status_code == 202
    await run_jobs(app)
    body = await detail(client, owner.business_id, investigation_id)
    assert body["status"] == "completed"
    assert body["findings"] is not None
    types = Counter(e["type"] for e in await events(client, owner.business_id, investigation_id))
    assert types["investigation.failed"] == 1
    assert types["run.retry_requested"] == 1
    assert types["run.resumed"] == 1
    assert types["hypotheses.generated"] == len([i for i in body["incidents"] if i["investigated"]])


async def test_completed_investigations_are_immutable(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
) -> None:
    dataset_id, _ = spike
    body = await investigate(app, client, owner.business_id, dataset_id)
    with owner_engine.connect() as conn, pytest.raises(DBAPIError, match="immutable"):
        conn.execute(
            text("UPDATE investigations SET status = 'queued' WHERE id = :i"), {"i": body["id"]}
        )
    # Re-running its job (e.g. a duplicate delivery) changes nothing.
    job = LeasedJob(
        id=uuid.uuid7(),
        type="run_investigation",
        business_id=owner.business_id,
        payload={"investigation_id": body["id"]},
        attempts=1,
        max_attempts=3,
    )
    await app.state.investigations.run(app.state.sessionmaker, job)
    assert (await detail(client, owner.business_id, body["id"]))["last_seq"] == body["last_seq"]


async def test_tool_selection_commit_is_recovered_without_repeating_llm_call(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    provider = ScriptedProvider(CooperativeModel())
    monkeypatch.setattr(app.state.investigations, "provider", provider)
    fail_once(monkeypatch, "select_tools", asyncio.CancelledError(), after=True)
    response = await start(client, owner.business_id, dataset_id)
    investigation_id = response.json()["id"]
    with pytest.raises(asyncio.CancelledError):
        await run_jobs(app)
    requests_before = len(provider.requests)
    assert provider.tasks()[-1] == "select_tools"
    expire_leases(owner_engine)
    await run_jobs(app)
    body = await detail(client, owner.business_id, investigation_id)
    assert body["status"] == "completed"
    timeline = await events(client, owner.business_id, investigation_id)
    selections = [e for e in timeline if e["type"] == "tools.selected"]
    assert provider.tasks().count("select_tools") == len(selections)
    assert len(provider.requests) > requests_before


async def test_provider_circuit_breaker_survives_commit_before_checkpoint_crash(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    provider = ScriptedProvider(lambda _: ProviderError("unavailable"))

    async def no_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(app.state.investigations, "provider", provider)
    monkeypatch.setattr(app.state.investigations, "sleep", no_sleep)
    fail_once(monkeypatch, "generate_hypotheses", asyncio.CancelledError(), after=True)
    response = await start(client, owner.business_id, dataset_id)
    investigation_id = response.json()["id"]
    with pytest.raises(asyncio.CancelledError):
        await run_jobs(app)
    assert len(provider.requests) == 3
    expire_leases(owner_engine)
    await run_jobs(app)
    body = await detail(client, owner.business_id, investigation_id)
    assert body["status"] == "completed"
    assert body["llm_mode"] == "deterministic"
    assert len(provider.requests) == 3


async def test_concurrent_starts_share_one_active_investigation(
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
) -> None:
    dataset_id, _ = spike
    headers = {**await csrf_headers(client), "Idempotency-Key": uuid.uuid4().hex}
    responses = await asyncio.gather(
        *(
            client.post(
                f"/api/v1/businesses/{owner.business_id}/investigations",
                json={"dataset_id": dataset_id},
                headers=headers,
            )
            for _ in range(4)
        )
    )
    assert [r.status_code for r in responses] == [202] * 4
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 1
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM investigations")) == 1
        assert conn.scalar(text("SELECT count(*) FROM jobs WHERE type = 'run_investigation'")) == 1


async def test_overlapping_job_invocations_serialize_the_graph(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    response = await start(client, owner.business_id, dataset_id)
    investigation_id = response.json()["id"]
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original = Nodes.initialize

    async def blocked(self: Nodes, state: State) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(self, state)

    monkeypatch.setattr(Nodes, "initialize", blocked)
    job = LeasedJob(
        id=uuid.uuid7(),
        type="run_investigation",
        business_id=owner.business_id,
        payload={"investigation_id": investigation_id},
        attempts=1,
        max_attempts=3,
    )
    runtime = app.state.investigations
    first = asyncio.create_task(runtime.run(app.state.sessionmaker, job))
    second: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(runtime.run(app.state.sessionmaker, job))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), timeout=0.2)
        assert calls == 1
    finally:
        release.set()
        await first
        if second is not None:
            await second
    assert calls == 1
    assert (await detail(client, owner.business_id, investigation_id))["status"] == "completed"


async def test_workflow_timeout_bounds_a_stalled_node(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    runtime = app.state.investigations
    monkeypatch.setattr(
        runtime,
        "settings",
        runtime.settings.model_copy(update={"investigation_timeout_seconds": 0.2}),
    )

    async def stalled(self: Nodes, state: State) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {}  # pragma: no cover - only cancellation can end the wait

    monkeypatch.setattr(Nodes, "initialize", stalled)
    response = await start(client, owner.business_id, dataset_id)
    await run_jobs(app)
    body = await detail(client, owner.business_id, response.json()["id"])
    assert body["status"] == "failed"
    assert body["error_code"] == "workflow_timeout"


async def test_corrupt_evidence_is_excluded_from_verdicts_prompts_and_output(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, _ = spike
    original = Nodes.verify_evidence
    corrupted: list[str] = []

    async def corrupt_once(self: Nodes, state: State) -> dict[str, Any]:
        if not corrupted:
            with owner_engine.begin() as conn:
                evidence_id = conn.scalar(
                    text(
                        "SELECT e.id FROM evidence_items e JOIN investigation_evidence l "
                        "ON e.id = l.evidence_id WHERE l.investigation_id = :i "
                        "AND l.incident_id = :c AND e.kind = 'return_investigation' LIMIT 1"
                    ),
                    {"i": state["investigation_id"], "c": state["current_incident_id"]},
                )
                assert evidence_id is not None
                conn.execute(
                    text("UPDATE evidence_items SET result = :result WHERE id = :e"),
                    {"e": evidence_id, "result": '{"facts": {}}'},
                )
                corrupted.append(str(evidence_id))
        return await original(self, state)

    monkeypatch.setattr(Nodes, "verify_evidence", corrupt_once)
    provider = ScriptedProvider(CooperativeModel())
    body = await investigate(app, client, owner.business_id, dataset_id, provider)
    assert body["status"] == "completed"
    assert corrupted
    assert all(e["id"] != corrupted[0] for i in body["incidents"] for e in i["evidence"])
    syntheses = [r for r in provider.requests if r.task == "synthesize_findings"]
    assert syntheses
    assert all(corrupted[0] not in r.user for r in syntheses)
    timeline = await events(client, owner.business_id, body["id"])
    assert any(
        e["type"] == "evidence.integrity_failed" and e["payload"]["evidence_id"] == corrupted[0]
        for e in timeline
    )
