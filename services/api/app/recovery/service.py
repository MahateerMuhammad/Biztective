"""Recovery plans: creation, the durable decision checkpoint, decisions and revisions (AMD-17).

`run_recovery_plan` jobs drive the recovery graph with the PostgreSQL checkpointer
(`thread_id` = `recovery:<plan id>`, advisory lock on the plan). When the graph pauses, the
runtime records the paused checkpoint on the current version; a decision is accepted only for
that version, content hash and checkpoint, stored with its author, and applied by a new job.
"""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError, NodeCancelledError
from langgraph.types import Command, StateSnapshot
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.investigation import output
from app.investigation.service import checkpointer
from app.investigation.store import jsonable, transaction
from app.jobs import queue
from app.jobs.queue import LeasedJob
from app.jobs.worker import PermanentJobError
from app.llm.provider import LlmProvider
from app.models import (
    Investigation,
    InvestigationVersion,
    PlanDecision,
    RecoveryPlan,
    RecoveryPlanVersion,
)
from app.recovery import plan
from app.recovery.catalog import CATALOG_VERSION, strategies_for
from app.recovery.graph import GRAPH_VERSION, Nodes, build, economics_of, initial_state

ACTIVE = frozenset({"queued", "running"})
OPEN = ACTIVE | {"awaiting_decision", "approved"}
REASON_MAX = 1000
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
DecisionKind = Literal["approve", "reject", "request_changes"]


def _invalid_edits(errors: list[dict[str, str]]) -> ApiError:
    return ApiError(422, "validation_error", "The changes do not fit this plan.", errors=errors)


class RecoveryRuntime:
    def __init__(
        self,
        settings: Settings,
        provider: LlmProvider | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.sleep = sleep

    def config(self, plan_id: UUID) -> RunnableConfig:
        return RunnableConfig(
            configurable={"thread_id": f"recovery:{plan_id}"},
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
        plan_id = UUID(job.payload["plan_id"])
        async with transaction(sessionmaker, job.business_id) as session:
            p = await session.get(RecoveryPlan, plan_id)
            if p is None or p.status not in ACTIVE:
                return
        if p.graph_version != GRAPH_VERSION:
            raise PermanentJobError("graph_version_unsupported")
        graph = build(Nodes(sessionmaker, self.settings, self.provider, self.sleep))
        config = self.config(plan_id)
        async with checkpointer(self.settings, plan_id) as saver:
            compiled = graph.compile(checkpointer=saver)
            snapshot = await compiled.aget_state(config)
            command: Command[Any] | None = None
            if snapshot.interrupts:
                command = await self._resume_command(
                    sessionmaker, job.business_id, plan_id, snapshot
                )
                if command is None:  # no decision yet: record the pause again
                    await self._pause(sessionmaker, job.business_id, plan_id, snapshot)
                    return
            async with transaction(sessionmaker, job.business_id) as session:
                await session.execute(
                    update(RecoveryPlan)
                    .where(RecoveryPlan.id == plan_id, RecoveryPlan.status.in_(ACTIVE))
                    .values(status="running", updated_at=func.now())
                )
            # A finished thread (an approved plan that was revised) starts a new pass.
            value: Any = (
                command if command is not None else None if snapshot.next else initial_state(p)
            )
            try:
                await compiled.ainvoke(value, config, durability="sync")
            except GraphRecursionError as exc:
                raise PermanentJobError("workflow_limit") from exc
            except NodeCancelledError as exc:
                raise asyncio.CancelledError from exc
            after = await compiled.aget_state(config)
            if after.interrupts:
                await self._pause(sessionmaker, job.business_id, plan_id, after)

    async def _resume_command(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        plan_id: UUID,
        snapshot: StateSnapshot,
    ) -> Command[Any] | None:
        """Resume with the pending decision for exactly this version and checkpoint."""
        version = int(snapshot.interrupts[0].value["version"])
        checkpoint_id = str(snapshot.config["configurable"]["checkpoint_id"])
        async with transaction(sessionmaker, business_id) as session:
            decision = await session.scalar(
                select(PlanDecision).where(
                    PlanDecision.plan_id == plan_id,
                    PlanDecision.version == version,
                    PlanDecision.decision != "revise",
                )
            )
        if decision is None:
            return None
        if decision.checkpoint_id != checkpoint_id:
            raise PermanentJobError("stale_checkpoint")
        return Command(resume={"decision_id": str(decision.id)})

    async def _pause(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        business_id: UUID,
        plan_id: UUID,
        snapshot: StateSnapshot,
    ) -> None:
        version = int(snapshot.interrupts[0].value["version"])
        checkpoint_id = str(snapshot.config["configurable"]["checkpoint_id"])
        async with transaction(sessionmaker, business_id) as session:
            await session.execute(
                update(RecoveryPlanVersion)
                .where(
                    RecoveryPlanVersion.plan_id == plan_id,
                    RecoveryPlanVersion.version == version,
                    RecoveryPlanVersion.checkpoint_id.is_(None),
                )
                .values(checkpoint_id=checkpoint_id)
            )
            await session.execute(
                update(RecoveryPlan)
                .where(RecoveryPlan.id == plan_id, RecoveryPlan.status.in_(ACTIVE))
                .values(status="awaiting_decision", updated_at=func.now())
            )

    async def mark_failed(
        self, sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob, code: str
    ) -> None:
        if job.business_id is None:
            return
        async with transaction(sessionmaker, job.business_id) as session:
            await session.execute(
                update(RecoveryPlan)
                .where(
                    RecoveryPlan.id == UUID(job.payload["plan_id"]),
                    RecoveryPlan.status.in_(ACTIVE),
                )
                .values(status="failed", error_code=code, updated_at=func.now())
            )


def _enqueue(session: AsyncSession, p: RecoveryPlan, cause: str) -> Awaitable[Any]:
    return queue.enqueue(
        session,
        job_type="run_recovery_plan",
        business_id=p.business_id,
        payload={"plan_id": str(p.id)},
        dedupe_key=f"run_recovery_plan:{p.id}:{cause}",
    )


async def create(
    session: AsyncSession,
    investigation: Investigation,
    *,
    user_id: UUID,
    constraints: plan.Constraints,
) -> RecoveryPlan:
    """A plan for a completed investigation with at least one actionable finding."""
    await session.refresh(investigation, with_for_update=True)
    if investigation.status != "completed":
        raise ApiError(
            409,
            "investigation_not_completed",
            "Only a completed investigation can be turned into a recovery plan.",
        )
    existing = await session.scalar(
        select(RecoveryPlan).where(
            RecoveryPlan.investigation_id == investigation.id, RecoveryPlan.status.in_(OPEN)
        )
    )
    if existing is not None:
        raise ApiError(
            409,
            "plan_exists",
            "This investigation already has a recovery plan.",
            errors=[{"field": "plan_id", "code": "plan_exists", "message": str(existing.id)}],
        )
    loaded = await output.load(session, investigation)
    actionable = [
        v
        for i in loaded.investigated
        for v in loaded.hypotheses.get(i.id, [])
        if v.status == "supported"
        and not v.explained
        and strategies_for(v.row.template, i.dimension)
    ]
    if not actionable:
        raise ApiError(
            409,
            "no_actionable_findings",
            "No validated finding has a recovery strategy; there is nothing to plan.",
        )
    version = await session.scalar(
        select(func.max(InvestigationVersion.version)).where(
            InvestigationVersion.investigation_id == investigation.id
        )
    )
    p = RecoveryPlan(
        id=uuid.uuid7(),
        business_id=investigation.business_id,
        investigation_id=investigation.id,
        investigation_version=int(version or 1),
        status="queued",
        current_version=0,
        initial_constraints=jsonable(constraints.model_dump(mode="json")),
        graph_version=GRAPH_VERSION,
        catalog_version=CATALOG_VERSION,
        created_by=user_id,
    )
    session.add(p)
    await session.flush()
    await _enqueue(session, p, "draft")
    await session.refresh(p)
    return p


async def _locked(session: AsyncSession, p: RecoveryPlan) -> RecoveryPlanVersion:
    await session.refresh(p, with_for_update=True)
    current = await session.scalar(
        select(RecoveryPlanVersion).where(
            RecoveryPlanVersion.plan_id == p.id, RecoveryPlanVersion.version == p.current_version
        )
    )
    if current is None:
        raise ApiError(409, "invalid_state", "The plan is still being prepared.")
    return current


def _reason(reason: str | None) -> str | None:
    text = reason.strip() if reason is not None else None
    if text and CONTROL.search(text):
        raise ApiError(
            422,
            "validation_error",
            "The reason may not contain control characters.",
            errors=[{"field": "reason", "code": "invalid_characters", "message": "Remove them."}],
        )
    return text or None


def _check_binding(
    current: RecoveryPlanVersion, version: int, content_hash: str, checkpoint_id: str | None
) -> None:
    if version != current.version:
        raise ApiError(409, "stale_plan_version", "The plan has a newer version; reload it.")
    if content_hash != current.content_hash:
        raise ApiError(
            409, "content_hash_mismatch", "The plan content does not match this version."
        )
    if checkpoint_id is not None and checkpoint_id != current.checkpoint_id:
        raise ApiError(409, "stale_checkpoint", "The plan has moved on; reload it.")


async def decide(
    session: AsyncSession,
    p: RecoveryPlan,
    *,
    kind: DecisionKind,
    user_id: UUID,
    role: str,
    version: int,
    content_hash: str,
    checkpoint_id: str,
    reason: str | None,
    edits: plan.PlanEdits | None,
) -> PlanDecision:
    """Record one decision on the paused version and queue the resume (one transaction).
    The plan row is locked first, so concurrent decisions are serialized."""
    current = await _locked(session, p)
    if p.status in ("approved", "rejected"):
        raise ApiError(409, "already_decided", "This plan has already been decided.")
    if current.checkpoint_id is None:
        raise ApiError(409, "invalid_state", "The plan is not waiting for a decision.")
    _check_binding(current, version, content_hash, checkpoint_id)
    taken = await session.scalar(
        select(PlanDecision.id).where(
            PlanDecision.plan_id == p.id,
            PlanDecision.version == current.version,
            PlanDecision.decision != "revise",
        )
    )
    if taken is not None:  # a duplicate, including one still being applied
        raise ApiError(409, "already_decided", "This version has already been decided.")
    if p.status != "awaiting_decision":
        raise ApiError(409, "invalid_state", "The plan is not waiting for a decision.")
    if kind == "approve" and not current.valid:
        raise ApiError(
            409, "plan_invalid", "This version breaks a constraint and cannot be approved."
        )
    stored_edits: dict[str, Any] | None = None
    if kind == "request_changes":
        if edits is None:  # pragma: no cover - the request model requires edits
            raise _invalid_edits([{"field": "edits", "code": "required", "message": "required"}])
        stored_edits = await _checked_edits(session, current, edits)
    decision = PlanDecision(
        id=uuid.uuid7(),
        business_id=p.business_id,
        plan_id=p.id,
        version=current.version,
        content_hash=current.content_hash,
        decision=kind,
        decided_by=user_id,
        decided_role=role,
        reason=_reason(reason),
        edits=stored_edits,
        checkpoint_id=current.checkpoint_id,
    )
    session.add(decision)
    await session.flush()
    await session.execute(
        update(RecoveryPlan)
        .where(RecoveryPlan.id == p.id)
        .values(status="queued", updated_at=func.now())
    )
    await _enqueue(session, p, f"decision:{decision.id}")
    await session.refresh(decision)
    return decision


async def _checked_edits(
    session: AsyncSession, current: RecoveryPlanVersion, edits: plan.PlanEdits
) -> dict[str, Any]:
    """The edits as stored, after checking they fit this version and change something."""
    evidence = await economics_of(session, current.content)
    try:
        revised = plan.revise(current.content, edits, evidence)
    except plan.EditError as exc:
        raise _invalid_edits(exc.errors) from exc
    if plan.content_hash(revised) == current.content_hash:
        raise _invalid_edits(
            [{"field": "edits", "code": "no_change", "message": "The edits change nothing."}]
        )
    stored: dict[str, Any] = jsonable(edits.model_dump(mode="json", exclude_unset=True))
    return stored


async def revise_approved(
    session: AsyncSession,
    p: RecoveryPlan,
    *,
    user_id: UUID,
    role: str,
    version: int,
    content_hash: str,
    reason: str | None,
    edits: plan.PlanEdits,
) -> RecoveryPlanVersion:
    """Edit an approved plan: the approval stops covering the plan at once, and the new
    version waits for a new decision."""
    current = await _locked(session, p)
    if p.status != "approved":
        raise ApiError(
            409, "invalid_state", "Only an approved plan is revised; request changes instead."
        )
    _check_binding(current, version, content_hash, None)
    stored = await _checked_edits(session, current, edits)
    evidence = await economics_of(session, current.content)
    content = plan.revise(current.content, edits, evidence)
    validation = plan.validate(content, evidence)
    decision = PlanDecision(
        id=uuid.uuid7(),
        business_id=p.business_id,
        plan_id=p.id,
        version=current.version,
        content_hash=current.content_hash,
        decision="revise",
        decided_by=user_id,
        decided_role=role,
        reason=_reason(reason),
        edits=stored,
        applied_at=func.now(),
        outcome="applied",
    )
    session.add(decision)
    await session.flush()
    new_version = current.version + 1
    await session.execute(
        insert(RecoveryPlanVersion).values(
            id=uuid.uuid7(),
            business_id=p.business_id,
            plan_id=p.id,
            version=new_version,
            content=jsonable(content),
            content_hash=plan.content_hash(content),
            valid=validation["valid"],
            validation=jsonable(validation),
            caused_by_decision_id=decision.id,
            created_by=user_id,
        )
    )
    await session.execute(
        update(RecoveryPlan)
        .where(RecoveryPlan.id == p.id)
        .values(
            status="queued",
            current_version=new_version,
            approved_version=None,
            updated_at=func.now(),
        )
    )
    await _enqueue(session, p, f"revise:{decision.id}")
    created = await session.scalar(
        select(RecoveryPlanVersion).where(
            RecoveryPlanVersion.plan_id == p.id, RecoveryPlanVersion.version == new_version
        )
    )
    if created is None:  # pragma: no cover - inserted above
        raise RuntimeError("plan version vanished")
    return created
