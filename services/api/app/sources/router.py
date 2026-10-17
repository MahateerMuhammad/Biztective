"""Read-only PostgreSQL sources: test, save (without the password), list and sync (owner only).

A sync extracts canonical CSV bytes and hands them to the same inspect/create_dataset path as a
file upload, so there is exactly one ingestion and analytics path.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.config import Settings
from app.core.db import DbSession
from app.core.errors import ApiError
from app.datasets import service
from app.ingestion.contract import FILE_KINDS, FileKind
from app.ingestion.upload import InspectedFile, UploadError, inspect_file
from app.models import Business, Dataset, SourceConnection
from app.sources.postgres import (
    SourceError,
    Target,
    allowed_hosts,
    check_identifier,
    extract,
    list_tables,
    validate_tables,
)
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}/sources", tags=["sources"])

SourceReader = Annotated[TenantContext, Depends(RequirePermission(Permission.DATASET_READ))]
SourceOwner = Annotated[TenantContext, Depends(RequirePermission(Permission.DATASET_UPLOAD))]
SYNCS_PER_HOUR = 10  # shares the SEC-20 dataset budget with uploads

Identifier = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
Host = Annotated[str, Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.-]+$")]


class ConnectionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: Host
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    username: str = Field(min_length=1, max_length=63)
    password: SecretStr = Field(min_length=1, max_length=200)
    schema_: Identifier = Field(default="public", alias="schema")


class SourceTables(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders: Identifier
    products: Identifier
    customers: Identifier
    returns: Identifier


class ConnectionCreate(ConnectionFields):
    tables: SourceTables


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(min_length=1, max_length=200)


class TestResult(BaseModel):
    ok: bool
    tables: list[str]


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    host: str
    port: int
    database: str
    username: str
    schema_name: str
    table_orders: str
    table_products: str
    table_customers: str
    table_returns: str
    created_at: datetime
    last_sync_at: datetime | None
    last_sync_status: Literal["succeeded", "failed"] | None
    last_sync_error: str | None
    last_dataset_id: UUID | None


class SyncResult(BaseModel):
    dataset_id: UUID | None
    status: Literal["created", "unchanged"]


def _target(request: Request, host: str, port: int, db: str, user: str, pw: SecretStr) -> Target:
    settings: Settings = request.app.state.settings
    return Target(
        host=host,
        port=port,
        database=db,
        username=user,
        password=pw.get_secret_value(),
        sslmode=settings.source_db_sslmode,
        allowed_hosts=allowed_hosts(settings.source_db_allowed_hosts),
    )


def _problem(exc: SourceError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message)


@router.post("/test")
async def test_connection(
    request: Request, tenant: SourceOwner, body: ConnectionFields
) -> TestResult:
    target = _target(request, body.host, body.port, body.database, body.username, body.password)
    try:
        tables = await asyncio.to_thread(list_tables, target, body.schema_)
    except SourceError as exc:
        raise _problem(exc) from None
    return TestResult(ok=True, tables=tables)


@router.post("", status_code=201)
async def create_connection(
    request: Request, db: DbSession, tenant: SourceOwner, body: ConnectionCreate
) -> ConnectionOut:
    target = _target(request, body.host, body.port, body.database, body.username, body.password)
    tables: dict[FileKind, str] = {
        "orders": body.tables.orders,
        "products": body.tables.products,
        "customers": body.tables.customers,
        "returns": body.tables.returns,
    }
    try:
        available = await asyncio.to_thread(list_tables, target, body.schema_)
        validate_tables(available, tables)
    except SourceError as exc:
        raise _problem(exc) from None
    connection = SourceConnection(
        business_id=tenant.business_id,
        kind="postgres",
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        schema_name=body.schema_,
        table_orders=tables["orders"],
        table_products=tables["products"],
        table_customers=tables["customers"],
        table_returns=tables["returns"],
        created_by=tenant.user.id,
    )
    db.add(connection)
    await db.flush()
    await record_audit(
        db,
        action="source.created",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="source_connection",
        target_id=str(connection.id),
        client_host=client_host(request),
        metadata={"kind": "postgres"},
    )
    await db.refresh(connection)
    out = ConnectionOut.model_validate(connection)
    await db.commit()
    return out


@router.get("")
async def list_connections(db: DbSession, tenant: SourceReader) -> list[ConnectionOut]:
    rows = await db.scalars(
        select(SourceConnection)
        .where(SourceConnection.business_id == tenant.business_id)
        .order_by(SourceConnection.created_at)
    )
    return [ConnectionOut.model_validate(r) for r in rows]


async def _connection(db: AsyncSession, business_id: UUID, source_id: UUID) -> SourceConnection:
    connection = await db.scalar(
        select(SourceConnection).where(
            SourceConnection.id == source_id, SourceConnection.business_id == business_id
        )
    )
    if connection is None:
        raise ApiError(404, "not_found", "Resource not found.")
    return connection


async def _record_failure(db: AsyncSession, connection: SourceConnection, code: str) -> None:
    # Nothing was written in this transaction yet; record the outcome and end it.
    connection.last_sync_at = datetime.now(UTC)
    connection.last_sync_status = "failed"
    connection.last_sync_error = code
    await db.commit()


@router.post("/{source_id}/sync")
async def sync_connection(
    request: Request, db: DbSession, tenant: SourceOwner, source_id: UUID, body: SyncRequest
) -> SyncResult:
    connection = await _connection(db, tenant.business_id, source_id)
    business = await db.scalar(select(Business).where(Business.id == tenant.business_id))
    if business is None:
        raise ApiError(404, "not_found", "Resource not found.")
    target = _target(
        request,
        connection.host,
        connection.port,
        connection.database,
        connection.username,
        body.password,
    )
    tables: dict[FileKind, str] = {
        "orders": check_identifier(connection.table_orders),
        "products": check_identifier(connection.table_products),
        "customers": check_identifier(connection.table_customers),
        "returns": check_identifier(connection.table_returns),
    }
    try:
        payloads = await asyncio.to_thread(extract, target, connection.schema_name, tables)
        inspected: dict[FileKind, InspectedFile] = {}
        for kind in FILE_KINDS:
            inspected[kind] = await asyncio.to_thread(
                inspect_file, kind, f"{kind}.csv", "text/csv", BytesIO(payloads[kind])
            )
    except SourceError as exc:
        await _record_failure(db, connection, exc.code)
        raise _problem(exc) from None
    except UploadError as exc:
        await _record_failure(db, connection, exc.code)
        raise ApiError(exc.status, exc.code, exc.message) from None

    await service.lock_business_uploads(db, tenant.business_id)
    recent = await db.scalar(
        select(func.count()).where(
            Dataset.business_id == tenant.business_id,
            Dataset.created_at > datetime.now(UTC) - timedelta(hours=1),
        )
    )
    if recent is not None and recent >= SYNCS_PER_HOUR:
        raise ApiError(
            429,
            "rate_limited",
            "Sync limit reached; try again later.",
            headers={"Retry-After": "3600"},
        )
    try:
        dataset_id: UUID | None = await service.create_dataset(
            db,
            tenant,
            files=inspected,
            source_timezone=business.timezone,
            business_timezone=business.timezone,
            currency=business.currency,
            client_host=client_host(request),
            source_type="postgres",
            source_connection_id=connection.id,
        )
        status: Literal["created", "unchanged"] = "created"
    except ApiError as exc:
        if exc.code != "duplicate_dataset":
            raise
        # create_dataset raises before writing, so the transaction is still usable.
        dataset_id, status = connection.last_dataset_id, "unchanged"
    connection.last_sync_at = datetime.now(UTC)
    connection.last_sync_status = "succeeded"
    connection.last_sync_error = None
    if dataset_id is not None:
        connection.last_dataset_id = dataset_id
    await record_audit(
        db,
        action="source.synced",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="source_connection",
        target_id=str(connection.id),
        client_host=client_host(request),
        metadata={"status": status, "dataset_id": str(dataset_id) if dataset_id else None},
    )
    await db.commit()
    return SyncResult(dataset_id=dataset_id, status=status)
