"""A deployed Apps Script bridge (AMD-19) behind httpx.MockTransport, sharing FakeGoogle's
spreadsheets and fault script. Like the real web app it answers a POST with a 302 to
script.googleusercontent.com, where the result is fetched."""

import itertools
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.actions.fake_google import RANGE, FakeGoogle, ProcessDied

BRIDGE_URL = "https://script.google.com/macros/s/AKfycbTestDeploymentId_0123456789abcdef/exec"
TOKEN = "bridge-token-" + "x" * 40
ECHO = "https://script.googleusercontent.com/macros/echo"
OPS = {
    "titles": "metadata",
    "add_tabs": "add_tabs",
    "put_rows": "put_rows",
    "read": "read",
    "append": "append",
}


@dataclass
class FakeBridge:
    google: FakeGoogle
    token: str = TOKEN
    answers: dict[str, Any] = field(default_factory=dict)
    counter: itertools.count[int] = field(default_factory=itertools.count)
    bound_to: str | None = None  # the spreadsheet the script lives in

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "script.googleusercontent.com":
            key = request.url.params["id"]
            hops = int(request.url.params.get("hops", "0"))
            if request.url.params.get("error"):  # Google's generic HTML error page
                return httpx.Response(200, text="<html><title>Error</title></html>")
            if hops > 0:  # Google redirecting its own result URL again
                return httpx.Response(
                    302,
                    headers={"Location": f"{ECHO}?id={key}&hops={hops - 1}"},
                )
            return httpx.Response(200, json=self.answers.pop(key))
        if str(request.url) != BRIDGE_URL or request.method != "POST":
            raise AssertionError(f"request outside the bridge allowlist: {request.url}")
        body = json.loads(request.content)
        op = OPS.get(body.get("op"), "unknown")
        self.google.log.append(op)
        fault = self.google._fault(op)
        if fault == "connect":
            raise httpx.ConnectError("refused", request=request)
        if fault == "timeout_before":
            raise httpx.ReadTimeout("timed out", request=request)
        if fault in {"429", "500"}:
            return httpx.Response(
                int(fault), headers={"Retry-After": "7"} if fault == "429" else {}
            )
        if fault == "html":
            return httpx.Response(200, text="<html>Sign in</html>")
        if fault == "evil_redirect":
            return httpx.Response(302, headers={"Location": "https://evil.example/x"})
        answer = self._answer(body)
        if fault == "timeout_after":
            raise httpx.ReadTimeout("timed out after the script ran", request=request)
        if fault == "crash_after":
            raise ProcessDied
        key = str(next(self.counter))
        self.answers[key] = answer
        hops = {"extra_hop": 1, "redirect_loop": 99}.get(fault or "", 0)
        error = "&error=1" if fault == "error_page" else ""
        return httpx.Response(302, headers={"Location": f"{ECHO}?id={key}&hops={hops}{error}"})

    def _answer(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("token") != self.token:
            return {"ok": False, "status": 401, "error": "bad_token"}
        sid = body.get("spreadsheet_id")
        if sid != (self.bound_to or sid) or sid not in self.google.spreadsheets:
            return {"ok": False, "status": 403, "error": "spreadsheet_not_allowed"}
        book = self.google.spreadsheets[sid]
        args = body.get("args", {})
        op = body.get("op")

        def tab(ref: str) -> tuple[list[list[str]] | None, re.Match[str] | None]:
            m = RANGE.match(ref)
            return (book.get(m["title"]) if m else None), m

        if op == "titles":
            return {"ok": True, "result": list(book)}
        if op == "add_tabs":
            if any(t in book for t in args["titles"]):
                return {"ok": False, "status": 400, "error": "tab_exists_or_invalid"}
            for t in args["titles"]:
                book[t] = []
            return {"ok": True, "result": {}}
        if op == "put_rows":
            for item in args["data"]:
                rows, _ = tab(item["range"])
                if rows is None:
                    return {"ok": False, "status": 400, "error": "unknown_tab"}
                if rows:
                    rows[0] = list(item["values"][0])
                else:
                    rows.append(list(item["values"][0]))
            return {"ok": True, "result": {}}
        if op == "read":
            out = []
            for ref in args["ranges"]:
                rows, m = tab(ref)
                if rows is None or m is None:
                    return {"ok": False, "status": 400, "error": "unknown_tab"}
                chosen = rows[:1] if m["cells"] == "A1" else rows
                out.append([FakeGoogle._trim(r) for r in chosen])
            return {"ok": True, "result": out}
        if op == "append":
            rows, m = tab(args["range"])
            if rows is None or m is None:
                return {"ok": False, "status": 400, "error": "unknown_tab"}
            start = len(rows) + 1
            rows.extend([list(r) for r in args["rows"]])
            end = start + len(args["rows"]) - 1
            return {"ok": True, "result": {"updatedRange": f"'{m['title']}'!A{start}:L{end}"}}
        return {"ok": False, "status": 400, "error": "unknown_op"}
