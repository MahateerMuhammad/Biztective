"""Executing approved recovery plans in Google Sheets (AMD-18) against a deterministic fake
Google: authorization, idempotency, reconciliation, retries, verification and history."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.actions import payload as payloads
from app.actions.executor import ActionRuntime
from app.jobs.queue import LeasedJob
from app.jobs.worker import RetryableJobError
from tests.actions.fake_google import SID, FakeGoogle, ProcessDied
from tests.actions.support import (
    action,
    approved_plan,
    base,
    binding,
    connect,
    execute,
    make_due,
    retry,
    rows_of,
    table_count,
)
from tests.helpers import (
    Tenant,
    add_member,
    create_tenant,
    create_user,
    csrf_headers,
    login,
    run_jobs,
)
from tests.recovery.support import decide, detail

REVIEW = payloads.REVIEW_TAB
QUEUE = payloads.QUEUE_TAB


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


@pytest.fixture
async def plan(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> dict[str, Any]:
    return await approved_plan(app, client, owner)


async def started(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> str:
    """Connect the spreadsheet and request execution; returns the action id (not yet run)."""
    await connect(client, owner, google)
    response = await execute(client, owner, plan["id"], binding(plan))
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def expected_rows(details: dict[str, Any], title: str) -> list[list[str]]:
    return next(t["rows"] for t in details["payload"]["tabs"] if t["title"] == title)


def attempts(details: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(a["status"], a["error_code"]) for a in details["attempts"]]


# ------------------------------------------------------------------------- happy path


async def test_authorized_write_is_verified_by_independent_read_back(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
) -> None:
    plan = await approved_plan(app, client, owner, queue=True)
    # Approval and workflow resumes create nothing outside the database.
    await run_jobs(app)
    assert (await detail(client, owner.business_id, plan["id"]))["execution"]["actions"] == []
    assert google.log == []
    action_id = await started(app, client, owner, google, plan)
    pending = await action(client, owner, action_id)
    assert pending["status"] == "pending"
    assert pending["attempts"] == []  # the intent is persisted before any external call
    assert google.ops("append") == 0

    await run_jobs(app)

    done = await action(client, owner, action_id)
    assert done["status"] == "verified", (done["error_code"], done["attempts"], done["audit"])
    assert done["error_code"] is None
    assert done["rows"] == {REVIEW: 1, QUEUE: 3}
    # The destination holds exactly the approved payload, once, under our headers.
    for title in (REVIEW, QUEUE):
        assert google.spreadsheets[SID][title][0] == payloads.LAYOUTS[title]
        assert rows_of(google, title) == expected_rows(done, title)
    review = rows_of(google, REVIEW)[0]
    assert review[0] == f"{uuid.UUID(action_id).hex}-0001"
    assert review[1:4] == [plan["id"], str(plan["approved"]["version"]), "S_QUALITY_REVIEW"]
    assert all(r[-1] == "to_review" for r in rows_of(google, QUEUE))
    # Keys are unique across tabs (a per-tab numbering once hid a row from reconciliation).
    assert [r[0].rsplit("-", 1)[1] for r in rows_of(google, QUEUE)] == ["0002", "0003", "0004"]
    assert {r["action_type"] for r in done["payload"]["not_executed"]} == {"customer_offer"}
    assert done["verification"]["ok"] is True
    assert done["verification"]["tabs"] == {
        REVIEW: {"expected": 1, "found": 1},
        QUEUE: {"expected": 3, "found": 3},
    }
    assert done["external_ref"]["spreadsheet_id"] == SID
    assert set(done["external_ref"]["updated_ranges"]) == {REVIEW, QUEUE}
    assert done["destination"]["url"] == f"https://docs.google.com/spreadsheets/d/{SID}"
    assert attempts(done) == [("completed", None)]
    assert done["attempts"][0]["writes_sent"] is True
    ops = [c["op"] for c in done["attempts"][0]["calls"]]
    assert ops.count("append") == 2  # one batched append per tab
    assert ops[-1] == "read"  # the read-back after the writes
    assert [a["action"] for a in done["audit"]] == [
        "action.requested",
        "action.succeeded",
        "action.verified",
    ]
    assert done["audit"][0]["actor_user_id"] == str(owner.user_id)
    plan_view = await detail(client, owner.business_id, plan["id"])
    assert plan_view["execution"]["integration_ready"] is True
    assert [a["status"] for a in plan_view["execution"]["actions"]] == ["verified"]
    listed = await client.get(f"{base(owner)}/recovery-plans/{plan['id']}/actions")
    assert [a["id"] for a in listed.json()["items"]] == [action_id]
    assert table_count(owner_engine, "plan_actions") == 1


# ------------------------------------------------------------------------ authorization


async def test_unapproved_or_rejected_plans_are_never_written(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
) -> None:
    from tests.recovery.support import binding as decision_binding
    from tests.recovery.support import completed, decided, planned

    await connect(client, owner, google)
    calls_before = list(google.log)
    investigation = await completed(app, client, owner.business_id)
    waiting = await planned(app, client, owner.business_id, investigation["id"])
    current = {
        "version": waiting["current"]["version"],
        "content_hash": waiting["current"]["content_hash"],
    }
    refused = await execute(client, owner, waiting["id"], current)
    assert (refused.status_code, refused.json()["code"]) == (409, "plan_not_approved")
    rejected = await decided(app, client, owner.business_id, waiting, "reject")
    assert rejected["status"] == "rejected"
    refused = await execute(client, owner, waiting["id"], current)
    assert (refused.status_code, refused.json()["code"]) == (409, "plan_not_approved")
    # A forged approval of the rejected version is refused too.
    forged = await decide(
        client, owner.business_id, waiting["id"], "approve", decision_binding(waiting)
    )
    assert forged.json()["code"] == "already_decided"
    await run_jobs(app)
    assert table_count(owner_engine, "plan_actions") == 0
    assert table_count(owner_engine, "action_executions") == 0
    with owner_engine.connect() as conn:
        jobs = conn.execute(text("SELECT count(*) FROM jobs WHERE type = 'execute_action'"))
        assert jobs.scalar_one() == 0
    assert google.log == calls_before  # no call reached Google at all
    assert google.ops("append") == 0


async def test_approval_binding_must_match_exactly(
    client: httpx.AsyncClient, owner: Tenant, google: FakeGoogle, plan: dict[str, Any]
) -> None:
    await connect(client, owner, google)
    good = binding(plan)
    cases = [
        ({**good, "content_hash": "0" * 64}, 409, "content_hash_mismatch"),
        ({**good, "version": good["version"] - 1}, 409, "stale_plan_version"),
        ({**good, "spreadsheet_id": SID}, 422, "validation_error"),  # no client destination
        ({**good, "payload": {"tabs": []}}, 422, "validation_error"),
        ({**good, "status": "verified"}, 422, "validation_error"),
    ]
    for body, status, code in cases:
        response = await execute(client, owner, plan["id"], body)
        assert (response.status_code, response.json()["code"]) == (status, code), body
    assert google.ops("append") == 0


async def test_execution_needs_a_verified_destination(
    client: httpx.AsyncClient, owner: Tenant, google: FakeGoogle, plan: dict[str, Any]
) -> None:
    response = await execute(client, owner, plan["id"], binding(plan))
    assert (response.status_code, response.json()["code"]) == (409, "integration_not_ready")
    google.sheet()
    created = (
        await client.post(
            f"{base(owner)}/integrations/google-sheets",
            json={"spreadsheet": SID},
            headers=await csrf_headers(client),
        )
    ).json()
    assert created["status"] == "pending_verification"
    response = await execute(client, owner, plan["id"], binding(plan))
    assert response.json()["code"] == "integration_not_ready"  # unproven binding is not enough


async def test_only_owners_execute_and_other_businesses_see_nothing(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    for role in ("analyst", "viewer"):
        email = f"{role}@example.test"
        user_id = create_user(owner_engine, email)
        add_member(owner_engine, owner.business_id, user_id, role)
        transport = httpx.ASGITransport(app=app, client=("203.0.113.40", 50040))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            assert (await login(c, email)).status_code == 200
            denied = await execute(c, owner, plan["id"], binding(plan))
            assert denied.status_code == 403
            assert (await retry(c, owner, action_id)).status_code == 403
            seen = await c.get(f"{base(owner)}/actions/{action_id}")
            assert seen.status_code == 200  # read access to history is fine
    other = create_tenant(owner_engine, "mallory")
    transport = httpx.ASGITransport(app=app, client=("203.0.113.50", 50050))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        assert (await login(c, other.email)).status_code == 200
        # Their own business id with our plan and action ids: nothing is found.
        assert (await execute(c, other, plan["id"], binding(plan))).status_code == 404
        assert (await c.get(f"{base(other)}/actions/{action_id}")).status_code == 404
        assert (await retry(c, other, action_id)).status_code == 404
        listed = await c.get(f"{base(other)}/recovery-plans/{plan['id']}/actions")
        assert listed.json()["items"] == []
        # Our business id: not a member.
        assert (await execute(c, owner, plan["id"], binding(plan))).status_code in (403, 404)


async def test_stale_approval_stops_a_pending_action_before_any_call(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    calls = len(google.log)
    # The owner edits the approved plan before the executor runs: the approval lapses.
    revised = await client.post(
        f"{base(owner)}/recovery-plans/{plan['id']}/revisions",
        json={**binding(plan), "edits": {"budget_amount": "90000"}},
        headers=await csrf_headers(client),
    )
    assert revised.status_code == 202, revised.text
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("failed", "approval_no_longer_valid")
    assert done["attempts"] == []
    assert [a["action"] for a in done["audit"]] == ["action.requested", "action.refused"]
    assert google.log[calls:] == []
    again = await retry(client, owner, action_id)
    assert (again.status_code, again.json()["code"]) == (409, "approval_no_longer_valid")


async def test_demoted_requester_or_approver_has_no_effect(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET role = 'analyst' WHERE user_id = :u"),
            {"u": owner.user_id},
        )
    await run_jobs(app)
    with owner_engine.connect() as conn:
        status, code = conn.execute(
            text("SELECT status, error_code FROM plan_actions WHERE id = :a"), {"a": action_id}
        ).one()
    assert (status, code) == ("failed", "approver_not_owner")
    assert google.ops("append") == 0


# ------------------------------------------------------------------------ idempotency


async def test_repeated_clicks_and_replays_create_one_action(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    await connect(client, owner, google)
    first = await execute(client, owner, plan["id"], binding(plan), idempotency_key="click-0001")
    replay = await execute(client, owner, plan["id"], binding(plan), idempotency_key="click-0001")
    second = await execute(client, owner, plan["id"], binding(plan))
    assert (first.status_code, first.json()["created"]) == (202, True)
    assert replay.status_code == 202
    assert replay.json() == first.json()
    assert (second.status_code, second.json()["created"]) == (200, False)
    assert first.json()["id"] == second.json()["id"]
    await run_jobs(app)
    third = await execute(client, owner, plan["id"], binding(plan))
    assert (third.status_code, third.json()["status"]) == (200, "verified")
    # A repeated approval cannot create a second authorization either.
    from tests.recovery.support import decide as decide_plan

    reapproved = await decide_plan(
        client,
        owner.business_id,
        plan["id"],
        "approve",
        {**binding(plan), "checkpoint_id": "anything"},
    )
    assert reapproved.status_code == 409
    await run_jobs(app)
    assert table_count(owner_engine, "plan_actions") == 1
    assert len(rows_of(google, REVIEW)) == 1
    assert google.ops("append") == 1
    with owner_engine.connect() as conn:
        jobs = conn.execute(text("SELECT count(*) FROM jobs WHERE type = 'execute_action'"))
        assert jobs.scalar_one() == 1


async def test_simultaneous_requests_have_one_effect(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    await connect(client, owner, google)
    responses = await asyncio.gather(
        *(execute(client, owner, plan["id"], binding(plan)) for _ in range(6))
    )
    assert sorted(r.status_code for r in responses) == [200] * 5 + [202]
    assert len({r.json()["id"] for r in responses}) == 1
    await run_jobs(app)
    assert table_count(owner_engine, "plan_actions") == 1
    assert len(rows_of(google, REVIEW)) == 1


async def test_concurrent_workers_write_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    runtime: ActionRuntime = app.state.actions
    google.gate = asyncio.Event()

    def job() -> LeasedJob:
        return LeasedJob(
            id=uuid.uuid7(),
            type="execute_action",
            business_id=owner.business_id,
            payload={"action_id": action_id},
            attempts=1,
            max_attempts=3,
        )

    first = asyncio.create_task(runtime.run(app.state.sessionmaker, job()))
    await google.writing.wait()  # the first worker is inside its write
    with pytest.raises(RetryableJobError) as busy:
        await runtime.run(app.state.sessionmaker, job())
    assert busy.value.code == "action_busy"
    google.gate.set()
    await first
    await runtime.run(app.state.sessionmaker, job())  # later: nothing left to do
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert len(done["attempts"]) == 1
    assert len(rows_of(google, REVIEW)) == 1
    assert google.ops("append") == 1


# ---------------------------------------------------------------- provider failures


async def test_timeout_before_the_write_is_retried_after_a_fresh_read(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", "timeout_before")
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert len(rows_of(google, REVIEW)) == 1
    calls = [(c["op"], c["status"]) for c in done["attempts"][0]["calls"]]
    timeout = calls.index(("append", "timeout"))
    assert ("read", 200) in calls[timeout:]  # re-read before writing again
    assert calls[timeout + 1 :].count(("append", 200)) == 1


async def test_timeout_after_remote_success_never_duplicates(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", "timeout_after")
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert google.ops("append") == 1  # the reconciling read found the rows: no second write
    assert len(rows_of(google, REVIEW)) == 1
    assert done["verification"]["duplicated"] == []


async def test_server_error_after_the_write_is_reconciled(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", "500_after")
    google.fail("append", "reset_after")
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert google.ops("append") == 1
    assert len(rows_of(google, REVIEW)) == 1


async def test_rate_limits_honour_retry_after(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    sleeps: list[float],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("read", "429")
    google.fail("append", "429")
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert sleeps.count(7.0) == 2  # the server's Retry-After, not our own schedule
    assert len(rows_of(google, REVIEW)) == 1
    statuses = [c["status"] for c in done["attempts"][0]["calls"]]
    assert statuses.count(429) == 2


async def test_backoff_is_exponential_and_bounded(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    sleeps: list[float],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("metadata", "500", "500", "500")
    await run_jobs(app)
    assert (await action(client, owner, action_id))["status"] == "verified"
    assert sleeps[:3] == [1.0, 2.0, 4.0]


async def test_expired_token_is_refreshed_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.expire_tokens()  # the cached token is no longer accepted
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert google.ops("token") == 2


async def test_revoked_credentials_fail_without_writing(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.expire_tokens()
    google.credentials_valid = False
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("failed", "integration_credentials_invalid")
    assert attempts(done) == [("failed", "integration_credentials_invalid")]
    assert done["attempts"][0]["writes_sent"] is False
    assert google.ops("append") == 0
    assert [a["action"] for a in done["audit"]][-1] == "action.failed"


async def test_network_disconnection_is_retried_by_the_queue(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("metadata", *["connect"] * 5)  # more than one run's retries
    await run_jobs(app)
    waiting = await action(client, owner, action_id)
    assert (waiting["status"], waiting["error_code"]) == ("pending", "network_error")
    assert attempts(waiting) == [("failed", "network_error")]
    make_due(owner_engine)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert attempts(done) == [("failed", "network_error"), ("completed", None)]
    assert len(rows_of(google, REVIEW)) == 1


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("missing", "destination_not_found"),
        ("unshared", "integration_permission_denied"),
        ("foreign_layout", "destination_layout_mismatch"),
    ],
)
async def test_invalid_destination_fails_without_writing(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    setup: str,
    code: str,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    if setup == "missing":
        del google.spreadsheets[SID]
    elif setup == "unshared":
        google.shared.discard(SID)
    else:
        google.spreadsheets[SID][REVIEW] = [["Name", "Phone"], ["Ada", "123"]]
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("failed", code)
    assert google.ops("append") == 0
    if setup == "foreign_layout":
        assert google.spreadsheets[SID][REVIEW] == [["Name", "Phone"], ["Ada", "123"]]


async def test_partial_failure_goes_to_manual_review_and_retry_completes_it(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
) -> None:
    plan = await approved_plan(app, client, owner, queue=True)
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", "none", "403")  # the review tab is written, the queue tab refused
    await run_jobs(app)
    held = await action(client, owner, action_id)
    assert (held["status"], held["error_code"]) == (
        "manual_review",
        "integration_permission_denied",
    )
    assert len(rows_of(google, REVIEW)) == 1
    assert rows_of(google, QUEUE) == []
    assert [a["action"] for a in held["audit"]][-1] == "action.manual_review"
    retried = await retry(client, owner, action_id)
    assert retried.status_code == 202
    assert retried.json()["queued"] is True
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified", str(done["verification"])
    assert len(rows_of(google, REVIEW)) == 1  # not written twice
    assert len(rows_of(google, QUEUE)) == 3
    assert attempts(done) == [("failed", "integration_permission_denied"), ("completed", None)]
    assert [a["action"] for a in done["audit"]] == [
        "action.requested",
        "action.manual_review",
        "action.retry_requested",
        "action.succeeded",
        "action.verified",
    ]


@pytest.mark.parametrize("tamper", ["edited", "duplicated"])
async def test_verification_mismatch_goes_to_manual_review_without_overwriting(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    tamper: str,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    key = f"{uuid.UUID(action_id).hex}-0001"
    if tamper == "edited":
        seeded = [[key, "someone", "changed", "this"]]
    else:
        expected = expected_rows(await action(client, owner, action_id), REVIEW)[0]
        seeded = [expected, expected]
    google.spreadsheets[SID][REVIEW] = [payloads.LAYOUTS[REVIEW], *seeded]
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("manual_review", "verification_mismatch")
    listed = done["verification"]["mismatched" if tamper == "edited" else "duplicated"]
    assert listed == [key]
    assert google.ops("append") == 0  # never overwritten or re-appended automatically
    assert rows_of(google, REVIEW) == seeded
    # Retrying re-verifies; it still refuses to guess.
    assert (await retry(client, owner, action_id)).status_code == 202
    await run_jobs(app)
    assert (await action(client, owner, action_id))["status"] == "manual_review"
    assert google.ops("append") == 0


async def test_exhausted_retries_after_a_sent_write_need_review_then_recover(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", *["500"] * 9)  # every write of three runs fails ambiguously
    for _ in range(3):
        await run_jobs(app)
        make_due(owner_engine)
    held = await action(client, owner, action_id)
    assert (held["status"], held["error_code"]) == ("manual_review", "write_unconfirmed")
    assert len(held["attempts"]) == 3
    assert all(a["writes_sent"] for a in held["attempts"])
    assert rows_of(google, REVIEW) == []
    assert (await retry(client, owner, action_id)).status_code == 202
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert len(rows_of(google, REVIEW)) == 1


async def test_exhausted_retries_before_any_write_fail_cleanly(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("metadata", *["connect"] * 30)
    for _ in range(3):
        await run_jobs(app)
        make_due(owner_engine)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("failed", "network_error")
    assert not any(a["writes_sent"] for a in done["attempts"])
    assert google.ops("append") == 0


async def test_server_restart_during_execution_resumes_without_duplicates(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", "crash_after")  # the write lands, then the process dies
    with pytest.raises(ProcessDied):
        await run_jobs(app)
    assert len(rows_of(google, REVIEW)) == 1
    crashed = await action(client, owner, action_id)
    assert crashed["status"] == "pending"
    assert attempts(crashed) == [("started", None)]
    make_due(owner_engine)  # the job lease expires; the restarted worker picks it up
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert attempts(done) == [("interrupted", "worker_lost"), ("completed", None)]
    assert google.ops("append") == 1
    assert len(rows_of(google, REVIEW)) == 1


async def test_revoked_destination_stops_retries(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
) -> None:
    action_id = await started(app, client, owner, google, plan)
    integration = (await client.get(f"{base(owner)}/integrations")).json()["items"][0]
    revoked = await client.delete(
        f"{base(owner)}/integrations/{integration['id']}", headers=await csrf_headers(client)
    )
    assert revoked.json()["status"] == "revoked"
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("failed", "integration_not_verified")
    assert google.ops("append") == 0


# ------------------------------------------------------------------- payload safety


async def test_formula_injection_payloads_are_written_inert(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
) -> None:
    """Customer IDs come from uploaded data: hostile ones reach the sheet only as text."""
    plan = await approved_plan(app, client, owner, queue=True)
    hostile = ['=HYPERLINK("https://evil.example")', "+1+1", "-2", "@SUM(A1)", "\tTAB", "\rCR"]
    with owner_engine.begin() as conn:
        version = conn.execute(
            text("SELECT content FROM recovery_plan_versions WHERE plan_id = :p AND version = 2"),
            {"p": plan["id"]},
        ).scalar_one()
    outreach = next(s for s in version["strategies"] if s["strategy"] == "S_GOODWILL_OUTREACH")
    built = payloads.build(
        action=uuid.uuid4(),
        plan_id=uuid.UUID(plan["id"]),
        version=2,
        content_hash=plan["approved"]["content_hash"],
        content={
            **version,
            "strategies": [
                {**outreach, "targeting": {**outreach["targeting"], "selected": hostile}}
            ],
            "selected": [outreach["id"]],
        },
        approval_id=uuid.uuid4(),
        approved_at="2026-10-17T00:00:00+00:00",
    )
    cells = [row[5] for row in built["tabs"][0]["rows"]]
    assert cells == ["'" + h for h in hostile]
    assert all(not c.startswith(("=", "+", "-", "@", "\t", "\r")) for c in cells)
    # End to end, the real payload is written RAW (asserted by the fake on every write).
    action_id = await started(app, client, owner, google, plan)
    await run_jobs(app)
    assert (await action(client, owner, action_id))["status"] == "verified"


def test_cell_length_is_capped() -> None:
    from app.actions.sheets import MAX_CELL, sanitize

    assert len(sanitize("x" * 9000)) == MAX_CELL
    assert len(sanitize("=" + "x" * 9000)) == MAX_CELL
    assert sanitize("=1").startswith("'")


@pytest.mark.parametrize("after_op", ["metadata", "read", "append"])
async def test_revocation_between_provider_calls_stops_every_later_mutation(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
    after_op: str,
) -> None:
    plan = await approved_plan(app, client, owner, queue=True)
    action_id = await started(app, client, owner, google, plan)
    triggered = False

    async def revoke_after_call(request: httpx.Request) -> httpx.Response:
        nonlocal triggered
        response = await google.handle(request)
        if google.log[-1] == after_op and not triggered:
            triggered = True
            with owner_engine.begin() as conn:
                conn.execute(text("UPDATE integrations SET status = 'revoked'"))
        return response

    app.state.actions.transport = httpx.MockTransport(revoke_after_call)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert triggered
    assert done["error_code"] == "integration_not_verified"
    if after_op == "append":
        assert done["status"] == "manual_review"
        assert len(rows_of(google, REVIEW)) == 1
        assert rows_of(google, QUEUE) == []
        assert google.ops("append") == 1
    else:
        assert done["status"] == "failed"
        assert google.ops("append") == 0
        assert google.ops("put_rows") == 0
        if after_op == "metadata":
            assert google.ops("add_tabs") == 0


async def test_crashed_write_followed_by_revocation_preserves_unknown_outcome(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    google.fail("append", "crash_after")
    with pytest.raises(ProcessDied):
        await run_jobs(app)
    crashed = await action(client, owner, action_id)
    assert crashed["attempts"][0]["writes_sent"] is True
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE integrations SET status = 'revoked'"))
    make_due(owner_engine)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("manual_review", "integration_not_verified")
    assert len(rows_of(google, REVIEW)) == 1
    assert google.ops("append") == 1


async def test_restart_after_succeeded_commit_finishes_read_back(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    original = ActionRuntime._record_success

    # Any here forwards the private runtime signature unchanged to inject a process kill.
    async def crash_after_commit(self: ActionRuntime, *args: Any, **kwargs: Any) -> None:
        await original(self, *args, **kwargs)
        raise ProcessDied

    monkeypatch.setattr(ActionRuntime, "_record_success", crash_after_commit)
    with pytest.raises(ProcessDied):
        await run_jobs(app)
    assert (await action(client, owner, action_id))["status"] == "succeeded"
    monkeypatch.setattr(ActionRuntime, "_record_success", original)
    make_due(owner_engine)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert done["verification"]["ok"] is True
    assert google.ops("append") == 1
    assert attempts(done) == [("interrupted", "worker_lost"), ("completed", None)]


async def test_crash_on_final_job_lease_closes_attempt_with_unknown_outcome(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    owner_engine: Engine,
) -> None:
    action_id = await started(app, client, owner, google, plan)
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET max_attempts = 1 WHERE type = 'execute_action'"))
    google.fail("append", "crash_after")
    with pytest.raises(ProcessDied):
        await run_jobs(app)
    make_due(owner_engine)
    await run_jobs(app)
    done = await action(client, owner, action_id)
    assert (done["status"], done["error_code"]) == ("manual_review", "lease_exhausted")
    assert attempts(done) == [("interrupted", "lease_exhausted")]
    assert done["attempts"][0]["writes_sent"] is True
    assert google.ops("append") == 1
    assert len(rows_of(google, REVIEW)) == 1
    assert (await retry(client, owner, action_id)).status_code == 202
    await run_jobs(app)
    assert (await action(client, owner, action_id))["status"] == "verified"
    assert google.ops("append") == 1


@pytest.mark.parametrize("stale_outcome", ["resume", "permanent_failure", "retryable_failure"])
async def test_replaced_attempt_cannot_write_or_close_its_replacement(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    plan: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    stale_outcome: str,
) -> None:
    from app.actions.sheets import SheetsApi, SheetsError

    action_id = await started(app, client, owner, google, plan)
    runtime: ActionRuntime = app.state.actions
    job = LeasedJob(
        id=uuid.uuid7(),
        type="execute_action",
        business_id=owner.business_id,
        payload={"action_id": action_id},
        attempts=1,
        max_attempts=3,
    )
    paused = asyncio.Event()
    release = asyncio.Event()
    original = ActionRuntime._read
    first = True

    async def pause_first_read(
        self: ActionRuntime, client_: SheetsApi, sid: str, tabs: list[dict[str, Any]]
    ) -> dict[str, list[list[str]]]:
        nonlocal first
        found = await original(self, client_, sid, tabs)
        if first:
            first = False
            paused.set()
            await release.wait()
            if stale_outcome != "resume":
                raise SheetsError("stale_failure", retryable=stale_outcome == "retryable_failure")
        return found

    monkeypatch.setattr(ActionRuntime, "_read", pause_first_read)
    old = asyncio.create_task(runtime.run(app.state.sessionmaker, job))
    await paused.wait()
    # The same job was re-leased to a replacement. It owns the next action attempt.
    replacement = await runtime._begin(
        app.state.sessionmaker, owner.business_id, uuid.UUID(action_id), job.id
    )
    assert replacement is not None
    assert replacement.number == 2
    release.set()
    if stale_outcome == "retryable_failure":
        with pytest.raises(RetryableJobError):
            await old
    else:
        await old
    waiting = await action(client, owner, action_id)
    assert waiting["status"] == "pending"
    assert waiting["attempt_count"] == 2
    assert attempts(waiting) == [("interrupted", "worker_lost"), ("started", None)]
    assert google.ops("append") == 0
    await runtime._execute(
        app.state.sessionmaker, owner.business_id, uuid.UUID(action_id), replacement
    )
    done = await action(client, owner, action_id)
    assert done["status"] == "verified"
    assert google.ops("append") == 1
