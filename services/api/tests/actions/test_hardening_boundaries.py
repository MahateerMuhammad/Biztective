"""External outcome boundaries that must fail closed without duplicating effects."""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.actions import payload
from app.actions.bridge import BridgeClient
from app.actions.sheets import SheetsError
from tests.actions.fake_bridge import BRIDGE_URL, TOKEN
from tests.actions.fake_google import SID, FakeGoogle
from tests.actions.support import (
    action,
    approved_plan,
    binding,
    connect,
    execute,
    make_due,
    rows_of,
)
from tests.helpers import add_member, create_tenant, create_user, login, run_jobs


async def test_demoted_requester_is_refused_when_approver_remains_owner(
    app: FastAPI, client: httpx.AsyncClient, google: FakeGoogle, owner_engine: Engine
) -> None:
    owner = create_tenant(owner_engine, "approver")
    assert (await login(client, owner.email)).status_code == 200
    plan = await approved_plan(app, client, owner)
    await connect(client, owner, google)
    requester_email = "requester@example.test"
    requester_id = create_user(owner_engine, requester_email)
    add_member(owner_engine, owner.business_id, requester_id, "owner")
    assert (await login(client, requester_email)).status_code == 200
    requested = await execute(client, owner, plan["id"], binding(plan))
    assert requested.status_code == 202
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET role = 'analyst' WHERE user_id = :u"), {"u": requester_id}
        )
    assert (await login(client, owner.email)).status_code == 200
    await run_jobs(app)
    refused = await action(client, owner, requested.json()["id"])
    assert (refused["status"], refused["error_code"]) == ("failed", "requester_not_owner")
    assert google.ops("append") == 0


async def test_attempt_deadline_after_applied_write_reconciles_on_retry(
    app: FastAPI,
    client: httpx.AsyncClient,
    google: FakeGoogle,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_tenant(owner_engine, "owner")
    assert (await login(client, owner.email)).status_code == 200
    plan = await approved_plan(app, client, owner)
    await connect(client, owner, google)
    requested = await execute(client, owner, plan["id"], binding(plan))
    assert requested.status_code == 202
    gate = asyncio.Event()

    async def stall_after_write(request: httpx.Request) -> httpx.Response:
        response = await google.handle(request)
        if google.log[-1] == "append":
            await gate.wait()
        return response

    app.state.actions.transport = httpx.MockTransport(stall_after_write)
    monkeypatch.setattr("app.actions.executor.ATTEMPT_TIMEOUT_SECONDS", 1.0)
    await run_jobs(app)
    waiting = await action(client, owner, requested.json()["id"])
    assert (waiting["status"], waiting["error_code"]) == ("pending", "attempt_timeout")
    assert waiting["attempts"][0]["writes_sent"] is True
    assert len(rows_of(google, payload.REVIEW_TAB)) == 1
    app.state.actions.transport = google.transport()
    monkeypatch.setattr("app.actions.executor.ATTEMPT_TIMEOUT_SECONDS", 240.0)
    make_due(owner_engine)
    await run_jobs(app)
    assert (await action(client, owner, requested.json()["id"]))["status"] == "verified"
    assert google.ops("append") == 1


@pytest.mark.parametrize("targets", [0, payload.MAX_ROWS + 1])
def test_payload_refuses_empty_effect_or_oversized_queue(targets: int) -> None:
    entry = {
        "id": "strategy",
        "rank": 1,
        "strategy": "S_GOODWILL_OUTREACH",
        "action_types": ["customer_contact"],
        "targeting": {"selected": [f"customer-{n}" for n in range(targets)]},
    }
    with pytest.raises(payload.NothingToExecuteError if targets == 0 else payload.TooManyRowsError):
        payload.build(
            action=uuid.uuid4(),
            plan_id=uuid.uuid4(),
            version=1,
            content_hash="0" * 64,
            content={"selected": ["strategy"], "strategies": [entry], "currency": "USD"},
            approval_id=uuid.uuid4(),
            approved_at="2026-10-17T00:00:00+00:00",
        )


@pytest.mark.parametrize(
    ("body", "code", "retryable"),
    [
        ([], "integration_misconfigured", False),
        ({"ok": False, "status": 503}, "provider_unavailable", True),
        ({"ok": False, "status": "bad"}, "destination_invalid", False),
    ],
)
async def test_malformed_bridge_answer_is_never_success(
    body: object,
    code: str,
    retryable: bool,
) -> None:
    async def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async def sleep(_: float) -> None:
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = BridgeClient(
            url=BRIDGE_URL, token=TOKEN, http=http, max_retries=0, backoff=0, sleep=sleep
        )
        with pytest.raises(SheetsError) as error:
            await client.append(SID, "'ROOT Review Tasks'!A:L", [["key", "task"]])
    assert error.value.code == code
    assert error.value.retryable is retryable
    if retryable:
        assert error.value.ambiguous
