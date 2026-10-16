"""Recovery-plan workflow `recovery-graph-1.0.0` (AMD-17).

`draft_plan → await_decision [interrupt] → apply_decision → (await_decision | finalize)`. The
graph is resumed only with a decision ID; the decision itself is read from the database and
re-checked (plan, version, content hash, checkpoint and, for approvals, that the person is
still an owner) before it has any effect. Nothing here calls anything outside the database.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.db import set_rls_context
from app.investigation import output
from app.investigation.critic import Citations
from app.investigation.facts import numeric
from app.investigation.store import jsonable, tool_scope, transaction
from app.investigation.tools import ToolContext
from app.llm.gateway import Accepted, CallRecord, Gateway
from app.llm.provider import LlmProvider
from app.models import (
    EvidenceItem,
    Investigation,
    LlmCall,
    Membership,
    PlanDecision,
    RecoveryPlan,
    RecoveryPlanVersion,
)
from app.recovery import llm, plan
from app.recovery.catalog import strategies_for
from app.recovery.economics import economics_evidence

GRAPH_VERSION = "recovery-graph-1.0.0"


class ResumeSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID


class State(TypedDict):
    plan_id: str
    business_id: str
    version: int
    decision_id: str | None
    outcome: str | None


def initial_state(p: RecoveryPlan) -> State:
    return {
        "plan_id": str(p.id),
        "business_id": str(p.business_id),
        "version": p.current_version,
        "decision_id": None,
        "outcome": None,
    }


class PlanRecorder:
    """`llm_calls` for a plan, under its investigation (shared budget). A finished
    investigation is immutable, so no event is appended to its log."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        investigation_id: UUID,
        business_id: UUID,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.investigation_id = investigation_id
        self.business_id = business_id

    async def usage(self) -> tuple[int, int]:
        async with transaction(self.sessionmaker, self.business_id) as session:
            row = (
                await session.execute(
                    select(
                        func.count(),
                        func.coalesce(func.sum(LlmCall.tokens_in + LlmCall.tokens_out), 0),
                    ).where(LlmCall.investigation_id == self.investigation_id)
                )
            ).one()
        return int(row[0]), int(row[1])

    async def record(self, call: CallRecord) -> UUID:
        call_id = uuid.uuid7()
        async with transaction(self.sessionmaker, self.business_id) as session:
            await session.execute(
                insert(LlmCall).values(
                    id=call_id,
                    business_id=self.business_id,
                    investigation_id=self.investigation_id,
                    task=call.task,
                    attempt=call.attempt,
                    provider=call.provider,
                    model=call.model,
                    prompt_version=call.prompt_version,
                    input_hash=call.input_hash,
                    input=jsonable(call.input),
                    output=jsonable(call.output) if call.output is not None else None,
                    stop_reason=call.stop_reason,
                    tokens_in=call.tokens_in,
                    tokens_out=call.tokens_out,
                    latency_ms=call.latency_ms,
                    validation=call.validation,
                    error_code=call.error_code,
                )
            )
        return call_id


async def economics_of(session: AsyncSession, content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The stored economics evidence a plan version was computed from."""
    ids = {UUID(plan.economics_id(s)) for s in content["strategies"]}
    rows = await session.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(ids)))
    return {str(r.id): r.result for r in rows}


async def candidates(session: AsyncSession, loaded: output.Loaded) -> list[plan.Candidate]:
    """Supported mechanisms not explained by business context, in investigated incidents,
    that the catalog has a strategy for, with each incident's slice economics."""
    out: list[plan.Candidate] = []
    for incident in loaded.investigated:
        views = [
            v
            for v in loaded.hypotheses.get(incident.id, [])
            if v.status == "supported"
            and not v.explained
            and strategies_for(v.row.template, incident.dimension)
        ]
        if not views:
            continue
        detection = next(
            e for e in loaded.evidence.get(incident.id, []) if e.kind == "incident_detection"
        )
        impact = numeric(detection.facts.get("impact"))
        if impact is None:  # pragma: no cover - detection evidence always has an impact
            continue
        ctx = ToolContext(session, await tool_scope(session, incident.id))
        economics_id, economics = await economics_evidence(ctx)
        for v in views:
            out.append(
                plan.Candidate(
                    incident_id=str(incident.id),
                    incident_key=incident.key,
                    incident_rank=incident.rank,
                    dimension=incident.dimension,
                    hypothesis_id=str(v.row.id),
                    template=v.row.template,
                    status=v.status,
                    confidence=str(v.confidence),
                    impact=impact,
                    detection_id=str(detection.id),
                    economics_id=economics_id,
                    economics=economics,
                )
            )
    return out


class Nodes:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        provider: LlmProvider | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.gateway = Gateway(provider, settings, sleep=sleep)

    def _tx(self, state: State) -> Any:
        return transaction(self.sessionmaker, UUID(state["business_id"]))

    async def draft_plan(self, state: State) -> dict[str, Any]:
        """Version 1 (once): candidates, estimates, optional model rationale, validation."""
        plan_id, bid = UUID(state["plan_id"]), UUID(state["business_id"])
        async with self._tx(state) as session:
            p = await session.get_one(RecoveryPlan, plan_id)
            if p.current_version >= 1:
                return {"version": p.current_version, "decision_id": None, "outcome": None}
            investigation = await session.get_one(Investigation, p.investigation_id)
            loaded = await output.load(session, investigation)
            found = await candidates(session, loaded)
            citations = loaded.citations()
            constraints = plan.Constraints.model_validate(p.initial_constraints)
        content = plan.draft(
            investigation_id=str(investigation.id),
            investigation_version=p.investigation_version,
            currency=loaded.currency,
            constraints=constraints,
            candidates=found,
        )
        evidence = {c.economics_id: c.economics for c in found}
        if investigation.llm_mode == "llm" and content["strategies"]:
            await self._explain(content, citations, evidence, investigation.id, bid)
        validation = plan.validate(content, evidence)
        digest = plan.content_hash(content)
        async with self._tx(state) as session:
            await session.execute(
                pg_insert(RecoveryPlanVersion)
                .values(
                    id=uuid.uuid7(),
                    business_id=bid,
                    plan_id=plan_id,
                    version=1,
                    content=jsonable(content),
                    content_hash=digest,
                    valid=validation["valid"],
                    validation=jsonable(validation),
                )
                .on_conflict_do_nothing(index_elements=["plan_id", "version"])
            )
            await session.execute(
                update(RecoveryPlan)
                .where(RecoveryPlan.id == plan_id)
                .values(current_version=1, updated_at=func.now())
            )
        return {"version": 1, "decision_id": None, "outcome": None}

    async def _explain(
        self,
        content: dict[str, Any],
        citations: Citations,
        evidence: dict[str, dict[str, Any]],
        investigation_id: UUID,
        business_id: UUID,
    ) -> None:
        """Model-written rationale, accepted only under the placeholder contract. Numbers,
        selection and ranking were computed before and do not depend on it."""
        for economics_id, stored in evidence.items():
            citations.add_evidence(economics_id, stored["facts"])
        envelope, aliases = llm.envelope(content["strategies"], citations.evidence, citations)
        scoped = {s["id"]: citations.restricted_to(s["evidence"]) for s in content["strategies"]}
        deadline = datetime.now(UTC) + timedelta(seconds=self.settings.llm_deadline_seconds)
        result = await self.gateway.generate(
            llm.EXPLAIN,
            envelope,
            lambda out: llm.validate(out, aliases, scoped),
            PlanRecorder(self.sessionmaker, investigation_id, business_id),
            deadline,
        )
        if not isinstance(result, Accepted):
            return
        by_alias = {e.strategy: e for e in result.value.explanations}
        for alias, instance in aliases.items():
            explanation = by_alias.get(alias)
            if explanation is None:
                continue
            entry = next(s for s in content["strategies"] if s["id"] == instance)
            entry["rationale"] = {
                "source": "llm",
                "llm_call_id": str(result.llm_call_id),
                "items": [r.model_dump() for r in explanation.rationale],
            }

    async def await_decision(self, state: State) -> dict[str, Any]:
        """Pause for a person. Only reads before `interrupt()`: the node re-runs on resume."""
        async with self._tx(state) as session:
            version = await session.scalar(
                select(RecoveryPlanVersion).where(
                    RecoveryPlanVersion.plan_id == UUID(state["plan_id"]),
                    RecoveryPlanVersion.version == state["version"],
                )
            )
        if version is None:  # pragma: no cover - versions are written before this node runs
            raise ValueError("plan version missing")
        signal = interrupt(
            {
                "plan_id": state["plan_id"],
                "version": state["version"],
                "content_hash": version.content_hash,
            },
            response_schema=ResumeSignal,
        )
        return {"decision_id": str(ResumeSignal.model_validate(signal).decision_id)}

    async def apply_decision(self, state: State) -> dict[str, Any]:
        plan_id, bid = UUID(state["plan_id"]), UUID(state["business_id"])
        decision_id = UUID(str(state["decision_id"]))
        async with self._tx(state) as session:
            p = await session.get_one(RecoveryPlan, plan_id, with_for_update=True)
            decision = await session.get(PlanDecision, decision_id, with_for_update=True)
            if decision is None or decision.plan_id != plan_id or decision.decision == "revise":
                raise ValueError("resume value does not name a decision of this plan")
            if decision.applied_at is not None:  # re-executed after its commit
                return self._route(decision.decision, decision.outcome, p.current_version)
            current = await session.scalar(
                select(RecoveryPlanVersion).where(
                    RecoveryPlanVersion.plan_id == plan_id,
                    RecoveryPlanVersion.version == p.current_version,
                )
            )
            if current is None:  # pragma: no cover - the plan's current version always exists
                raise ValueError("plan version missing")
            refusal = await self._refusal(session, decision, current, state, bid)
            if refusal is not None:
                await self._mark(session, decision, "refused", refusal)
                return await self._new_version(
                    session, p, current, decision, current.content, bid, "refused"
                )
            await self._mark(session, decision, "applied", None)
            if decision.decision == "approve":
                await session.execute(
                    update(RecoveryPlan)
                    .where(RecoveryPlan.id == plan_id)
                    .values(
                        status="approved", approved_version=current.version, updated_at=func.now()
                    )
                )
                return {"outcome": "approved"}
            if decision.decision == "reject":
                await session.execute(
                    update(RecoveryPlan)
                    .where(RecoveryPlan.id == plan_id)
                    .values(status="rejected", updated_at=func.now())
                )
                return {"outcome": "rejected"}
            evidence = await economics_of(session, current.content)
            edits = plan.PlanEdits.model_validate(decision.edits)
            revised = plan.revise(current.content, edits, evidence)  # checked when submitted
            return await self._new_version(session, p, current, decision, revised, bid, "changed")

    @staticmethod
    def _route(decision: str, outcome: str | None, version: int) -> dict[str, Any]:
        if outcome == "applied" and decision == "approve":
            return {"outcome": "approved"}
        if outcome == "applied" and decision == "reject":
            return {"outcome": "rejected"}
        return {"outcome": "changed", "version": version, "decision_id": None}

    async def _refusal(
        self,
        session: AsyncSession,
        decision: PlanDecision,
        current: RecoveryPlanVersion,
        state: State,
        bid: UUID,
    ) -> str | None:
        """Why a stored decision must not take effect now (None: it may)."""
        if decision.version != current.version or decision.version != state["version"]:
            return "stale_plan_version"
        if decision.content_hash != current.content_hash:
            return "content_hash_mismatch"
        if decision.checkpoint_id is None or decision.checkpoint_id != current.checkpoint_id:
            return "stale_checkpoint"
        if decision.decision == "approve":
            if not current.valid:
                return "plan_invalid"
            # Re-checked now, not at submission: a demoted owner's approval has no effect.
            await set_rls_context(session, user_id=decision.decided_by, business_id=bid)
            role = await session.scalar(
                select(Membership.role).where(
                    Membership.business_id == bid, Membership.user_id == decision.decided_by
                )
            )
            if role != "owner":
                return "approver_not_owner"
        return None

    @staticmethod
    async def _mark(
        session: AsyncSession, decision: PlanDecision, outcome: str, refusal: str | None
    ) -> None:
        await session.execute(
            update(PlanDecision)
            .where(PlanDecision.id == decision.id)
            .values(applied_at=func.now(), outcome=outcome, refusal=refusal)
        )

    async def _new_version(
        self,
        session: AsyncSession,
        p: RecoveryPlan,
        current: RecoveryPlanVersion,
        decision: PlanDecision,
        content: dict[str, Any],
        bid: UUID,
        outcome: str,
    ) -> dict[str, Any]:
        """Version n+1 (recalculated and re-validated), presented for a new decision."""
        evidence = await economics_of(session, content)
        validation = plan.validate(content, evidence)
        version = current.version + 1
        await session.execute(
            pg_insert(RecoveryPlanVersion)
            .values(
                id=uuid.uuid7(),
                business_id=bid,
                plan_id=p.id,
                version=version,
                content=jsonable(content),
                content_hash=plan.content_hash(content),
                valid=validation["valid"],
                validation=jsonable(validation),
                caused_by_decision_id=decision.id,
                created_by=decision.decided_by if outcome == "changed" else None,
            )
            .on_conflict_do_nothing(index_elements=["plan_id", "version"])
        )
        await session.execute(
            update(RecoveryPlan)
            .where(RecoveryPlan.id == p.id)
            .values(current_version=version, updated_at=func.now())
        )
        return {"outcome": outcome, "version": version, "decision_id": None}

    async def finalize(self, state: State) -> dict[str, Any]:
        return {}


def build(nodes: Nodes) -> StateGraph[State]:
    graph: StateGraph[State] = StateGraph(State)
    graph.add_node("draft_plan", nodes.draft_plan)
    graph.add_node("await_decision", nodes.await_decision)
    graph.add_node("apply_decision", nodes.apply_decision)
    graph.add_node("finalize", nodes.finalize)

    def after_apply(state: State) -> str:
        return "finalize" if state["outcome"] in ("approved", "rejected") else "await_decision"

    graph.add_edge(START, "draft_plan")
    graph.add_edge("draft_plan", "await_decision")
    graph.add_edge("await_decision", "apply_decision")
    graph.add_conditional_edges("apply_decision", after_apply, ["await_decision", "finalize"])
    graph.add_edge("finalize", END)
    return graph
