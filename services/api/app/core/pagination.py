"""Opaque cursors for keyset pagination (DATA_CONTRACTS §7.1)."""

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from app.core.errors import ApiError

Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=200)]


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise _invalid_cursor() from exc


def _invalid_cursor() -> ApiError:
    return ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")


def decode_int_cursor(cursor: str) -> int:
    raw = decode_cursor(cursor)
    if not raw.isdigit():
        raise _invalid_cursor()
    return int(raw)


def decode_uuid_cursor(cursor: str) -> UUID:
    try:
        return UUID(decode_cursor(cursor))
    except ValueError as exc:
        raise _invalid_cursor() from exc
