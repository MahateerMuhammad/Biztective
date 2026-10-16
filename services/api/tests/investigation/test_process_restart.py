"""A real SIGKILL followed by recovery in a different Python worker process."""

import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine, text

from tests.conftest import DatabaseUrls
from tests.helpers import create_tenant, login
from tests.investigation.support import (
    answer,
    detail,
    events,
    files_of,
    finish,
    ingest,
    investigate,
    options,
    signature,
    start,
)
from tests.recovery.support import binding, completed, decide, decided, planned
from tests.recovery.support import detail as detail_plan

WORKER = """
import asyncio
import importlib
import os
from pathlib import Path

from app.main import create_app
from tests.conftest import make_settings
from tests.helpers import run_jobs

async def main():
    marker = os.environ.get("ROOT_RESTART_MARKER")
    if marker:
        async def pause(self, state):
            Path(marker).write_text("ready", encoding="utf-8")
            await asyncio.Event().wait()
        graph = importlib.import_module(os.environ["ROOT_RESTART_GRAPH"])
        setattr(graph.Nodes, os.environ.get("ROOT_RESTART_NODE", "verify_evidence"), pause)
    settings = make_settings(
        os.environ["ROOT_RESTART_DB_URL"], llm_provider="disabled", anthropic_api_key=None
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        assert await run_jobs(application) == 1

asyncio.run(main())
"""


async def spawn_worker(
    database_url: str,
    marker: Path | None = None,
    node: str = "verify_evidence",
    graph: str = "app.investigation.graph",
) -> asyncio.subprocess.Process:
    environment = dict(os.environ)
    environment["ROOT_RESTART_DB_URL"] = database_url
    environment["ROOT_RESTART_NODE"] = node
    environment["ROOT_RESTART_GRAPH"] = graph
    environment.pop("ROOT_RESTART_MARKER", None)
    if marker is not None:
        environment["ROOT_RESTART_MARKER"] = str(marker)
    # A constant Python program, launched directly without a shell. Connection settings
    # travel only through the child environment and are never printed or persisted.
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        WORKER,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def test_sigkill_worker_resumes_durable_checkpoint_in_fresh_process(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner_engine: Engine,
    db_urls: DatabaseUrls,
    tmp_path: Path,
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    files, _ = files_of(options(7, "return_spike"))
    dataset_id = await ingest(app, client, tenant.business_id, files)
    expected = signature(await investigate(app, client, tenant.business_id, dataset_id))
    response = await start(client, tenant.business_id, dataset_id)
    assert response.status_code == 202
    investigation_id = response.json()["id"]

    marker = tmp_path / "worker_paused"
    killed = await spawn_worker(db_urls.app, marker)
    try:
        async with asyncio.timeout(20):
            while not marker.exists():
                assert killed.returncode is None, "Worker exited before reaching the pause node."
                await asyncio.sleep(0.05)
        assert (await detail(client, tenant.business_id, investigation_id))["status"] == "running"
    finally:
        if killed.returncode is None:
            killed.kill()
        await asyncio.wait_for(killed.communicate(), timeout=5)
    assert killed.returncode is not None
    assert killed.returncode < 0

    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE status = 'running'"
            )
        )
    restarted = await spawn_worker(db_urls.app)
    try:
        await asyncio.wait_for(restarted.communicate(), timeout=30)
        assert restarted.returncode == 0, "Fresh worker must recover and finish successfully."
    finally:
        if restarted.returncode is None:
            restarted.kill()
            await restarted.communicate()
    assert restarted.pid != killed.pid

    body = await detail(client, tenant.business_id, investigation_id)
    assert body["status"] == "completed"
    assert signature(body) == expected
    timeline = await events(client, tenant.business_id, investigation_id)
    types = Counter(event["type"] for event in timeline)
    incident_count = sum(incident["investigated"] for incident in body["incidents"])
    assert types["run.started"] == 1
    assert types["run.resumed"] == 1
    assert types["hypotheses.generated"] == incident_count
    assert types["tools.selected"] == incident_count
    assert types["findings.drafted"] == 1
    resumed = next(event for event in timeline if event["type"] == "run.resumed")
    assert resumed["payload"]["next"] == ["verify_evidence"]
    assert [event["seq"] for event in timeline] == list(range(1, len(timeline) + 1))
    with owner_engine.connect() as conn:
        duplicates = conn.scalar(
            text(
                "SELECT count(*) FROM (SELECT evidence_id FROM investigation_evidence "
                "WHERE investigation_id = :i GROUP BY evidence_id HAVING count(*) > 1) d"
            ),
            {"i": investigation_id},
        )
    assert duplicates == 0


async def test_sigkill_while_applying_an_answer_resumes_exactly_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner_engine: Engine,
    db_urls: DatabaseUrls,
    tmp_path: Path,
) -> None:
    """Answer stored → a worker resumes the graph and is killed inside `apply_answer` → a
    fresh process resumes from the durable checkpoint and applies the answer once."""
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    files, _ = files_of(options(7, "discount_spike"))
    dataset_id = await ingest(app, client, tenant.business_id, files)
    body = await investigate(app, client, tenant.business_id, dataset_id)
    assert body["status"] == "awaiting_human"
    awaiting = body["awaiting"]
    response = await answer(
        client,
        tenant.business_id,
        body["id"],
        awaiting["id"],
        {"checkpoint_id": awaiting["checkpoint_id"], "option_id": "planned_campaign"},
    )
    assert response.status_code == 202

    marker = tmp_path / "worker_paused"
    killed = await spawn_worker(db_urls.app, marker, node="apply_answer")
    try:
        async with asyncio.timeout(30):
            while not marker.exists():
                assert killed.returncode is None, "Worker exited before applying the answer."
                await asyncio.sleep(0.05)
    finally:
        if killed.returncode is None:
            killed.kill()
        await asyncio.wait_for(killed.communicate(), timeout=5)
    assert killed.returncode is not None
    assert killed.returncode < 0
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT status FROM clarifications")) == "answered"
        assert conn.scalar(text("SELECT applied_at FROM human_inputs")) is None

    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE status = 'running'"
            )
        )
    restarted = await spawn_worker(db_urls.app)
    try:
        await asyncio.wait_for(restarted.communicate(), timeout=60)
        assert restarted.returncode == 0, "Fresh worker must apply the answer and finish."
    finally:
        if restarted.returncode is None:
            restarted.kill()
            await restarted.communicate()

    after = await detail(client, tenant.business_id, body["id"])
    assert after["version"] == 2
    assert after["status"] == "awaiting_human"  # the next question, not a repeat
    assert after["awaiting"]["id"] != awaiting["id"]
    timeline = await events(client, tenant.business_id, body["id"])
    types = Counter(e["type"] for e in timeline)
    assert types["human.context_added"] == 1
    assert types["hypotheses.added"] == 1
    assert types["version.created"] == 2
    resumed = [e for e in timeline if e["type"] == "run.resumed"]
    assert [e["payload"]["next"] for e in resumed] == [["await_clarification"], ["apply_answer"]]
    assert [e["seq"] for e in timeline] == list(range(1, len(timeline) + 1))
    with owner_engine.connect() as conn:
        applied = conn.scalar(text("SELECT count(applied_at) FROM human_inputs"))
        first = conn.scalar(text("SELECT status FROM clarifications ORDER BY created_at LIMIT 1"))
    assert (applied, first) == (1, "applied")
    final = await finish(app, client, tenant.business_id, after)
    assert final["status"] == "completed"


async def test_sigkill_while_applying_an_approval_applies_it_exactly_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner_engine: Engine,
    db_urls: DatabaseUrls,
    tmp_path: Path,
) -> None:
    """Approval stored → a worker resumes the plan graph and is killed inside
    `apply_decision` → a fresh process resumes the paused checkpoint and approves once."""
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    body = await completed(app, client, tenant.business_id)
    proposed = await planned(app, client, tenant.business_id, body["id"])
    instance = proposed["current"]["content"]["selected"][0]
    valid = await decided(
        app,
        client,
        tenant.business_id,
        proposed,
        "request-changes",
        edits={
            "assumptions": {instance: {"review_cost": {"low": "1", "mid": "2", "high": "3"}}},
            "budget_amount": "10",
        },
    )
    assert valid["current"]["valid"] is True
    response = await decide(client, tenant.business_id, valid["id"], "approve", binding(valid))
    assert response.status_code == 202

    marker = tmp_path / "worker_paused"
    killed = await spawn_worker(db_urls.app, marker, "apply_decision", "app.recovery.graph")
    try:
        async with asyncio.timeout(30):
            while not marker.exists():
                assert killed.returncode is None, "Worker exited before applying the approval."
                await asyncio.sleep(0.05)
    finally:
        if killed.returncode is None:
            killed.kill()
        await asyncio.wait_for(killed.communicate(), timeout=5)
    assert killed.returncode is not None
    assert killed.returncode < 0
    assert (await detail_plan(client, tenant.business_id, valid["id"]))["status"] == "running"

    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE status = 'running'"
            )
        )
    restarted = await spawn_worker(db_urls.app)
    try:
        await asyncio.wait_for(restarted.communicate(), timeout=60)
        assert restarted.returncode == 0, "Fresh worker must apply the approval and finish."
    finally:
        if restarted.returncode is None:
            restarted.kill()
            await restarted.communicate()
    after = await detail_plan(client, tenant.business_id, valid["id"])
    assert after["status"] == "approved"
    assert after["approved"]["version"] == valid["current"]["version"]
    assert after["approved"]["content_hash"] == valid["current"]["content_hash"]
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT decision, outcome FROM plan_decisions ORDER BY created_at")
        ).all()
    assert [tuple(r) for r in rows] == [("request_changes", "applied"), ("approve", "applied")]
