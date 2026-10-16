"""Dataset upload, mapping confirmation, quality report, records and safe export (AMD-8)."""

import asyncio
import csv
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import IO, Annotated, Any, Literal, cast
from uuid import UUID
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.core.pagination import Cursor, Limit, Page, decode_int_cursor, encode_cursor
from app.datasets import service
from app.ingestion.contract import FILE_KINDS, FileKind
from app.ingestion.export import safe_cell
from app.ingestion.upload import InspectedFile, UploadError, inspect_file
from app.models import (
    Business,
    Customer,
    Dataset,
    DatasetFile,
    OrderLine,
    Product,
    Return,
    ValidationIssue,
)
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}/datasets", tags=["datasets"])

DatasetReader = Annotated[TenantContext, Depends(RequirePermission(Permission.DATASET_READ))]
DatasetUploader = Annotated[TenantContext, Depends(RequirePermission(Permission.DATASET_UPLOAD))]
IdempotencyKeyHeader = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
]

DatasetStatus = Literal["awaiting_mapping", "queued", "validating", "ready", "rejected", "failed"]
RecordKind = Literal["orders", "products", "customers", "returns"]
ExportKind = Literal["orders", "products", "customers", "returns", "issues"]
UPLOADS_PER_HOUR = 10  # SEC-20

RECORD_MODELS: dict[RecordKind, Any] = {
    "products": Product,
    "customers": Customer,
    "orders": OrderLine,
    "returns": Return,
}
RECORD_COLUMNS: dict[RecordKind, tuple[str, ...]] = {
    "products": ("product_id", "product_name", "category", "unit_cost", "list_price"),
    "customers": ("customer_id", "signup_date", "region", "acquisition_channel"),
    "orders": (
        "order_line_id",
        "order_id",
        "order_date",
        "ordered_at",
        "customer_id",
        "product_id",
        "quantity",
        "unit_price",
        "discount_amount",
        "channel",
        "region",
        "status",
    ),
    "returns": (
        "return_id",
        "order_line_id",
        "return_date",
        "returned_at",
        "quantity_returned",
        "refund_amount",
        "reason_code",
        "reason_text",
    ),
}
ISSUE_COLUMNS = ("file_kind", "row_number", "column_name", "code", "severity", "message", "excerpt")


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    status: DatasetStatus
    products_rows: int | None
    customers_rows: int | None
    orders_rows: int | None
    returns_rows: int | None
    date_min: date | None
    date_max: date | None
    created_at: datetime


class DatasetFileOut(BaseModel):
    kind: FileKind
    filename: str
    size_bytes: int
    sha256: str
    headers: list[str]
    mapping: dict[str, Any]
    mapping_confirmed_at: datetime | None
    rows_total: int | None
    rows_accepted: int | None
    rows_rejected: int | None
    rows_duplicate: int | None
    profile: dict[str, Any] | None


class IssueSummaryOut(BaseModel):
    file: str
    code: str
    severity: Literal["error", "warning"]
    description: str
    count: int


class DatasetDetail(DatasetOut):
    source_timezone: str
    business_timezone: str
    currency: str
    checksum_sha256: str | None
    error_code: str | None
    completed_at: datetime | None
    files: list[DatasetFileOut]
    issues: list[IssueSummaryOut]


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_kind: str
    row_number: int | None
    column_name: str | None
    code: str
    severity: Literal["error", "warning"]
    message: str
    excerpt: str | None


class MappingConfirmation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    files: dict[FileKind, Annotated[dict[str, str | None], Field(max_length=20)]] = Field(
        min_length=1
    )


class RecordPage(BaseModel):
    items: list[dict[str, str | int | None]]
    next_cursor: str | None


def _upload_problem(exc: UploadError) -> ApiError:
    return ApiError(
        exc.status, exc.code, exc.message, errors=_details_as_errors(exc.code, exc.details)
    )


def _details_as_errors(code: str, details: list[dict[str, str]]) -> list[dict[str, str]]:
    errors = []
    for d in details:
        message = d["problem"]
        if d.get("accepted_headers"):
            message += f" (accepted headers: {d['accepted_headers']})"
        field = ".".join(part for part in (d.get("file", ""), d.get("column", "")) if part)
        errors.append({"field": field or "files", "code": code, "message": message})
    return errors


async def _detail(db: AsyncSession, business_id: UUID, dataset_id: UUID) -> DatasetDetail:
    dataset = await db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.business_id == business_id)
    )
    if dataset is None:
        raise ApiError(404, "not_found", "Resource not found.")
    files = (
        await db.scalars(
            select(DatasetFile)
            .where(DatasetFile.dataset_id == dataset_id)
            .order_by(DatasetFile.kind)
        )
    ).all()
    return DatasetDetail(
        **DatasetOut.model_validate(dataset).model_dump(),
        source_timezone=dataset.source_timezone,
        business_timezone=dataset.business_timezone,
        currency=dataset.currency,
        checksum_sha256=dataset.checksum_sha256,
        error_code=dataset.error_code,
        completed_at=dataset.completed_at,
        files=[
            DatasetFileOut(
                kind=cast(FileKind, f.kind),
                filename=f.original_filename,
                size_bytes=f.size_bytes,
                sha256=f.sha256,
                headers=f.headers,
                mapping=f.mapping,
                mapping_confirmed_at=f.mapping_confirmed_at,
                rows_total=f.rows_total,
                rows_accepted=f.rows_accepted,
                rows_rejected=f.rows_rejected,
                rows_duplicate=f.rows_duplicate,
                profile=f.profile,
            )
            for f in files
        ],
        issues=[IssueSummaryOut(**i) for i in service.issue_summary(dataset.issue_counts)],
    )


async def _read_upload_form(request: Request) -> tuple[dict[FileKind, UploadFile], dict[str, str]]:
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise ApiError(415, "unsupported_media_type", "Upload files as multipart/form-data.")
    try:
        form = await request.form(max_files=4, max_fields=2, max_part_size=1024)
    except (MultiPartException, StarletteHTTPException) as exc:
        raise ApiError(
            422, "invalid_multipart", "The upload form is malformed or has too many parts."
        ) from exc
    request.state.upload_form = form  # closed by the caller (temporary files are deleted)
    files: dict[FileKind, UploadFile] = {}
    fields: dict[str, str] = {}
    for key in set(form.keys()):
        values = form.getlist(key)
        if len(values) > 1:
            raise ApiError(422, "duplicate_field", f"'{key}' was sent more than once.")
        value = values[0]
        if key in FILE_KINDS and isinstance(value, UploadFile):
            files[key] = value
        elif key in ("currency", "source_timezone") and isinstance(value, str):
            fields[key] = value.strip()
        else:
            raise ApiError(422, "unexpected_field", f"Unexpected form field '{key[:40]}'.")
    missing = [kind for kind in FILE_KINDS if kind not in files]
    if missing:
        raise ApiError(
            422,
            "missing_files",
            "All four files are required: orders, products, customers, returns.",
            errors=[
                {"field": k, "code": "missing_file", "message": f"{k}.csv is required."}
                for k in missing
            ],
        )
    return files, fields


@router.post("", status_code=202, response_model=DatasetDetail)
async def upload_dataset(
    request: Request, db: DbSession, tenant: DatasetUploader, idempotency_key: IdempotencyKeyHeader
) -> Response:
    try:
        uploads, fields = await _read_upload_form(request)
        business = await db.scalar(select(Business).where(Business.id == tenant.business_id))
        if business is None:
            raise ApiError(404, "not_found", "Resource not found.")
        source_timezone = fields.get("source_timezone") or business.timezone
        if source_timezone not in available_timezones():
            raise ApiError(
                422, "invalid_timezone", "source_timezone must be an IANA time zone name."
            )
        currency = fields.get("currency", "").upper() or business.currency
        if len(currency) != 3 or not currency.isalpha():
            raise ApiError(422, "invalid_currency", "currency must be a 3-letter ISO 4217 code.")
        if currency != business.currency:
            raise ApiError(
                422,
                "currency_mismatch",
                f"Files must be in the business currency {business.currency}; "
                "no conversion is applied.",
            )
        inspected: dict[FileKind, InspectedFile] = {}
        for kind in FILE_KINDS:
            upload = uploads[kind]
            try:
                inspected[kind] = await asyncio.to_thread(
                    inspect_file, kind, upload.filename, upload.content_type, upload.file
                )
            except UploadError as exc:
                raise _upload_problem(exc) from exc
    finally:
        form = getattr(request.state, "upload_form", None)
        if form is not None:
            await form.close()

    req_hash = service.request_hash(inspected, source_timezone, currency)
    await service.lock_business_uploads(db, tenant.business_id)
    replay = await service.replay_idempotent(db, tenant, idempotency_key, req_hash)
    if replay is not None:
        await db.rollback()
        return JSONResponse(replay.body, status_code=replay.status)
    recent = await db.scalar(
        select(func.count()).where(
            Dataset.business_id == tenant.business_id,
            Dataset.created_at > datetime.now(UTC) - timedelta(hours=1),
        )
    )
    if recent is not None and recent >= UPLOADS_PER_HOUR:
        raise ApiError(
            429,
            "rate_limited",
            "Upload limit reached; try again later.",
            headers={"Retry-After": "3600"},
        )

    dataset_id = await service.create_dataset(
        db,
        tenant,
        files=inspected,
        source_timezone=source_timezone,
        business_timezone=business.timezone,
        currency=currency,
        client_host=client_host(request),
    )
    body = (await _detail(db, tenant.business_id, dataset_id)).model_dump(mode="json")
    await service.store_idempotent_response(
        db, tenant, idempotency_key, req_hash, service.StoredResponse(202, body)
    )
    await db.commit()
    return JSONResponse(body, status_code=202)


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
async def get_dataset(db: DbSession, tenant: DatasetReader, dataset_id: UUID) -> DatasetDetail:
    return await _detail(db, tenant.business_id, dataset_id)


@router.post("/{dataset_id}/mapping", status_code=202)
async def confirm_mapping(
    request: Request,
    db: DbSession,
    tenant: DatasetUploader,
    dataset_id: UUID,
    body: MappingConfirmation,
) -> DatasetDetail:
    try:
        await service.confirm_dataset_mapping(
            db, tenant, dataset_id, dict(body.files), client_host(request)
        )
    except service.MappingApiError as exc:
        raise ApiError(
            422, exc.code, exc.detail, errors=_details_as_errors("invalid_mapping", exc.problems)
        ) from exc
    detail = await _detail(db, tenant.business_id, dataset_id)
    await db.commit()
    return detail


@router.get("/{dataset_id}/issues")
async def list_issues(
    db: DbSession,
    tenant: DatasetReader,
    dataset_id: UUID,
    limit: Limit = 50,
    cursor: Cursor = None,
    file: Annotated[
        Literal["orders", "products", "customers", "returns", "dataset"] | None, Query()
    ] = None,
    code: Annotated[str | None, Query(pattern=r"^V-[0-9]{3}$")] = None,
) -> Page[IssueOut]:
    await _detail(db, tenant.business_id, dataset_id)  # 404 if not in this business
    query = (
        select(ValidationIssue)
        .where(ValidationIssue.dataset_id == dataset_id)
        .order_by(ValidationIssue.id)
        .limit(limit + 1)
    )
    if file is not None:
        query = query.where(ValidationIssue.file_kind == file)
    if code is not None:
        query = query.where(ValidationIssue.code == code)
    if cursor is not None:
        query = query.where(ValidationIssue.id > decode_int_cursor(cursor))
    rows = list((await db.scalars(query)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[IssueOut](
        items=[IssueOut.model_validate(r) for r in rows],
        next_cursor=encode_cursor(str(rows[-1].id)) if has_more else None,
    )


def _json_cell(value: Any) -> str | int | None:
    if value is None or isinstance(value, int | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal | date):
        return str(value)
    return str(value)


async def _ready_dataset(db: AsyncSession, business_id: UUID, dataset_id: UUID) -> Dataset:
    dataset = await db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.business_id == business_id)
    )
    if dataset is None:
        raise ApiError(404, "not_found", "Resource not found.")
    if dataset.status != "ready":
        raise ApiError(409, "dataset_not_ready", "Records are available only for ready datasets.")
    return dataset


@router.get("/{dataset_id}/records/{kind}")
async def list_records(
    db: DbSession,
    tenant: DatasetReader,
    dataset_id: UUID,
    kind: RecordKind,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> RecordPage:
    await _ready_dataset(db, tenant.business_id, dataset_id)
    model = RECORD_MODELS[kind]
    query = (
        select(model)
        .where(model.dataset_id == dataset_id)
        .order_by(model.source_row)
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(model.source_row > decode_int_cursor(cursor))
    rows = list((await db.scalars(query)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    columns = ("source_row", *RECORD_COLUMNS[kind])
    return RecordPage(
        items=[{c: _json_cell(getattr(r, c)) for c in columns} for r in rows],
        next_cursor=encode_cursor(str(rows[-1].source_row)) if has_more else None,
    )


EXPORT_BATCH = 5_000


@router.get(
    "/{dataset_id}/export/{kind}",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV export"}},
)
async def export_csv(
    db: DbSession, tenant: DatasetReader, dataset_id: UUID, kind: ExportKind
) -> StreamingResponse:
    """CSV with spreadsheet-formula escaping (SEC-16), built into a bounded temp file."""
    if kind == "issues":
        dataset = await _detail(db, tenant.business_id, dataset_id)
        version = dataset.version
        model: Any = ValidationIssue
        columns: tuple[str, ...] = ISSUE_COLUMNS
        order_column = ValidationIssue.id
    else:
        version = (await _ready_dataset(db, tenant.business_id, dataset_id)).version
        model = RECORD_MODELS[kind]
        columns = ("source_row", *RECORD_COLUMNS[kind])
        order_column = model.source_row

    spool: IO[str] = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+", newline="")  # noqa: SIM115
    writer = csv.writer(spool, lineterminator="\r\n")
    writer.writerow(columns)
    last: Any = None
    while True:
        query = (
            select(model)
            .where(model.dataset_id == dataset_id)
            .order_by(order_column)
            .limit(EXPORT_BATCH)
        )
        if last is not None:
            query = query.where(order_column > last)
        batch = list((await db.scalars(query)).all())
        for row in batch:
            writer.writerow([safe_cell(_json_cell(getattr(row, c))) for c in columns])
        if len(batch) < EXPORT_BATCH:
            break
        last = getattr(batch[-1], "id" if kind == "issues" else "source_row")
    spool.seek(0)

    async def stream() -> AsyncIterator[bytes]:
        try:
            while chunk := spool.read(64 * 1024):
                yield chunk.encode("utf-8")
        finally:
            spool.close()

    filename = f"root-dataset-v{version}-{kind}.csv"
    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
