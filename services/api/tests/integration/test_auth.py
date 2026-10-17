from datetime import timedelta

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import Engine, select, text

from app.auth import service
from app.models import AuditLog, Session
from tests.helpers import (
    DEFAULT_PASSWORD,
    create_tenant,
    create_user,
    csrf_headers,
    login,
)


def _set_cookie_header(response: httpx.Response, name: str) -> str:
    headers = [v for v in response.headers.get_list("set-cookie") if v.startswith(f"{name}=")]
    assert len(headers) == 1
    return headers[0]


async def test_login_success_sets_secure_session_cookie(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    response = await login(client, "  Alice@Example.TEST ")
    assert response.status_code == 200
    assert response.json()["email"] == tenant.email
    assert "password" not in response.text
    cookie = _set_cookie_header(response, "root_session").lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=604800" in cookie


async def test_me_returns_user_and_memberships(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    create_tenant(owner_engine, "bob")
    await login(client, tenant.email)
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == tenant.email
    assert body["memberships"] == [
        {"business_id": str(tenant.business_id), "business_name": "Business alice", "role": "owner"}
    ]


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("alice@example.test", "wrong password value"),
        ("nobody@example.test", DEFAULT_PASSWORD),
    ],
)
async def test_login_failure_is_uniform(
    client: httpx.AsyncClient, owner_engine: Engine, email: str, password: str
) -> None:
    create_tenant(owner_engine, "alice")
    response = await login(client, email, password)
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "invalid_credentials"
    assert body["detail"] == "Invalid email or password."
    assert response.headers["content-type"] == "application/problem+json"
    assert not any(c.startswith("root_session=") for c in response.headers.get_list("set-cookie"))


async def test_inactive_user_cannot_log_in(client: httpx.AsyncClient, owner_engine: Engine) -> None:
    create_user(owner_engine, "inactive@example.test", is_active=False)
    response = await login(client, "inactive@example.test")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_unauthenticated_me_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_forged_session_cookie_is_rejected(client: httpx.AsyncClient) -> None:
    client.cookies.set("root_session", "forged-token-value")
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_logout_revokes_session_server_side(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    stolen = client.cookies.get("root_session")
    assert stolen
    response = await client.post("/api/v1/auth/logout", headers=await csrf_headers(client))
    assert response.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    # Replaying the old cookie after logout must fail: revocation is server-side.
    client.cookies.set("root_session", stolen)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    with owner_engine.connect() as conn:
        revoked = conn.scalar(select(Session.revoked_at).where(Session.user_id == tenant.user_id))
    assert revoked is not None


async def test_logout_without_session_is_idempotent(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/logout", headers=await csrf_headers(client))
    assert response.status_code == 204


async def test_login_rotates_and_revokes_previous_session(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    first = client.cookies.get("root_session")
    await login(client, tenant.email)
    second = client.cookies.get("root_session")
    assert first
    assert second
    assert first != second
    client.cookies.set("root_session", first)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    client.cookies.set("root_session", second)
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_session_idle_timeout(
    client: httpx.AsyncClient, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    real_now = service.utcnow()
    monkeypatch.setattr(service, "utcnow", lambda: real_now + timedelta(hours=7, minutes=59))
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # touches last_seen
    later = real_now + timedelta(hours=7, minutes=59) + service.SESSION_IDLE_TIMEOUT
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_session_absolute_timeout(
    client: httpx.AsyncClient, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    start = service.utcnow()
    # Stay active every few hours, then cross the 7-day absolute limit.
    for hours in range(4, 24 * 7, 4):
        moment = start + timedelta(hours=hours)
        monkeypatch.setattr(service, "utcnow", lambda m=moment: m)
        assert (await client.get("/api/v1/auth/me")).status_code == 200
    beyond = start + service.SESSION_ABSOLUTE_TIMEOUT + timedelta(seconds=1)
    monkeypatch.setattr(service, "utcnow", lambda: beyond)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_account_throttling_after_five_failures(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    for _ in range(service.MAX_FAILURES_PER_ACCOUNT):
        assert (await login(client, tenant.email, "wrong password value")).status_code == 401
    blocked = await login(client, tenant.email)  # even the correct password is refused
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert 1 <= int(blocked.headers["Retry-After"]) <= 900


async def test_ip_throttling_across_accounts(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    for i in range(service.MAX_FAILURES_PER_IP):
        response = await login(client, f"user{i}@example.test", "wrong password value")
        assert response.status_code == 401
    assert (await login(client, "another@example.test", "wrong password value")).status_code == 429


async def test_throttling_window_expires(
    client: httpx.AsyncClient, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    for _ in range(service.MAX_FAILURES_PER_ACCOUNT):
        await login(client, tenant.email, "wrong password value")
    later = service.utcnow() + service.THROTTLE_WINDOW + timedelta(seconds=1)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert (await login(client, tenant.email)).status_code == 200


async def test_auth_events_are_audited(client: httpx.AsyncClient, owner_engine: Engine) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email, "wrong password value")
    await login(client, tenant.email)
    await client.post("/api/v1/auth/logout", headers=await csrf_headers(client))
    with owner_engine.connect() as conn:
        actions = conn.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
        request_ids = conn.scalars(select(AuditLog.request_id)).all()
    assert actions == ["auth.login_failed", "auth.login_succeeded", "auth.logout"]
    assert all(request_ids)


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "a@example.test"},
        {"email": "a@example.test", "password": "x", "extra": "field"},
        {"email": "not-an-email", "password": "x"},
        {"email": "a@example.test", "password": "x" * 129},
        {"email": 123, "password": "x"},
        [],
    ],
)
async def test_malformed_login_payloads_are_422(client: httpx.AsyncClient, payload: object) -> None:
    response = await client.post(
        "/api/v1/auth/login", json=payload, headers=await csrf_headers(client)
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"]
    assert "x" * 129 not in response.text  # rejected input is never echoed


async def test_invalid_json_body_is_422(client: httpx.AsyncClient) -> None:
    headers = {**await csrf_headers(client), "Content-Type": "application/json"}
    response = await client.post("/api/v1/auth/login", content=b"{not json", headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_password_is_rehashed_when_parameters_change(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(DEFAULT_PASSWORD)
    tenant = create_tenant(owner_engine, "alice")
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :id"),
            {"h": weak, "id": tenant.user_id},
        )
    assert (await login(client, tenant.email)).status_code == 200
    with owner_engine.connect() as conn:
        stored = conn.scalar(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": tenant.user_id}
        )
    assert stored != weak
    assert stored is not None
    assert stored.startswith("$argon2id$")


async def test_concurrent_login_failures_cannot_bypass_account_quota(
    app: FastAPI, owner_engine: Engine
) -> None:
    import asyncio

    tenant = create_tenant(owner_engine, "alice")

    async def attempt() -> int:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 50000)),
            base_url="http://testserver",
        ) as other:
            response = await login(other, tenant.email, "wrong password value")
            return response.status_code

    responses = await asyncio.gather(*(attempt() for _ in range(12)))
    assert responses.count(401) == service.MAX_FAILURES_PER_ACCOUNT
    assert responses.count(429) == 12 - service.MAX_FAILURES_PER_ACCOUNT
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT count(*) FROM auth_attempts"))
            == service.MAX_FAILURES_PER_ACCOUNT
        )
