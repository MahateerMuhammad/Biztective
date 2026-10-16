"""T-21 / SEC-24: credentials and session material never reach logs, responses or audit rows."""

import logging

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.logging import JsonFormatter
from tests.helpers import TEST_SESSION_SECRET, create_user, csrf_headers, login

SENTINEL_PASSWORD = "sentinel-password-9f3c2b71"


async def test_no_secrets_in_logs_responses_or_audit(
    client: httpx.AsyncClient, owner_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    create_user(owner_engine, "carol@example.test", password=SENTINEL_PASSWORD)
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []

    failed = await login(client, "carol@example.test", SENTINEL_PASSWORD + "x")
    bodies.append(failed.text)
    succeeded = await login(client, "carol@example.test", SENTINEL_PASSWORD)
    bodies.append(succeeded.text)
    session_token = client.cookies.get("root_session")
    csrf_nonce = client.cookies.get("root_csrf")
    csrf_token = (await csrf_headers(client))["X-CSRF-Token"]
    bodies.append((await client.get("/api/v1/auth/me")).text)
    bodies.append(
        (await client.post("/api/v1/auth/logout", headers=await csrf_headers(client))).text
    )
    assert session_token
    assert csrf_nonce
    assert csrf_token

    formatter = JsonFormatter()
    log_text = "\n".join(formatter.format(record) for record in caplog.records)
    assert caplog.records, "expected request logs to be captured"
    with owner_engine.connect() as conn:
        audit_text = " ".join(
            str(row) for row in conn.execute(text("SELECT * FROM audit_log")).all()
        )
        stored_hashes = conn.scalars(text("SELECT id_hash FROM sessions")).all()

    secrets = {
        "password": SENTINEL_PASSWORD,
        "session token": session_token,
        "csrf nonce": csrf_nonce,
        "csrf token": csrf_token,
        "session secret": TEST_SESSION_SECRET,
    }
    for label, value in secrets.items():
        assert value not in log_text, f"{label} leaked into logs"
        assert value not in audit_text, f"{label} leaked into audit_log"
        for body in bodies:
            assert value not in body, f"{label} leaked into a response body"
    assert session_token not in stored_hashes  # only the SHA-256 of the token is stored
