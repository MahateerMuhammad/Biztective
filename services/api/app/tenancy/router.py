from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import exists, select, update

from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.models import Business, Dataset
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


class BusinessUpdate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _at_least_one(self) -> BusinessUpdate:
        if self.currency is None and self.timezone is None:
            raise ValueError("Provide currency and/or timezone.")
        if self.timezone is not None and self.timezone not in available_timezones():
            raise ValueError("timezone must be an IANA time zone name.")
        return self


@router.patch("/{business_id}")
async def update_business(
    request: Request,
    db: DbSession,
    tenant: Annotated[TenantContext, Depends(RequirePermission(Permission.BUSINESS_UPDATE))],
    body: BusinessUpdate,
) -> BusinessOut:
    """Business currency and time zone (AMD-8). Currency is fixed once any dataset exists."""
    business = await db.scalar(select(Business).where(Business.id == tenant.business_id))
    if business is None:
        raise ApiError(404, "not_found", "Resource not found.")
    changes: dict[str, str] = {}
    if body.currency is not None and body.currency != business.currency:
        has_datasets = await db.scalar(
            select(exists().where(Dataset.business_id == tenant.business_id))
        )
        if has_datasets:
            raise ApiError(
                409, "currency_locked", "Currency cannot change after datasets were uploaded."
            )
        changes["currency"] = body.currency
    if body.timezone is not None and body.timezone != business.timezone:
        changes["timezone"] = body.timezone
    if changes:
        await db.execute(update(Business).where(Business.id == business.id).values(**changes))
        await record_audit(
            db,
            action="business.updated",
            actor_user_id=tenant.user.id,
            business_id=tenant.business_id,
            target_type="business",
            target_id=str(business.id),
            client_host=client_host(request),
            metadata={"changed": sorted(changes)},
        )
    response = BusinessOut(
        id=business.id,
        name=business.name,
        currency=changes.get("currency", business.currency),
        timezone=changes.get("timezone", business.timezone),
        role=tenant.role,
        created_at=business.created_at,
    )
    await db.commit()
    return response
