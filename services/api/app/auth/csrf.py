"""Signed double-submit CSRF tokens bound to the session, plus Origin verification (AMD-4)."""

import base64
import hashlib
import hmac
import secrets
from http.cookies import SimpleCookie

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import problem_response

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = b"x-csrf-token"


def new_csrf_nonce() -> str:
    return secrets.token_urlsafe(32)


def session_binding(session_token: str | None) -> str:
    if not session_token:
        return "anon"
    return hashlib.sha256(session_token.encode()).hexdigest()


def csrf_token_for(secret: bytes, nonce: str, session_token: str | None) -> str:
    message = f"csrf|{nonce}|{session_binding(session_token)}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _cookies(scope: Scope) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    for name, value in scope["headers"]:
        if name == b"cookie":
            try:
                jar.load(value.decode("latin-1"))
            except Exception:  # noqa: BLE001 - a malformed cookie header simply yields no cookies
                return {}
    return {key: morsel.value for key, morsel in jar.items()}


class CsrfMiddleware:
    """Rejects unsafe /api/ requests lacking a matching Origin and a valid CSRF token."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.secret = settings.session_secret.get_secret_value().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in UNSAFE_METHODS
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        if not self._origin_allowed(scope) or not self._token_valid(scope):
            response = problem_response(
                403,
                instance=scope["path"],
                code="csrf_failed",
                detail="Missing or invalid CSRF protection.",
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _origin_allowed(self, scope: Scope) -> bool:
        headers: dict[bytes, bytes] = dict(scope["headers"])
        origin = headers.get(b"origin")
        if origin is not None:
            return origin.decode("latin-1") == self.settings.frontend_origin
        referer = headers.get(b"referer")
        if referer is not None:
            return referer.decode("latin-1").startswith(self.settings.frontend_origin + "/")
        return False

    def _token_valid(self, scope: Scope) -> bool:
        headers: dict[bytes, bytes] = dict(scope["headers"])
        presented = headers.get(CSRF_HEADER)
        cookies = _cookies(scope)
        nonce = cookies.get(self.settings.csrf_cookie_name)
        if not presented or not nonce:
            return False
        expected = csrf_token_for(
            self.secret, nonce, cookies.get(self.settings.session_cookie_name)
        )
        return hmac.compare_digest(presented, expected.encode("ascii"))
