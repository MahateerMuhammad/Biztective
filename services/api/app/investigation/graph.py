"""The investigation graph 2.0.0 (ARCHITECTURE §12 as amended by AMD-12 and AMD-15).

    START → initialize ─┬─(insufficient data | no incidents)─────────────────────► finalize
                        └─► select_incident ─(none left)─► draft_findings ─► assess_context
                              ▲      └─(next)─► generate_hypotheses → select_tools     │   │
                              │                   ▲                        │          │   │
                              │                   └──(LLM asked for more)──┤          │   │
                              ├──────── verify_evidence ◄── retrieve_evidence ◄────────┘   │
                              │                                                       (question)
                              └── apply_answer ◄── await_clarification [interrupt] ◄──────┘
                                         assess_context ─(none, or caps reached)─► finalize → END

Every incident is investigated before the first question, so findings version 1 is ROOT's
complete interpretation before human input. One question is asked at a time; the graph pauses
durably at `await_clarification` and resumes there when the answer is stored. `apply_answer`
records the answer as human-context evidence, adds follow-up hypotheses, and sends the affected
incidents round again; their measurements are reused, never recomputed.

State holds IDs, counters and validated tool requests only; the database is the source of
truth. Each node commits its writes and events in one transaction, keyed so that a node
re-executed after a crash writes nothing twice. LLM calls never happen inside a transaction.

Any annotations below describe heterogeneous JSON payloads and LangGraph partial state
updates; strict parameter models validate tool requests again before dispatch.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.evidence import canonical_json, sha256_of
from app.core.config import Settings
from app.investigation import output, replay
from app.investigation.catalog import (
    CATALOG,
    CATALOG_VERSION,
    ORDER,
    Evidence,
    candidates,
    human_criterion,
)
from app.investigation.critic import Citations, check_tool_result, intact
from app.investigation.llm_contracts import (
    PRIORITIZE,
    SELECT_TOOLS,
    SYNTHESIZE,
    PrioritizeOutput,
    SelectToolsOutput,
    SynthesisOutput,
    validate_prioritize,
    validate_select_tools,
    validate_synthesis,
)
from app.investigation.store import (
    LinkedEvidence,
    Recorder,
    cached_evidence,
    emit,
    evidence_params,
    jsonable,
    link_evidence,
    linked_evidence,
    store_human_evidence,
    store_tool_evidence,
    tool_scope,
    transaction,
)
from app.investigation.tools import TOOLS, EvidenceLookupParams, ToolContext
from app.investigation.verification import Answer, evaluate
from app.llm.gateway import Accepted, Fallback, Gateway
from app.llm.provider import LlmProvider
from app.llm.schema import provider_schema
from app.models import (
    AnalysisRevision,
    Clarification,
    EvidenceItem,
    Finding,
    HumanInput,
    Hypothesis,
    HypothesisEvaluation,
    Incident,
    Investigation,
    InvestigationEvent,
    InvestigationEvidence,
    InvestigationVersion,
    Product,
)

GRAPH_VERSION = "investigation-graph-2.0.0"
DISABLING = frozenset({"provider_error", "budget", "deadline"})
MAX_CLARIFICATIONS_PER_INCIDENT = 3
MAX_CLARIFICATIONS = 6
AMBIGUITY_GAP = Decimal("0.15")  # CT-1: top two non-refuted confidences closer than this
AMBIGUITY_FLOOR = Decimal("0.3")


Trigger = Literal["CT-1", "CT-2", "CT-3", "CT-4", "CT-5"]
TRIGGER_WHY: dict[str, str] = {
    "CT-5": "The data supports this explanation, but it cannot show whether it was a deliberate "
    "business decision.",
    "CT-2": "No explanation is supported yet, and this one depends on context only the business "
    "has.",
    "CT-1": "Two explanations are about equally likely; your answer separates them.",
    "CT-3": "The data shows signs of a recording problem that only the business can confirm.",
    "CT-4": "This explanation can be confirmed or ruled out only with your context.",
}


class ResumeSignal(BaseModel):
    """The value a paused graph is resumed with. The answer itself is read from the database
    (human_inputs), so nothing a person typed travels through graph state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clarification_id: UUID
    human_input_id: UUID


class State(TypedDict):
    investigation_id: str
    business_id: str
    incident_queue: list[str]
    current_incident_id: str | None
    tool_round: int
    pending_tools: list[
        dict[str, Any]
    ]  # validated requests: tool, params, requested_by, hypothesis
    llm_tool_calls: int
    more_tools: bool
    llm_mode: str
    terminal_status: str | None
    cycle: int  # answers applied so far; findings version = cycle + 1
    clarification_id: str | None  # the question being asked
    answer_input_id: str | None  # the stored answer the graph was resumed with
    last_input_id: str | None  # the answer that caused the current cycle


def initial_state(investigation: Investigation) -> State:
    return State(
        investigation_id=str(investigation.id),
        business_id=str(investigation.business_id),
        incident_queue=[],
        current_incident_id=None,
        tool_round=0,
        pending_tools=[],
        llm_tool_calls=0,
        more_tools=False,
        llm_mode=investigation.llm_mode,
        terminal_status=None,
        cycle=0,
        clarification_id=None,
        answer_input_id=None,
        last_input_id=None,
    )


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

    # -------------------------------------------------------------------------- helpers

    @staticmethod
    def _ids(state: State) -> tuple[UUID, UUID]:
        return UUID(state["investigation_id"]), UUID(state["business_id"])

    def _tx(self, state: State, *, lock: bool = True) -> Any:
        iid, bid = self._ids(state)
        return transaction(self.sessionmaker, bid, iid if lock else None)

    async def _emit(
        self,
        session: AsyncSession,
        state: State,
        event_type: str,
        payload: dict[str, Any],
        dedupe: str,
        actor: str = "system",
        actor_user_id: UUID | None = None,
    ) -> None:
        iid, bid = self._ids(state)
        await emit(session, iid, bid, event_type, payload, dedupe, actor, actor_user_id)

    async def _phase(self, session: AsyncSession, state: State, phase: str) -> None:
        await session.execute(
            update(Investigation)
            .where(Investigation.id == UUID(state["investigation_id"]))
            .values(phase=phase)
        )

    async def _deadline(self, state: State) -> datetime | None:
        """The LLM deadline of the current active run segment (AMD-15): time spent waiting
        for a person never counts against it."""
        async with self._tx(state, lock=False) as session:
            started = await session.scalar(
                select(Investigation.run_started_at).where(
                    Investigation.id == UUID(state["investigation_id"])
                )
            )
        return started + timedelta(seconds=self.settings.llm_deadline_seconds) if started else None

    def _recorder(self, state: State) -> Recorder:
        iid, bid = self._ids(state)
        return Recorder(self.sessionmaker, iid, bid)

    async def _after_llm(
        self,
        session: AsyncSession,
        state: State,
        result: Accepted[Any] | Fallback | None,
        task: str,
        scope_key: str,
    ) -> dict[str, Any]:
        """Record a fallback and trip the circuit breaker for provider, budget or deadline."""
        if not isinstance(result, Fallback):
            return {}
        if result.reason != "disabled":
            await self._emit(
                session,
                state,
                "llm.fallback_used",
                {"task": task, "reason": result.reason, "scope": scope_key},
                f"fallback:{task}:{scope_key}",
            )
        if result.reason in DISABLING and state["llm_mode"] == "llm":
            await session.execute(
                update(Investigation)
                .where(Investigation.id == UUID(state["investigation_id"]))
                .values(llm_mode="deterministic", llm_disabled_reason=result.reason)
            )
            await self._emit(
                session, state, "llm.disabled", {"reason": result.reason}, "llm.disabled"
            )
            return {"llm_mode": "deterministic"}
        return {}

    async def _citations(
        self, session: AsyncSession, dataset_id: UUID, items: list[LinkedEvidence]
    ) -> Citations:
        citations = Citations()
        for item in items:
            citations.add_evidence(str(item.id), item.facts)
        products = [
            f["value"]
            for item in items
            for f in item.facts.values()
            if f.get("unit") == "entity" and f.get("kind") == "product"
        ]
        if products:
            for name in await session.scalars(
                select(Product.product_name).where(
                    Product.dataset_id == dataset_id, Product.product_id.in_(products)
                )
            ):
                citations.add_name(name)
        return citations

    # ---------------------------------------------------------------------------- nodes

    async def initialize(self, state: State) -> dict[str, Any]:
        iid = UUID(state["investigation_id"])
        async with self._tx(state) as session:
            investigation = await session.get_one(Investigation, iid)
            revision = await session.get_one(AnalysisRevision, investigation.revision_id)
            await session.execute(
                update(Investigation)
                .where(Investigation.id == iid)
                .values(
                    status="running",
                    phase="initializing",
                    started_at=func.coalesce(Investigation.started_at, func.now()),
                    run_started_at=func.coalesce(Investigation.run_started_at, func.now()),
                )
            )
            await self._emit(
                session,
                state,
                "run.started",
                {"revision_id": str(revision.id), "llm_mode": investigation.llm_mode},
                "run.started",
            )
            if revision.status == "insufficient_data":
                return {"terminal_status": "insufficient_data"}
            incidents = list(
                await session.scalars(
                    select(Incident)
                    .where(Incident.revision_id == revision.id, Incident.status == "investigated")
                    .order_by(Incident.rank)
                )
            )
            for incident in incidents:
                for evidence_id in await session.scalars(
                    select(EvidenceItem.id).where(
                        EvidenceItem.incident_id == incident.id,
                        EvidenceItem.params_hash.is_(None),
                    )
                ):
                    await link_evidence(
                        session, investigation, evidence_id, incident.id, "detection"
                    )
            await self._emit(
                session,
                state,
                "incidents.detected",
                {
                    "incidents": [
                        {
                            "incident_id": str(i.id),
                            "rank": i.rank,
                            "metric": i.metric,
                            "dimension": i.dimension,
                            "severity": i.severity,
                        }
                        for i in incidents
                    ]
                },
                "incidents.detected",
            )
        if not incidents:
            return {"terminal_status": "completed_no_incidents"}
        return {"incident_queue": [str(i.id) for i in incidents]}

    async def select_incident(self, state: State) -> dict[str, Any]:
        queue = state["incident_queue"]
        if not queue:
            return {"current_incident_id": None}
        incident_id = queue[0]
        async with self._tx(state) as session:
            await self._phase(session, state, "hypothesizing")
            await self._emit(
                session,
                state,
                "incident.selected",
                {"incident_id": incident_id, "cycle": state["cycle"]},
                f"incident.selected:{incident_id}:{state['cycle']}",
            )
        return {
            "current_incident_id": incident_id,
            "incident_queue": queue[1:],
            "tool_round": 0,
            "pending_tools": [],
            "llm_tool_calls": 0,
            "more_tools": False,
        }

    async def generate_hypotheses(self, state: State) -> dict[str, Any]:
        iid, bid = self._ids(state)
        incident_id = UUID(str(state["current_incident_id"]))
        async with self._tx(state, lock=False) as session:
            existing = await session.scalar(
                select(func.count()).where(
                    Hypothesis.investigation_id == iid, Hypothesis.incident_id == incident_id
                )
            )
            if existing:
                # The node may have committed its circuit breaker immediately before
                # the process died, before LangGraph checkpointed the returned mode.
                mode = await session.scalar(
                    select(Investigation.llm_mode).where(Investigation.id == iid)
                )
                return {"llm_mode": mode}
            incident = await session.get_one(Incident, incident_id)
            investigation = await session.get_one(Investigation, iid)
            items = (await linked_evidence(session, iid, [incident_id])).get(incident_id, [])
            citations = await self._citations(session, investigation.dataset_id, items)
        templates = candidates(incident.metric)
        names = {t.name for t in templates}

        result: Accepted[PrioritizeOutput] | Fallback | None = None
        if state["llm_mode"] == "llm":
            envelope = {
                "task": PRIORITIZE.name,
                "prompt_version": PRIORITIZE.prompt_version,
                "context": {
                    "incident": output.incident_view(incident),
                    "evidence": [output.evidence_view(e) for e in items],
                    "candidates": [
                        {
                            "template": t.name,
                            "claim": t.claim,
                            "criteria": [c.description for c in t.criteria],
                        }
                        for t in templates
                    ],
                },
                "untrusted_text": [],
                "allowed": {
                    "templates": sorted(names),
                    "evidence_ids": sorted(citations.evidence),
                    "entity_placeholders": sorted(citations.entity_tokens),
                },
            }
            result = await self.gateway.generate(
                PRIORITIZE,
                envelope,
                lambda out: validate_prioritize(out, set(names), citations),
                self._recorder(state),
                await self._deadline(state),
            )
        priorities: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        if isinstance(result, Accepted):
            for ranked in result.value.ranking:
                priorities[ranked.template] = (
                    ranked.priority,
                    [r.model_dump() for r in ranked.rationale],
                )
        source = "llm_prioritized" if isinstance(result, Accepted) else "catalog"
        rows = []
        for template in sorted(
            templates,
            key=lambda t: (priorities.get(t.name, (10 + ORDER[t.name], []))[0], ORDER[t.name]),
        ):
            priority, rationale = priorities.get(template.name, (10 + ORDER[template.name], []))
            rows.append(
                {
                    "id": uuid.uuid7(),
                    "business_id": bid,
                    "investigation_id": iid,
                    "incident_id": incident_id,
                    "template": template.name,
                    "catalog_version": CATALOG_VERSION,
                    "priority": priority,
                    "source": source,
                    "rationale": rationale,
                }
            )
        async with self._tx(state) as session:
            await session.execute(
                pg_insert(Hypothesis)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=["investigation_id", "incident_id", "template"]
                )
            )
            await self._emit(
                session,
                state,
                "hypotheses.generated",
                {
                    "incident_id": str(incident_id),
                    "source": source,
                    "hypotheses": [
                        {"hypothesis_id": str(r["id"]), "template": r["template"]} for r in rows
                    ],
                },
                f"hypotheses.generated:{incident_id}",
                "llm" if source == "llm_prioritized" else "system",
            )
            update_ = await self._after_llm(
                session, state, result, PRIORITIZE.name, str(incident_id)
            )
        return update_

    async def select_tools(self, state: State) -> dict[str, Any]:
        iid, _ = self._ids(state)
        incident_id = UUID(str(state["current_incident_id"]))
        round_ = state["tool_round"]
        selection_key = f"tools.selected:{incident_id}:{state['cycle']}:{round_ + 1}"
        async with self._tx(state, lock=False) as session:
            prior = await session.scalar(
                select(InvestigationEvent.payload).where(
                    InvestigationEvent.investigation_id == iid,
                    InvestigationEvent.dedupe_key == selection_key,
                )
            )
            if prior is not None:
                # Restore the exact committed decision after a crash between the
                # event transaction and checkpoint, without another provider call.
                return dict(prior["state"])
            scope = await tool_scope(session, incident_id)
            hypotheses = list(
                await session.scalars(
                    select(Hypothesis.template).where(
                        Hypothesis.investigation_id == iid, Hypothesis.incident_id == incident_id
                    )
                )
            )
            items = (await linked_evidence(session, iid, [incident_id])).get(incident_id, [])
            citations = await self._citations(session, scope.dataset_id, items)
            incident = await session.get_one(Incident, incident_id)
        collected = {item.params_hash for item in items if item.params_hash}

        requests: list[dict[str, Any]] = []
        keys: set[str] = set()

        def add(tool: str, params: Any, requested_by: str, hypothesis: str) -> bool:
            _, key = evidence_params(tool, params, scope)
            if key in keys or key in collected:
                return False
            keys.add(key)
            requests.append(
                {
                    "tool": tool,
                    "params": params.model_dump(mode="json"),
                    "requested_by": requested_by,
                    "hypothesis": hypothesis,
                }
            )
            return True

        if round_ == 0:  # verdicts never depend on the LLM: required calls always run
            for name in hypotheses:
                for requirement in CATALOG[name].requirements:
                    add(requirement.tool, requirement.params(scope.metric), "system", name)
        required = list(requests)

        remaining = self.settings.investigation_llm_tools_per_incident - state["llm_tool_calls"]
        result: Accepted[SelectToolsOutput] | Fallback | None = None
        accepted = dropped = 0
        more = False
        # Revisits after an answer run only the catalog-required tools of new hypotheses.
        if state["llm_mode"] == "llm" and remaining > 0 and state["cycle"] == 0:
            envelope = {
                "task": SELECT_TOOLS.name,
                "prompt_version": SELECT_TOOLS.prompt_version,
                "context": {
                    "incident": output.incident_view(incident),
                    "hypotheses": [{"template": h, "claim": CATALOG[h].claim} for h in hypotheses],
                    "evidence": [output.evidence_view(e) for e in items],
                    "scheduled": [{"tool": r["tool"], "arguments": r["params"]} for r in required],
                    "tools": [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "arguments": provider_schema(spec.params),
                        }
                        for spec in TOOLS.values()
                    ],
                    "round": round_ + 1,
                    "remaining_calls": remaining,
                },
                "untrusted_text": [],
                "allowed": {
                    "hypotheses": sorted(hypotheses),
                    "evidence_ids": sorted(citations.evidence),
                },
            }
            result = await self.gateway.generate(
                SELECT_TOOLS,
                envelope,
                lambda out: validate_select_tools(out, set(hypotheses), citations),
                self._recorder(state),
                await self._deadline(state),
            )
            if isinstance(result, Accepted):
                more = not result.value.done
                for call in result.value.calls:
                    if accepted >= remaining:
                        dropped += 1
                    elif add(call.tool, call.arguments, "llm", call.hypothesis):
                        accepted += 1
        async with self._tx(state) as session:
            update_ = await self._after_llm(
                session, state, result, SELECT_TOOLS.name, f"{incident_id}:{round_ + 1}"
            )
            selection = {
                "pending_tools": requests,
                "tool_round": round_ + 1,
                "llm_tool_calls": state["llm_tool_calls"] + accepted,
                "more_tools": more and accepted > 0,
                **update_,
            }
            await self._phase(session, state, "gathering_evidence")
            await self._emit(
                session,
                state,
                "tools.selected",
                {
                    "incident_id": str(incident_id),
                    "cycle": state["cycle"],
                    "round": round_ + 1,
                    "calls": [
                        {
                            "tool": r["tool"],
                            "arguments": r["params"],
                            "requested_by": r["requested_by"],
                            "hypothesis": r["hypothesis"],
                        }
                        for r in requests
                    ],
                    "dropped_over_limit": dropped,
                    "state": selection,
                },
                selection_key,
            )
            if dropped:
                await self._emit(
                    session,
                    state,
                    "tools.limit_reached",
                    {"incident_id": str(incident_id), "dropped": dropped},
                    f"tools.limit_reached:{incident_id}:{state['cycle']}:{round_ + 1}",
                )
        return selection

    async def retrieve_evidence(self, state: State) -> dict[str, Any]:
        iid, _ = self._ids(state)
        incident_id = UUID(str(state["current_incident_id"]))
        collected: list[dict[str, Any]] = []
        async with self._tx(state) as session:
            investigation = await session.get_one(Investigation, iid)
            scope = await tool_scope(session, incident_id)
            ctx = ToolContext(session, scope)
            linked = await session.scalar(
                select(func.count()).where(
                    InvestigationEvidence.investigation_id == iid,
                    InvestigationEvidence.incident_id == incident_id,
                )
            )
            count = int(linked or 0)
            for request in state["pending_tools"]:
                spec = TOOLS[request["tool"]]
                # Re-validate at the execution boundary (defence in depth).
                params = spec.params.model_validate_json(canonical_json(request["params"]))
                if isinstance(params, EvidenceLookupParams):
                    await self._lookup(session, state, incident_id, params, request)
                    continue
                if count >= self.settings.investigation_evidence_per_incident:
                    await self._emit(
                        session,
                        state,
                        "evidence.limit_reached",
                        {"incident_id": str(incident_id)},
                        f"evidence.limit_reached:{incident_id}:{state['cycle']}",
                    )
                    break
                stored, key = evidence_params(spec.name, params, scope)
                evidence_id = await cached_evidence(session, scope.revision_id, spec.name, key)
                cached = evidence_id is not None
                if evidence_id is None and spec.run is not None:
                    try:
                        async with session.begin_nested():
                            result = await spec.run(ctx, params)
                    except OperationalError, InterfaceError:
                        raise  # the connection is unusable: let the job retry and resume
                    except Exception as exc:  # noqa: BLE001 - a failing tool must not stop the investigation; it is recorded and its criteria become unknown
                        ctx = ToolContext(session, scope)  # temp tables rolled back with it
                        await self._emit(
                            session,
                            state,
                            "tool.failed",
                            {
                                "incident_id": str(incident_id),
                                "tool": spec.name,
                                "error": type(exc).__name__
                                if not isinstance(exc, DBAPIError)
                                else "database_error",
                            },
                            f"tool.failed:{incident_id}:{state['cycle']}:{key}",
                        )
                        continue
                    problems = check_tool_result(result)
                    if problems:
                        await self._emit(
                            session,
                            state,
                            "tool.rejected",
                            {
                                "incident_id": str(incident_id),
                                "tool": spec.name,
                                "problems": problems,
                            },
                            f"tool.rejected:{incident_id}:{state['cycle']}:{key}",
                        )
                        continue
                    evidence_id = await store_tool_evidence(
                        session, scope, spec.name, stored, key, spec.query_version, result
                    )
                if evidence_id is None:
                    continue
                await link_evidence(
                    session, investigation, evidence_id, incident_id, request["requested_by"]
                )
                count += 1
                collected.append(
                    {
                        "evidence_id": str(evidence_id),
                        "tool": spec.name,
                        "requested_by": request["requested_by"],
                        "cached": cached,
                    }
                )
            await self._emit(
                session,
                state,
                "evidence.collected",
                {
                    "incident_id": str(incident_id),
                    "cycle": state["cycle"],
                    "round": state["tool_round"],
                    "items": collected,
                },
                f"evidence.collected:{incident_id}:{state['cycle']}:{state['tool_round']}",
            )
        return {"pending_tools": []}

    async def _lookup(
        self,
        session: AsyncSession,
        state: State,
        incident_id: UUID,
        params: EvidenceLookupParams,
        request: dict[str, Any],
    ) -> None:
        """Evidence retrieval: only items already linked to this investigation and incident.
        Their facts are part of every later prompt, so nothing new is stored."""
        allowed = set(
            await session.scalars(
                select(InvestigationEvidence.evidence_id).where(
                    InvestigationEvidence.investigation_id == UUID(state["investigation_id"]),
                    InvestigationEvidence.incident_id == incident_id,
                    InvestigationEvidence.evidence_id.in_(params.evidence_ids),
                )
            )
        )
        await self._emit(
            session,
            state,
            "evidence.looked_up",
            {
                "incident_id": str(incident_id),
                "found": sorted(str(e) for e in allowed),
                "refused": len(set(params.evidence_ids) - allowed),
                "requested_by": request["requested_by"],
            },
            f"evidence.looked_up:{incident_id}:{state['cycle']}:{state['tool_round']}:"
            + ",".join(sorted(str(e) for e in params.evidence_ids)),
        )

    async def verify_evidence(self, state: State) -> dict[str, Any]:
        """Evaluate every hypothesis of the incident from its required evidence and the
        answers applied to it. A new evaluation is stored only when those inputs changed, so
        earlier evaluations remain as history and a re-executed node adds nothing."""
        iid, bid = self._ids(state)
        incident_id = UUID(str(state["current_incident_id"]))
        async with self._tx(state) as session:
            await self._phase(session, state, "verifying")
            scope = await tool_scope(session, incident_id)
            items = (await linked_evidence(session, iid, [incident_id], include_invalid=True)).get(
                incident_id, []
            )
            by_key: dict[str, Evidence] = {}
            hashes: dict[UUID, str] = {}
            answers: dict[str, dict[str, Answer]] = {}
            for item in items:
                if not intact(item.result, item.result_hash):
                    await self._emit(
                        session,
                        state,
                        "evidence.integrity_failed",
                        {"incident_id": str(incident_id), "evidence_id": str(item.id)},
                        f"evidence.integrity_failed:{item.id}",
                    )
                    continue
                hashes[item.id] = item.result_hash
                if item.kind == "human_context":
                    facts = item.facts
                    answered, key = str(facts["answer.criterion"]["value"]).split(".", 1)
                    answers.setdefault(answered, {})[key] = Answer(
                        facts["answer.effect"]["value"],
                        facts["answer.option"]["value"],
                        str(item.id),
                    )
                elif item.params_hash:
                    by_key[item.params_hash] = Evidence(item.id, item.facts)
            for hypothesis, latest in await output.latest_evaluations(session, iid, incident_id):
                template = CATALOG[hypothesis.template]
                evidence = {
                    r.key: by_key.get(evidence_params(r.tool, r.params(scope.metric), scope)[1])
                    for r in template.requirements
                }
                given = answers.get(hypothesis.template, {})
                inputs_hash = sha256_of(
                    {
                        "catalog": CATALOG_VERSION,
                        "template": hypothesis.template,
                        "evidence": sorted(
                            [key, str(e.id), hashes[e.id]]
                            for key, e in evidence.items()
                            if e is not None
                        ),
                        "answers": sorted(
                            [key, a.effect, a.option_id, a.evidence_id] for key, a in given.items()
                        ),
                    }
                )
                if latest is not None and latest.inputs_hash == inputs_hash:
                    continue
                result = evaluate(template, evidence, given)
                await session.execute(
                    pg_insert(HypothesisEvaluation)
                    .values(
                        id=uuid.uuid7(),
                        business_id=bid,
                        hypothesis_id=hypothesis.id,
                        status=result.status,
                        confidence=result.confidence,
                        data_confidence=result.data_confidence,
                        human_confidence=result.human_confidence,
                        criteria=jsonable([c.as_json() for c in result.criteria]),
                        inputs_hash=inputs_hash,
                        interpretation=result.interpretation,
                        conflicts=list(result.conflicts),
                    )
                    .on_conflict_do_nothing(index_elements=["hypothesis_id", "inputs_hash"])
                )
                await self._emit(
                    session,
                    state,
                    "hypothesis.evaluated",
                    {
                        "incident_id": str(incident_id),
                        "hypothesis_id": str(hypothesis.id),
                        "template": hypothesis.template,
                        "status": result.status,
                        "confidence": str(result.confidence),
                        "data_confidence": str(result.data_confidence)
                        if result.data_confidence is not None
                        else None,
                        "human_confidence": str(result.human_confidence)
                        if result.human_confidence is not None
                        else None,
                        "interpretation": result.interpretation,
                        "conflicts": list(result.conflicts),
                        "satisfied": [c.key for c in result.criteria if c.satisfied],
                        "unknown": [c.key for c in result.unknown_data],
                        "previous": None
                        if latest is None
                        else {
                            "status": latest.status,
                            "confidence": str(latest.confidence),
                            "interpretation": latest.interpretation,
                        },
                    },
                    f"hypothesis.evaluated:{hypothesis.id}:{inputs_hash}",
                )
        return {}

    async def draft_findings(self, state: State) -> dict[str, Any]:
        """Findings round = version: the narrative of the current interpretation plus an
        immutable, hashed snapshot of it for comparison (AMD-15)."""
        iid, bid = self._ids(state)
        version = state["cycle"] + 1
        async with self._tx(state, lock=False) as session:
            investigation = await session.get_one(Investigation, iid)
            if await session.scalar(
                select(Finding.id).where(Finding.investigation_id == iid, Finding.round == version)
            ):
                return {"llm_mode": investigation.llm_mode}
            loaded = await output.load(session, investigation)
        statuses = {
            str(v.row.id): "explained_by_context" if v.explained else v.status
            for views in loaded.hypotheses.values()
            for v in views
        }
        incident_of = {
            str(v.row.id): str(v.row.incident_id)
            for views in loaded.hypotheses.values()
            for v in views
        }
        incident_evidence = {
            str(incident_id): {str(e.id) for e in items}
            for incident_id, items in loaded.evidence.items()
        }
        for incident_id in incident_of.values():
            incident_evidence.setdefault(incident_id, set())
        citations = loaded.citations()

        result: Accepted[SynthesisOutput] | Fallback | None = None
        if state["llm_mode"] == "llm":
            envelope = {
                "task": SYNTHESIZE.name,
                "prompt_version": SYNTHESIZE.prompt_version,
                "context": output.synthesis_context(loaded),
                "untrusted_text": [],
                "allowed": {
                    "hypothesis_ids": sorted(statuses),
                    "evidence_ids": sorted(citations.evidence),
                    "entity_placeholders": sorted(citations.entity_tokens),
                },
            }
            result = await self.gateway.generate(
                SYNTHESIZE,
                envelope,
                lambda out: validate_synthesis(
                    out, statuses, incident_of, citations, incident_evidence
                ),
                self._recorder(state),
                await self._deadline(state),
            )
        if isinstance(result, Accepted):
            content = result.value.model_dump()
            source, call_id = "llm", result.llm_call_id
        else:
            content = output.fallback_synthesis(loaded)
            source, call_id = "deterministic", None
        rendered = output.render_findings(loaded, content)
        async with self._tx(state) as session:
            await self._phase(session, state, "synthesizing")
            findings = await session.scalar(
                pg_insert(Finding)
                .values(
                    id=uuid.uuid7(),
                    business_id=bid,
                    investigation_id=iid,
                    round=version,
                    content=jsonable(content),
                    rendered=jsonable(rendered),
                    source=source,
                    llm_call_id=call_id,
                )
                .on_conflict_do_nothing(index_elements=["investigation_id", "round"])
                .returning(Finding)
            )
            if findings is not None:
                cause = (
                    await session.get_one(HumanInput, UUID(state["last_input_id"]))
                    if state["last_input_id"]
                    else None
                )
                snap = jsonable(replay.snapshot(loaded, version, findings, cause))
                await session.execute(
                    pg_insert(InvestigationVersion).values(
                        id=uuid.uuid7(),
                        business_id=bid,
                        investigation_id=iid,
                        version=version,
                        findings_id=findings.id,
                        caused_by_input_id=cause.id if cause else None,
                        snapshot=snap,
                        snapshot_hash=sha256_of(snap),
                    )
                )
                await self._emit(
                    session,
                    state,
                    "findings.drafted",
                    {
                        "findings_id": str(findings.id),
                        "version": version,
                        "source": source,
                        "root_cause_hypothesis_ids": [
                            c["hypothesis_id"] for c in content["root_causes"]
                        ],
                    },
                    f"findings.drafted:{version}",
                    "llm" if source == "llm" else "system",
                )
                await self._emit(
                    session,
                    state,
                    "version.created",
                    {
                        "version": version,
                        "snapshot_hash": sha256_of(snap),
                        "caused_by_input_id": str(cause.id) if cause else None,
                    },
                    f"version.created:{version}",
                )
            update_ = await self._after_llm(session, state, result, SYNTHESIZE.name, f"v{version}")
        return update_

    async def assess_context(self, state: State) -> dict[str, Any]:
        """Pick the single most useful open question (ARCHITECTURE §7.2 + CT-5), within caps."""
        iid, bid = self._ids(state)
        async with self._tx(state) as session:
            pending = await session.scalar(
                select(Clarification.id)
                .where(
                    Clarification.investigation_id == iid,
                    Clarification.status.in_(("open", "awaiting", "answered")),
                )
                .order_by(Clarification.created_at, Clarification.id)
                .limit(1)
            )
            if pending is not None:  # restore after a crash before the checkpoint
                return {"clarification_id": str(pending)}
            investigation = await session.get_one(Investigation, iid)
            loaded = await output.load(session, investigation)
            incidents = {i.id: i for i in loaded.incidents}
            asked = loaded.clarifications
            per_incident: dict[UUID, int] = {}
            asked_for_slice = set()
            for c in asked:
                per_incident[c.incident_id] = per_incident.get(c.incident_id, 0) + 1
                asked_for_slice.add((_slice(incidents[c.incident_id]), c.criterion_key))
            not_asked = 0
            for incident in loaded.investigated:
                wanted = [
                    (view, key, trigger)
                    for view, key, trigger in _context_requests(
                        loaded.hypotheses.get(incident.id, [])
                    )
                    if (_slice(incident), f"{view.row.template}.{key}") not in asked_for_slice
                ]
                if not wanted:
                    continue
                if (
                    len(asked) >= MAX_CLARIFICATIONS
                    or per_incident.get(incident.id, 0) >= MAX_CLARIFICATIONS_PER_INCIDENT
                ):
                    not_asked += len(wanted)
                    continue
                view, key, trigger = wanted[0]
                criterion_key = f"{view.row.template}.{key}"
                spec = _question(view.row.template, key, trigger)
                clarification_id = await session.scalar(
                    pg_insert(Clarification)
                    .values(
                        id=uuid.uuid7(),
                        business_id=bid,
                        investigation_id=iid,
                        incident_id=incident.id,
                        incident_key=incident.key,
                        hypothesis_id=view.row.id,
                        criterion_key=criterion_key,
                        trigger=trigger,
                        question_spec=spec,
                        response_schema=response_schema(spec),
                    )
                    .on_conflict_do_nothing(
                        index_elements=["investigation_id", "incident_key", "criterion_key"]
                    )
                    .returning(Clarification.id)
                )
                if clarification_id is None:  # pragma: no cover - filtered by asked_for_slice
                    continue
                await self._emit(
                    session,
                    state,
                    "clarification.requested",
                    {
                        "clarification_id": str(clarification_id),
                        "incident_id": str(incident.id),
                        "trigger": trigger,
                        "criterion": criterion_key,
                    },
                    f"clarification.requested:{incident.key}:{criterion_key}",
                )
                return {"clarification_id": str(clarification_id)}
            if not_asked:
                await self._emit(
                    session,
                    state,
                    "clarification.cap_reached",
                    {"asked": len(asked), "not_asked": not_asked},
                    f"clarification.cap_reached:{len(asked)}",
                )
        return {"clarification_id": None}

    async def await_clarification(self, state: State) -> dict[str, Any]:
        """Pause until an answer is stored. Everything before `interrupt()` only reads,
        because LangGraph re-executes this node from the start when it resumes."""
        clarification_id = str(state["clarification_id"])
        async with self._tx(state, lock=False) as session:
            clarification = await session.get_one(Clarification, UUID(clarification_id))
        signal = interrupt(
            {
                "clarification_id": clarification_id,
                "incident_id": str(clarification.incident_id),
                "criterion": clarification.criterion_key,
            },
            response_schema=ResumeSignal,
        )
        if str(signal.clarification_id) != clarification_id:
            raise ValueError("resume value names another clarification")
        return {"answer_input_id": str(signal.human_input_id)}

    async def apply_answer(self, state: State) -> dict[str, Any]:
        """Record the answer as attributed human-context evidence on every investigated
        incident of the same slice, add the follow-up hypotheses its option names, and send
        those incidents round again. Measurements are untouched; only interpretation inputs
        change (AMD-15)."""
        iid, _ = self._ids(state)
        human_input_id = UUID(str(state["answer_input_id"]))
        applied_key = f"human.context_applied:{human_input_id}"
        async with self._tx(state) as session:
            prior = await session.scalar(
                select(InvestigationEvent.payload).where(
                    InvestigationEvent.investigation_id == iid,
                    InvestigationEvent.dedupe_key == applied_key,
                )
            )
            if prior is not None:  # restore after a crash before the checkpoint
                return dict(prior["state"])
            answer = await session.get_one(HumanInput, human_input_id)
            clarification = await session.get_one(Clarification, answer.clarification_id)
            if (
                str(clarification.id) != state["clarification_id"]
                or clarification.status != "answered"
            ):
                raise ValueError("the stored answer does not belong to the paused question")
            template, _ = clarification.criterion_key.split(".", 1)
            asked = await session.get_one(Incident, clarification.incident_id)
            spec = clarification.question_spec
            option = next((o for o in spec["options"] if o["id"] == answer.option_id), None)
            rows = (
                await session.execute(
                    select(Incident, Hypothesis.template)
                    .join(Hypothesis, Hypothesis.incident_id == Incident.id)
                    .where(
                        Hypothesis.investigation_id == iid,
                        Incident.dimension == asked.dimension,
                        Incident.dimension_value.is_not_distinct_from(asked.dimension_value),
                    )
                    .order_by(Incident.rank)
                )
            ).all()
            templates: dict[UUID, set[str]] = {}
            incidents: dict[UUID, Incident] = {}
            for incident, name in rows:
                incidents[incident.id] = incident
                templates.setdefault(incident.id, set()).add(name)
            affected = [i for i in incidents.values() if template in templates[i.id]]
            evidence_ids = []
            follow_ups: dict[str, list[str]] = {}
            for incident in affected:
                evidence_id = await store_human_evidence(
                    session,
                    incident,
                    answer.id,
                    {
                        "facts": {
                            "answer.criterion": {
                                "value": clarification.criterion_key,
                                "unit": "code",
                            },
                            "answer.option": {"value": answer.option_id, "unit": "code"},
                            "answer.effect": {"value": answer.effect, "unit": "code"},
                        },
                        "detail": {
                            "human_input_id": str(answer.id),
                            "clarification_id": str(clarification.id),
                            "asked_for_incident": asked.key,
                            "question": spec["question"],
                            "option_label": option["label"] if option else None,
                            "free_text": answer.free_text,
                            "answered_by": str(answer.answered_by),
                            "answered_at": answer.answered_at.isoformat(),
                        },
                    },
                )
                await link_evidence(
                    session, (iid, incident.business_id), evidence_id, incident.id, "human"
                )
                evidence_ids.append(str(evidence_id))
                new = [
                    name
                    for name in (option["follow_up"] if option else [])
                    if name not in templates[incident.id]
                ]
                if new:
                    await session.execute(
                        pg_insert(Hypothesis)
                        .values(
                            [
                                {
                                    "id": uuid.uuid7(),
                                    "business_id": incident.business_id,
                                    "investigation_id": iid,
                                    "incident_id": incident.id,
                                    "template": name,
                                    "catalog_version": CATALOG_VERSION,
                                    "priority": 50 + ORDER[name],
                                    "source": "human_context",
                                    "rationale": [],
                                }
                                for name in new
                            ]
                        )
                        .on_conflict_do_nothing(
                            index_elements=["investigation_id", "incident_id", "template"]
                        )
                    )
                    follow_ups[str(incident.id)] = new
            now = func.now()
            await session.execute(
                update(HumanInput).where(HumanInput.id == answer.id).values(applied_at=now)
            )
            await session.execute(
                update(Clarification)
                .where(Clarification.id == clarification.id)
                .values(status="applied", applied_at=now)
            )
            await self._emit(
                session,
                state,
                "human.context_added",
                {
                    "human_input_id": str(answer.id),
                    "clarification_id": str(clarification.id),
                    "criterion": clarification.criterion_key,
                    "option_id": answer.option_id,
                    "effect": answer.effect,
                    "incidents": [str(i.id) for i in affected],
                    "evidence_ids": evidence_ids,
                },
                f"human.context_added:{answer.id}",
                "human",
                answer.answered_by,
            )
            if follow_ups:
                await self._emit(
                    session,
                    state,
                    "hypotheses.added",
                    {
                        "source": "human_context",
                        "human_input_id": str(answer.id),
                        "added": follow_ups,
                    },
                    f"hypotheses.added:{answer.id}",
                )
            update_: dict[str, Any] = {
                "incident_queue": [str(i.id) for i in affected],
                "cycle": state["cycle"] + 1,
                "clarification_id": None,
                "answer_input_id": None,
                "last_input_id": str(answer.id),
            }
            await self._emit(
                session, state, "human.context_applied", {"state": update_}, applied_key
            )
        return update_

    async def finalize(self, state: State) -> dict[str, Any]:
        status = state["terminal_status"] or "completed"
        async with self._tx(state) as session:
            # The event first: a finished investigation row is immutable (trigger).
            await self._emit(session, state, f"investigation.{status}", {}, "investigation.final")
            await session.execute(
                update(Investigation)
                .where(Investigation.id == UUID(state["investigation_id"]))
                .values(status=status, phase="finished", completed_at=func.now(), error_code=None)
            )
        return {"terminal_status": status}


def _slice(incident: Incident) -> tuple[str, str | None]:
    return incident.dimension, incident.dimension_value


def _context_requests(
    views: list[output.HypothesisView],
) -> list[tuple[output.HypothesisView, str, Trigger]]:
    """Deterministic missing-context detection (ARCHITECTURE §7.2 plus CT-5, AMD-15), most
    useful first: (hypothesis, unanswered human criterion key, trigger)."""
    out: list[tuple[output.HypothesisView, str, Trigger]] = []
    seen: set[str] = set()

    def add(view: output.HypothesisView, trigger: Trigger) -> None:
        criterion = human_criterion(view.row.template)
        if criterion is None or view.row.template in seen:
            return
        outcome = next(
            (
                c
                for c in (view.evaluation.criteria if view.evaluation else [])
                if c["key"] == criterion.key
            ),
            None,
        )
        if outcome is not None and outcome["satisfied"] is not None:
            return  # already answered
        seen.add(view.row.template)
        out.append((view, criterion.key, trigger))

    ordered = output.ranked(views)
    problems = [v for v in ordered if v.status == "supported" and not v.explained]
    alive = [v for v in ordered if v.status != "refuted" and not v.explained]
    if problems:
        criterion = human_criterion(problems[0].row.template)
        if (
            criterion is not None
            and criterion.question is not None
            and criterion.question.asks_intent
        ):
            add(problems[0], "CT-5")
    if alive and alive[0].status == "needs_human_context" and not problems:
        add(alive[0], "CT-2")
    if (
        len(alive) >= 2
        and alive[1].confidence >= AMBIGUITY_FLOOR
        and alive[0].confidence - alive[1].confidence < AMBIGUITY_GAP
    ):
        add(alive[0], "CT-1")
    for view in ordered:
        if view.row.template == "H_DATA_QUALITY" and any(
            c["satisfied"] and c["polarity"] == "support" and c["source"] == "data"
            for c in (view.evaluation.criteria if view.evaluation else [])
        ):
            add(view, "CT-3")
    for view in ordered:
        if view.status == "needs_human_context" and (
            view.row.template != "H_EXTERNAL_CONTEXT" or not problems
        ):
            add(view, "CT-4")
    return out


def _question(template: str, criterion_key: str, trigger: Trigger) -> dict[str, Any]:
    criterion = next(c for c in CATALOG[template].criteria if c.key == criterion_key and c.question)
    question = criterion.question.as_json() if criterion.question else {}
    return {
        **question,
        "template": template,
        "criterion": criterion_key,
        "claim": CATALOG[template].claim,
        "why": TRIGGER_WHY[trigger],
    }


FREE_TEXT_MAX = 1000


def response_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """The JSON schema an answer must satisfy (persisted with the question, AMD-15)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["checkpoint_id"],
        "properties": {
            "checkpoint_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "option_id": {"enum": [o["id"] for o in spec["options"]]},
            "free_text": {"type": "string", "minLength": 1, "maxLength": FREE_TEXT_MAX},
        },
        "anyOf": [{"required": ["option_id"]}, {"required": ["free_text"]}],
    }


def build(nodes: Nodes) -> StateGraph[State]:
    graph: StateGraph[State] = StateGraph(State)
    graph.add_node("initialize", nodes.initialize)
    graph.add_node("select_incident", nodes.select_incident)
    graph.add_node("generate_hypotheses", nodes.generate_hypotheses)
    graph.add_node("select_tools", nodes.select_tools)
    graph.add_node("retrieve_evidence", nodes.retrieve_evidence)
    graph.add_node("verify_evidence", nodes.verify_evidence)
    graph.add_node("draft_findings", nodes.draft_findings)
    graph.add_node("assess_context", nodes.assess_context)
    graph.add_node("await_clarification", nodes.await_clarification)
    graph.add_node("apply_answer", nodes.apply_answer)
    graph.add_node("finalize", nodes.finalize)

    max_rounds = nodes.settings.investigation_tool_rounds
    max_llm_tools = nodes.settings.investigation_llm_tools_per_incident

    def after_initialize(state: State) -> str:
        return "finalize" if state["terminal_status"] else "select_incident"

    def after_select(state: State) -> str:
        return "generate_hypotheses" if state["current_incident_id"] else "draft_findings"

    def after_retrieve(state: State) -> str:
        again = (
            state["more_tools"]
            and state["llm_mode"] == "llm"
            and state["tool_round"] < max_rounds
            and state["llm_tool_calls"] < max_llm_tools
        )
        return "select_tools" if again else "verify_evidence"

    def after_assess(state: State) -> str:
        return "await_clarification" if state["clarification_id"] else "finalize"

    graph.add_edge(START, "initialize")
    graph.add_conditional_edges("initialize", after_initialize, ["finalize", "select_incident"])
    graph.add_conditional_edges(
        "select_incident", after_select, ["generate_hypotheses", "draft_findings"]
    )
    graph.add_edge("generate_hypotheses", "select_tools")
    graph.add_edge("select_tools", "retrieve_evidence")
    graph.add_conditional_edges(
        "retrieve_evidence", after_retrieve, ["select_tools", "verify_evidence"]
    )
    graph.add_edge("verify_evidence", "select_incident")
    graph.add_edge("draft_findings", "assess_context")
    graph.add_conditional_edges("assess_context", after_assess, ["await_clarification", "finalize"])
    graph.add_edge("await_clarification", "apply_answer")
    graph.add_edge("apply_answer", "select_incident")
    graph.add_edge("finalize", END)
    return graph
