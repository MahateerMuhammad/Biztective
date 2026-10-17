"""The action executor (AMD-18): the only code that changes anything outside ROOT.

It runs as an `execute_action` job, separate from the investigation and recovery graphs, and
acts only on an owner-approved, hashed payload bound to the business's verified spreadsheet.
Every attempt is recorded and committed before the first external call. Writes are
reconciled: the destination is read before every write and only rows whose key is absent are
appended, so a timeout after a successful write never produces a second copy. A read-back then
compares every row cell by cell.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import payload as payloads
from app.actions.bridge import BridgeClient
from app.actions.service import authorization_problem, utcnow
from app.actions.sheets import (
    ServiceAccount,
    SheetsApi,
    SheetsClient,
    SheetsError,
    TokenCache,
    load_service_account,
)
from app.audit.writer import record_audit
from app.core.config import Settings
from app.investigation.store import transaction
from app.jobs.queue import LeasedJob
from app.jobs.worker import PermanentJobError, RetryableJobError
from app.models import ActionExecution, PlanAction

logger = logging.getLogger(__name__)

LEASE = timedelta(seconds=300)
ATTEMPT_TIMEOUT_SECONDS = 240.0
WRITE_ROUNDS = 3
MAX_LISTED = 50


class AuthorizationLostError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Attempt:
    number: int
    writes_sent: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)


def _padded(row: list[str], width: int) -> list[str]:
    # The API omits trailing empty cells.
    return (row + [""] * width)[:width]


def _same(expected: str, found: str) -> bool:
    # A spreadsheet may keep the escaping apostrophe as a quote prefix rather than as text.
    return found == expected or (expected.startswith("'") and found == expected[1:])


def compare(
    tabs: list[dict[str, Any]], found: dict[str, list[list[str]]], prefix: str
) -> dict[str, Any]:
    """Read-back verification: every expected row exactly once and identical."""
    missing: list[str] = []
    duplicated: list[str] = []
    mismatched: list[str] = []
    unexpected: list[str] = []
    counts: dict[str, dict[str, int]] = {}
    for tab in tabs:
        width = len(tab["header"])
        expected = {row[0]: row for row in tab["rows"]}
        mine: dict[str, list[list[str]]] = {}
        for row in found.get(tab["title"], []):
            if row and row[0].startswith(prefix):
                mine.setdefault(row[0], []).append(_padded(row, width))
        for key, row in expected.items():
            copies = mine.get(key, [])
            if not copies:
                missing.append(key)
            elif len(copies) > 1:
                duplicated.append(key)
            elif not all(_same(e, f) for e, f in zip(row, copies[0], strict=True)):
                mismatched.append(key)
        unexpected += [k for k in mine if k not in expected]
        counts[tab["title"]] = {
            "expected": len(expected),
            "found": sum(len(v) for k, v in mine.items() if k in expected),
        }
    return {
        "ok": not (missing or duplicated or mismatched or unexpected),
        "tabs": counts,
        "missing": missing[:MAX_LISTED],
        "duplicated": duplicated[:MAX_LISTED],
        "mismatched": mismatched[:MAX_LISTED],
        "unexpected": unexpected[:MAX_LISTED],
        "checked_at": utcnow().isoformat(),
    }


class ActionRuntime:
    def __init__(
        self,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.sleep = sleep
        secret = settings.google_service_account_json
        self.account: ServiceAccount | None = (
            load_service_account(secret.get_secret_value()) if secret is not None else None
        )
        self.tokens = TokenCache()
        # AMD-19: an Apps Script web app owned by the business, preferred when configured.
        self.bridge_url = settings.sheets_bridge_url
        self.bridge_token = (
            settings.sheets_bridge_token.get_secret_value()
            if settings.sheets_bridge_token is not None
            else None
        )
        # Tests replace the transport with a fake Google; production uses the network.
        self.transport: httpx.AsyncBaseTransport | None = None

    @property
    def mode(self) -> str | None:
        if self.bridge_url is not None and self.bridge_token is not None:
            return "apps_script"
        return "service_account" if self.account is not None else None

    @contextlib.asynccontextmanager
    async def client(self, on_write: Callable[[], None] = lambda: None) -> AsyncIterator[SheetsApi]:
        mode = self.mode
        if mode is None:
            raise SheetsError("integration_unavailable")
        timeout = httpx.Timeout(self.settings.sheets_timeout_seconds)
        common: dict[str, Any] = {
            "max_retries": self.settings.sheets_max_retries,
            "backoff": self.settings.sheets_backoff_seconds,
            "sleep": self.sleep,
            "on_write": on_write,
        }
        async with httpx.AsyncClient(
            transport=self.transport, timeout=timeout, follow_redirects=False
        ) as http:
            if self.bridge_url is not None and self.bridge_token is not None:
                yield BridgeClient(
                    url=self.bridge_url, token=self.bridge_token, http=http, **common
                )
            elif self.account is not None:
                yield SheetsClient(account=self.account, tokens=self.tokens, http=http, **common)

    # ------------------------------------------------------------------- job handler

    async def run(self, sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob) -> None:
        if job.business_id is None:
            raise PermanentJobError("missing_business")
        business_id = job.business_id
        action_id = UUID(job.payload["action_id"])
        attempt = await self._begin(sessionmaker, business_id, action_id, job.id)
        if attempt is None:
            return
        try:
            async with asyncio.timeout(ATTEMPT_TIMEOUT_SECONDS):
                await self._execute(sessionmaker, business_id, action_id, attempt)
        except AuthorizationLostError as exc:
            await self._settle(sessionmaker, business_id, action_id, attempt, exc.code, False)
        except SheetsError as exc:
            if exc.retryable:
                await self._close(sessionmaker, business_id, action_id, attempt, exc.code)
                raise RetryableJobError(exc.code) from exc
            await self._settle(sessionmaker, business_id, action_id, attempt, exc.code, False)
        except TimeoutError as exc:
            await self._close(sessionmaker, business_id, action_id, attempt, "attempt_timeout")
            raise RetryableJobError("attempt_timeout") from exc

    async def mark_failed(
        self, sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob, code: str
    ) -> None:
        """The job will not run again: close a still-pending action."""
        if job.business_id is None:
            return
        await self._settle(
            sessionmaker, job.business_id, UUID(job.payload["action_id"]), None, code, True
        )

    # ------------------------------------------------------------------------ steps

    async def _begin(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        action_id: UUID,
        job_id: UUID,
    ) -> Attempt | None:
        """Lease, re-authorize and record the attempt; committed before any external call.
        Another job holding a live lease makes this one retry later; the same job re-leased
        after its worker died takes the action over."""
        async with transaction(sessionmaker, business_id) as session:
            action = await session.get(PlanAction, action_id, with_for_update=True)
            if action is None or action.status != "pending":
                return None
            now = utcnow()
            if (
                action.lease_expires_at is not None
                and action.lease_expires_at > now
                and action.lease_owner != job_id
            ):
                raise RetryableJobError("action_busy")
            # A worker that died mid-attempt left its attempt open.
            await session.execute(
                update(ActionExecution)
                .where(ActionExecution.action_id == action_id, ActionExecution.status == "started")
                .values(status="interrupted", error_code="worker_lost", finished_at=func.now())
            )
            problem = await authorization_problem(session, action)
            if problem is not None:
                await self._finish(session, action, "failed", problem)
                await record_audit(
                    session,
                    action="action.refused",
                    actor_user_id=None,
                    business_id=business_id,
                    target_type="plan_action",
                    target_id=str(action_id),
                    metadata={"reason": problem, "plan_id": str(action.plan_id)},
                )
                return None
            number = action.attempts + 1
            await session.execute(
                update(PlanAction)
                .where(PlanAction.id == action_id)
                .values(
                    attempts=number,
                    lease_owner=job_id,
                    lease_expires_at=now + LEASE,
                    updated_at=func.now(),
                )
            )
            await session.execute(
                insert(ActionExecution).values(
                    business_id=business_id,
                    action_id=action_id,
                    attempt=number,
                    status="started",
                )
            )
        return Attempt(number)

    async def _recheck(
        self, sessionmaker: async_sessionmaker[AsyncSession], business_id: UUID, action_id: UUID
    ) -> PlanAction:
        async with transaction(sessionmaker, business_id) as session:
            action = await session.get(PlanAction, action_id)
            if action is None:  # pragma: no cover - rows are never deleted while pending
                raise AuthorizationLostError("action_missing")
            problem = await authorization_problem(session, action)
        if problem is not None:
            raise AuthorizationLostError(problem)
        return action

    async def _read(
        self, client: SheetsApi, sid: str, tabs: list[dict[str, Any]]
    ) -> dict[str, list[list[str]]]:
        """The destination's rows per tab (header excluded); creates missing tabs and refuses a
        tab whose header is not ours."""
        titles = await client.titles(sid)
        absent = [t["title"] for t in tabs if t["title"] not in titles]
        if absent:
            try:
                await client.add_tabs(sid, absent)
            except SheetsError as exc:
                if exc.retryable or exc.code != "destination_invalid":
                    raise
                # Another attempt may have created them meanwhile.
                if {*absent} - await client.titles(sid):
                    raise
        values = await client.read(sid, [payloads.tab_range(t["title"]) for t in tabs])
        headers: dict[str, list[list[str]]] = {}
        found: dict[str, list[list[str]]] = {}
        for tab, rows in zip(tabs, values, strict=True):
            if not rows:
                headers[f"'{tab['title']}'!A1"] = [tab["header"]]
                found[tab["title"]] = []
            elif _padded(rows[0], len(tab["header"])) != tab["header"]:
                raise SheetsError("destination_layout_mismatch")
            else:
                found[tab["title"]] = rows[1:]
        if headers:
            await client.put_rows(sid, headers)
        return found

    async def _execute(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        action_id: UUID,
        attempt: Attempt,
    ) -> None:
        action = await self._recheck(sessionmaker, business_id, action_id)
        sid = action.destination["spreadsheet_id"]
        tabs: list[dict[str, Any]] = action.payload["tabs"]
        prefix = action.id.hex
        confirmed: set[str] = set()
        refs: dict[str, str | None] = {}

        def sent() -> None:
            attempt.writes_sent = True

        async with self.client(on_write=sent) as client:
            try:
                for round_ in range(WRITE_ROUNDS + 1):
                    found = await self._read(client, sid, tabs)
                    present = {title: {r[0] for r in rows if r} for title, rows in found.items()}
                    missing = {
                        t["title"]: [
                            r
                            for r in t["rows"]
                            if r[0] not in present[t["title"]] and r[0] not in confirmed
                        ]
                        for t in tabs
                    }
                    if not any(missing.values()):
                        break
                    if round_ == WRITE_ROUNDS:
                        raise SheetsError("write_unconfirmed", retryable=True)
                    await self._recheck(sessionmaker, business_id, action_id)
                    try:
                        for title, rows in missing.items():
                            if rows:
                                refs[title] = await client.append(
                                    sid, payloads.tab_range(title), rows
                                )
                                confirmed.update(r[0] for r in rows)
                    except SheetsError as exc:
                        if not (exc.retryable or exc.ambiguous):
                            raise
                        # Unknown outcome: the next round re-reads before writing again.
                        await self.sleep(
                            exc.retry_after
                            if exc.retry_after is not None
                            else self.settings.sheets_backoff_seconds * 2**round_
                        )
            finally:
                attempt.calls = client.calls
        await self._record_success(sessionmaker, business_id, action_id, sid, refs)
        verification = compare(tabs, found, prefix)
        status = "verified" if verification["ok"] else "manual_review"
        async with transaction(sessionmaker, business_id) as session:
            row = await session.get(PlanAction, action_id, with_for_update=True)
            if row is None or row.status not in ("pending", "succeeded"):  # pragma: no cover
                return
            await self._close_attempt(session, action_id, attempt, "completed", None)
            await session.execute(
                update(PlanAction)
                .where(PlanAction.id == action_id)
                .values(verification=verification)
            )
            await self._finish(
                session, row, status, None if verification["ok"] else "verification_mismatch"
            )
            await record_audit(
                session,
                action=f"action.{status}",
                actor_user_id=None,
                business_id=business_id,
                target_type="plan_action",
                target_id=str(action_id),
                metadata={
                    "plan_id": str(row.plan_id),
                    "attempt": attempt.number,
                    "rows": {t: c["expected"] for t, c in verification["tabs"].items()},
                    "missing": len(verification["missing"]),
                    "duplicated": len(verification["duplicated"]),
                    "mismatched": len(verification["mismatched"]),
                },
            )

    async def _record_success(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        action_id: UUID,
        sid: str,
        refs: dict[str, str | None],
    ) -> None:
        """Every row is confirmed present: record `succeeded` before verifying."""
        async with transaction(sessionmaker, business_id) as session:
            row = await session.get(PlanAction, action_id, with_for_update=True)
            if row is None or row.status != "pending":
                return
            await session.execute(
                update(PlanAction)
                .where(PlanAction.id == action_id)
                .values(
                    status="succeeded",
                    external_ref={
                        "spreadsheet_id": sid,
                        "tabs": row.destination["tabs"],
                        "updated_ranges": refs,
                    },
                    updated_at=func.now(),
                )
            )
            await record_audit(
                session,
                action="action.succeeded",
                actor_user_id=None,
                business_id=business_id,
                target_type="plan_action",
                target_id=str(action_id),
                metadata={"plan_id": str(row.plan_id), "rows_appended_now": bool(refs)},
            )

    # ---------------------------------------------------------------------- closing

    async def _close_attempt(
        self,
        session: AsyncSession,
        action_id: UUID,
        attempt: Attempt,
        status: str,
        code: str | None,
    ) -> None:
        await session.execute(
            update(ActionExecution)
            .where(
                ActionExecution.action_id == action_id,
                ActionExecution.attempt == attempt.number,
                ActionExecution.status == "started",
            )
            .values(
                status=status,
                error_code=code,
                writes_sent=attempt.writes_sent,
                calls=attempt.calls,
                finished_at=func.now(),
            )
        )

    async def _close(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        action_id: UUID,
        attempt: Attempt,
        code: str,
    ) -> None:
        """A retryable failure: close the attempt and release the lease; the action stays
        pending for the queue's next run."""
        async with transaction(sessionmaker, business_id) as session:
            await self._close_attempt(session, action_id, attempt, "failed", code)
            await session.execute(
                update(PlanAction)
                .where(PlanAction.id == action_id)
                .values(
                    lease_owner=None, lease_expires_at=None, error_code=code, updated_at=func.now()
                )
            )
        logger.warning("action_attempt_failed", extra={"action_id": str(action_id), "code": code})

    async def _settle(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        action_id: UUID,
        attempt: Attempt | None,
        code: str,
        exhausted: bool,
    ) -> None:
        """A final failure: `failed` when no row write was ever sent, else `manual_review`."""
        async with transaction(sessionmaker, business_id) as session:
            action = await session.get(PlanAction, action_id, with_for_update=True)
            if action is None or action.status not in ("pending", "succeeded"):
                return
            if attempt is not None:
                await self._close_attempt(session, action_id, attempt, "failed", code)
            sent = await session.scalar(
                select(func.bool_or(ActionExecution.writes_sent)).where(
                    ActionExecution.action_id == action_id
                )
            )
            status = "manual_review" if sent or action.status == "succeeded" else "failed"
            await self._finish(session, action, status, code)
            await record_audit(
                session,
                action=f"action.{status}",
                actor_user_id=None,
                business_id=business_id,
                target_type="plan_action",
                target_id=str(action_id),
                metadata={
                    "plan_id": str(action.plan_id),
                    "error_code": code,
                    "retries_exhausted": exhausted,
                },
            )

    async def _finish(
        self, session: AsyncSession, action: PlanAction, status: str, code: str | None
    ) -> None:
        await session.execute(
            update(PlanAction)
            .where(PlanAction.id == action.id)
            .values(
                status=status,
                error_code=code,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=func.now(),
                updated_at=func.now(),
            )
        )
