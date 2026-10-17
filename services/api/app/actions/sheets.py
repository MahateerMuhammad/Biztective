"""Google Sheets REST adapter (AMD-18).

Service-account tokens are signed with `google-auth` and exchanged at the constant Google token
endpoint; every request goes to `sheets.googleapis.com` with explicit timeouts. Errors are
classified by what they mean for the destination: `ambiguous` errors may have been applied
(a write timed out after it was sent), so the caller must re-read before writing again.
Reads and requests that were certainly not applied are retried here with exponential backoff
honouring `Retry-After`. Cell values never reach logs.
"""

import base64
import binascii
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from google.auth import crypt, jwt

# Any is confined to the JSON bodies of the Sheets API.

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SPREADSHEET_ID = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
URL_ID = re.compile(
    r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,100})(?:[/?#].*)?$"
)
MAX_CELL = 5000
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MAX_RETRY_AFTER = 60.0
TOKEN_MARGIN = 300.0  # refresh this many seconds before expiry


def sanitize(value: str) -> str:
    """A cell that a spreadsheet can never evaluate (SEC-16), capped at MAX_CELL characters."""
    if value.startswith(FORMULA_START):
        return "'" + value[: MAX_CELL - 1]
    return value[:MAX_CELL]


def spreadsheet_id(given: str) -> str | None:
    """The spreadsheet ID from a Google Sheets URL or a bare ID, or None when invalid."""
    given = given.strip()
    match = URL_ID.match(given)
    if match:
        return match.group(1)
    return given if SPREADSHEET_ID.match(given) else None


def spreadsheet_url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}"


@dataclass(frozen=True)
class ServiceAccount:
    email: str
    signer: crypt.Signer


def load_service_account(encoded: str) -> ServiceAccount:
    """Parse the base64 key file. The key file's own token URI is ignored (SEC-15)."""
    try:
        info = json.loads(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must be base64 of a key file") from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not a service-account key")
    email = info.get("client_email")
    if not isinstance(email, str) or not email.endswith(".gserviceaccount.com"):
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON has no service-account email")
    try:
        # google-auth ships without annotations for these helpers.
        signer = crypt.RSASigner.from_service_account_info(info)  # type: ignore[no-untyped-call]
    except (KeyError, ValueError) as exc:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON has no usable private key") from exc
    return ServiceAccount(email=email, signer=signer)


class SheetsError(Exception):
    """`retryable`: may succeed later. `ambiguous`: a write may have been applied."""

    def __init__(
        self,
        code: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "")
    try:
        return min(MAX_RETRY_AFTER, max(0.0, float(value)))
    except ValueError:
        return None


def _status_error(response: httpx.Response, write: bool) -> SheetsError:
    status = response.status_code
    if status == 429:
        return SheetsError(
            "rate_limited", status=status, retryable=True, retry_after=_retry_after(response)
        )
    if status >= 500:
        # A server error on a write may still have been applied.
        return SheetsError(
            "provider_unavailable",
            status=status,
            retryable=True,
            ambiguous=write,
            retry_after=_retry_after(response),
        )
    if status == 401:
        return SheetsError("integration_credentials_invalid", status=status)
    if status == 403:
        return SheetsError("integration_permission_denied", status=status)
    if status == 404:
        return SheetsError("destination_not_found", status=status)
    return SheetsError("destination_invalid", status=status)


@dataclass
class TokenCache:
    token: str | None = None
    expires_at: float = 0.0


@dataclass
class SheetsClient:
    """One client per execution attempt; `calls` records operation, status and duration."""

    account: ServiceAccount
    http: httpx.AsyncClient
    tokens: TokenCache
    max_retries: int
    backoff: float
    sleep: Callable[[float], Awaitable[None]]
    on_write: Callable[[], None] = lambda: None
    clock: Callable[[], float] = time.time
    calls: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ transport

    async def _token(self) -> str:
        if self.tokens.token and self.clock() < self.tokens.expires_at - TOKEN_MARGIN:
            return self.tokens.token
        now = int(self.clock())
        assertion = jwt.encode(  # type: ignore[no-untyped-call]  # untyped google-auth helper
            self.account.signer,
            {
                "iss": self.account.email,
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
        )
        response = await self._send(
            "token",
            "POST",
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion.decode(),
            },
        )
        if response.status_code in (400, 401, 403):
            raise SheetsError("integration_credentials_invalid", status=response.status_code)
        if response.status_code != 200:
            raise _status_error(response, write=False)
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str):
            raise SheetsError("integration_credentials_invalid", status=response.status_code)
        self.tokens.token = token
        self.tokens.expires_at = now + float(body.get("expires_in", 3600))
        return token

    async def _send(
        self,
        op: str,
        method: str,
        url: str,
        *,
        write: bool = False,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        started = time.perf_counter()
        status: int | str = "no_response"
        try:
            if write:
                self.on_write()
            response = await self.http.request(method, url, headers=headers, **kwargs)
            status = response.status_code
            return response
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            status = "connect_error"
            # The request never reached Google: certainly not applied.
            raise SheetsError("network_error", retryable=True) from exc
        except httpx.TimeoutException as exc:
            status = "timeout"
            raise SheetsError("timeout", retryable=True, ambiguous=write) from exc
        except httpx.TransportError as exc:
            status = "network_error"
            raise SheetsError("network_error", retryable=True, ambiguous=write) from exc
        finally:
            self.calls.append(
                {
                    "op": op,
                    "status": status,
                    "ms": round((time.perf_counter() - started) * 1000),
                }
            )

    async def _call(
        self, op: str, method: str, url: str, *, write: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        """One API operation. Retries only what was certainly not applied (or is a read);
        an ambiguous write error is raised for the caller to reconcile."""
        attempt = 0
        refreshed = False
        while True:
            try:
                token = await self._token()
                response = await self._send(
                    op,
                    method,
                    url,
                    write=write,
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
                if response.status_code == 401 and not refreshed:
                    self.tokens.token = None  # expired or revoked token: refresh once
                    refreshed = True
                    continue
                if response.status_code >= 400:
                    raise _status_error(response, write)
                body: dict[str, Any] = response.json() if response.content else {}
                return body
            except SheetsError as exc:
                if not exc.retryable or exc.ambiguous or attempt >= self.max_retries:
                    raise
                delay = exc.retry_after
                if delay is None:
                    delay = min(MAX_RETRY_AFTER, self.backoff * 2**attempt)
                attempt += 1
                await self.sleep(delay)

    # ---------------------------------------------------------------------- API

    def _url(self, sid: str, suffix: str = "") -> str:
        if not SPREADSHEET_ID.match(sid):  # pragma: no cover - validated on binding
            raise SheetsError("destination_invalid")
        return f"{SHEETS_URL}/{sid}{suffix}"

    async def titles(self, sid: str) -> set[str]:
        body = await self._call(
            "metadata", "GET", self._url(sid), params={"fields": "sheets.properties.title"}
        )
        return {s["properties"]["title"] for s in body.get("sheets", [])}

    async def add_tabs(self, sid: str, titles: list[str]) -> None:
        await self._call(
            "add_tabs",
            "POST",
            self._url(sid, ":batchUpdate"),
            json={"requests": [{"addSheet": {"properties": {"title": t}}} for t in titles]},
        )

    async def put_rows(self, sid: str, data: dict[str, list[list[str]]]) -> None:
        """Overwrite fixed ranges (headers, verification cells): repeating it is harmless."""
        await self._call(
            "put_rows",
            "POST",
            self._url(sid, "/values:batchUpdate"),
            json={
                "valueInputOption": "RAW",
                "data": [{"range": r, "values": v} for r, v in data.items()],
            },
        )

    async def read(self, sid: str, ranges: list[str]) -> list[list[list[str]]]:
        body = await self._call(
            "read",
            "GET",
            self._url(sid, "/values:batchGet"),
            params=[("ranges", r) for r in ranges]
            + [("majorDimension", "ROWS"), ("valueRenderOption", "UNFORMATTED_VALUE")],
        )
        found = body.get("valueRanges", [])
        return [[[str(c) for c in row] for row in vr.get("values", [])] for vr in found]

    async def append(self, sid: str, range_: str, rows: list[list[str]]) -> str | None:
        """Append rows as typed (RAW). Returns the updated range."""
        body = await self._call(
            "append",
            "POST",
            self._url(sid, f"/values/{quote(range_, safe='')}:append"),
            write=True,
            params={
                "valueInputOption": "RAW",
                "insertDataOption": "INSERT_ROWS",
                "includeValuesInResponse": "false",
            },
            json={"majorDimension": "ROWS", "values": rows},
        )
        updated = body.get("updates", {}).get("updatedRange")
        return updated if isinstance(updated, str) else None
