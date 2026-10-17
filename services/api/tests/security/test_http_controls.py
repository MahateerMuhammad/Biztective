"""CSRF and origin checks (T-14), CORS (T-47), headers (T-20), error responses (T-39), limits."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from app.main import MAX_JSON_BODY_BYTES, create_app
from tests.conftest import DatabaseUrls, make_settings
from tests.helpers import FRONTEND_ORIGIN, create_tenant, csrf_headers, login

LOGIN_BODY = {"email": "a@example.test", "password": "irrelevant password"}


async def test_unsafe_request_without_csrf_token_is_403(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json=LOGIN_BODY, headers={"Origin": FRONTEND_ORIGIN}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


async def test_wrong_csrf_token_is_403(client: httpx.AsyncClient) -> None:
    headers = await csrf_headers(client)
    headers["X-CSRF-Token"] = "A" * 43
    response = await client.post("/api/v1/auth/login", json=LOGIN_BODY, headers=headers)
    assert response.status_code == 403


async def test_token_without_cookie_is_403(client: httpx.AsyncClient) -> None:
    headers = await csrf_headers(client)
    client.cookies.clear()
    response = await client.post("/api/v1/auth/login", json=LOGIN_BODY, headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "origin_headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://localhost:3000.evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/page"},
        {"Referer": "http://localhost:30000/page"},
        {},
    ],
)
async def test_foreign_or_missing_origin_is_403(
    client: httpx.AsyncClient, origin_headers: dict[str, str]
) -> None:
    token = (await csrf_headers(client))["X-CSRF-Token"]
    response = await client.post(
        "/api/v1/auth/login", json=LOGIN_BODY, headers={"X-CSRF-Token": token, **origin_headers}
    )
    assert response.status_code == 403


async def test_referer_from_frontend_is_accepted(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    token = (await csrf_headers(client))["X-CSRF-Token"]
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": tenant.email, "password": "correct horse battery staple"},
        headers={"X-CSRF-Token": token, "Referer": f"{FRONTEND_ORIGIN}/login"},
    )
    assert response.status_code == 200


async def test_anonymous_token_is_invalid_after_login(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    anonymous = await csrf_headers(client)
    await client.post(
        "/api/v1/auth/login",
        json={"email": tenant.email, "password": "correct horse battery staple"},
        headers=anonymous,
    )
    stale = await client.post("/api/v1/auth/logout", headers=anonymous)
    assert stale.status_code == 403
    fresh = await client.post("/api/v1/auth/logout", headers=await csrf_headers(client))
    assert fresh.status_code == 204


async def test_token_from_another_session_is_rejected(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    create_tenant(owner_engine, "alice")
    create_tenant(owner_engine, "mallory")
    await login(client, "alice@example.test")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as attacker:
        await login(attacker, "mallory@example.test")
        mallory_token = (await csrf_headers(attacker))["X-CSRF-Token"]
    response = await client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": mallory_token, "Origin": FRONTEND_ORIGIN}
    )
    assert response.status_code == 403


async def test_csrf_cookie_attributes(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/csrf")
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("root_csrf=")
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    second = await client.get("/api/v1/auth/csrf")
    assert "set-cookie" not in second.headers  # nonce is reused, token stays stable
    assert second.json() == response.json()


async def test_cors_allows_only_frontend_origin(client: httpx.AsyncClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST"}
    allowed = await client.options(
        "/api/v1/auth/login", headers={"Origin": FRONTEND_ORIGIN, **preflight}
    )
    assert allowed.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = await client.options(
        "/api/v1/auth/login", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in denied.headers
    simple = await client.get("/api/v1/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


SECURITY_HEADERS = {
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "cross-origin-opener-policy": "same-origin",
    "cache-control": "no-store",
}


@pytest.mark.parametrize("path", ["/api/v1/healthz", "/api/v1/auth/me", "/nope"])
async def test_security_headers_on_every_response(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value
    assert "strict-transport-security" not in response.headers  # HSTS only in production
    assert response.headers["x-request-id"]


async def test_production_adds_hsts(db_urls: DatabaseUrls) -> None:
    app = create_app(
        make_settings(db_urls.app, environment="production", frontend_origin="https://app.example")
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c,
    ):
        response = await c.get("/api/v1/healthz")
        docs = await c.get("/api/docs")
    assert response.headers["strict-transport-security"].startswith("max-age=63072000")
    assert docs.status_code == 404  # API docs are not exposed in production


async def test_request_id_is_echoed_or_generated(client: httpx.AsyncClient) -> None:
    echoed = await client.get("/api/v1/healthz", headers={"X-Request-ID": "trace-abc-12345"})
    assert echoed.headers["x-request-id"] == "trace-abc-12345"
    replaced = await client.get("/api/v1/healthz", headers={"X-Request-ID": "bad id\nvalue"})
    assert replaced.headers["x-request-id"] != "bad id\nvalue"
    assert len(replaced.headers["x-request-id"]) == 36


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert set(body) == {"type", "title", "status", "detail", "instance", "code", "request_id"}
    assert body["type"] == "urn:root:problem:not_found"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_method_not_allowed_is_problem_json(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/healthz", headers=await csrf_headers(client))
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


async def test_oversized_body_is_413(client: httpx.AsyncClient) -> None:
    headers = {**await csrf_headers(client), "Content-Type": "application/json"}
    big = b'{"email": "' + b"a" * MAX_JSON_BODY_BYTES + b'"}'
    response = await client.post("/api/v1/auth/login", content=big, headers=headers)
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


async def test_oversized_streamed_body_is_413(client: httpx.AsyncClient) -> None:
    headers = {**await csrf_headers(client), "Content-Type": "application/json"}

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b" " * (MAX_JSON_BODY_BYTES // 2)

    response = await client.post("/api/v1/auth/login", content=chunks(), headers=headers)
    assert response.status_code == 413


async def test_invalid_content_length_is_413(client: httpx.AsyncClient) -> None:
    headers = {**await csrf_headers(client), "Content-Length": "abc"}
    response = await client.post("/api/v1/auth/login", content=b"{}", headers=headers)
    assert response.status_code == 413


async def test_unhandled_error_is_generic_500(db_urls: DatabaseUrls) -> None:
    app = create_app(make_settings(db_urls.app))

    @app.get("/api/v1/test-boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail /etc/passwd SELECT * FROM users")

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        response = await c.get("/api/v1/test-boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["request_id"] == response.headers["x-request-id"]
    for leaked in ("secret internal detail", "/etc/passwd", "SELECT", "Traceback", "RuntimeError"):
        assert leaked not in response.text


async def test_non_ascii_csrf_header_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await csrf_headers(client)
    raw_headers = [(key.encode(), value.encode()) for key, value in headers.items()]
    raw_headers = [
        (key, b"\xff" if key.lower() == b"x-csrf-token" else value) for key, value in raw_headers
    ]
    response = await client.post("/api/v1/auth/login", json=LOGIN_BODY, headers=raw_headers)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


async def test_verified_session_global_rate_limit(
    db_urls: DatabaseUrls, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from app.auth import rate_limit
    from app.auth.rate_limit import CAPACITY

    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(monotonic=lambda: 100.0))

    tenant = create_tenant(owner_engine, "limited")
    application = create_app(make_settings(db_urls.app))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://t"
        ) as other,
    ):
        assert (await login(other, tenant.email)).status_code == 200
        for _ in range(CAPACITY):
            assert (await other.get("/api/v1/healthz")).status_code == 200
        response = await other.get("/api/v1/healthz")
        assert response.status_code == 429
        assert response.headers["retry-after"]
        assert response.json()["code"] == "rate_limited"
        other.cookies.set("root_session", "A" * 43)
        assert (await other.get("/api/v1/auth/me")).status_code == 401
