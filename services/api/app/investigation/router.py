"""Investigations API (DATA_CONTRACTS §12.6, §13): start, list, detail, events, retry,
evidence, clarification questions and answers, and interpretation versions.

Any annotations represent heterogeneous JSON response fields; incoming requests instead
use explicit Pydantic models with forbidden extra fields.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.analytics.evidence import sha256_of
from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.core.pagination import Cursor, decode_uuid_cursor, encode_cursor
from app.datasets import service as idempotency
from app.investigation import output, replay, service
from app.investigation.critic import intact
from app.investigation.graph import FREE_TEXT_MAX
from app.models import (
    Clarification,
    EvidenceItem,
    HumanInput,
    Investigation,
    InvestigationEvent,
    InvestigationVersion,
    LlmCall,
    User,
)
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}", tags=["investigations"])

Reader = Annotated[TenantContext, Depends(RequirePermission(Permission.INVESTIGATION_READ))]
Runner = Annotated[TenantContext, Depends(RequirePermission(Permission.INVESTIGATION_RUN))]
Answerer = Annotated[TenantContext, Depends(RequirePermission(Permission.HITL_ANSWER))]
IdempotencyKeyHeader = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
]


class StartInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID


class AnswerClarification(BaseModel):
    """A structured option, free text, or both. Free text is stored as untrusted data."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: Annotated[str, Field(min_length=1, max_length=64)]
    option_id: Annotated[str | None, Field(pattern=r"^[a-z_]{1,40}$")] = None
    free_text: Annotated[str | None, Field(max_length=FREE_TEXT_MAX)] = None


def _summary(i: Investigation) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "dataset_id": str(i.dataset_id),
        "revision_id": str(i.revision_id),
        "status": i.status,
        "phase": i.phase,
        "llm_mode": i.llm_mode,
        "llm_disabled_reason": i.llm_disabled_reason,
        "error_code": i.error_code,
        "graph_version": i.graph_version,
        "catalog_version": i.catalog_version,
        "last_seq": i.last_seq,
        "created_at": i.created_at.isoformat(),
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "completed_at": i.completed_at.isoformat() if i.completed_at else None,
    }


def _clarification(
    c: Clarification, answer: HumanInput | None, names: dict[UUID, str]
) -> dict[str, Any]:
    spec = c.question_spec
    return {
        "id": str(c.id),
        "incident_id": str(c.incident_id),
        "incident_key": c.incident_key,
        "criterion": c.criterion_key,
        "trigger": c.trigger,
        "status": c.status,
        "question": spec["question"],
        "claim": spec.get("claim"),
        "why": spec.get("why"),
        "options": [{"id": o["id"], "label": o["label"]} for o in spec["options"]],
        "response_schema": c.response_schema,
        # Only a question that is waiting can be answered, and only at its checkpoint.
        "checkpoint_id": c.checkpoint_id if c.status == "awaiting" else None,
        "asked_at": c.asked_at.isoformat() if c.asked_at else None,
        "answered_at": c.answered_at.isoformat() if c.answered_at else None,
        "applied_at": c.applied_at.isoformat() if c.applied_at else None,
        "answer": None
        if answer is None
        else {
            "human_input_id": str(answer.id),
            "option_id": answer.option_id,
            "option_label": next(
                (o["label"] for o in spec["options"] if o["id"] == answer.option_id), None
            ),
            "effect": answer.effect,
            "free_text": answer.free_text,  # plain text; clients must not interpret it
            "answered_by": {
                "id": str(answer.answered_by),
                "display_name": names.get(answer.answered_by),
            },
            "answered_at": answer.answered_at.isoformat(),
        },
    }


async def _clarifications(db: DbSession, investigation: Investigation) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Clarification, HumanInput)
            .outerjoin(HumanInput, HumanInput.clarification_id == Clarification.id)
            .where(Clarification.investigation_id == investigation.id)
            .order_by(Clarification.created_at, Clarification.id)
        )
    ).all()
    authors = {a.answered_by for _, a in rows if a is not None}
    names: dict[UUID, str] = {}
    if authors:
        names = {
            u.id: u.display_name for u in await db.scalars(select(User).where(User.id.in_(authors)))
        }
    return [_clarification(c, a, names) for c, a in rows]


async def _investigation(
    db: DbSession, tenant: TenantContext, investigation_id: UUID
) -> Investigation:
    investigation = await db.scalar(
        select(Investigation).where(
            Investigation.id == investigation_id, Investigation.business_id == tenant.business_id
        )
    )
    if investigation is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return investigation


@router.post("/investigations", status_code=202)
async def start_investigation(
    request: Request,
    db: DbSession,
    tenant: Runner,
    body: StartInvestigation,
    idempotency_key: IdempotencyKeyHeader,
) -> JSONResponse:
    await service.lock_starts(db, tenant.business_id)
    request_hash = sha256_of(
        {"operation": "investigation.start", "dataset_id": str(body.dataset_id)}
    )
    replay = await idempotency.replay_idempotent(db, tenant, idempotency_key, request_hash)
    if replay is not None:
        await db.commit()
        return JSONResponse(replay.body, status_code=replay.status)
    runtime: service.InvestigationRuntime = request.app.state.investigations
    investigation, created = await service.start(
        db,
        business_id=tenant.business_id,
        dataset_id=body.dataset_id,
        user_id=tenant.user.id,
        llm_available=runtime.provider is not None,
        starts_per_hour=runtime.settings.investigation_starts_per_hour,
    )
    if created:
        await record_audit(
            db,
            action="investigation.started",
            actor_user_id=tenant.user.id,
            business_id=tenant.business_id,
            target_type="investigation",
            target_id=str(investigation.id),
            client_host=client_host(request),
            metadata={"dataset_id": str(body.dataset_id), "llm_mode": investigation.llm_mode},
        )
    response = _summary(investigation)
    await idempotency.store_idempotent_response(
        db, tenant, idempotency_key, request_hash, idempotency.StoredResponse(202, response)
    )
    await db.commit()
    return JSONResponse(response, status_code=202)


@router.get("/investigations")
async def list_investigations(
    db: DbSession,
    tenant: Reader,
    dataset_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Cursor = None,
) -> dict[str, Any]:
    # UUIDv7 ids are time-ordered, so id order is creation order.
    query = (
        select(Investigation)
        .where(Investigation.business_id == tenant.business_id)
        .order_by(Investigation.id.desc())
        .limit(limit + 1)
    )
    if dataset_id is not None:
        query = query.where(Investigation.dataset_id == dataset_id)
    if cursor is not None:
        query = query.where(Investigation.id < decode_uuid_cursor(cursor))
    rows = list(await db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_summary(i) for i in rows],
        "next_cursor": encode_cursor(str(rows[-1].id)) if more else None,
    }


@router.get("/investigations/{investigation_id}")
async def get_investigation(
    db: DbSession, tenant: Reader, investigation_id: UUID
) -> dict[str, Any]:
    investigation = await _investigation(db, tenant, investigation_id)
    loaded = await output.load(db, investigation)
    usage = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LlmCall.tokens_in + LlmCall.tokens_out), 0),
            ).where(LlmCall.investigation_id == investigation.id)
        )
    ).one()
    fallbacks = await db.scalar(
        select(func.count()).where(
            InvestigationEvent.investigation_id == investigation.id,
            InvestigationEvent.type == "llm.fallback_used",
        )
    )
    clarifications = await _clarifications(db, investigation)
    return {
        **_summary(investigation),
        "version": loaded.findings.round if loaded.findings else None,
        "awaiting": next((c for c in clarifications if c["status"] == "awaiting"), None),
        "currency": loaded.currency,
        "usage": {
            "llm_calls": int(usage[0]),
            "llm_tokens": int(usage[1]),
            "llm_fallbacks": int(fallbacks or 0),
            "evidence_items": sum(len(v) for v in loaded.evidence.values()),
        },
        "restrictions": loaded.revision.restrictions,
        **output.report(loaded),
    }


@router.get("/investigations/{investigation_id}/events")
async def list_events(
    db: DbSession,
    tenant: Reader,
    investigation_id: UUID,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    investigation = await _investigation(db, tenant, investigation_id)
    events = await db.scalars(
        select(InvestigationEvent)
        .where(
            InvestigationEvent.investigation_id == investigation.id,
            InvestigationEvent.seq > after_seq,
        )
        .order_by(InvestigationEvent.seq)
        .limit(limit)
    )
    return {
        "items": [
            {
                "seq": e.seq,
                "type": e.type,
                "actor_kind": e.actor_kind,
                "occurred_at": e.occurred_at.isoformat(),
                "payload": e.payload,
            }
            for e in events
        ],
        "last_seq": investigation.last_seq,
    }


@router.post("/investigations/{investigation_id}/retry", status_code=202)
async def retry_investigation(
    request: Request, db: DbSession, tenant: Runner, investigation_id: UUID
) -> JSONResponse:
    investigation = await _investigation(db, tenant, investigation_id)
    await service.retry(db, investigation)
    await record_audit(
        db,
        action="investigation.retried",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="investigation",
        target_id=str(investigation.id),
        client_host=client_host(request),
    )
    await db.commit()
    return JSONResponse({"id": str(investigation.id), "status": "queued"}, status_code=202)


@router.get("/investigations/{investigation_id}/clarifications")
async def list_clarifications(
    db: DbSession, tenant: Reader, investigation_id: UUID
) -> dict[str, Any]:
    investigation = await _investigation(db, tenant, investigation_id)
    return {"items": await _clarifications(db, investigation)}


@router.post(
    "/investigations/{investigation_id}/clarifications/{clarification_id}/answer",
    status_code=202,
)
async def answer_clarification(
    request: Request,
    db: DbSession,
    tenant: Answerer,
    investigation_id: UUID,
    clarification_id: UUID,
    body: AnswerClarification,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
    ] = None,
) -> JSONResponse:
    investigation = await _investigation(db, tenant, investigation_id)
    # Serialize answers to this investigation before any replay or state decision.
    await db.refresh(investigation, with_for_update=True)
    request_hash = sha256_of(
        {
            "operation": "clarification.answer",
            "clarification_id": str(clarification_id),
            **body.model_dump(),
        }
    )
    if idempotency_key is not None:
        replay_ = await idempotency.replay_idempotent(db, tenant, idempotency_key, request_hash)
        if replay_ is not None:
            await db.commit()
            return JSONResponse(replay_.body, status_code=replay_.status)
    answer = await service.answer(
        db,
        investigation,
        clarification_id,
        user_id=tenant.user.id,
        checkpoint_id=body.checkpoint_id,
        option_id=body.option_id,
        free_text=body.free_text,
    )
    await record_audit(
        db,
        action="hitl.answered",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="clarification",
        target_id=str(clarification_id),
        client_host=client_host(request),
        metadata={
            "investigation_id": str(investigation.id),
            "criterion": answer.criterion_key,
            "option_id": answer.option_id,
            "effect": answer.effect,
            "free_text": answer.free_text is not None,  # never the text itself
        },
    )
    response = {
        "clarification_id": str(clarification_id),
        "human_input_id": str(answer.id),
        "status": "answered",
        "investigation_status": "queued",
    }
    if idempotency_key is not None:
        await idempotency.store_idempotent_response(
            db, tenant, idempotency_key, request_hash, idempotency.StoredResponse(202, response)
        )
    await db.commit()
    return JSONResponse(response, status_code=202)


@router.get("/investigations/{investigation_id}/versions")
async def list_versions(db: DbSession, tenant: Reader, investigation_id: UUID) -> dict[str, Any]:
    investigation = await _investigation(db, tenant, investigation_id)
    versions = await db.scalars(
        select(InvestigationVersion)
        .where(InvestigationVersion.investigation_id == investigation.id)
        .order_by(InvestigationVersion.version)
    )
    return {
        "items": [
            {
                "version": v.version,
                "snapshot_hash": v.snapshot_hash,
                "caused_by": v.snapshot["caused_by"],
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ]
    }


async def _version(
    db: DbSession, investigation: Investigation, version: int
) -> InvestigationVersion:
    found = await db.scalar(
        select(InvestigationVersion).where(
            InvestigationVersion.investigation_id == investigation.id,
            InvestigationVersion.version == version,
        )
    )
    if found is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return found


@router.get("/investigations/{investigation_id}/versions/compare")
async def compare_versions(
    db: DbSession,
    tenant: Reader,
    investigation_id: UUID,
    from_version: Annotated[int, Query(ge=1, le=100)] = 1,
    to_version: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> dict[str, Any]:
    """A pure diff of two stored snapshots: no LLM call and no analytics query (AMD-15)."""
    investigation = await _investigation(db, tenant, investigation_id)
    if to_version is None:
        to_version = await db.scalar(
            select(func.max(InvestigationVersion.version)).where(
                InvestigationVersion.investigation_id == investigation.id
            )
        )
        if to_version is None:
            raise ApiError(404, "not_found", "Resource not found.")
    a = await _version(db, investigation, from_version)
    b = await _version(db, investigation, to_version)
    return replay.compare(a.snapshot, b.snapshot)


@router.get("/investigations/{investigation_id}/versions/{version}")
async def get_version(
    db: DbSession,
    tenant: Reader,
    investigation_id: UUID,
    version: Annotated[int, Path(ge=1, le=100)],
) -> dict[str, Any]:
    investigation = await _investigation(db, tenant, investigation_id)
    found = await _version(db, investigation, version)
    return {
        "version": found.version,
        "snapshot_hash": found.snapshot_hash,
        "intact": sha256_of(found.snapshot) == found.snapshot_hash,
        "created_at": found.created_at.isoformat(),
        "snapshot": found.snapshot,
    }


@router.get("/evidence/{evidence_id}")
async def get_evidence(db: DbSession, tenant: Reader, evidence_id: UUID) -> dict[str, Any]:
    item = await db.scalar(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id, EvidenceItem.business_id == tenant.business_id
        )
    )
    if item is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return {
        "id": str(item.id),
        "kind": item.kind,
        "revision_id": str(item.revision_id),
        "incident_id": str(item.incident_id),
        "query_version": item.query_version,
        "params": item.params,
        "params_hash": item.params_hash,
        "result": item.result,
        "result_hash": item.result_hash,
        "intact": intact(item.result, item.result_hash),
        "created_at": item.created_at.isoformat(),
    }
