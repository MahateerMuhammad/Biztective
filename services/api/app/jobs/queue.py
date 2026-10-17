"""PostgreSQL job queue (ADR-003): atomic enqueue, SKIP LOCKED leasing, leases and retries."""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job

JobType = Literal[
    "ingest_dataset", "analyze_dataset", "run_investigation", "run_recovery_plan", "execute_action"
]
LEASE = timedelta(seconds=120)
MAX_ATTEMPTS: dict[JobType, int] = {
    "ingest_dataset": 3,
    "analyze_dataset": 3,
    "run_investigation": 3,
    "run_recovery_plan": 3,
    "execute_action": 3,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class LeasedJob:
    id: uuid.UUID
    type: JobType
    business_id: uuid.UUID | None
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


async def enqueue(
    session: AsyncSession,
    *,
    job_type: JobType,
    business_id: uuid.UUID | None,
    payload: dict[str, Any],
    dedupe_key: str,
) -> None:
    """Add a job in the caller's transaction; a duplicate dedupe key is a no-op."""
    await session.execute(
        insert(Job)
        .values(
            id=uuid.uuid7(),
            business_id=business_id,
            type=job_type,
            payload=payload,
            status="queued",
            max_attempts=MAX_ATTEMPTS[job_type],
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def lease_next(session: AsyncSession) -> LeasedJob | None:
    """Recover expired leases, then lease one due job. Commits."""
    now = utcnow()
    await session.execute(
        text("""
            UPDATE jobs SET status = 'queued', lease_expires_at = NULL, updated_at = :now
            WHERE status = 'running' AND lease_expires_at < :now
        """),
        {"now": now},
    )
    row = (
        await session.execute(
            text("""
                UPDATE jobs SET status = 'running', attempts = attempts + 1,
                    lease_expires_at = :lease, updated_at = :now
                WHERE id = (
                    SELECT id FROM jobs WHERE status = 'queued' AND run_after <= :now
                    ORDER BY priority, run_after, id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                RETURNING id, type, business_id, payload, attempts, max_attempts
            """),
            {"now": now, "lease": now + LEASE},
        )
    ).one_or_none()
    await session.commit()
    if row is None:
        return None
    return LeasedJob(
        id=row.id,
        type=row.type,
        business_id=row.business_id,
        payload=row.payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
    )


async def extend_lease(session: AsyncSession, job_id: uuid.UUID) -> None:
    now = utcnow()
    await session.execute(
        text("""
            UPDATE jobs SET lease_expires_at = :lease, updated_at = :now
            WHERE id = :id AND status = 'running'
        """),
        {"id": job_id, "lease": now + LEASE, "now": now},
    )
    await session.commit()


async def finish(session: AsyncSession, job_id: uuid.UUID) -> None:
    now = utcnow()
    await session.execute(
        text("""
            UPDATE jobs SET status = 'succeeded', lease_expires_at = NULL, finished_at = :now,
                updated_at = :now
            WHERE id = :id
        """),
        {"id": job_id, "now": now},
    )
    await session.commit()


async def fail(session: AsyncSession, job: LeasedJob, *, error_code: str, retryable: bool) -> bool:
    """Record a failure. Returns True if the job will be retried."""
    now = utcnow()
    retry = retryable and job.attempts < job.max_attempts
    backoff = timedelta(seconds=(2**job.attempts) * 5 * random.uniform(0.8, 1.2))  # noqa: S311
    await session.execute(
        text("""
            UPDATE jobs SET status = :status, lease_expires_at = NULL, last_error_code = :code,
                run_after = :run_after, finished_at = :finished, updated_at = :now
            WHERE id = :id
        """),
        {
            "id": job.id,
            "status": "queued" if retry else "failed",
            "code": error_code,
            "run_after": now + backoff if retry else now,
            "finished": None if retry else now,
            "now": now,
        },
    )
    await session.commit()
    return retry
