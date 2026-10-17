"""Execution through the Apps Script bridge (AMD-19): the same executor, reconciliation and
verification, reached through a business-owned web app instead of a service account."""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from app.actions import payload as payloads
from app.actions.executor import ActionRuntime
from tests.actions.fake_bridge import BRIDGE_URL, TOKEN, FakeBridge
from tests.actions.fake_google import OTHER_SID, SID, FakeGoogle, ProcessDied
from tests.actions.support import (
    action,
    approved_plan,
    base,
    bind,
    binding,
    connect,
    execute,
    make_due,
    rows_of,
    verify,
)
from tests.helpers import Tenant, create_tenant, login, run_jobs

REVIEW = payloads.REVIEW_TAB
QUEUE = payloads.QUEUE_TAB


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


@pytest.fixture
def bridge(app: FastAPI, google: FakeGoogle) -> Iterator[FakeBridge]:
    """The runtime talks to a deployed bridge; there is no service account at all."""
    fake = FakeBridge(google=google, bound_to=SID)
    runtime: ActionRuntime = app.state.actions
    saved = (runtime.account, runtime.bridge_url, runtime.bridge_token, runtime.transport)
    runtime.account, runtime.bridge_url, runtime.bridge_token = None, BRIDGE_URL, TOKEN
    runtime.transport = fake.transport()
    yield fake
    runtime.account, runtime.bridge_url, runtime.bridge_token, runtime.transport = saved


async def started(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, google: FakeGoogle, queue: bool = False
) -> tuple[dict[str, Any], str]:
    plan = await approved_plan(app, client, owner, queue=queue)
    await connect(client, owner, google)
    response = await execute(client, owner, plan["id"], binding(plan))
    assert response.status_code == 202, response.text
    return plan, str(response.json()["id"])


async def test_bridge_execution_is_verified_end_to_end(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    bridge: FakeBridge,
) -> None:
    listed = (await client.get(f"{base(owner)}/integrations")).json()
    assert listed["available"] is True
    assert listed["mode"] == "apps_script"
    _, action_id = await started(app, client, owner, google, queue=True)
    created = (await client.get(f"{base(owner)}/integrations")).json()["items"][0]
    assert created["service_account_email"] is None  # nothing to share with a robot account
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified", (done["error_code"], done["attempts"])
    for title in (REVIEW, QUEUE):
        rows = next(t["rows"] for t in done["payload"]["tabs"] if t["title"] == title)
        assert rows_of(google, title) == rows
    assert done["verification"]["ok"] is True
    assert "token" not in google.log  # no OAuth exchange in this mode
    ops = [c["op"] for c in done["attempts"][0]["calls"]]
    assert ops.count("append") == 4  # POST + redirect fetch, per tab
    assert ops[-2:] == ["read", "read"]  # the read-back, also through the redirect


async def test_timeout_after_the_script_ran_is_reconciled_without_duplicates(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    bridge: FakeBridge,
) -> None:
    _, action_id = await started(app, client, owner, google)
    google.fail("append", "timeout_after")
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert len(rows_of(google, REVIEW)) == 1
    assert google.ops("append") == 1


async def test_restart_after_the_script_ran_resumes_without_duplicates(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    bridge: FakeBridge,
    owner_engine: Engine,
) -> None:
    _, action_id = await started(app, client, owner, google)
    google.fail("append", "crash_after")
    with pytest.raises(ProcessDied):
        await run_jobs(app)
    make_due(owner_engine)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert len(rows_of(google, REVIEW)) == 1


async def test_rate_limits_and_server_errors_are_retried(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    bridge: FakeBridge,
    sleeps: list[float],
) -> None:
    _, action_id = await started(app, client, owner, google)
    google.fail("read", "429")
    google.fail("append", "500")
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert 7.0 in sleeps
    assert len(rows_of(google, REVIEW)) == 1


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("wrong_token", "integration_credentials_invalid"),
        ("html", "integration_misconfigured"),
        ("evil_redirect", "integration_misconfigured"),
    ],
)
async def test_misconfigured_bridges_fail_without_writing(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    bridge: FakeBridge,
    setup: str,
    code: str,
) -> None:
    _, action_id = await started(app, client, owner, google)
    if setup == "wrong_token":
        bridge.token = "rotated-" + "y" * 40
    else:
        google.fail("metadata", setup)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("failed", code)
    assert google.ops("append") == 0
    assert rows_of(google, REVIEW) == []


async def test_only_the_scripts_own_spreadsheet_can_be_bound(
    client: httpx.AsyncClient, owner: Tenant, google: FakeGoogle, bridge: FakeBridge
) -> None:
    google.sheet(OTHER_SID)
    created = await bind(client, owner, OTHER_SID)
    google.spreadsheets[OTHER_SID]["ROOT_VERIFY"] = [[created.json()["verification_code"]]]
    refused = await verify(client, owner, created.json()["id"])
    assert (refused.status_code, refused.json()["errors"][0]["code"]) == (
        422,
        "integration_permission_denied",
    )


async def test_bridge_token_never_leaks(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    bridge: FakeBridge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _, action_id = await started(app, client, owner, google)
    await run_jobs(app)
    bodies = [
        (await client.get(f"{base(owner)}/integrations")).text,
        json.dumps(await action(client, owner, action_id)),
    ]
    logged = "\n".join(r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records)
    assert TOKEN not in logged
    assert all(TOKEN not in b for b in bodies)
    assert str(uuid.UUID(action_id)) in bodies[1]
