"""Recovery plans API (AMD-17, DATA_CONTRACTS §14.6).

Approve, reject, request changes and revise are separate routes so each declares its own
permission; approval is owner-only and enforced here, independently of any client. Requests
forbid extra fields: the approver, role, status and recorded hash come from the server.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.actions import service as actions
from app.analytics.evidence import sha256_of
from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.core.pagination import Cursor, decode_uuid_cursor, encode_cursor
from app.datasets import service as idempotency
from app.investigation.facts import detection_facts, entity_token
from app.investigation.render import Renderer
from app.investigation.store import jsonable
from app.models import (
    EvidenceItem,
    Investigation,
    PlanAction,
    PlanDecision,
    RecoveryPlan,
    RecoveryPlanVersion,
    User,
)
from app.recovery import plan, service
from app.recovery.catalog import ACTION_TYPES, CATALOG_VERSION
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}", tags=["recovery"])

Reader = Annotated[TenantContext, Depends(RequirePermission(Permission.INVESTIGATION_READ))]
Creator = Annotated[TenantContext, Depends(RequirePermission(Permission.PLAN_CREATE))]
Decider = Annotated[TenantContext, Depends(RequirePermission(Permission.PLAN_DECIDE))]
Approver = Annotated[TenantContext, Depends(RequirePermission(Permission.PLAN_APPROVE))]
OptionalKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Checkpoint = Annotated[str, Field(min_length=1, max_length=64)]
Reason = Annotated[str | None, Field(max_length=service.REASON_MAX)]


class CreatePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: plan.Constraints = Field(default_factory=plan.Constraints)


class Decision(BaseModel):
    """Binds the decision to one exact version, its content hash and the paused checkpoint."""

    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1, le=32767)]
    content_hash: Hash
    checkpoint_id: Checkpoint
    reason: Reason = None


class RequestChanges(Decision):
    edits: plan.PlanEdits


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1, le=32767)]
    content_hash: Hash
    edits: plan.PlanEdits
    reason: Reason = None


async def _plan(db: DbSession, tenant: TenantContext, plan_id: UUID) -> RecoveryPlan:
    found = await db.scalar(
        select(RecoveryPlan).where(
            RecoveryPlan.id == plan_id, RecoveryPlan.business_id == tenant.business_id
        )
    )
    if found is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return found


def _summary(p: RecoveryPlan) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "investigation_id": str(p.investigation_id),
        "investigation_version": p.investigation_version,
        "status": p.status,
        "current_version": p.current_version,
        "approved_version": p.approved_version,
        "error_code": p.error_code,
        "graph_version": p.graph_version,
        "catalog_version": p.catalog_version,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _version(v: RecoveryPlanVersion) -> dict[str, Any]:
    return {
        "version": v.version,
        "content_hash": v.content_hash,
        "intact": plan.content_hash(v.content) == v.content_hash,
        "valid": v.valid,
        "validation": v.validation,
        "caused_by_decision_id": str(v.caused_by_decision_id) if v.caused_by_decision_id else None,
        "created_by": str(v.created_by) if v.created_by else None,
        "created_at": v.created_at.isoformat(),
        "content": v.content,
    }


async def _presented(db: DbSession, v: RecoveryPlanVersion) -> dict[str, Any]:
    """The version with each rationale rendered into plain segments from the plan's own
    stored evidence (presentation only; the stored, hashed content is unchanged)."""
    shown = _version(v)
    shown["content"] = jsonable(v.content)  # a copy: the loaded row is never touched
    ids = {e for s in v.content["strategies"] for e in s["evidence"]}
    rows = list(await db.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(ids))))
    facts = {str(r.id): detection_facts(r.kind, r.result) for r in rows}
    labels = {
        entity_token(f["kind"], f["value"]): (f["kind"], f["value"])
        for item in facts.values()
        for f in item.values()
        if f.get("unit") == "entity" and isinstance(f.get("value"), str)
    }
    renderer = Renderer(facts, labels, v.content["currency"])
    for s in shown["content"]["strategies"]:
        s["rationale_segments"] = [renderer.segments(i["text"]) for i in s["rationale"]["items"]]
    return shown


async def _decisions(db: DbSession, p: RecoveryPlan) -> list[dict[str, Any]]:
    rows = list(
        await db.scalars(
            select(PlanDecision)
            .where(PlanDecision.plan_id == p.id)
            .order_by(PlanDecision.created_at, PlanDecision.id)
        )
    )
    people = {d.decided_by for d in rows}
    names = (
        {u.id: u.display_name for u in await db.scalars(select(User).where(User.id.in_(people)))}
        if people
        else {}
    )
    return [
        {
            "id": str(d.id),
            "decision": d.decision,
            "version": d.version,
            "content_hash": d.content_hash,
            "decided_by": {"id": str(d.decided_by), "display_name": names.get(d.decided_by)},
            "decided_role": d.decided_role,
            "reason": d.reason,  # plain text; clients must not interpret it
            "edits": d.edits,
            "created_at": d.created_at.isoformat(),
            "applied_at": d.applied_at.isoformat() if d.applied_at else None,
            "outcome": d.outcome,
            "refusal": d.refusal,
        }
        for d in rows
    ]


async def _execution(db: DbSession, p: RecoveryPlan) -> dict[str, Any]:
    integration = await actions.verified_integration(db, p.business_id)
    rows = await db.scalars(
        select(PlanAction).where(PlanAction.plan_id == p.id).order_by(PlanAction.created_at)
    )
    return {
        "integration_ready": integration is not None,
        "actions": [
            {
                "id": str(a.id),
                "plan_version": a.plan_version,
                "status": a.status,
                "error_code": a.error_code,
            }
            for a in rows
        ],
    }


@router.post("/investigations/{investigation_id}/recovery-plans", status_code=202)
async def create_plan(
    request: Request,
    db: DbSession,
    tenant: Creator,
    investigation_id: UUID,
    body: CreatePlan,
    idempotency_key: OptionalKey = None,
) -> JSONResponse:
    investigation = await db.scalar(
        select(Investigation).where(
            Investigation.id == investigation_id, Investigation.business_id == tenant.business_id
        )
    )
    if investigation is None:
        raise ApiError(404, "not_found", "Resource not found.")
    request_hash = sha256_of(
        {
            "operation": "plan.create",
            "investigation_id": str(investigation_id),
            **body.model_dump(mode="json"),
        }
    )
    if idempotency_key is not None:
        replay = await idempotency.replay_idempotent(db, tenant, idempotency_key, request_hash)
        if replay is not None:
            await db.commit()
            return JSONResponse(replay.body, status_code=replay.status)
    p = await service.create(
        db, investigation, user_id=tenant.user.id, constraints=body.constraints
    )
    await record_audit(
        db,
        action="plan.created",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="recovery_plan",
        target_id=str(p.id),
        client_host=client_host(request),
        metadata={"investigation_id": str(investigation_id)},
    )
    response = {"id": str(p.id), "status": p.status}
    if idempotency_key is not None:
        await idempotency.store_idempotent_response(
            db, tenant, idempotency_key, request_hash, idempotency.StoredResponse(202, response)
        )
    await db.commit()
    return JSONResponse(response, status_code=202)


@router.get("/investigations/{investigation_id}/recovery-plans")
async def list_plans(db: DbSession, tenant: Reader, investigation_id: UUID) -> dict[str, Any]:
    plans = await db.scalars(
        select(RecoveryPlan)
        .where(
            RecoveryPlan.investigation_id == investigation_id,
            RecoveryPlan.business_id == tenant.business_id,
        )
        .order_by(RecoveryPlan.created_at.desc())
    )
    return {"items": [_summary(p) for p in plans]}


@router.get("/recovery-plans")
async def list_business_plans(
    db: DbSession,
    tenant: Reader,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Cursor = None,
) -> dict[str, Any]:
    """Every plan of the business, newest first (the Decision Center index)."""
    query = (
        select(RecoveryPlan)
        .where(RecoveryPlan.business_id == tenant.business_id)
        .order_by(RecoveryPlan.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(RecoveryPlan.id < decode_uuid_cursor(cursor))
    rows = list(await db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    latest: dict[UUID, PlanAction] = {}
    if rows:
        for a in await db.scalars(
            select(PlanAction)
            .where(PlanAction.plan_id.in_([p.id for p in rows]))
            .order_by(PlanAction.created_at)
        ):
            latest[a.plan_id] = a
    return {
        "items": [
            {
                **_summary(p),
                "execution_status": latest[p.id].status if p.id in latest else None,
            }
            for p in rows
        ],
        "next_cursor": encode_cursor(str(rows[-1].id)) if more else None,
    }


@router.get("/recovery-plans/{plan_id}")
async def get_plan(db: DbSession, tenant: Reader, plan_id: UUID) -> dict[str, Any]:
    p = await _plan(db, tenant, plan_id)
    current = await db.scalar(
        select(RecoveryPlanVersion).where(
            RecoveryPlanVersion.plan_id == p.id, RecoveryPlanVersion.version == p.current_version
        )
    )
    decisions = await _decisions(db, p)
    decided = current is not None and any(
        d["version"] == current.version and d["decision"] != "revise" for d in decisions
    )
    awaiting = (
        {
            "version": current.version,
            "content_hash": current.content_hash,
            # Only a version that is waiting can be decided, and only at its checkpoint.
            "checkpoint_id": current.checkpoint_id,
            "can_approve": current.valid,
        }
        if current is not None
        and p.status == "awaiting_decision"
        and current.checkpoint_id is not None
        and not decided
        else None
    )
    approved = None
    if p.approved_version is not None:
        approval = next(
            d
            for d in reversed(decisions)
            if d["decision"] == "approve" and d["version"] == p.approved_version
        )
        approved = {
            "version": p.approved_version,
            "content_hash": approval["content_hash"],
            "approved_by": approval["decided_by"],
            "approved_at": approval["applied_at"],
        }
    return {
        **_summary(p),
        "current": await _presented(db, current) if current is not None else None,
        "awaiting": awaiting,
        "approved": approved,
        "decisions": decisions,
        "catalog": {
            "version": CATALOG_VERSION,
            "action_types": {k: dict(v) for k, v in ACTION_TYPES.items()},
        },
        # Approval records a decision only; an owner requests execution separately (AMD-18).
        "execution": await _execution(db, p),
    }


@router.get("/recovery-plans/{plan_id}/versions")
async def list_plan_versions(db: DbSession, tenant: Reader, plan_id: UUID) -> dict[str, Any]:
    p = await _plan(db, tenant, plan_id)
    versions = await db.scalars(
        select(RecoveryPlanVersion)
        .where(RecoveryPlanVersion.plan_id == p.id)
        .order_by(RecoveryPlanVersion.version)
    )
    return {
        "items": [
            {
                "version": v.version,
                "content_hash": v.content_hash,
                "valid": v.valid,
                "caused_by_decision_id": str(v.caused_by_decision_id)
                if v.caused_by_decision_id
                else None,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ]
    }


@router.get("/recovery-plans/{plan_id}/versions/{version}")
async def get_plan_version(
    db: DbSession, tenant: Reader, plan_id: UUID, version: int
) -> dict[str, Any]:
    p = await _plan(db, tenant, plan_id)
    found = await db.scalar(
        select(RecoveryPlanVersion).where(
            RecoveryPlanVersion.plan_id == p.id, RecoveryPlanVersion.version == version
        )
    )
    if found is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return _version(found)


async def _decide(
    request: Request,
    db: DbSession,
    tenant: TenantContext,
    plan_id: UUID,
    kind: service.DecisionKind,
    body: Decision,
    idempotency_key: str | None,
) -> JSONResponse:
    p = await _plan(db, tenant, plan_id)
    await db.refresh(p, with_for_update=True)  # serialize before any replay or decision
    request_hash = sha256_of(
        {"operation": f"plan.{kind}", "plan_id": str(plan_id), **body.model_dump(mode="json")}
    )
    if idempotency_key is not None:
        replay = await idempotency.replay_idempotent(db, tenant, idempotency_key, request_hash)
        if replay is not None:
            await db.commit()
            return JSONResponse(replay.body, status_code=replay.status)
    decision = await service.decide(
        db,
        p,
        kind=kind,
        user_id=tenant.user.id,
        role=tenant.role,
        version=body.version,
        content_hash=body.content_hash,
        checkpoint_id=body.checkpoint_id,
        reason=body.reason,
        edits=body.edits if isinstance(body, RequestChanges) else None,
    )
    action = {
        "approve": "plan.approved",
        "reject": "plan.rejected",
        "request_changes": "plan.changes_requested",
    }[kind]
    await record_audit(
        db,
        action=action,
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="recovery_plan",
        target_id=str(plan_id),
        client_host=client_host(request),
        metadata={
            "decision_id": str(decision.id),
            "version": decision.version,
            "content_hash": decision.content_hash,
            "reason": decision.reason is not None,  # never the text itself
        },
    )
    response = {
        "decision_id": str(decision.id),
        "decision": kind,
        "version": decision.version,
        "content_hash": decision.content_hash,
        "plan_status": "queued",
    }
    if idempotency_key is not None:
        await idempotency.store_idempotent_response(
            db, tenant, idempotency_key, request_hash, idempotency.StoredResponse(202, response)
        )
    await db.commit()
    return JSONResponse(response, status_code=202)


@router.post("/recovery-plans/{plan_id}/approve", status_code=202)
async def approve_plan(
    request: Request,
    db: DbSession,
    tenant: Approver,
    plan_id: UUID,
    body: Decision,
    idempotency_key: OptionalKey = None,
) -> JSONResponse:
    return await _decide(request, db, tenant, plan_id, "approve", body, idempotency_key)


@router.post("/recovery-plans/{plan_id}/reject", status_code=202)
async def reject_plan(
    request: Request,
    db: DbSession,
    tenant: Decider,
    plan_id: UUID,
    body: Decision,
    idempotency_key: OptionalKey = None,
) -> JSONResponse:
    return await _decide(request, db, tenant, plan_id, "reject", body, idempotency_key)


@router.post("/recovery-plans/{plan_id}/request-changes", status_code=202)
async def request_plan_changes(
    request: Request,
    db: DbSession,
    tenant: Decider,
    plan_id: UUID,
    body: RequestChanges,
    idempotency_key: OptionalKey = None,
) -> JSONResponse:
    return await _decide(request, db, tenant, plan_id, "request_changes", body, idempotency_key)


@router.post("/recovery-plans/{plan_id}/revisions", status_code=202)
async def revise_plan(
    request: Request,
    db: DbSession,
    tenant: Decider,
    plan_id: UUID,
    body: Revision,
    idempotency_key: OptionalKey = None,
) -> JSONResponse:
    p = await _plan(db, tenant, plan_id)
    await db.refresh(p, with_for_update=True)
    request_hash = sha256_of(
        {"operation": "plan.revise", "plan_id": str(plan_id), **body.model_dump(mode="json")}
    )
    if idempotency_key is not None:
        replay = await idempotency.replay_idempotent(db, tenant, idempotency_key, request_hash)
        if replay is not None:
            await db.commit()
            return JSONResponse(replay.body, status_code=replay.status)
    version = await service.revise_approved(
        db,
        p,
        user_id=tenant.user.id,
        role=tenant.role,
        version=body.version,
        content_hash=body.content_hash,
        reason=body.reason,
        edits=body.edits,
    )
    await record_audit(
        db,
        action="plan.revised",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="recovery_plan",
        target_id=str(plan_id),
        client_host=client_host(request),
        metadata={
            "from_version": body.version,
            "to_version": version.version,
            "content_hash": version.content_hash,
            "approval_invalidated": True,
        },
    )
    response = {
        "version": version.version,
        "content_hash": version.content_hash,
        "plan_status": "queued",
        "approval_invalidated": True,
    }
    if idempotency_key is not None:
        await idempotency.store_idempotent_response(
            db, tenant, idempotency_key, request_hash, idempotency.StoredResponse(202, response)
        )
    await db.commit()
    return JSONResponse(response, status_code=202)
