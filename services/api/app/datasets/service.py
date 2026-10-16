"""Dataset versions: creation from inspected uploads, mapping confirmation and read models."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import record_audit
from app.core.errors import ApiError
from app.ingestion.contract import FILE_KINDS, ISSUE_CODES, FileKind
from app.ingestion.mapping import FileMapping, MappingError, confirm_mapping
from app.ingestion.upload import InspectedFile
from app.jobs import queue
from app.models import Dataset, DatasetFile, IdempotencyKey
from app.tenancy.dependencies import TenantContext

IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class StoredResponse:
    status: int
    body: dict[str, Any]


def request_hash(files: dict[FileKind, InspectedFile], source_timezone: str, currency: str) -> str:
    material = {
        "files": {kind: files[kind].sha256 for kind in FILE_KINDS},
        "source_timezone": source_timezone,
        "currency": currency,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def dataset_checksum(files: dict[FileKind, InspectedFile]) -> str:
    joined = "|".join(f"{kind}:{files[kind].sha256}" for kind in FILE_KINDS)
    return hashlib.sha256(joined.encode()).hexdigest()


async def replay_idempotent(
    db: AsyncSession, tenant: TenantContext, key: str, req_hash: str
) -> StoredResponse | None:
    stored = await db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.business_id == tenant.business_id,
            IdempotencyKey.user_id == tenant.user.id,
            IdempotencyKey.key == key,
            IdempotencyKey.expires_at > func.now(),
        )
    )
    if stored is None:
        return None
    if stored.request_hash != req_hash:
        raise ApiError(
            409, "idempotency_key_reuse", "This Idempotency-Key was used for a different request."
        )
    return StoredResponse(stored.response_status, stored.response_body)


async def lock_business_uploads(db: AsyncSession, business_id: uuid.UUID) -> None:
    """Serialise idempotency replay, version allocation and duplicate detection per business.

    Transaction-scoped: released at commit or rollback, so concurrent uploads queue here.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('datasets:' || :bid, 0))"),
        {"bid": str(business_id)},
    )


async def create_dataset(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    files: dict[FileKind, InspectedFile],
    source_timezone: str,
    business_timezone: str,
    currency: str,
    client_host: str | None,
) -> uuid.UUID:
    """Create version N+1 (caller holds the upload lock and commits)."""
    checksum = dataset_checksum(files)
    existing = (
        await db.execute(
            select(Dataset.id, Dataset.version).where(
                Dataset.business_id == tenant.business_id,
                Dataset.checksum_sha256 == checksum,
                Dataset.status != "failed",
            )
        )
    ).first()
    if existing is not None:
        raise ApiError(
            409,
            "duplicate_dataset",
            f"These files are identical to dataset version {existing.version}; "
            "upload corrected files to create a new version.",
        )
    version = (
        await db.scalar(
            select(func.coalesce(func.max(Dataset.version), 0)).where(
                Dataset.business_id == tenant.business_id
            )
        )
        or 0
    ) + 1

    needs_mapping = any(files[k].mapping["needs_confirmation"] for k in FILE_KINDS)
    dataset_id = uuid.uuid7()
    db.add(
        Dataset(
            id=dataset_id,
            business_id=tenant.business_id,
            version=version,
            status="awaiting_mapping" if needs_mapping else "queued",
            checksum_sha256=checksum,
            source_timezone=source_timezone,
            business_timezone=business_timezone,
            currency=currency,
            issue_counts={},
            uploaded_by=tenant.user.id,
        )
    )
    await db.flush()
    for kind in FILE_KINDS:
        inspected = files[kind]
        db.add(
            DatasetFile(
                id=uuid.uuid7(),
                business_id=tenant.business_id,
                dataset_id=dataset_id,
                kind=kind,
                original_filename=inspected.filename,
                size_bytes=inspected.size_bytes,
                sha256=inspected.sha256,
                raw=inspected.content,
                headers=inspected.headers,
                mapping=dict(inspected.mapping),
            )
        )
    await db.flush()
    if not needs_mapping:
        await _enqueue_ingestion(db, tenant.business_id, dataset_id)
    await record_audit(
        db,
        action="dataset.uploaded",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="dataset",
        target_id=str(dataset_id),
        client_host=client_host,
        metadata={"version": version, "awaiting_mapping": needs_mapping},
    )
    return dataset_id


async def store_idempotent_response(
    db: AsyncSession, tenant: TenantContext, key: str, req_hash: str, response: StoredResponse
) -> None:
    await db.execute(
        insert(IdempotencyKey).values(
            business_id=tenant.business_id,
            user_id=tenant.user.id,
            key=key,
            request_hash=req_hash,
            response_status=response.status,
            response_body=response.body,
            expires_at=datetime.now(UTC) + IDEMPOTENCY_TTL,
        )
    )


async def _enqueue_ingestion(
    db: AsyncSession, business_id: uuid.UUID, dataset_id: uuid.UUID
) -> None:
    await queue.enqueue(
        db,
        job_type="ingest_dataset",
        business_id=business_id,
        payload={"dataset_id": str(dataset_id)},
        dedupe_key=f"ingest_dataset:{dataset_id}",
    )


async def confirm_dataset_mapping(
    db: AsyncSession,
    tenant: TenantContext,
    dataset_id: uuid.UUID,
    selections: dict[FileKind, dict[str, str | None]],
    client_host: str | None,
) -> None:
    dataset = await db.scalar(
        select(Dataset)
        .where(Dataset.id == dataset_id, Dataset.business_id == tenant.business_id)
        .with_for_update()
    )
    if dataset is None:
        raise ApiError(404, "not_found", "Resource not found.")
    if dataset.status != "awaiting_mapping":
        raise ApiError(409, "invalid_state", "This dataset is not waiting for a column mapping.")
    files = {
        cast(FileKind, f.kind): f
        for f in await db.scalars(select(DatasetFile).where(DatasetFile.dataset_id == dataset_id))
    }
    problems: list[dict[str, str]] = []
    confirmed: dict[FileKind, FileMapping] = {}
    for kind in FILE_KINDS:
        current = cast(FileMapping, files[kind].mapping)
        if kind not in selections:
            if current["needs_confirmation"]:
                problems.append(
                    {"file": kind, "column": "", "problem": "mapping decision required"}
                )
            continue
        try:
            confirmed[kind] = confirm_mapping(kind, files[kind].headers, current, selections[kind])
        except MappingError as exc:
            problems.extend(exc.details)
    if problems:
        raise MappingApiError(problems)

    now = datetime.now(UTC)
    for kind, mapping in confirmed.items():
        await db.execute(
            update(DatasetFile)
            .where(DatasetFile.id == files[kind].id)
            .values(
                mapping=dict(mapping), mapping_confirmed_by=tenant.user.id, mapping_confirmed_at=now
            )
        )
    await db.execute(update(Dataset).where(Dataset.id == dataset_id).values(status="queued"))
    await _enqueue_ingestion(db, tenant.business_id, dataset_id)
    await record_audit(
        db,
        action="dataset.mapping_confirmed",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="dataset",
        target_id=str(dataset_id),
        client_host=client_host,
        metadata={"files": sorted(confirmed)},
    )


class MappingApiError(ApiError):
    def __init__(self, problems: list[dict[str, str]]) -> None:
        super().__init__(422, "invalid_mapping", "The column mapping is incomplete or invalid.")
        self.problems = problems


def issue_summary(issue_counts: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    return [
        {
            "file": file_kind,
            "code": code,
            "severity": ISSUE_CODES[code][0],
            "description": ISSUE_CODES[code][1],
            "count": count,
        }
        for file_kind, codes in sorted(issue_counts.items())
        for code, count in sorted(codes.items())
    ]
