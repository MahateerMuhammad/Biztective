"""Google Sheets through an Apps Script web app owned by the business (AMD-19).

The script (integrations/apps_script/Code.gs) runs as the spreadsheet owner, accepts only its
own spreadsheet and a shared token, and offers the same five operations as the REST client.
The token travels in the JSON body, never in a URL. Apps Script answers a POST with a redirect
to `script.googleusercontent.com`; at most three hops between the two Apps Script hosts are
followed, never to another host (SEC-15).
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.actions.sheets import RetryingClient, SheetsError, _retry_after

# Any is confined to the JSON bodies exchanged with the script.

BRIDGE_URL = re.compile(r"^https://script\.google\.com/macros/s/[A-Za-z0-9_-]{20,200}/exec$")
REDIRECT_HOSTS = frozenset({"script.googleusercontent.com", "script.google.com"})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
MAX_HOPS = 3
CODES = {
    401: "integration_credentials_invalid",
    403: "integration_permission_denied",
    404: "destination_not_found",
}


@dataclass(kw_only=True)
class BridgeClient(RetryingClient):
    url: str
    token: str

    async def _rpc(self, op: str, sid: str, args: dict[str, Any], *, write: bool = False) -> Any:
        async def once() -> dict[str, Any]:
            response = await self._send(
                op,
                "POST",
                self.url,
                write=write,
                json={"token": self.token, "op": op, "spreadsheet_id": sid, "args": args},
            )
            hops = 0
            while response.status_code in REDIRECTS:
                location = response.headers.get("Location", "")
                parts = urlsplit(location)
                if parts.scheme != "https" or parts.hostname not in REDIRECT_HOSTS:
                    # A sign-in page or any other host: never followed.
                    raise SheetsError("integration_misconfigured", status=response.status_code)
                if hops == MAX_HOPS:
                    # Google kept redirecting its own result URL: transient, and for a write
                    # the script may already have run.
                    raise SheetsError(
                        "provider_unavailable",
                        status=response.status_code,
                        retryable=True,
                        ambiguous=write,
                    )
                hops += 1
                # The script has already run; fetching its answer can still fail ambiguously.
                try:
                    response = await self._send(op, "GET", location, write=write)
                except SheetsError as exc:
                    # Even failure to connect to the result URL says nothing about the
                    # original POST: the script has already run. Never resend it blindly.
                    if write:
                        exc.ambiguous = True
                    raise
            if response.status_code == 429 or response.status_code >= 500:
                raise SheetsError(
                    "rate_limited" if response.status_code == 429 else "provider_unavailable",
                    status=response.status_code,
                    retryable=True,
                    ambiguous=write and (hops > 0 or response.status_code >= 500),
                    retry_after=_retry_after(response),
                )
            try:
                body = response.json()
            except ValueError as exc:
                if hops:
                    # Seen live: Google's own result URL occasionally ends on its generic
                    # HTML "Error" page. Transient; for a write the script may have run.
                    raise SheetsError(
                        "provider_unavailable",
                        status=response.status_code,
                        retryable=True,
                        ambiguous=write,
                    ) from exc
                # Straight from the deployment URL: removed, or not deployed for "Anyone".
                raise SheetsError("integration_misconfigured", status=response.status_code) from exc
            if response.status_code != 200 or not isinstance(body, dict):
                raise SheetsError("integration_misconfigured", status=response.status_code)
            if body.get("ok") is True:
                return {"result": body.get("result")}
            status = body.get("status")
            status = status if isinstance(status, int) else 400
            if status >= 500:
                raise SheetsError(
                    "provider_unavailable", status=status, retryable=True, ambiguous=write
                )
            raise SheetsError(CODES.get(status, "destination_invalid"), status=status)

        return (await self._retrying(once))["result"]

    async def titles(self, sid: str) -> set[str]:
        found = await self._rpc("titles", sid, {})
        return {str(t) for t in found}

    async def add_tabs(self, sid: str, titles: list[str]) -> None:
        await self._rpc("add_tabs", sid, {"titles": titles})

    async def put_rows(self, sid: str, data: dict[str, list[list[str]]]) -> None:
        await self._rpc(
            "put_rows", sid, {"data": [{"range": r, "values": v} for r, v in data.items()]}
        )

    async def read(self, sid: str, ranges: list[str]) -> list[list[list[str]]]:
        found = await self._rpc("read", sid, {"ranges": ranges})
        return [[[str(c) for c in row] for row in block] for block in found]

    async def append(self, sid: str, range_: str, rows: list[list[str]]) -> str | None:
        found = await self._rpc("append", sid, {"range": range_, "rows": rows}, write=True)
        updated = found.get("updatedRange") if isinstance(found, dict) else None
        return updated if isinstance(updated, str) else None
