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
    detail,
    events,
    files_of,
    ingest,
    investigate,
    options,
    signature,
    start,
)

WORKER = """
import asyncio
import os
from pathlib import Path

from app.investigation.graph import Nodes
from app.main import create_app
from tests.conftest import make_settings
from tests.helpers import run_jobs

async def main():
    marker = os.environ.get("ROOT_RESTART_MARKER")
    if marker:
        async def pause(self, state):
            Path(marker).write_text("ready", encoding="utf-8")
            await asyncio.Event().wait()
        Nodes.verify_evidence = pause
    settings = make_settings(
        os.environ["ROOT_RESTART_DB_URL"], llm_provider="disabled", anthropic_api_key=None
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        assert await run_jobs(application) == 1

asyncio.run(main())
"""


async def spawn_worker(database_url: str, marker: Path | None = None) -> asyncio.subprocess.Process:
    environment = dict(os.environ)
    environment["ROOT_RESTART_DB_URL"] = database_url
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
