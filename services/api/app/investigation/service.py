"""Starting, running, pausing, answering, resuming and failing investigations (AMD-12, AMD-15).

`run_investigation` jobs drive the LangGraph graph with a PostgreSQL checkpointer
(`thread_id` = investigation ID). A job that fails or loses its lease is re-run and resumes from
the last checkpoint; completed nodes are not executed again. When the graph pauses for a
question, the runtime records the paused checkpoint on the clarification; an answer is accepted
only for that checkpoint, stored with its author, and resumed by a new job.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphRecursionError, NodeCancelledError
from langgraph.types import Command, StateSnapshot
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.config import CODE_VERSION, CURRENT_CONFIG_VERSION
from app.core.config import Settings
from app.core.errors import ApiError
from app.investigation.catalog import CATALOG_VERSION
from app.investigation.graph import FREE_TEXT_MAX, GRAPH_VERSION, Nodes, build, initial_state
from app.investigation.store import emit, transaction
from app.jobs import queue
from app.jobs.queue import LeasedJob
from app.jobs.worker import PermanentJobError
from app.llm.provider import AnthropicProvider, LlmProvider
from app.models import (
    AnalysisRevision,
    Business,
    Clarification,
    Dataset,
    HumanInput,
    Investigation,
)

TERMINAL = frozenset({"completed", "completed_no_incidents", "insufficient_data"})
ACTIVE = frozenset({"queued", "running"})  # leased by workers
UNFINISHED = ACTIVE | {"awaiting_human"}  # one per dataset at a time
# Free text: printable characters, tab and newline only (AMD-15).
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


async def lock_starts(session: AsyncSession, business_id: UUID) -> None:
    """Serialize start deduplication, idempotency replay and hourly budget decisions."""
    await session.execute(select(Business.id).where(Business.id == business_id).with_for_update())


def provider_from_settings(settings: Settings) -> LlmProvider | None:
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is not None:
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
            effort=settings.llm_effort,
        )
    return None


@asynccontextmanager
async def checkpointer(
    settings: Settings, investigation_id: UUID | None = None
) -> AsyncIterator[AsyncPostgresSaver]:
    """A dedicated autocommit connection to the `langgraph` schema (the library's contract)."""
    conninfo = settings.database_url.get_secret_value().replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    connection: AsyncConnection[DictRow] = await AsyncConnection.connect(
        conninfo,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-c search_path=langgraph",
        connect_timeout=5,
    )
    async with connection:
        if investigation_id is not None:
            # Held for the lifetime of this dedicated connection, including provider
            # calls. A replacement lease cannot run the same graph concurrently.
            await connection.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(investigation_id),)
            )
        yield AsyncPostgresSaver(connection)


class InvestigationRuntime:
    """Job handler state: the provider is read on every run, so it can be swapped (tests)."""

    def __init__(
        self,
        settings: Settings,
        provider: LlmProvider | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.sleep = sleep

    def config(self, investigation_id: UUID) -> RunnableConfig:
        return RunnableConfig(
            configurable={"thread_id": str(investigation_id)},
            recursion_limit=self.settings.investigation_recursion_limit,
        )

    async def run(self, sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob) -> None:
        try:
            async with asyncio.timeout(self.settings.investigation_timeout_seconds):
                await self._run(sessionmaker, job)
        except TimeoutError as exc:
            raise PermanentJobError("workflow_timeout") from exc

    async def _run(self, sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob) -> None:
        if job.business_id is None:
            raise PermanentJobError("missing_business")
        investigation_id = UUID(job.payload["investigation_id"])
        async with transaction(sessionmaker, job.business_id, investigation_id) as session:
            investigation = await session.get(Investigation, investigation_id)
            if investigation is None or investigation.status not in ACTIVE:
                return
        if investigation.graph_version != GRAPH_VERSION:
            # Checkpoints of another graph version cannot be resumed by this graph.
            raise PermanentJobError("graph_version_unsupported")
        graph = build(Nodes(sessionmaker, self.settings, self.provider, self.sleep))
        config = self.config(investigation_id)
        async with checkpointer(self.settings, investigation_id) as saver:
            compiled = graph.compile(checkpointer=saver)
            snapshot = await compiled.aget_state(config)
            if snapshot.values and not snapshot.next:
                return  # the graph already finished; its final node set the status
            command: Command[Any] | None = None
            if snapshot.interrupts:
                command = await self._resume_command(sessionmaker, job, snapshot)
                if command is None:  # still waiting for an answer: record the pause again
                    await self._pause(sessionmaker, job.business_id, investigation_id, snapshot)
                    return
            resuming = bool(snapshot.next)
            async with transaction(sessionmaker, job.business_id, investigation_id) as s:
                values: dict[str, Any] = {"status": "running"}
                if resuming:
                    values["run_started_at"] = func.now()
                await s.execute(
                    update(Investigation).where(Investigation.id == investigation_id).values(values)
                )
                if resuming:
                    await emit(
                        s,
                        investigation_id,
                        job.business_id,
                        "run.resumed",
                        {
                            "job_id": str(job.id),
                            "attempt": job.attempts,
                            "next": list(snapshot.next),
                            "answer": command is not None,
                        },
                        f"run.resumed:{job.id}:{job.attempts}",
                    )
            try:
                await compiled.ainvoke(
                    command
                    if command is not None
                    else None
                    if resuming
                    else initial_state(investigation),
                    config,
                    durability="sync",
                )
            except GraphRecursionError as exc:
                raise PermanentJobError("workflow_limit") from exc
            except NodeCancelledError as exc:
                # LangGraph wraps a node's CancelledError as an Exception. Preserve
                # cancellation semantics so the worker leaves the lease recoverable
                # rather than marking a killed run as a permanent defect.
                raise asyncio.CancelledError from exc
            after = await compiled.aget_state(config)
            if after.interrupts:
                await self._pause(sessionmaker, job.business_id, investigation_id, after)

    async def _resume_command(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        job: LeasedJob,
        snapshot: StateSnapshot,
    ) -> Command[Any] | None:
        """The resume command for an answered question at exactly this checkpoint, or None
        while it is unanswered. Anything else is a stale or foreign answer: refused."""
        if job.business_id is None:  # pragma: no cover - checked by the caller
            raise PermanentJobError("missing_business")
        clarification_id = UUID(str(snapshot.interrupts[0].value["clarification_id"]))
        checkpoint_id = str(snapshot.config["configurable"]["checkpoint_id"])
        async with transaction(sessionmaker, job.business_id) as session:
            clarification = await session.get_one(Clarification, clarification_id)
            answer = await session.scalar(
                select(HumanInput).where(HumanInput.clarification_id == clarification_id)
            )
        if answer is None:
            return None
        if clarification.checkpoint_id != checkpoint_id or answer.checkpoint_id != checkpoint_id:
            raise PermanentJobError("stale_checkpoint")
        return Command(
            resume={"clarification_id": str(clarification_id), "human_input_id": str(answer.id)}
        )

    async def _pause(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        investigation_id: UUID,
        snapshot: StateSnapshot,
    ) -> None:
        """Record the paused checkpoint on its question and mark the investigation as waiting
        for a person, in one transaction (idempotent: a re-run job repeats it harmlessly)."""
        pending = snapshot.interrupts[0]
        clarification_id = UUID(str(pending.value["clarification_id"]))
        checkpoint_id = str(snapshot.config["configurable"]["checkpoint_id"])
        async with transaction(sessionmaker, business_id, investigation_id) as session:
            moved = await session.scalar(
                update(Clarification)
                .where(Clarification.id == clarification_id, Clarification.status == "open")
                .values(
                    status="awaiting",
                    checkpoint_id=checkpoint_id,
                    interrupt_id=pending.id,
                    asked_at=func.now(),
                )
                .returning(Clarification.id)
            )
            await session.execute(
                update(Investigation)
                .where(Investigation.id == investigation_id, Investigation.status.in_(ACTIVE))
                .values(status="awaiting_human", phase="awaiting_human")
            )
            if moved is not None:
                await emit(
                    session,
                    investigation_id,
                    business_id,
                    "investigation.awaiting_human",
                    {"clarification_id": str(clarification_id), "checkpoint_id": checkpoint_id},
                    f"investigation.awaiting_human:{clarification_id}",
                )

    async def mark_failed(
        self, sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob, code: str
    ) -> None:
        if job.business_id is None:
            return
        investigation_id = UUID(job.payload["investigation_id"])
        async with transaction(sessionmaker, job.business_id, investigation_id) as session:
            failed = await session.scalar(
                update(Investigation)
                .where(Investigation.id == investigation_id, Investigation.status.in_(ACTIVE))
                .values(status="failed", error_code=code, completed_at=func.now())
                .returning(Investigation.id)
            )
            if failed is not None:
                await emit(
                    session,
                    investigation_id,
                    job.business_id,
                    "investigation.failed",
                    {"error_code": code, "job_id": str(job.id)},
                    f"investigation.failed:{job.id}",
                )


async def start(
    session: AsyncSession,
    *,
    business_id: UUID,
    dataset_id: UUID,
    user_id: UUID,
    llm_available: bool,
    starts_per_hour: int,
) -> tuple[Investigation, bool]:
    """Create and enqueue an investigation; an active one for the dataset is returned instead."""
    # Serialize starts within a tenant: both deduplication and the hourly budget are
    # check-then-insert decisions, including starts for different datasets.
    await lock_starts(session, business_id)
    dataset = await session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.business_id == business_id)
    )
    if dataset is None:
        raise ApiError(404, "not_found", "Resource not found.")
    if dataset.status != "ready":
        raise ApiError(409, "dataset_not_ready", "Only ready datasets can be investigated.")
    revision = await session.scalar(
        select(AnalysisRevision).where(
            AnalysisRevision.dataset_id == dataset_id,
            AnalysisRevision.config_version == CURRENT_CONFIG_VERSION,
            AnalysisRevision.code_version == CODE_VERSION,
        )
    )
    if revision is None or revision.status not in ("completed", "insufficient_data"):
        raise ApiError(
            409, "analysis_not_ready", "The dataset's analysis must finish before investigating."
        )
    active = await session.scalar(
        select(Investigation)
        .where(Investigation.dataset_id == dataset_id, Investigation.status.in_(UNFINISHED))
        .order_by(Investigation.created_at.desc())
        .limit(1)
    )
    if active is not None:
        return active, False
    recent = await session.scalar(
        select(func.count()).where(
            Investigation.business_id == business_id,
            Investigation.created_at > datetime.now(UTC) - timedelta(hours=1),
        )
    )
    if int(recent or 0) >= starts_per_hour:
        raise ApiError(
            429,
            "rate_limited",
            "Too many investigations were started in the last hour. Try again later.",
            headers={"Retry-After": "600"},
        )
    investigation_id = uuid.uuid7()
    llm_mode = "llm" if llm_available else "deterministic"
    await session.execute(
        insert(Investigation).values(
            id=investigation_id,
            business_id=business_id,
            dataset_id=dataset_id,
            revision_id=revision.id,
            status="queued",
            llm_mode=llm_mode,
            llm_disabled_reason=None if llm_available else "not_configured",
            graph_version=GRAPH_VERSION,
            catalog_version=CATALOG_VERSION,
            started_by=user_id,
        )
    )
    await emit(
        session,
        investigation_id,
        business_id,
        "investigation.created",
        {"dataset_id": str(dataset_id), "revision_id": str(revision.id), "llm_mode": llm_mode},
        "investigation.created",
        actor_kind="human",
    )
    await queue.enqueue(
        session,
        job_type="run_investigation",
        business_id=business_id,
        payload={"investigation_id": str(investigation_id)},
        dedupe_key=f"run_investigation:{investigation_id}",
    )
    created = await session.get_one(Investigation, investigation_id)
    return created, True


async def retry(session: AsyncSession, investigation: Investigation) -> None:
    """Re-queue a failed investigation; the graph resumes from its last checkpoint."""
    await session.refresh(investigation, with_for_update=True)
    if investigation.status != "failed":
        raise ApiError(409, "not_retryable", "Only failed investigations can be retried.")
    await session.execute(
        update(Investigation)
        .where(Investigation.id == investigation.id)
        .values(status="queued", error_code=None, completed_at=None)
    )
    await emit(
        session,
        investigation.id,
        investigation.business_id,
        "run.retry_requested",
        {},
        f"run.retry_requested:{uuid.uuid7()}",
        actor_kind="human",
    )
    await queue.enqueue(
        session,
        job_type="run_investigation",
        business_id=investigation.business_id,
        payload={"investigation_id": str(investigation.id)},
        dedupe_key=f"run_investigation:{investigation.id}:{uuid.uuid7()}",
    )


async def answer(
    session: AsyncSession,
    investigation: Investigation,
    clarification_id: UUID,
    *,
    user_id: UUID,
    checkpoint_id: str,
    option_id: str | None,
    free_text: str | None,
) -> HumanInput:
    """Store an answer to the paused question and queue the resume (one transaction, AMD-15).

    The investigation row is locked first, so concurrent answers are serialized: exactly one
    succeeds and every other one sees the question already answered."""
    await session.refresh(investigation, with_for_update=True)
    clarification = await session.scalar(
        select(Clarification)
        .where(
            Clarification.id == clarification_id,
            Clarification.investigation_id == investigation.id,
            Clarification.business_id == investigation.business_id,
        )
        .with_for_update()
    )
    if clarification is None:
        raise ApiError(404, "not_found", "Resource not found.")
    if clarification.status in ("answered", "applied"):
        raise ApiError(409, "already_answered", "This question has already been answered.")
    if clarification.status != "awaiting" or investigation.status != "awaiting_human":
        raise ApiError(409, "invalid_state", "This investigation is not waiting for this answer.")
    if checkpoint_id != clarification.checkpoint_id:
        raise ApiError(
            409, "stale_checkpoint", "The investigation has moved on; reload the question."
        )
    text = free_text.strip() if free_text is not None else None
    if text is not None and CONTROL.search(text):
        raise ApiError(
            422,
            "validation_error",
            "Free text may not contain control characters.",
            errors=[
                {
                    "field": "free_text",
                    "code": "invalid_characters",
                    "message": "Remove control characters.",
                }
            ],
        )
    text = text or None
    if text is not None and len(text) > FREE_TEXT_MAX:  # pragma: no cover - the model limits it
        raise ApiError(422, "validation_error", "Free text is too long.")
    options = {o["id"]: o for o in clarification.question_spec["options"]}
    if option_id is not None and option_id not in options:
        raise ApiError(
            422,
            "validation_error",
            "Choose one of the offered options.",
            errors=[
                {
                    "field": "option_id",
                    "code": "unknown_option",
                    "message": "Not an offered option.",
                }
            ],
        )
    if option_id is None and text is None:
        raise ApiError(
            422,
            "validation_error",
            "Choose an option or write an answer.",
            errors=[{"field": "option_id", "code": "empty_answer", "message": "No answer given."}],
        )
    effect = options[option_id]["effect"] if option_id is not None else "unstructured"
    human_input = HumanInput(
        id=uuid.uuid7(),
        business_id=investigation.business_id,
        investigation_id=investigation.id,
        clarification_id=clarification.id,
        kind="context",
        criterion_key=clarification.criterion_key,
        option_id=option_id,
        effect=effect,
        free_text=text,
        checkpoint_id=checkpoint_id,
        answered_by=user_id,
    )
    session.add(human_input)
    await session.flush()
    await session.execute(
        update(Clarification)
        .where(Clarification.id == clarification.id)
        .values(status="answered", answered_at=func.now())
    )
    await session.execute(
        update(Investigation)
        .where(Investigation.id == investigation.id)
        .values(status="queued", phase="resuming")
    )
    await emit(
        session,
        investigation.id,
        investigation.business_id,
        "clarification.answered",
        {
            "clarification_id": str(clarification.id),
            "human_input_id": str(human_input.id),
            "criterion": clarification.criterion_key,
            "option_id": option_id,
            "effect": effect,
            "has_free_text": text is not None,
        },
        f"clarification.answered:{clarification.id}",
        actor_kind="human",
        actor_user_id=user_id,
    )
    await queue.enqueue(
        session,
        job_type="run_investigation",
        business_id=investigation.business_id,
        payload={
            "investigation_id": str(investigation.id),
            "clarification_id": str(clarification.id),
        },
        dedupe_key=f"run_investigation:{investigation.id}:resume:{clarification.id}",
    )
    await session.refresh(human_input)
    return human_input
