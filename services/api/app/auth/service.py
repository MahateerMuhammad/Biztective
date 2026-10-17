"""Login, session lifecycle and throttling (SEC-01, SEC-02, SEC-04)."""

import asyncio
import hashlib
import hmac
import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.models import AuthAttempt, Session, User

SESSION_IDLE_TIMEOUT = timedelta(hours=8)
SESSION_ABSOLUTE_TIMEOUT = timedelta(days=7)
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)
THROTTLE_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_ACCOUNT = 5
MAX_FAILURES_PER_IP = 20


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str
    display_name: str
    session_id_hash: str


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _keyed_hash(secret: bytes, value: str) -> str:
    # Keyed so the throttling table cannot be reversed with a dictionary of emails or IPs.
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


async def lock_login_attempt(
    session: AsyncSession, *, secret: bytes, email: str, client_host: str
) -> None:
    """Serialize throttle check and recorded outcome for both shared quota dimensions.

    Acquire in a consistent order so overlapping account/IP pairs cannot deadlock.
    Locks are transaction-local and released with the login's audit/attempt commit.
    """
    keys = sorted((_keyed_hash(secret, "email:" + email), _keyed_hash(secret, "ip:" + client_host)))
    for key in keys:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )


async def throttle_retry_after(
    session: AsyncSession, *, secret: bytes, email: str, client_host: str
) -> int | None:
    """Seconds until another attempt is allowed, or None when not throttled."""
    since = utcnow() - THROTTLE_WINDOW
    checks = (
        (AuthAttempt.email_hash, _keyed_hash(secret, email), MAX_FAILURES_PER_ACCOUNT),
        (AuthAttempt.ip_hash, _keyed_hash(secret, client_host), MAX_FAILURES_PER_IP),
    )
    waits: list[int] = []
    for column, value, limit in checks:
        failures = (
            select(AuthAttempt.at)
            .where(column == value, AuthAttempt.succeeded.is_(False), AuthAttempt.at > since)
            .order_by(AuthAttempt.at.desc())
            .limit(limit)
            .subquery()
        )
        count, oldest = (await session.execute(select(func.count(), func.min(failures.c.at)))).one()
        if count >= limit and oldest is not None:
            seconds = (oldest + THROTTLE_WINDOW - utcnow()).total_seconds()
            waits.append(max(1, math.ceil(seconds)))
    return max(waits) if waits else None


async def record_attempt(
    session: AsyncSession, *, secret: bytes, email: str, client_host: str, succeeded: bool
) -> None:
    await session.execute(
        insert(AuthAttempt).values(
            email_hash=_keyed_hash(secret, email),
            ip_hash=_keyed_hash(secret, client_host),
            succeeded=succeeded,
            at=utcnow(),
        )
    )


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == email))
    valid = await asyncio.to_thread(verify_password, user.password_hash if user else None, password)
    if user is None or not valid or not user.is_active:
        return None
    if needs_rehash(user.password_hash):
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(password_hash=await asyncio.to_thread(hash_password, password))
        )
    return user


async def create_session(session: AsyncSession, *, user_id: UUID) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    await session.execute(
        insert(Session).values(
            id_hash=hash_session_token(token),
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_ABSOLUTE_TIMEOUT,
        )
    )
    return token


async def revoke_session(session: AsyncSession, *, token: str) -> None:
    await session.execute(
        update(Session)
        .where(Session.id_hash == hash_session_token(token), Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def resolve_session(session: AsyncSession, *, token: str) -> AuthenticatedUser | None:
    """Return the user for a valid session, refreshing its idle timer at most once a minute."""
    id_hash = hash_session_token(token)
    row = (
        await session.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.id_hash == id_hash)
        )
    ).one_or_none()
    if row is None:
        return None
    stored, user = row
    now = utcnow()
    if (
        stored.revoked_at is not None
        or stored.expires_at <= now
        or stored.last_seen_at + SESSION_IDLE_TIMEOUT <= now
        or not user.is_active
    ):
        return None
    if now - stored.last_seen_at >= SESSION_TOUCH_INTERVAL:
        await session.execute(
            update(Session).where(Session.id_hash == id_hash).values(last_seen_at=now)
        )
        await session.commit()
    return AuthenticatedUser(
        id=user.id, email=user.email, display_name=user.display_name, session_id_hash=id_hash
    )
