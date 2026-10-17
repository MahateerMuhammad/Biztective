"""Opt-in smoke test against the real Google Sheets API (AMD-18 completion gate).

CI runs the fake-Google suite instead. To run it:

1. Create a Google Cloud service account with the Sheets API enabled and download its key.
2. Create an empty spreadsheet and share it with the service account email as Editor.
3. ROOT_LIVE_SHEETS_TESTS=1 GOOGLE_SERVICE_ACCOUNT_JSON="$(base64 < key.json)" \
   ROOT_LIVE_SPREADSHEET_ID=<id> pytest tests/actions/test_live_sheets.py -s

The test stands in for the owner by writing the verification code into ROOT_VERIFY!A1 with
the same service account, executes an approved plan through the normal job, then verifies the
result independently with a second client and a fresh token. Each run writes new rows (a new
plan and action); nothing is deleted. No credentials are printed.
"""

import asyncio
import os

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from app.actions import payload as payloads
from app.actions.executor import ActionRuntime
from app.actions.sheets import SheetsClient, TokenCache, load_service_account
from tests.actions.support import action, approved_plan, bind, binding, execute, verify
from tests.helpers import create_tenant, login, run_jobs

pytestmark = pytest.mark.skipif(
    os.environ.get("ROOT_LIVE_SHEETS_TESTS") != "1",
    reason="Real Google Sheets requires explicit ROOT_LIVE_SHEETS_TESTS=1 opt-in.",
)


async def test_live_sheets_execution_is_independently_verified(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    encoded = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sid = os.environ.get("ROOT_LIVE_SPREADSHEET_ID")
    assert encoded, "Set GOOGLE_SERVICE_ACCOUNT_JSON."
    assert sid, "Set ROOT_LIVE_SPREADSHEET_ID."
    account = load_service_account(encoded)
    runtime: ActionRuntime = app.state.actions
    saved = (runtime.account, runtime.transport, runtime.tokens)
    runtime.account, runtime.transport, runtime.tokens = account, None, TokenCache()
    try:
        owner = create_tenant(owner_engine, "live")
        assert (await login(client, owner.email)).status_code == 200
        plan = await approved_plan(app, client, owner, queue=True)

        created = await bind(client, owner, sid)
        assert created.status_code == 201, created.text
        async with runtime.client() as sheets:
            if "ROOT_VERIFY" not in await sheets.titles(sid):
                await sheets.add_tabs(sid, ["ROOT_VERIFY"])
            await sheets.put_rows(
                sid, {"'ROOT_VERIFY'!A1": [[created.json()["verification_code"]]]}
            )
        verified = await verify(client, owner, created.json()["id"])
        assert verified.status_code == 200, verified.text

        response = await execute(client, owner, plan["id"], binding(plan))
        assert response.status_code == 202, response.text
        action_id = response.json()["id"]
        await run_jobs(app)
        done = await action(client, owner, action_id)
        assert done["status"] == "verified", (done["error_code"], done["attempts"])

        # Independent check: a separate client with its own token reads the sheet back.
        async with httpx.AsyncClient(timeout=30) as http:
            reader = SheetsClient(
                account=account,
                http=http,
                tokens=TokenCache(),
                max_retries=3,
                backoff=1.0,
                sleep=asyncio.sleep,
            )
            tabs = done["payload"]["tabs"]
            values = await reader.read(sid, [payloads.tab_range(t["title"]) for t in tabs])
        for tab, rows in zip(tabs, values, strict=True):
            mine = [r for r in rows if r and r[0].startswith(action_id.replace("-", ""))]
            assert len(mine) == len(tab["rows"]), tab["title"]
            assert {r[0] for r in mine} == {r[0] for r in tab["rows"]}
        print(  # noqa: T201 - evidence for the engineering log, no secrets
            f"\nLIVE OK action={action_id} spreadsheet={sid} rows={done['rows']} "
            f"ranges={done['external_ref']['updated_ranges']} "
            f"calls={[c['op'] for c in done['attempts'][-1]['calls']]}"
        )
    finally:
        runtime.account, runtime.transport, runtime.tokens = saved
