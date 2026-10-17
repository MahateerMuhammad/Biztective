"""Expired workers cannot mutate the queue or terminate a newer attempt."""

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs import queue
from app.jobs.worker import PermanentJobError, _record_failure, process_next_job


@pytest.mark.parametrize("stale_operation", ["heartbeat", "success", "retry", "failure"])
async def test_expired_attempt_is_fenced(
    app: FastAPI, owner_engine: Engine, stale_operation: str
) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        await queue.enqueue(
            session,
            job_type="analyze_dataset",
            business_id=None,
            payload={},
            dedupe_key=uuid.uuid4().hex,
        )
        await session.commit()
        old = await queue.lease_next(session)
    assert old is not None
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
            {"id": old.id},
        )
    async with maker() as session:
        current = await queue.lease_next(session)
    assert current is not None
    assert current.attempts == old.attempts + 1
    with owner_engine.connect() as conn:
        before = conn.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": old.id}).one()
    callbacks: list[str] = []

    async def failure_hook(job: queue.LeasedJob, code: str) -> None:
        callbacks.append(code)

    if stale_operation in ("retry", "failure"):
        await _record_failure(maker, old, "stale_error", stale_operation == "retry", failure_hook)
    else:
        async with maker() as session:
            if stale_operation == "heartbeat":
                await queue.extend_lease(session, old)
            else:
                await queue.finish(session, old)
    with owner_engine.connect() as conn:
        after = conn.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": old.id}).one()
    assert after == before
    assert callbacks == []
    async with maker() as session:
        await queue.finish(session, current)
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT status FROM jobs WHERE id = :id"), {"id": old.id})
            == "succeeded"
        )


async def test_current_terminal_failure_still_invokes_hook(app: FastAPI) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        await queue.enqueue(
            session,
            job_type="analyze_dataset",
            business_id=None,
            payload={},
            dedupe_key=uuid.uuid4().hex,
        )
        await session.commit()
    callbacks: list[str] = []

    async def handler(sessionmaker: async_sessionmaker[AsyncSession], job: queue.LeasedJob) -> None:
        raise PermanentJobError("proven_terminal_error")

    async def failure_hook(job: queue.LeasedJob, code: str) -> None:
        callbacks.append(code)

    assert await process_next_job(maker, {"analyze_dataset": handler}, failure_hook)
    assert callbacks == ["proven_terminal_error"]


async def test_expired_final_attempt_delivers_failure_without_running_handler(
    app: FastAPI, owner_engine: Engine
) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        await queue.enqueue(
            session,
            job_type="analyze_dataset",
            business_id=None,
            payload={},
            dedupe_key=uuid.uuid4().hex,
        )
        await session.commit()
        old = await queue.lease_next(session)
    assert old is not None
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET attempts = max_attempts, "
                "lease_expires_at = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": old.id},
        )
    callbacks: list[str] = []
    handler_calls: list[uuid.UUID] = []

    async def handler(sessionmaker: async_sessionmaker[AsyncSession], job: queue.LeasedJob) -> None:
        handler_calls.append(job.id)

    async def failure_hook(job: queue.LeasedJob, code: str) -> None:
        callbacks.append(code)

    assert await process_next_job(maker, {"analyze_dataset": handler}, failure_hook)
    assert handler_calls == []
    assert callbacks == ["lease_exhausted"]
    assert not await process_next_job(maker, {"analyze_dataset": handler}, failure_hook)
    with owner_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, last_error_code FROM jobs WHERE id = :id"), {"id": old.id}
        ).one()
    assert row.status == "failed"
    assert row.last_error_code == "lease_exhausted"
