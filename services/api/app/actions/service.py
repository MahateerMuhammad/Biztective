"""Execution requests, retries and spreadsheet bindings (AMD-18).

`authorization_problem` is the single statement of who may cause an external effect; the
request handlers and the executor both call it, the executor again before every write.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions import payload as payloads
from app.core.errors import ApiError
from app.jobs import queue
from app.models import (
    Integration,
    Membership,
    PlanAction,
    PlanDecision,
    RecoveryPlan,
    RecoveryPlanVersion,
)

CODE_TTL = timedelta(minutes=30)
CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
RETRYABLE = frozenset({"failed", "manual_review"})


def utcnow() -> datetime:
    return datetime.now(UTC)


def code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def new_code() -> str:
    return "ROOT-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))


async def verified_integration(session: AsyncSession, business_id: uuid.UUID) -> Integration | None:
    found: Integration | None = await session.scalar(
        select(Integration).where(
            Integration.business_id == business_id,
            Integration.kind == "google_sheets",
            Integration.status == "verified",
        )
    )
    return found


async def _role(session: AsyncSession, business_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    """The user's current role here. Memberships are visible only to their own user (RLS), so
    the user context is switched for this read and then restored."""
    previous = await session.scalar(text("SELECT current_setting('app.current_user_id', true)"))
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(user_id)}
    )
    role: str | None = await session.scalar(
        select(Membership.role).where(
            Membership.business_id == business_id, Membership.user_id == user_id
        )
    )
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": previous or ""}
    )
    return role


async def _approval(
    session: AsyncSession, plan_id: uuid.UUID, version: int, content_hash: str
) -> PlanDecision | None:
    found: PlanDecision | None = await session.scalar(
        select(PlanDecision).where(
            PlanDecision.plan_id == plan_id,
            PlanDecision.version == version,
            PlanDecision.content_hash == content_hash,
            PlanDecision.decision == "approve",
            PlanDecision.outcome == "applied",
        )
    )
    return found


async def authorization_problem(session: AsyncSession, action: PlanAction) -> str | None:
    """Why this action may not touch the destination now, or None."""
    p = await session.get(RecoveryPlan, action.plan_id, populate_existing=True)
    if p is None or p.business_id != action.business_id:
        return "plan_missing"
    if (
        p.status != "approved"
        or p.approved_version != action.plan_version
        or p.current_version != action.plan_version
    ):
        return "approval_no_longer_valid"
    stored = await session.scalar(
        select(RecoveryPlanVersion.content_hash).where(
            RecoveryPlanVersion.plan_id == p.id, RecoveryPlanVersion.version == action.plan_version
        )
    )
    if stored != action.content_hash:
        return "content_changed"
    approval = await _approval(session, p.id, action.plan_version, action.content_hash)
    if approval is None or approval.id != action.approval_id:
        return "approval_no_longer_valid"
    if await _role(session, p.business_id, approval.decided_by) != "owner":
        return "approver_not_owner"
    if await _role(session, p.business_id, action.requested_by) != "owner":
        return "requester_not_owner"
    integration = await session.get(Integration, action.integration_id, populate_existing=True)
    if (
        integration is None
        or integration.status != "verified"
        or integration.business_id != action.business_id
        or integration.spreadsheet_id != action.destination["spreadsheet_id"]
    ):
        return "integration_not_verified"
    return None


def _enqueue(session: AsyncSession, action: PlanAction, cause: str) -> Any:
    return queue.enqueue(
        session,
        job_type="execute_action",
        business_id=action.business_id,
        payload={"action_id": str(action.id)},
        dedupe_key=f"execute_action:{action.id}:{cause}",
    )


async def request_execution(
    session: AsyncSession,
    p: RecoveryPlan,
    *,
    user_id: uuid.UUID,
    version: int,
    content_hash: str,
) -> tuple[PlanAction, bool]:
    """The action for the approved version, created and queued once. Returns (action, created).
    The plan row is locked first, so concurrent requests are serialized."""
    await session.refresh(p, with_for_update=True)
    if p.status != "approved" or p.approved_version is None:
        raise ApiError(409, "plan_not_approved", "Only an approved plan can be executed.")
    if version != p.approved_version:
        raise ApiError(409, "stale_plan_version", "The approved version has changed; reload it.")
    current = await session.scalar(
        select(RecoveryPlanVersion).where(
            RecoveryPlanVersion.plan_id == p.id, RecoveryPlanVersion.version == version
        )
    )
    if current is None or content_hash != current.content_hash:
        raise ApiError(
            409, "content_hash_mismatch", "The plan content does not match the approval."
        )
    approval = await _approval(session, p.id, version, content_hash)
    if approval is None or approval.applied_at is None:
        raise ApiError(409, "plan_not_approved", "Only an approved plan can be executed.")
    action_id = payloads.action_id(p.id, version, content_hash)
    existing = await session.get(PlanAction, action_id)
    if existing is not None:
        return existing, False
    integration = await verified_integration(session, p.business_id)
    if integration is None:
        raise ApiError(
            409, "integration_not_ready", "Connect and verify a Google Sheet before executing."
        )
    try:
        body = payloads.build(
            action=action_id,
            plan_id=p.id,
            version=version,
            content_hash=content_hash,
            content=current.content,
            approval_id=approval.id,
            approved_at=approval.applied_at.astimezone(UTC).isoformat(),
        )
    except payloads.NothingToExecuteError as exc:
        raise ApiError(
            409,
            "nothing_to_execute",
            "No selected strategy has an action that Biztective can carry out.",
        ) from exc
    except payloads.TooManyRowsError as exc:
        raise ApiError(
            409, "too_many_rows", f"The plan would write more than {payloads.MAX_ROWS} rows."
        ) from exc
    digest = payloads.payload_hash(body)
    await session.execute(
        insert(PlanAction)
        .values(
            id=action_id,
            business_id=p.business_id,
            plan_id=p.id,
            plan_version=version,
            content_hash=content_hash,
            approval_id=approval.id,
            kind=payloads.KIND,
            integration_id=integration.id,
            destination={
                "spreadsheet_id": integration.spreadsheet_id,
                "tabs": [t["title"] for t in body["tabs"]],
            },
            payload=body,
            payload_hash=digest,
            idempotency_key=payloads.idempotency_key(action_id, digest),
            status="pending",
            requested_by=user_id,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    action = await session.get(PlanAction, action_id, populate_existing=True)
    if action is None:  # pragma: no cover - inserted above
        raise RuntimeError("plan action vanished")
    problem = await authorization_problem(session, action)
    if problem is not None:
        raise ApiError(409, problem, "This plan cannot be executed now.")
    await _enqueue(session, action, "1")
    return action, True


async def request_retry(session: AsyncSession, action: PlanAction) -> bool:
    """Queue another attempt (it always reconciles first). Returns False when one is already
    pending."""
    await session.refresh(action, with_for_update=True)
    if action.status == "pending":
        return False
    if action.status not in RETRYABLE:
        raise ApiError(409, "invalid_state", "Only a failed or held action can be retried.")
    problem = await authorization_problem(session, action)
    if problem is not None:
        raise ApiError(409, problem, "This action can no longer be executed.")
    await session.execute(
        update(PlanAction)
        .where(PlanAction.id == action.id)
        .values(status="pending", error_code=None, finished_at=None, updated_at=func.now())
    )
    await _enqueue(session, action, f"retry:{action.attempts}")
    return True


# ------------------------------------------------------------------------- integrations


async def bind_spreadsheet(
    session: AsyncSession, business_id: uuid.UUID, user_id: uuid.UUID, sid: str
) -> tuple[Integration, str]:
    if await verified_integration(session, business_id) is not None:
        raise ApiError(
            409,
            "integration_exists",
            "This business already has a verified spreadsheet; disconnect it first.",
        )
    code = new_code()
    integration = Integration(
        id=uuid.uuid7(),
        business_id=business_id,
        kind="google_sheets",
        status="pending_verification",
        spreadsheet_id=sid,
        verify_code_hash=code_hash(code),
        verify_expires_at=utcnow() + CODE_TTL,
        created_by=user_id,
    )
    session.add(integration)
    await session.flush()
    await session.refresh(integration)
    return integration, code


def state_problem(integration: Integration) -> str | None:
    """Why this binding cannot be verified now, before reading anything remote."""
    if integration.status != "pending_verification" or integration.verify_code_hash is None:
        return "code_used"
    if integration.verify_expires_at is None or integration.verify_expires_at <= utcnow():
        return "code_expired"
    return None


def code_problem(integration: Integration, found: str | None) -> str | None:
    """Why the code read from the spreadsheet does not prove ownership, or None."""
    expected = integration.verify_code_hash or ""
    if found is None or not hmac.compare_digest(code_hash(found), expected):
        return "code_mismatch"
    return state_problem(integration)
