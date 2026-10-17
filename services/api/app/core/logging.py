"""Structured JSON logging with request correlation and redaction of sensitive fields."""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SENSITIVE_MARKERS = (
    "password",
    "secret",
    "token",
    "cookie",
    "authorization",
    "csrf",
    "verification_code",
    "webhook",
    "credential",
)
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}
REDACTED = "[REDACTED]"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_sensitive(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # Client SDK log messages can embed full URLs, auth headers or request bodies.
            # Their operational outcomes are recorded separately by our gateways/executor.
            "message": REDACTED
            if record.name.split(".")[0] in {"httpx", "httpcore", "anthropic", "urllib3", "google"}
            else record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = REDACTED if _is_sensitive(key) else _redact(value)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            # Exception messages (including chained SQL/HTTP errors) can contain bound
            # values, credentials and provider bodies. Retain type and frame locations only.
            payload["exc"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn's access log would duplicate our request log and include query strings.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
