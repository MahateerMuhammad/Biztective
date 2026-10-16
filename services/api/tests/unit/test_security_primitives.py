import json
import logging

import pytest

from app.audit.writer import ip_prefix
from app.auth.csrf import csrf_token_for, session_binding
from app.auth.passwords import (
    hash_password,
    needs_rehash,
    password_policy_errors,
    verify_password,
)
from app.core.errors import ApiError
from app.core.logging import REDACTED, JsonFormatter, request_id_var
from app.core.pagination import decode_int_cursor, decode_uuid_cursor, encode_cursor


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("a sufficiently long password")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a sufficiently long password")
    assert not verify_password(hashed, "wrong password value")
    assert not needs_rehash(hashed)


def test_verify_without_hash_or_with_garbage_is_false() -> None:
    assert not verify_password(None, "anything at all")
    assert not verify_password("not-a-hash", "anything at all")


@pytest.mark.parametrize(
    ("password", "email", "expected_errors"),
    [
        ("x" * 11, "a@b.c", 1),
        ("x" * 12, "a@b.c", 0),
        ("x" * 128, "a@b.c", 0),
        ("x" * 129, "a@b.c", 1),
        ("owner@example.test", "Owner@Example.test", 1),
    ],
)
def test_password_policy(password: str, email: str, expected_errors: int) -> None:
    assert len(password_policy_errors(password, email)) == expected_errors


def test_csrf_token_is_bound_to_nonce_and_session() -> None:
    secret = b"k" * 32
    anon = csrf_token_for(secret, "nonce", None)
    assert anon == csrf_token_for(secret, "nonce", "")
    assert anon != csrf_token_for(secret, "nonce", "session-a")
    assert csrf_token_for(secret, "nonce", "session-a") != csrf_token_for(
        secret, "nonce", "session-b"
    )
    assert anon != csrf_token_for(secret, "other-nonce", None)
    assert anon != csrf_token_for(b"j" * 32, "nonce", None)
    assert session_binding(None) == "anon"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("203.0.113.77", "203.0.113.0/24"),
        ("2001:db8:1234:5678::1", "2001:db8:1234::/48"),
        ("not-an-ip", None),
        (None, None),
    ],
)
def test_ip_prefix_truncates(host: str | None, expected: str | None) -> None:
    assert ip_prefix(host) == expected


def test_json_formatter_redacts_sensitive_fields_and_adds_request_id() -> None:
    record = logging.makeLogRecord(
        {
            "msg": "event",
            "levelname": "INFO",
            "name": "t",
            "password": "hunter2-sentinel",
            "session_token": "tok-sentinel",
            "csrf": "csrf-sentinel",
            "status": 200,
        }
    )
    token = request_id_var.set("req-12345678")
    try:
        output = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    payload = json.loads(output)
    assert payload["password"] == REDACTED
    assert payload["session_token"] == REDACTED
    assert payload["csrf"] == REDACTED
    assert payload["status"] == 200
    assert payload["request_id"] == "req-12345678"
    assert "sentinel" not in output


def test_cursor_round_trip_and_rejection() -> None:
    assert decode_int_cursor(encode_cursor("42")) == 42
    with pytest.raises(ApiError):
        decode_int_cursor(encode_cursor("-1"))
    with pytest.raises(ApiError):
        decode_int_cursor("%%%")
    with pytest.raises(ApiError):
        decode_uuid_cursor(encode_cursor("not-a-uuid"))
    with pytest.raises(ApiError):
        decode_uuid_cursor("\udcff")
