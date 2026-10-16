"""Persistence helpers for investigations: events, scopes, evidence and LLM call records.

All functions run inside a transaction whose RLS business context is already set. Writes are
keyed by natural keys with ON CONFLICT DO NOTHING, so a node re-executed after a crash adds
nothing twice (ARCHITECTURE §12.5).

Any annotations describe heterogeneous JSON evidence, event and provider payloads; SQL
identifiers and tool routing never come from those payloads.
"""

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.config import load_config
from app.analytics.evidence import canonical_json, sha256_of
from app.analytics.periods import detection_windows
from app.core.db import set_business_context
from app.investigation.critic import intact
from app.investigation.facts import Facts, detection_facts
from app.investigation.tools import ToolParams, ToolScope
from app.llm.gateway import CallRecord
from app.models import (
    AnalysisRevision,
    EvidenceItem,
    Incident,
    Investigation,
    InvestigationEvidence,
    LlmCall,
)


def jsonable(value: Any) -> Any:
    return json.loads(canonical_json(value))


@asynccontextmanager
async def transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
    business_id: UUID,
    investigation_id: UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """One transaction in the business's RLS context; locks the investigation row if given
    (a single writer per investigation keeps event sequence numbers gap-free)."""
    async with sessionmaker() as session, session.begin():
        await set_business_context(session, business_id)
        if investigation_id is not None:
            await session.execute(
                select(Investigation.id)
                .where(Investigation.id == investigation_id)
                .with_for_update()
            )
        yield session


async def emit(
    session: AsyncSession,
    investigation_id: UUID,
    business_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    dedupe_key: str,
    actor_kind: str = "system",
) -> int | None:
    """Append an event exactly once per dedupe key; returns its seq (None if it existed)."""
    seq: int | None = await session.scalar(
        text("""
            WITH next AS (
                UPDATE investigations SET last_seq = last_seq + 1
                WHERE id = :i AND NOT EXISTS (
                    SELECT 1 FROM investigation_events WHERE investigation_id = :i
                        AND dedupe_key = :k)
                RETURNING last_seq
            )
            INSERT INTO investigation_events
                (business_id, investigation_id, seq, type, actor_kind, payload, dedupe_key)
            SELECT :b, :i, last_seq, :t, :a, CAST(:p AS jsonb), :k FROM next
            RETURNING seq
        """),
        {
            "i": investigation_id,
            "b": business_id,
            "t": event_type,
            "a": actor_kind,
            "p": canonical_json(payload),
            "k": dedupe_key,
        },
    )
    return seq


async def tool_scope(session: AsyncSession, incident_id: UUID) -> ToolScope:
    incident = await session.get_one(Incident, incident_id)
    revision = await session.get_one(AnalysisRevision, incident.revision_id)
    config = await load_config(session, revision.config_version)
    first_order = date.fromisoformat(revision.summary["first_order_date"])
    as_of = revision.as_of_date
    windows = (
        detection_windows(first_order, as_of, config.current_weeks, config.baseline_weeks)
        if as_of is not None
        else None
    )
    if as_of is None or windows is None:  # an incident implies detection windows existed
        raise ValueError("incident without detection windows")
    return ToolScope(
        business_id=incident.business_id,
        dataset_id=revision.dataset_id,
        revision_id=revision.id,
        incident_id=incident.id,
        metric=incident.metric,
        dimension=incident.dimension,
        value=incident.dimension_value,
        windows=windows,
        first_order=first_order,
        as_of=as_of,
        config=config,
    )


def evidence_params(tool: str, params: ToolParams, scope: ToolScope) -> tuple[dict[str, Any], str]:
    """(stored params, cache key) of a tool call. The key covers the tool, its arguments and
    the incident scope; with the revision it identifies an immutable result."""
    stored = jsonable({"arguments": params.model_dump(mode="json"), "scope": scope.describe()})
    return stored, sha256_of({"tool": tool, **stored})


@dataclass(frozen=True)
class LinkedEvidence:
    id: UUID
    kind: str
    params_hash: str | None
    result: dict[str, Any]
    result_hash: str
    requested_by: str

    @property
    def facts(self) -> Facts:
        return detection_facts(self.kind, self.result)


async def linked_evidence(
    session: AsyncSession,
    investigation_id: UUID,
    incident_ids: Sequence[UUID] | None = None,
    *,
    include_invalid: bool = False,
) -> dict[UUID, list[LinkedEvidence]]:
    """Evidence of the investigation by incident, in the order it was linked."""
    query = (
        select(EvidenceItem, InvestigationEvidence.requested_by, InvestigationEvidence.incident_id)
        .join(InvestigationEvidence, InvestigationEvidence.evidence_id == EvidenceItem.id)
        .where(InvestigationEvidence.investigation_id == investigation_id)
        .order_by(InvestigationEvidence.created_at, EvidenceItem.id)
    )
    if incident_ids is not None:
        query = query.where(InvestigationEvidence.incident_id.in_(incident_ids))
    out: dict[UUID, list[LinkedEvidence]] = {}
    for item, requested_by, incident_id in (await session.execute(query)).all():
        if not include_invalid and not intact(item.result, item.result_hash):
            continue
        out.setdefault(incident_id, []).append(
            LinkedEvidence(
                item.id, item.kind, item.params_hash, item.result, item.result_hash, requested_by
            )
        )
    return out


async def store_tool_evidence(
    session: AsyncSession,
    scope: ToolScope,
    kind: str,
    params: dict[str, Any],
    params_hash: str,
    query_version: str,
    result: dict[str, Any],
) -> UUID:
    payload = jsonable(result)
    await session.execute(
        pg_insert(EvidenceItem)
        .values(
            id=uuid.uuid7(),
            business_id=scope.business_id,
            revision_id=scope.revision_id,
            incident_id=scope.incident_id,
            kind=kind,
            params=params,
            result=payload,
            result_hash=sha256_of(payload),
            params_hash=params_hash,
            query_version=query_version,
        )
        .on_conflict_do_nothing(
            index_elements=["revision_id", "kind", "params_hash"],
            index_where=text("params_hash IS NOT NULL"),
        )
    )
    stored = await cached_evidence(session, scope.revision_id, kind, params_hash)
    if stored is None:  # pragma: no cover - the row was inserted or existed a moment ago
        raise RuntimeError("tool evidence vanished")
    return stored


async def cached_evidence(
    session: AsyncSession, revision_id: UUID, kind: str, params_hash: str
) -> UUID | None:
    found: UUID | None = await session.scalar(
        select(EvidenceItem.id).where(
            EvidenceItem.revision_id == revision_id,
            EvidenceItem.kind == kind,
            EvidenceItem.params_hash == params_hash,
        )
    )
    return found


async def link_evidence(
    session: AsyncSession,
    investigation: Investigation | tuple[UUID, UUID],
    evidence_id: UUID,
    incident_id: UUID,
    requested_by: str,
) -> None:
    investigation_id, business_id = (
        (investigation.id, investigation.business_id)
        if isinstance(investigation, Investigation)
        else investigation
    )
    await session.execute(
        pg_insert(InvestigationEvidence)
        .values(
            business_id=business_id,
            investigation_id=investigation_id,
            evidence_id=evidence_id,
            incident_id=incident_id,
            requested_by=requested_by,
        )
        .on_conflict_do_nothing(index_elements=["investigation_id", "evidence_id"])
    )


class Recorder:
    """`llm_calls` persistence and budget usage for one investigation (gateway protocol).

    Each record commits on its own, so spent budget survives any later failure."""

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
        async with transaction(self.sessionmaker, self.business_id, self.investigation_id) as s:
            await s.execute(
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
            await emit(
                s,
                self.investigation_id,
                self.business_id,
                "llm.call_completed",
                {
                    "llm_call_id": str(call_id),
                    "task": call.task,
                    "attempt": call.attempt,
                    "validation": call.validation,
                    "error_code": call.error_code,
                },
                f"llm:{call_id}",
                actor_kind="llm",
            )
        return call_id
