"""A deterministic in-memory Google (token endpoint + Sheets v4) behind httpx.MockTransport.

Faults are scripted per operation and consumed in order, so tests can reproduce timeouts
before and after a write is applied, rate limits, server errors, expired tokens, network
failures and a process dying right after a write.
"""

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import jwt

from app.actions.sheets import SCOPE, TOKEN_URL, ServiceAccount, load_service_account

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-ab"
OTHER_SID = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210_-zz"
RANGE = re.compile(r"^'(?P<title>[^']+)'!(?P<cells>[A-Z0-9:]+)$")
APPLIED_THEN = {"timeout_after", "500_after", "crash_after", "reset_after"}


class ProcessDied(BaseException):
    """Raised from inside the transport like a kill: nothing after it runs."""


def make_account() -> tuple[ServiceAccount, str, bytes]:
    """A freshly generated service account: (account, base64 key file, public key PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "project_id": "root-test",
        "private_key_id": "k1",
        "private_key": pem,
        "client_email": "root-executor@root-test.iam.gserviceaccount.com",
        "client_id": "1",
        # Must be ignored by the adapter (SEC-15).
        "token_uri": "https://attacker.example/token",
    }
    encoded = base64.b64encode(json.dumps(info).encode()).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return load_service_account(encoded), encoded, public


@dataclass
class FakeGoogle:
    public_key: bytes
    email: str
    spreadsheets: dict[str, dict[str, list[list[str]]]] = field(default_factory=dict)
    shared: set[str] = field(default_factory=set)
    faults: list[tuple[str, str]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    credentials_valid: bool = True
    issued: set[str] = field(default_factory=set)
    gate: asyncio.Event | None = None  # when set, appends wait for it
    writing: asyncio.Event = field(default_factory=asyncio.Event)  # an append is in progress

    def sheet(self, sid: str = SID, *, shared: bool = True) -> dict[str, list[list[str]]]:
        book = self.spreadsheets.setdefault(sid, {"Sheet1": []})
        if shared:
            self.shared.add(sid)
        return book

    def fail(self, op: str, *faults: str) -> None:
        self.faults.extend((op, f) for f in faults)

    def expire_tokens(self) -> None:
        self.issued.clear()

    def ops(self, name: str) -> int:
        return sum(1 for entry in self.log if entry == name)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------ dispatch

    def _fault(self, op: str) -> str | None:
        for i, (name, fault) in enumerate(self.faults):
            if name == op:
                del self.faults[i]
                return fault
        return None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.scheme != "https" or host not in {
            "oauth2.googleapis.com",
            "sheets.googleapis.com",
        }:
            raise AssertionError(f"request to a host outside the allowlist: {request.url}")
        if host == "oauth2.googleapis.com":
            return self._token(request)
        op, sid, rest = self._route(request)
        self.log.append(op)
        fault = self._fault(op)
        if fault == "connect":
            raise httpx.ConnectError("connection refused", request=request)
        if fault == "timeout_before":
            raise httpx.ReadTimeout("timed out", request=request)
        if fault == "reset_before":
            raise httpx.RemoteProtocolError("connection reset", request=request)
        if fault in {"429", "500", "401", "403", "404", "400"}:
            headers = {"Retry-After": "7"} if fault == "429" else {}
            return httpx.Response(int(fault), json={"error": {"code": int(fault)}}, headers=headers)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.issued:
            return httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}})
        if sid not in self.spreadsheets:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
        if sid not in self.shared:
            return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})
        if op == "append":
            self.writing.set()
            if self.gate is not None:
                await self.gate.wait()
        response = self._apply(op, sid, rest, request)
        if fault in APPLIED_THEN:
            if fault == "timeout_after":
                raise httpx.ReadTimeout("timed out after the write", request=request)
            if fault == "reset_after":
                raise httpx.RemoteProtocolError("connection reset", request=request)
            if fault == "crash_after":
                raise ProcessDied
            return httpx.Response(500, json={"error": {"code": 500}})
        return response

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.log.append("token")
        fault = self._fault("token")
        if fault == "500":
            return httpx.Response(500)
        form = parse_qs(request.content.decode())
        claims = jwt.decode(  # type: ignore[no-untyped-call]  # untyped google-auth helper
            form["assertion"][0], certs=self.public_key, audience=TOKEN_URL
        )
        assert claims["iss"] == self.email
        assert claims["scope"] == SCOPE
        if not self.credentials_valid:
            return httpx.Response(400, json={"error": "invalid_grant"})
        token = f"token-{len(self.issued) + 1}-{len(self.log)}"
        self.issued.add(token)
        return httpx.Response(200, json={"access_token": token, "expires_in": 3600})

    def _route(self, request: httpx.Request) -> tuple[str, str, str]:
        path = request.url.raw_path.decode().split("?")[0]
        tail = path.removeprefix("/v4/spreadsheets/")
        sid, _, rest = tail.partition("/")
        if sid.endswith(":batchUpdate"):
            return "add_tabs", sid.removesuffix(":batchUpdate"), ""
        if not rest:
            return "metadata", sid, ""
        if rest == "values:batchGet":
            return "read", sid, ""
        if rest == "values:batchUpdate":
            return "put_rows", sid, ""
        if rest.endswith(":append"):
            return "append", sid, unquote(rest.removeprefix("values/").removesuffix(":append"))
        raise AssertionError(f"unexpected Sheets call {path}")

    # --------------------------------------------------------------------- state

    def _tab(self, sid: str, ref: str) -> tuple[list[list[str]] | None, str]:
        match = RANGE.match(ref)
        if match is None:
            return None, ""
        return self.spreadsheets[sid].get(match["title"]), match["title"]

    def _apply(self, op: str, sid: str, rest: str, request: httpx.Request) -> httpx.Response:
        book = self.spreadsheets[sid]
        if op == "metadata":
            return httpx.Response(
                200, json={"sheets": [{"properties": {"title": t}} for t in book]}
            )
        if op == "add_tabs":
            titles = [
                r["addSheet"]["properties"]["title"]
                for r in json.loads(request.content)["requests"]
            ]
            if any(t in book for t in titles):
                return httpx.Response(400, json={"error": {"message": "already exists"}})
            for t in titles:
                book[t] = []
            return httpx.Response(200, json={})
        if op == "read":
            assert request.url.params.get("valueRenderOption") == "UNFORMATTED_VALUE"
            ranges = request.url.params.get_list("ranges")
            out = []
            for ref in ranges:
                rows, _ = self._tab(sid, ref)
                if rows is None:
                    return httpx.Response(400, json={"error": {"message": "Unable to parse range"}})
                cells = RANGE.match(ref)
                limit = 1 if cells and cells["cells"] == "A1" else None
                values = [self._trim(r) for r in rows[:limit]]
                out.append({"range": ref, "values": values} if values else {"range": ref})
            return httpx.Response(200, json={"valueRanges": out})
        if op == "put_rows":
            body = json.loads(request.content)
            assert body["valueInputOption"] == "RAW"
            for item in body["data"]:
                rows, _ = self._tab(sid, item["range"])
                if rows is None:
                    return httpx.Response(400, json={"error": {"message": "Unable to parse range"}})
                if rows:
                    rows[0] = list(item["values"][0])
                else:
                    rows.append(list(item["values"][0]))
            return httpx.Response(200, json={})
        if op == "append":
            params = request.url.params
            assert params["valueInputOption"] == "RAW"
            assert params["insertDataOption"] == "INSERT_ROWS"
            rows, title = self._tab(sid, rest)
            if rows is None:
                return httpx.Response(400, json={"error": {"message": "Unable to parse range"}})
            new = json.loads(request.content)["values"]
            start = len(rows) + 1
            rows.extend([list(r) for r in new])
            return httpx.Response(
                200,
                json={"updates": {"updatedRange": f"'{title}'!A{start}:L{start + len(new) - 1}"}},
            )
        raise AssertionError(op)  # pragma: no cover

    @staticmethod
    def _trim(row: list[str]) -> list[str]:
        out = list(row)
        while out and out[-1] == "":
            out.pop()
        return out
