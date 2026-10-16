"""Investigations API (DATA_CONTRACTS §12.6): start, list, detail, events, retry, evidence.

Any annotations represent heterogeneous JSON response fields; incoming requests instead
use explicit Pydantic models with forbidden extra fields.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.analytics.evidence import sha256_of
from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.datasets import service as idempotency
from app.investigation import output, service
from app.investigation.critic import intact
from app.models import EvidenceItem, Investigation, InvestigationEvent, LlmCall
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}", tags=["investigations"])

Reader = Annotated[TenantContext, Depends(RequirePermission(Permission.INVESTIGATION_READ))]
Runner = Annotated[TenantContext, Depends(RequirePermission(Permission.INVESTIGATION_RUN))]
IdempotencyKeyHeader = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
]


class StartInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID


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
) -> dict[str, Any]:
    query = (
        select(Investigation)
        .where(Investigation.business_id == tenant.business_id)
        .order_by(Investigation.created_at.desc(), Investigation.id.desc())
        .limit(limit)
    )
    if dataset_id is not None:
        query = query.where(Investigation.dataset_id == dataset_id)
    return {"items": [_summary(i) for i in await db.scalars(query)]}


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
    return {
        **_summary(investigation),
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
