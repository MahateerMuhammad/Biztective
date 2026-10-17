"""Opt-in smoke test against the real Google Sheets API (AMD-18 completion gate).

CI runs the fake-Google suite instead. Credentials come from the environment, for example
from the git-ignored services/api/.env.live, with ROOT_LIVE_SHEETS_TESTS=1 and
ROOT_LIVE_SPREADSHEET_ID=<id>, plus either

- AMD-19 (Apps Script bridge): SHEETS_BRIDGE_URL and SHEETS_BRIDGE_TOKEN of the web app
  deployed from integrations/apps_script/Code.gs inside that spreadsheet, or
- AMD-18 (service account): GOOGLE_SERVICE_ACCOUNT_JSON (base64 key) for a service account
  the spreadsheet is shared with as Editor.

  pytest tests/actions/test_live_sheets.py -s

The test stands in for the owner by writing the verification code into ROOT_VERIFY!A1 with
the same credentials, executes an approved plan through the normal job, then verifies the
result independently with a second client (a new connection and, for a service account, a
fresh token). Each run writes new rows (a new
plan and action); nothing is deleted. No credentials are printed.
"""

import os

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from app.actions import payload as payloads
from app.actions.executor import ActionRuntime
from app.actions.sheets import TokenCache, load_service_account
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
    bridge_url = os.environ.get("SHEETS_BRIDGE_URL")
    bridge_token = os.environ.get("SHEETS_BRIDGE_TOKEN")
    sid = os.environ.get("ROOT_LIVE_SPREADSHEET_ID")
    assert sid, "Set ROOT_LIVE_SPREADSHEET_ID."
    assert encoded or (bridge_url and bridge_token), "Set the bridge or service-account values."
    runtime: ActionRuntime = app.state.actions
    saved = (
        runtime.account,
        runtime.bridge_url,
        runtime.bridge_token,
        runtime.transport,
        runtime.tokens,
    )
    runtime.account = load_service_account(encoded) if encoded else None
    runtime.bridge_url, runtime.bridge_token = bridge_url, bridge_token
    runtime.transport, runtime.tokens = None, TokenCache()
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

        # Independent check: a separate client on a new connection reads the sheet back.
        tabs = done["payload"]["tabs"]
        fresh = ActionRuntime(app.state.settings)
        fresh.account, fresh.bridge_url, fresh.bridge_token = (
            runtime.account,
            runtime.bridge_url,
            runtime.bridge_token,
        )
        async with fresh.client() as reader:
            values = await reader.read(sid, [payloads.tab_range(t["title"]) for t in tabs])
        for tab, rows in zip(tabs, values, strict=True):
            mine = [r for r in rows if r and r[0].startswith(action_id.replace("-", ""))]
            assert len(mine) == len(tab["rows"]), tab["title"]
            assert {r[0] for r in mine} == {r[0] for r in tab["rows"]}
        print(  # noqa: T201 - evidence for the engineering log, no secrets
            f"\nLIVE OK mode={runtime.mode} action={action_id} spreadsheet={sid} "
            f"rows={done['rows']} "
            f"ranges={done['external_ref']['updated_ranges']} "
            f"calls={[c['op'] for c in done['attempts'][-1]['calls']]}"
        )
    finally:
        (
            runtime.account,
            runtime.bridge_url,
            runtime.bridge_token,
            runtime.transport,
            runtime.tokens,
        ) = saved
