"""Read-only dataset metadata. Uploads arrive in Phase 4 (DATA_CONTRACTS §7.4)."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.db import DbSession
from app.core.errors import ApiError
from app.core.pagination import Cursor, Limit, Page, decode_int_cursor, encode_cursor
from app.models import Dataset
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}/datasets", tags=["datasets"])

DatasetReader = Annotated[TenantContext, Depends(RequirePermission(Permission.DATASET_READ))]


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    status: Literal["uploaded", "validating", "ready", "rejected", "deleted"]
    products_rows: int | None
    customers_rows: int | None
    sales_rows: int | None
    returns_rows: int | None
    date_min: date | None
    date_max: date | None
    created_at: datetime


@router.get("")
async def list_datasets(
    db: DbSession, tenant: DatasetReader, limit: Limit = 50, cursor: Cursor = None
) -> Page[DatasetOut]:
    query = (
        select(Dataset)
        .where(Dataset.business_id == tenant.business_id)
        .order_by(Dataset.version.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(Dataset.version < decode_int_cursor(cursor))
    rows = list((await db.scalars(query)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[DatasetOut](
        items=[DatasetOut.model_validate(row) for row in rows],
        next_cursor=encode_cursor(str(rows[-1].version)) if has_more else None,
    )


@router.get("/{dataset_id}")
async def get_dataset(db: DbSession, tenant: DatasetReader, dataset_id: UUID) -> DatasetOut:
    dataset = await db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.business_id == tenant.business_id)
    )
    if dataset is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return DatasetOut.model_validate(dataset)
