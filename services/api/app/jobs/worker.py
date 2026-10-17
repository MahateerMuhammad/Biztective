"""In-process job worker started from the application lifespan (ADR-003)."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs import queue
from app.jobs.queue import JobType, LeasedJob

logger = logging.getLogger(__name__)

Handler = Callable[[async_sessionmaker[AsyncSession], LeasedJob], Awaitable[None]]
HEARTBEAT_SECONDS = 30.0


class PermanentJobError(Exception):
    """The job cannot succeed by retrying; `code` is recorded on the job."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RetryableJobError(Exception):
    """A transient failure the queue should retry with backoff; `code` is recorded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _heartbeat(sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        async with sessionmaker() as session:
            await queue.extend_lease(session, job)


async def process_next_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    handlers: dict[JobType, Handler],
    on_final_failure: Callable[[LeasedJob, str], Awaitable[None]] | None = None,
) -> bool:
    """Lease and run one job. Returns False when no job was due."""
    async with sessionmaker() as session:
        job = await queue.lease_next(session)
    if job is None:
        return False
    heartbeat = asyncio.create_task(_heartbeat(sessionmaker, job))
    try:
        # An expired final lease is reclaimed once to deliver terminal failure through
        # the normal hook, but must never run the costly handler for another attempt.
        if job.attempts > job.max_attempts:
            raise PermanentJobError("lease_exhausted")
        await handlers[job.type](sessionmaker, job)
    except PermanentJobError as exc:
        await _record_failure(sessionmaker, job, exc.code, False, on_final_failure)
    except RetryableJobError as exc:
        await _record_failure(sessionmaker, job, exc.code, True, on_final_failure)
    except (OperationalError, DBAPIError, OSError) as exc:
        logger.warning(
            "job_transient_error", extra={"job_id": str(job.id), "error": type(exc).__name__}
        )
        await _record_failure(sessionmaker, job, "transient_error", True, on_final_failure)
    except Exception:
        logger.exception("job_failed", extra={"job_id": str(job.id), "job_type": job.type})
        await _record_failure(sessionmaker, job, "unexpected_error", False, on_final_failure)
    else:
        async with sessionmaker() as session:
            await queue.finish(session, job)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    return True


async def _record_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
    job: LeasedJob,
    code: str,
    retryable: bool,
    on_final_failure: Callable[[LeasedJob, str], Awaitable[None]] | None,
) -> None:
    async with sessionmaker() as session:
        retrying = await queue.fail(session, job, error_code=code, retryable=retryable)
    if retrying is False and on_final_failure is not None:
        await on_final_failure(job, code)


async def run_worker(
    sessionmaker: async_sessionmaker[AsyncSession],
    handlers: dict[JobType, Handler],
    on_final_failure: Callable[[LeasedJob, str], Awaitable[None]],
    *,
    poll_seconds: float,
) -> None:
    """Poll forever; cancelled by the lifespan on shutdown."""
    while True:
        try:
            worked = await process_next_job(sessionmaker, handlers, on_final_failure)
        except OperationalError, DBAPIError, OSError:
            logger.warning("worker_poll_failed")
            worked = False
        if not worked:
            await asyncio.sleep(poll_seconds)
