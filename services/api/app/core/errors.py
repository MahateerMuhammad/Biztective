"""RFC 9457 problem details for every error response."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

PROBLEM_MEDIA_TYPE = "application/problem+json"

_DEFAULT_CODES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Bad request"),
    401: ("unauthenticated", "Authentication required"),
    403: ("forbidden", "Forbidden"),
    404: ("not_found", "Resource not found"),
    405: ("method_not_allowed", "Method not allowed"),
    409: ("conflict", "Conflict"),
    413: ("payload_too_large", "Payload too large"),
    415: ("unsupported_media_type", "Unsupported media type"),
    422: ("validation_error", "Request validation failed"),
    429: ("rate_limited", "Too many requests"),
    500: ("internal_error", "Internal server error"),
    503: ("unavailable", "Service unavailable"),
}


class ApiError(Exception):
    """An expected error with a stable machine-readable code."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers


def problem_response(
    status: int,
    *,
    instance: str,
    code: str | None = None,
    detail: str | None = None,
    errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    default_code, title = _DEFAULT_CODES.get(status, ("error", "Error"))
    final_code = code or default_code
    body: dict[str, Any] = {
        "type": f"urn:root:problem:{final_code}",
        "title": title,
        "status": status,
        "detail": detail or title,
        "instance": instance,
        "code": final_code,
        "request_id": request_id_var.get(),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):
        raise TypeError(exc)
    return problem_response(
        exc.status,
        instance=request.url.path,
        code=exc.code,
        detail=exc.detail,
        headers=exc.headers,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(exc)
    # Framework-raised errors (404 route, 405) never carry internal detail to the client.
    headers = dict(exc.headers) if exc.headers else None
    return problem_response(exc.status_code, instance=request.url.path, headers=headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(exc)
    errors = [
        {
            # Drop the "body"/"query"/"path" prefix; never echo the rejected input value.
            "field": ".".join(str(part) for part in err.get("loc", ())[1:]) or "body",
            "code": str(err.get("type", "invalid")),
            "message": str(err.get("msg", "Invalid value.")),
        }
        for err in exc.errors()
    ]
    return problem_response(
        422,
        instance=request.url.path,
        detail="One or more fields are invalid.",
        errors=errors,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
