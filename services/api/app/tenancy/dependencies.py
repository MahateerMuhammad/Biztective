"""Business-scoped authorization: membership, permission and RLS context (SEC-05, SEC-06)."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from fastapi import Request
from sqlalchemy import select

from app.audit.writer import record_audit
from app.auth.dependencies import CurrentUser, client_host
from app.auth.service import AuthenticatedUser
from app.core.db import DbSession, set_rls_context
from app.core.errors import ApiError
from app.models import Business, Membership
from app.tenancy.permissions import ROLE_PERMISSIONS, Permission, Role


@dataclass(frozen=True)
class TenantContext:
    user: AuthenticatedUser
    business_id: UUID
    role: Role


class RequirePermission:
    """Dependency for every /businesses/{business_id}/… route.

    Non-members get 404 so resource existence is not disclosed; members lacking the
    permission get 403. Both outcomes are audited. On success the transaction's RLS
    context is scoped to the business.
    """

    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    async def __call__(
        self, business_id: UUID, request: Request, db: DbSession, user: CurrentUser
    ) -> TenantContext:
        await set_rls_context(db, user_id=user.id)
        role = await db.scalar(
            select(Membership.role)
            .join(Business, Business.id == Membership.business_id)
            .where(
                Membership.business_id == business_id,
                Membership.user_id == user.id,
                Business.deleted_at.is_(None),
            )
        )
        if role is None:
            await self._deny(db, request, user, None, business_id, "not_member")
            raise ApiError(404, "not_found", "Resource not found.")
        typed_role = cast(Role, role)
        if self.permission not in ROLE_PERMISSIONS[typed_role]:
            await set_rls_context(db, user_id=user.id, business_id=business_id)
            await self._deny(db, request, user, business_id, business_id, "missing_permission")
            raise ApiError(403, "forbidden", "You do not have permission for this action.")
        await set_rls_context(db, user_id=user.id, business_id=business_id)
        return TenantContext(user=user, business_id=business_id, role=typed_role)

    async def _deny(
        self,
        db: DbSession,
        request: Request,
        user: AuthenticatedUser,
        audit_business_id: UUID | None,
        requested_business_id: UUID,
        reason: str,
    ) -> None:
        await record_audit(
            db,
            action="authz.denied",
            actor_user_id=user.id,
            business_id=audit_business_id,
            target_type="route",
            target_id=request.url.path,
            client_host=client_host(request),
            metadata={
                "permission": self.permission.value,
                "reason": reason,
                "requested_business_id": str(requested_business_id),
            },
        )
        await db.commit()
