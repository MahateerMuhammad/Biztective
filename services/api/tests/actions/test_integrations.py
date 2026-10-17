"""Binding a business spreadsheet (AMD-18, ARCHITECTURE §11.2, SEC-17, T-21, T-36)."""

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.actions.sheets import ServiceAccount
from tests.actions.fake_google import OTHER_SID, SID, FakeGoogle
from tests.actions.support import base, bind, connect, verify
from tests.helpers import Tenant, add_member, create_tenant, create_user, csrf_headers, login


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


def code_of(response: httpx.Response) -> str:
    code: str = response.json()["verification_code"]
    return code


async def test_binding_requires_proof_of_ownership(
    client: httpx.AsyncClient, owner: Tenant, google: FakeGoogle
) -> None:
    book = google.sheet()
    created = await bind(client, owner, f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["spreadsheet_id"] == SID
    assert body["status"] == "pending_verification"
    assert body["service_account_email"] == google.email
    assert body["verify_cell"] == "'ROOT_VERIFY'!A1"
    code = code_of(created)
    assert code.startswith("ROOT-")
    assert len(code) == 15
    # Not yet proven: wrong or absent codes fail.
    no_tab = await verify(client, owner, body["id"])
    assert (no_tab.status_code, no_tab.json()["errors"][0]["code"]) == (
        422,
        "destination_invalid",
    )
    book["ROOT_VERIFY"] = [["ROOT-AAAAAAAAAA"]]
    wrong = await verify(client, owner, body["id"])
    assert wrong.json()["errors"][0]["code"] == "code_mismatch"
    book["ROOT_VERIFY"] = [[f"  {code} "]]
    good = await verify(client, owner, body["id"])
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "verified"
    assert good.json()["verified_at"]
    # The code is single use.
    reused = await verify(client, owner, body["id"])
    assert reused.json()["errors"][0]["code"] == "code_used"
    listed = (await client.get(f"{base(owner)}/integrations")).json()
    assert listed["available"] is True
    assert [i["status"] for i in listed["items"]] == ["verified"]
    assert code not in json.dumps(listed)


async def test_expired_code_and_unshared_sheet_fail(
    client: httpx.AsyncClient, owner: Tenant, google: FakeGoogle, owner_engine: Engine
) -> None:
    google.sheet(shared=False)
    created = await bind(client, owner)
    google.spreadsheets[SID]["ROOT_VERIFY"] = [[code_of(created)]]
    unshared = await verify(client, owner, created.json()["id"])
    assert unshared.json()["errors"][0]["code"] == "integration_permission_denied"
    google.shared.add(SID)
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE integrations SET verify_expires_at = now() - interval '1 minute'")
        )
    expired = await verify(client, owner, created.json()["id"])
    assert expired.json()["errors"][0]["code"] == "code_expired"
    with owner_engine.connect() as conn:
        audit = conn.execute(
            text(
                "SELECT action, metadata FROM audit_log WHERE target_type = 'integration' "
                "ORDER BY at, id"
            )
        ).all()
    assert [a for a, _ in audit] == [
        "integration.created",
        "integration.verification_failed",
        "integration.verification_failed",
    ]
    assert [m.get("reason") for _, m in audit[1:]] == [
        "integration_permission_denied",
        "code_expired",
    ]


async def test_a_spreadsheet_binds_to_one_business_only(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
) -> None:
    await connect(client, owner, google)
    again = await bind(client, owner, OTHER_SID)
    assert (again.status_code, again.json()["code"]) == (409, "integration_exists")
    other = create_tenant(owner_engine, "mallory")
    transport = httpx.ASGITransport(app=app, client=("203.0.113.60", 50060))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        assert (await login(c, other.email)).status_code == 200
        # Even with a valid code in the sheet, the second business cannot take it over.
        created = await bind(c, other, SID)
        assert created.status_code == 201
        google.spreadsheets[SID]["ROOT_VERIFY"] = [[code_of(created)]]
        taken = await verify(c, other, created.json()["id"])
        assert (taken.status_code, taken.json()["code"]) == (409, "integration_already_bound")
        assert str(owner.business_id) not in taken.text
        assert "alice" not in taken.text
        # And they cannot see or touch the first business's binding.
        mine = (await client.get(f"{base(owner)}/integrations")).json()["items"][0]["id"]
        assert (await verify(c, other, mine)).status_code == 404
        gone = await c.delete(f"{base(other)}/integrations/{mine}", headers=await csrf_headers(c))
        assert gone.status_code == 404
    with owner_engine.connect() as conn:
        bound = conn.execute(
            text("SELECT count(*) FROM integrations WHERE status = 'verified'")
        ).scalar_one()
    assert bound == 1


async def test_only_owners_manage_and_invalid_input_is_refused(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    owner_engine: Engine,
) -> None:
    for bad in ("not a sheet", "https://evil.example/spreadsheets/d/" + SID, "x" * 25 + "!"):
        response = await bind(client, owner, bad)
        assert response.status_code == 422, bad
    user_id = create_user(owner_engine, "ann@example.test")
    add_member(owner_engine, owner.business_id, user_id, "analyst")
    transport = httpx.ASGITransport(app=app, client=("203.0.113.70", 50070))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        assert (await login(c, "ann@example.test")).status_code == 200
        assert (await bind(c, owner)).status_code == 403
        assert (await c.get(f"{base(owner)}/integrations")).status_code == 200


async def test_unconfigured_deployment_cannot_bind(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    assert app.state.actions.account is None  # no GOOGLE_SERVICE_ACCOUNT_JSON in tests
    response = await bind(client, owner)
    assert (response.status_code, response.json()["code"]) == (503, "integration_unavailable")
    assert (await client.get(f"{base(owner)}/integrations")).json()["available"] is False


async def test_credentials_and_codes_never_leak(
    client: httpx.AsyncClient,
    owner: Tenant,
    google: FakeGoogle,
    service_account: tuple[ServiceAccount, str, bytes],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _, encoded, _ = service_account
    key_file = json.loads(__import__("base64").b64decode(encoded))
    sentinels = [
        encoded[:40],
        key_file["private_key"].splitlines()[1],
        *google.issued,
    ]
    google.sheet()
    created = await bind(client, owner)
    code = code_of(created)
    google.spreadsheets[SID]["ROOT_VERIFY"] = [[code]]
    bodies: list[str] = [created.text.replace(code, "")]
    bodies.append((await verify(client, owner, created.json()["id"])).text)
    bodies.append((await client.get(f"{base(owner)}/integrations")).text)
    sentinels += [*google.issued, code]
    logged = "\n".join(r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records)
    for secret in sentinels:
        assert secret not in logged
        assert all(secret not in b for b in bodies)
