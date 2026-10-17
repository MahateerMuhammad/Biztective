"""Bounded per-session token buckets (SEC-20, single application process)."""

import math
import re
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.service import resolve_session
from app.core.errors import problem_response

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
CAPACITY = 300
REFILL_PER_SECOND = CAPACITY / 60
MAX_BUCKETS = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class SessionRateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, sessionmaker: async_sessionmaker[AsyncSession], cookie_name: str
    ) -> None:
        self.app = app
        self.sessionmaker = sessionmaker
        self.cookie_name = cookie_name
        self.buckets: dict[str, _Bucket] = {}
        self.last_sweep = 0.0

    def retry_after(self, session_hash: str, now: float) -> int | None:
        # Entries with a full bucket can be removed without resetting any quota.
        if now - self.last_sweep >= 60:
            self.buckets = {
                key: bucket
                for key, bucket in self.buckets.items()
                if bucket.tokens + (now - bucket.updated_at) * REFILL_PER_SECOND < CAPACITY
            }
            self.last_sweep = now
        bucket = self.buckets.get(session_hash)
        if bucket is None:
            if len(self.buckets) >= MAX_BUCKETS:
                return 60  # Do not evict active buckets and let them bypass their quota.
            bucket = _Bucket(float(CAPACITY), now)
            self.buckets[session_hash] = bucket
        bucket.tokens = min(CAPACITY, bucket.tokens + (now - bucket.updated_at) * REFILL_PER_SECOND)
        bucket.updated_at = now
        if bucket.tokens < 1:
            return max(1, math.ceil((1 - bucket.tokens) / REFILL_PER_SECOND))
        bucket.tokens -= 1
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        token = Request(scope).cookies.get(self.cookie_name)
        if token and _TOKEN.fullmatch(token):
            async with self.sessionmaker() as db:
                user = await resolve_session(db, token=token)
            if user is not None:
                wait = self.retry_after(user.session_id_hash, time.monotonic())
                if wait is not None:
                    await problem_response(
                        429, instance=scope["path"], headers={"Retry-After": str(wait)}
                    )(scope, receive, send)
                    return
        await self.app(scope, receive, send)
