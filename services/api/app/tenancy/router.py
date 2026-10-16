from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.core.db import DbSession
from app.core.errors import ApiError
from app.models import Business
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission, Role

router = APIRouter(prefix="/businesses", tags=["businesses"])


class BusinessOut(BaseModel):
    id: UUID
    name: str
    currency: str
    timezone: str
    role: Role
    created_at: datetime


@router.get("/{business_id}")
async def get_business(
    db: DbSession,
    tenant: Annotated[TenantContext, Depends(RequirePermission(Permission.BUSINESS_READ))],
) -> BusinessOut:
    business = await db.scalar(
        select(Business).where(Business.id == tenant.business_id, Business.deleted_at.is_(None))
    )
    if business is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return BusinessOut(
        id=business.id,
        name=business.name,
        currency=business.currency,
        timezone=business.timezone,
        role=tenant.role,
        created_at=business.created_at,
    )
