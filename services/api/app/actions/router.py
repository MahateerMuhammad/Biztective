"""Integrations and action-execution API (AMD-18, DATA_CONTRACTS §7.8–§7.9 as amended).

No route accepts a destination, range, payload or status from the client: execution names only
the approved version and its hash; the server builds the payload and takes the destination
from the business's verified binding. Responses never contain credentials or codes.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.actions import service
from app.actions.executor import ActionRuntime
from app.actions.sheets import SheetsError, spreadsheet_id, spreadsheet_url
from app.analytics.evidence import sha256_of
from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.datasets import service as idempotency
from app.models import ActionExecution, AuditLog, Integration, PlanAction, RecoveryPlan
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}", tags=["actions"])

Reader = Annotated[TenantContext, Depends(RequirePermission(Permission.INVESTIGATION_READ))]
Executor = Annotated[TenantContext, Depends(RequirePermission(Permission.ACTION_EXECUTE))]
Retrier = Annotated[TenantContext, Depends(RequirePermission(Permission.ACTION_RETRY))]
IntegrationReader = Annotated[
    TenantContext, Depends(RequirePermission(Permission.INTEGRATION_READ))
]
IntegrationManager = Annotated[
    TenantContext, Depends(RequirePermission(Permission.INTEGRATION_MANAGE))
]
OptionalKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
]
VERIFY_RANGE = "'ROOT_VERIFY'!A1"


class Execute(BaseModel):
    """Binds the request to the approved version and its content hash; nothing else."""

    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1, le=32767)]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BindSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spreadsheet: Annotated[str, Field(min_length=20, max_length=300)]


def _runtime(request: Request) -> ActionRuntime:
    runtime: ActionRuntime = request.app.state.actions
    return runtime


def _summary(a: PlanAction) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "plan_id": str(a.plan_id),
        "plan_version": a.plan_version,
        "content_hash": a.content_hash,
        "approval_id": str(a.approval_id),
        "kind": a.kind,
        "status": a.status,
        "error_code": a.error_code,
        "attempt_count": a.attempts,
        "payload_hash": a.payload_hash,
        "destination": {
            **a.destination,
            "url": spreadsheet_url(a.destination["spreadsheet_id"]),
        },
        "rows": {t["title"]: len(t["rows"]) for t in a.payload["tabs"]},
        "not_executed": a.payload["not_executed"],
        "external_ref": a.external_ref,
        "verification": a.verification,
        "requested_by": str(a.requested_by),
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "finished_at": a.finished_at.isoformat() if a.finished_at else None,
    }


async def _action(db: DbSession, tenant: TenantContext, action_id: UUID) -> PlanAction:
    found = await db.scalar(
        select(PlanAction).where(
            PlanAction.id == action_id, PlanAction.business_id == tenant.business_id
        )
    )
    if found is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return found


@router.post("/recovery-plans/{plan_id}/actions", status_code=202)
async def execute_plan(
    request: Request,
    db: DbSession,
    tenant: Executor,
    plan_id: UUID,
    body: Execute,
    idempotency_key: OptionalKey = None,
) -> JSONResponse:
    p = await db.scalar(
        select(RecoveryPlan).where(
            RecoveryPlan.id == plan_id, RecoveryPlan.business_id == tenant.business_id
        )
    )
    if p is None:
        raise ApiError(404, "not_found", "Resource not found.")
    await db.refresh(p, with_for_update=True)
    request_hash = sha256_of(
        {"operation": "action.execute", "plan_id": str(plan_id), **body.model_dump(mode="json")}
    )
    if idempotency_key is not None:
        replay = await idempotency.replay_idempotent(db, tenant, idempotency_key, request_hash)
        if replay is not None:
            await db.commit()
            return JSONResponse(replay.body, status_code=replay.status)
    action, created = await service.request_execution(
        db, p, user_id=tenant.user.id, version=body.version, content_hash=body.content_hash
    )
    if created:
        await record_audit(
            db,
            action="action.requested",
            actor_user_id=tenant.user.id,
            business_id=tenant.business_id,
            target_type="plan_action",
            target_id=str(action.id),
            client_host=client_host(request),
            metadata={
                "plan_id": str(plan_id),
                "version": action.plan_version,
                "content_hash": action.content_hash,
                "payload_hash": action.payload_hash,
                "spreadsheet_id": action.destination["spreadsheet_id"],
            },
        )
    response = {"id": str(action.id), "status": action.status, "created": created}
    status = 202 if created else 200
    if idempotency_key is not None:
        await idempotency.store_idempotent_response(
            db, tenant, idempotency_key, request_hash, idempotency.StoredResponse(status, response)
        )
    await db.commit()
    return JSONResponse(response, status_code=status)


@router.get("/recovery-plans/{plan_id}/actions")
async def list_actions(db: DbSession, tenant: Reader, plan_id: UUID) -> dict[str, Any]:
    rows = await db.scalars(
        select(PlanAction)
        .where(PlanAction.plan_id == plan_id, PlanAction.business_id == tenant.business_id)
        .order_by(PlanAction.created_at)
    )
    return {"items": [_summary(a) for a in rows]}


@router.get("/actions/{action_id}")
async def get_action(db: DbSession, tenant: Reader, action_id: UUID) -> dict[str, Any]:
    a = await _action(db, tenant, action_id)
    attempts = await db.scalars(
        select(ActionExecution)
        .where(ActionExecution.action_id == a.id)
        .order_by(ActionExecution.attempt)
    )
    audit = await db.scalars(
        select(AuditLog)
        .where(
            AuditLog.business_id == tenant.business_id,
            AuditLog.target_type == "plan_action",
            AuditLog.target_id == str(a.id),
        )
        .order_by(AuditLog.at, AuditLog.id)
    )
    return {
        **_summary(a),
        "payload": a.payload,
        "attempts": [
            {
                "attempt": e.attempt,
                "status": e.status,
                "error_code": e.error_code,
                "writes_sent": e.writes_sent,
                "calls": e.calls,
                "started_at": e.started_at.isoformat(),
                "finished_at": e.finished_at.isoformat() if e.finished_at else None,
            }
            for e in attempts
        ],
        "audit": [
            {
                "action": r.action,
                "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
                "at": r.at.isoformat(),
                "metadata": r.metadata_,
            }
            for r in audit
        ],
    }


@router.post("/actions/{action_id}/retry", status_code=202)
async def retry_action(
    request: Request, db: DbSession, tenant: Retrier, action_id: UUID
) -> JSONResponse:
    a = await _action(db, tenant, action_id)
    queued = await service.request_retry(db, a)
    if queued:
        await record_audit(
            db,
            action="action.retry_requested",
            actor_user_id=tenant.user.id,
            business_id=tenant.business_id,
            target_type="plan_action",
            target_id=str(a.id),
            client_host=client_host(request),
            metadata={"plan_id": str(a.plan_id), "after_attempts": a.attempts},
        )
    await db.commit()
    return JSONResponse({"id": str(a.id), "status": "pending", "queued": queued}, status_code=202)


# ------------------------------------------------------------------------ integrations


def _integration(i: Integration, runtime: ActionRuntime) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "kind": i.kind,
        "status": i.status,
        "spreadsheet_id": i.spreadsheet_id,
        "spreadsheet_url": spreadsheet_url(i.spreadsheet_id),
        "service_account_email": runtime.account.email if runtime.account else None,
        "verify_expires_at": i.verify_expires_at.isoformat()
        if i.verify_expires_at and i.status == "pending_verification"
        else None,
        "verified_at": i.verified_at.isoformat() if i.verified_at else None,
        "created_at": i.created_at.isoformat(),
    }


async def _owned(db: DbSession, tenant: TenantContext, integration_id: UUID) -> Integration:
    found = await db.scalar(
        select(Integration).where(
            Integration.id == integration_id, Integration.business_id == tenant.business_id
        )
    )
    if found is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return found


@router.get("/integrations")
async def list_integrations(
    request: Request, db: DbSession, tenant: IntegrationReader
) -> dict[str, Any]:
    runtime = _runtime(request)
    rows = await db.scalars(
        select(Integration)
        .where(Integration.business_id == tenant.business_id)
        .order_by(Integration.created_at.desc())
    )
    return {
        "available": runtime.account is not None,
        "items": [_integration(i, runtime) for i in rows],
    }


@router.post("/integrations/google-sheets", status_code=201)
async def bind_google_sheet(
    request: Request, db: DbSession, tenant: IntegrationManager, body: BindSheet
) -> JSONResponse:
    runtime = _runtime(request)
    if runtime.account is None:
        raise ApiError(503, "integration_unavailable", "Google Sheets is not configured.")
    sid = spreadsheet_id(body.spreadsheet)
    if sid is None:
        raise ApiError(
            422,
            "validation_error",
            "Enter a Google Sheets link or spreadsheet ID.",
            errors=[{"field": "spreadsheet", "code": "invalid", "message": "Not a spreadsheet."}],
        )
    integration, code = await service.bind_spreadsheet(db, tenant.business_id, tenant.user.id, sid)
    await record_audit(
        db,
        action="integration.created",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="integration",
        target_id=str(integration.id),
        client_host=client_host(request),
        metadata={"kind": "google_sheets", "spreadsheet_id": sid},
    )
    await db.commit()
    return JSONResponse(
        {
            **_integration(integration, runtime),
            # Shown once; only its hash is stored.
            "verification_code": code,
            "verify_cell": VERIFY_RANGE,
        },
        status_code=201,
    )


@router.post("/integrations/{integration_id}/verify")
async def verify_google_sheet(
    request: Request, db: DbSession, tenant: IntegrationManager, integration_id: UUID
) -> dict[str, Any]:
    runtime = _runtime(request)
    integration = await _owned(db, tenant, integration_id)
    await db.refresh(integration, with_for_update=True)
    problem = service.state_problem(integration)
    if problem is None:
        try:
            async with runtime.client() as client:
                values = await client.read(integration.spreadsheet_id, [VERIFY_RANGE])
        except SheetsError as exc:
            problem = exc.code
        else:
            cell = values[0][0][0] if values and values[0] and values[0][0] else None
            problem = service.code_problem(integration, cell)
    if problem is None:
        try:
            await db.execute(
                update(Integration)
                .where(Integration.id == integration.id)
                .values(
                    status="verified",
                    verified_at=func.now(),
                    verify_code_hash=None,
                    verify_expires_at=None,
                    updated_at=func.now(),
                )
            )
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError(
                409,
                "integration_already_bound",
                "This spreadsheet is already connected to another account.",
            ) from exc
    await record_audit(
        db,
        action="integration.verified" if problem is None else "integration.verification_failed",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="integration",
        target_id=str(integration.id),
        client_host=client_host(request),
        metadata={"reason": problem} if problem else {},
    )
    await db.refresh(integration)
    shown = _integration(integration, runtime)
    await db.commit()
    if problem is not None:
        raise ApiError(
            422,
            "verification_failed",
            "The spreadsheet does not prove ownership yet. Share it with the service account "
            "and type the code into cell A1 of a tab named ROOT_VERIFY.",
            errors=[{"field": "verification", "code": problem, "message": problem}],
        )
    return shown


@router.delete("/integrations/{integration_id}")
async def revoke_integration(
    request: Request, db: DbSession, tenant: IntegrationManager, integration_id: UUID
) -> dict[str, Any]:
    integration = await _owned(db, tenant, integration_id)
    await db.execute(
        update(Integration)
        .where(Integration.id == integration.id)
        .values(status="revoked", verify_code_hash=None, updated_at=func.now())
    )
    await record_audit(
        db,
        action="integration.revoked",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="integration",
        target_id=str(integration.id),
        client_host=client_host(request),
    )
    await db.refresh(integration)
    shown = _integration(integration, _runtime(request))
    await db.commit()
    return shown
