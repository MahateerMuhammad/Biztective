from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.core.db import DbSession
from app.core.pagination import Cursor, Limit, Page, decode_uuid_cursor, encode_cursor
from app.models import AuditLog
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}/audit-log", tags=["audit"])


class AuditEntryOut(BaseModel):
    id: UUID
    at: datetime
    action: str
    actor_user_id: UUID | None
    target_type: str | None
    target_id: str | None
    request_id: str | None
    metadata: dict[str, Any]


@router.get("")
async def list_audit_log(
    db: DbSession,
    tenant: Annotated[TenantContext, Depends(RequirePermission(Permission.AUDIT_READ))],
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[AuditEntryOut]:
    # UUIDv7 ids are time-ordered, so id order is chronological order.
    query = (
        select(AuditLog)
        .where(AuditLog.business_id == tenant.business_id)
        .order_by(AuditLog.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(AuditLog.id < decode_uuid_cursor(cursor))
    rows = list((await db.scalars(query)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[AuditEntryOut](
        items=[
            AuditEntryOut(
                id=row.id,
                at=row.at,
                action=row.action,
                actor_user_id=row.actor_user_id,
                target_type=row.target_type,
                target_id=row.target_id,
                request_id=row.request_id,
                metadata=row.metadata_,
            )
            for row in rows
        ],
        next_cursor=encode_cursor(str(rows[-1].id)) if has_more else None,
    )
