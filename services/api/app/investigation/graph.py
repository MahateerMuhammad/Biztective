"""The investigation graph (ARCHITECTURE §12 as amended by AMD-12).

    START → initialize ─┬─(insufficient data | no incidents)──────────────────────► finalize
                        └─► select_incident ─(none left)─► synthesize_output ─► finalize → END
                              ▲      └─(next)─► generate_hypotheses → select_tools
                              │                   ▲                        │
                              │                   └──(LLM asked for more)──┤ retrieve_evidence
                              └─ detect_missing_context ◄─ verify_evidence ◄┘

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
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.evidence import canonical_json
from app.core.config import Settings
from app.investigation import output
from app.investigation.catalog import CATALOG, CATALOG_VERSION, ORDER, Evidence, candidates
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
    store_tool_evidence,
    tool_scope,
    transaction,
)
from app.investigation.tools import TOOLS, EvidenceLookupParams, ToolContext
from app.investigation.verification import evaluate
from app.llm.gateway import Accepted, Fallback, Gateway
from app.llm.provider import LlmProvider
from app.llm.schema import provider_schema
from app.models import (
    AnalysisRevision,
    Clarification,
    EvidenceItem,
    Finding,
    Hypothesis,
    HypothesisEvaluation,
    Incident,
    Investigation,
    InvestigationEvent,
    InvestigationEvidence,
    Product,
)

GRAPH_VERSION = "investigation-graph-1.0.0"
DISABLING = frozenset({"provider_error", "budget", "deadline"})
MAX_CLARIFICATIONS_PER_INCIDENT = 3
MAX_CLARIFICATIONS = 6
AMBIGUITY_GAP = Decimal("0.15")  # CT-1: top two non-refuted confidences closer than this
AMBIGUITY_FLOOR = Decimal("0.3")


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
    ) -> None:
        iid, bid = self._ids(state)
        await emit(session, iid, bid, event_type, payload, dedupe, actor)

    async def _phase(self, session: AsyncSession, state: State, phase: str) -> None:
        await session.execute(
            update(Investigation)
            .where(Investigation.id == UUID(state["investigation_id"]))
            .values(phase=phase)
        )

    async def _deadline(self, state: State) -> datetime | None:
        async with self._tx(state, lock=False) as session:
            started = await session.scalar(
                select(Investigation.started_at).where(
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
                {"incident_id": incident_id},
                f"incident.selected:{incident_id}",
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
        async with self._tx(state, lock=False) as session:
            prior = await session.scalar(
                select(InvestigationEvent.payload).where(
                    InvestigationEvent.investigation_id == iid,
                    InvestigationEvent.dedupe_key == f"tools.selected:{incident_id}:{round_ + 1}",
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
        if state["llm_mode"] == "llm" and remaining > 0:
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
                f"tools.selected:{incident_id}:{round_ + 1}",
            )
            if dropped:
                await self._emit(
                    session,
                    state,
                    "tools.limit_reached",
                    {"incident_id": str(incident_id), "dropped": dropped},
                    f"tools.limit_reached:{incident_id}:{round_ + 1}",
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
                        f"evidence.limit_reached:{incident_id}",
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
                            f"tool.failed:{incident_id}:{key}",
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
                            f"tool.rejected:{incident_id}:{key}",
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
                {"incident_id": str(incident_id), "round": state["tool_round"], "items": collected},
                f"evidence.collected:{incident_id}:{state['tool_round']}",
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
            f"evidence.looked_up:{incident_id}:{state['tool_round']}:"
            + ",".join(sorted(str(e) for e in params.evidence_ids)),
        )

    async def verify_evidence(self, state: State) -> dict[str, Any]:
        iid, bid = self._ids(state)
        incident_id = UUID(str(state["current_incident_id"]))
        async with self._tx(state) as session:
            await self._phase(session, state, "verifying")
            scope = await tool_scope(session, incident_id)
            items = (await linked_evidence(session, iid, [incident_id], include_invalid=True)).get(
                incident_id, []
            )
            by_key: dict[str, Evidence] = {}
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
                if item.params_hash:
                    by_key[item.params_hash] = Evidence(item.id, item.facts)
            hypotheses = list(
                await session.scalars(
                    select(Hypothesis)
                    .where(
                        Hypothesis.investigation_id == iid, Hypothesis.incident_id == incident_id
                    )
                    .order_by(Hypothesis.priority)
                )
            )
            done = set(
                await session.scalars(
                    select(HypothesisEvaluation.hypothesis_id).where(
                        HypothesisEvaluation.hypothesis_id.in_([h.id for h in hypotheses])
                    )
                )
            )
            for hypothesis in hypotheses:
                if hypothesis.id in done:
                    continue
                template = CATALOG[hypothesis.template]
                evidence = {
                    r.key: by_key.get(evidence_params(r.tool, r.params(scope.metric), scope)[1])
                    for r in template.requirements
                }
                result = evaluate(template, evidence)
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
                    )
                    .on_conflict_do_nothing(index_elements=["hypothesis_id"])
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
                        "satisfied": [c.key for c in result.criteria if c.satisfied],
                        "unknown": [c.key for c in result.unknown_data],
                    },
                    f"hypothesis.evaluated:{hypothesis.id}",
                )
        return {}

    async def detect_missing_context(self, state: State) -> dict[str, Any]:
        iid, bid = self._ids(state)
        incident_id = UUID(str(state["current_incident_id"]))
        async with self._tx(state) as session:
            incident = await session.get_one(Incident, incident_id)
            rows = (
                await session.execute(
                    select(Hypothesis, HypothesisEvaluation)
                    .join(HypothesisEvaluation, HypothesisEvaluation.hypothesis_id == Hypothesis.id)
                    .where(
                        Hypothesis.investigation_id == iid, Hypothesis.incident_id == incident_id
                    )
                )
            ).all()
            views = output.ranked([output.HypothesisView(h, e) for h, e in rows])
            asked = await session.scalar(
                select(func.count()).where(Clarification.investigation_id == iid)
            )
            wanted = _context_requests(views)
            room = min(MAX_CLARIFICATIONS_PER_INCIDENT, MAX_CLARIFICATIONS - int(asked or 0))
            for view, criterion, trigger in wanted[: max(room, 0)]:
                clarification_id = uuid.uuid7()
                inserted = await session.scalar(
                    pg_insert(Clarification)
                    .values(
                        id=clarification_id,
                        business_id=bid,
                        investigation_id=iid,
                        incident_id=incident_id,
                        incident_key=incident.key,
                        hypothesis_id=view.row.id,
                        criterion_key=f"{view.row.template}.{criterion['key']}",
                        trigger=trigger,
                        question_spec=_question(view.row.template, criterion["key"]),
                    )
                    .on_conflict_do_nothing(
                        index_elements=["investigation_id", "incident_key", "criterion_key"]
                    )
                    .returning(Clarification.id)
                )
                if inserted is not None:
                    await self._emit(
                        session,
                        state,
                        "clarification.requested",
                        {
                            "clarification_id": str(inserted),
                            "incident_id": str(incident_id),
                            "trigger": trigger,
                            "criterion": f"{view.row.template}.{criterion['key']}",
                        },
                        f"clarification.requested:{incident.key}:{view.row.template}."
                        f"{criterion['key']}",
                    )
            if len(wanted) > max(room, 0):
                await self._emit(
                    session,
                    state,
                    "clarification.cap_reached",
                    {"incident_id": str(incident_id), "not_asked": len(wanted) - max(room, 0)},
                    f"clarification.cap_reached:{incident_id}",
                )
        return {"current_incident_id": None}

    async def synthesize_output(self, state: State) -> dict[str, Any]:
        iid, bid = self._ids(state)
        async with self._tx(state, lock=False) as session:
            investigation = await session.get_one(Investigation, iid)
            if await session.scalar(select(Finding.id).where(Finding.investigation_id == iid)):
                return {"llm_mode": investigation.llm_mode}
            loaded = await output.load(session, investigation)
        statuses = {str(v.row.id): v.status for views in loaded.hypotheses.values() for v in views}
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
            findings_id = await session.scalar(
                pg_insert(Finding)
                .values(
                    id=uuid.uuid7(),
                    business_id=bid,
                    investigation_id=iid,
                    round=1,
                    content=jsonable(content),
                    rendered=jsonable(rendered),
                    source=source,
                    llm_call_id=call_id,
                )
                .on_conflict_do_nothing(index_elements=["investigation_id", "round"])
                .returning(Finding.id)
            )
            if findings_id is not None:
                await self._emit(
                    session,
                    state,
                    "findings.drafted",
                    {
                        "findings_id": str(findings_id),
                        "source": source,
                        "root_cause_hypothesis_ids": [
                            c["hypothesis_id"] for c in content["root_causes"]
                        ],
                    },
                    "findings.drafted",
                    "llm" if source == "llm" else "system",
                )
            update_ = await self._after_llm(session, state, result, SYNTHESIZE.name, "all")
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


def _context_requests(
    views: list[output.HypothesisView],
) -> list[tuple[output.HypothesisView, dict[str, Any], Literal["CT-1", "CT-2", "CT-3", "CT-4"]]]:
    """Deterministic missing-context detection (ARCHITECTURE §7.2), most useful first."""
    out: list[
        tuple[output.HypothesisView, dict[str, Any], Literal["CT-1", "CT-2", "CT-3", "CT-4"]]
    ] = []
    seen: set[str] = set()

    def human_criterion(view: output.HypothesisView) -> dict[str, Any] | None:
        criteria = view.evaluation.criteria if view.evaluation else []
        return next((c for c in criteria if c["source"] == "human"), None)

    def add(view: output.HypothesisView, trigger: Literal["CT-1", "CT-2", "CT-3", "CT-4"]) -> None:
        criterion = human_criterion(view)
        if criterion is not None and view.row.template not in seen:
            seen.add(view.row.template)
            out.append((view, criterion, trigger))

    alive = [v for v in views if v.status != "refuted"]
    supported = [v for v in views if v.status == "supported"]
    if alive and alive[0].status == "needs_human_context" and not supported:
        add(alive[0], "CT-2")
    if (
        len(alive) >= 2
        and alive[1].confidence >= AMBIGUITY_FLOOR
        and alive[0].confidence - alive[1].confidence < AMBIGUITY_GAP
    ):
        add(alive[0], "CT-1")
    for view in views:
        if view.row.template == "H_DATA_QUALITY" and any(
            c["satisfied"] and c["polarity"] == "support" and c["source"] == "data"
            for c in (view.evaluation.criteria if view.evaluation else [])
        ):
            add(view, "CT-3")
    for view in views:
        if view.status == "needs_human_context" and (
            view.row.template != "H_EXTERNAL_CONTEXT" or not supported
        ):
            add(view, "CT-4")
    return out


def _question(template: str, criterion_key: str) -> dict[str, Any]:
    criterion = next(c for c in CATALOG[template].criteria if c.key == criterion_key and c.question)
    question = criterion.question.as_json() if criterion.question else {}
    return {**question, "template": template, "criterion": criterion_key}


def build(nodes: Nodes) -> StateGraph[State]:
    graph: StateGraph[State] = StateGraph(State)
    graph.add_node("initialize", nodes.initialize)
    graph.add_node("select_incident", nodes.select_incident)
    graph.add_node("generate_hypotheses", nodes.generate_hypotheses)
    graph.add_node("select_tools", nodes.select_tools)
    graph.add_node("retrieve_evidence", nodes.retrieve_evidence)
    graph.add_node("verify_evidence", nodes.verify_evidence)
    graph.add_node("detect_missing_context", nodes.detect_missing_context)
    graph.add_node("synthesize_output", nodes.synthesize_output)
    graph.add_node("finalize", nodes.finalize)

    max_rounds = nodes.settings.investigation_tool_rounds
    max_llm_tools = nodes.settings.investigation_llm_tools_per_incident

    def after_initialize(state: State) -> str:
        return "finalize" if state["terminal_status"] else "select_incident"

    def after_select(state: State) -> str:
        return "generate_hypotheses" if state["current_incident_id"] else "synthesize_output"

    def after_retrieve(state: State) -> str:
        again = (
            state["more_tools"]
            and state["llm_mode"] == "llm"
            and state["tool_round"] < max_rounds
            and state["llm_tool_calls"] < max_llm_tools
        )
        return "select_tools" if again else "verify_evidence"

    graph.add_edge(START, "initialize")
    graph.add_conditional_edges("initialize", after_initialize, ["finalize", "select_incident"])
    graph.add_conditional_edges(
        "select_incident", after_select, ["generate_hypotheses", "synthesize_output"]
    )
    graph.add_edge("generate_hypotheses", "select_tools")
    graph.add_edge("select_tools", "retrieve_evidence")
    graph.add_conditional_edges(
        "retrieve_evidence", after_retrieve, ["select_tools", "verify_evidence"]
    )
    graph.add_edge("verify_evidence", "detect_missing_context")
    graph.add_edge("detect_missing_context", "select_incident")
    graph.add_edge("synthesize_output", "finalize")
    graph.add_edge("finalize", END)
    return graph
