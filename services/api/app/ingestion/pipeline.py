"""Dataset ingestion job: parse → stage → validate (set-based) → persist, in one transaction.

Rows are parsed one at a time from the stored CSV bytes and streamed with COPY into staging
tables that live only for the transaction (ON COMMIT DROP). Cross-row rules (duplicates,
references, cumulative returns) run as SQL over the staging tables. Records reach the
canonical tables only if the whole dataset passes; otherwise the dataset is `rejected` with
its issues and profile kept. Any exception rolls everything back (the job then marks the
dataset `failed`).
"""

import csv
import io
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import undefer

from app.core.db import set_business_context
from app.ingestion.contract import (
    COLUMNS,
    FILE_KINDS,
    ISSUE_CODES,
    MAX_DIMENSION_VALUES,
    MAX_ROWS,
    MIN_HISTORY_WEEKS,
    REJECTED_ROW_THRESHOLD,
    STORED_ISSUES_PER_CODE,
    Column,
    FileKind,
)
from app.ingestion.mapping import source_indices
from app.ingestion.parsing import (
    InvalidValueError,
    TimeContext,
    excerpt,
    parse_currency,
    parse_decimal,
    parse_dimension,
    parse_enum,
    parse_id,
    parse_int,
    parse_moment,
    parse_text,
)
from app.jobs.queue import LeasedJob
from app.jobs.worker import PermanentJobError
from app.models import Dataset, DatasetFile

logger = logging.getLogger(__name__)

TABLES: dict[FileKind, str] = {
    "products": "products",
    "customers": "customers",
    "orders": "order_lines",
    "returns": "returns",
}
KEYS: dict[FileKind, str] = {
    "products": "product_id",
    "customers": "customer_id",
    "orders": "order_line_id",
    "returns": "return_id",
}
# Staged/stored columns per kind, in COPY order (after source_row).
STORED: dict[FileKind, tuple[str, ...]] = {
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
STAGING_DDL: dict[FileKind, str] = {
    "products": "product_id text, product_name text, category text, unit_cost numeric(18,4), "
    "list_price numeric(18,4)",
    "customers": "customer_id text, signup_date date, region text, acquisition_channel text",
    "orders": "order_line_id text, order_id text, order_date date, ordered_at timestamptz, "
    "customer_id text, product_id text, quantity integer, unit_price numeric(18,4), "
    "discount_amount numeric(18,4), channel text, region text",
    "returns": "return_id text, order_line_id text, return_date date, returned_at timestamptz, "
    "quantity_returned integer, refund_amount numeric(18,4), reason_code text, "
    "reason_text text",
}
TIMESTAMP_COLUMNS = {"order_date": "ordered_at", "return_date": "returned_at"}
DIMENSIONS: dict[FileKind, tuple[str, ...]] = {
    "products": ("category",),
    "customers": ("region", "acquisition_channel"),
    "orders": ("channel", "region"),
    "returns": (),
}


_ISSUE_COLUMNS = (
    "business_id, dataset_id, file_kind, row_number, column_name, code, severity, message, excerpt"
)


@dataclass
class IssueSink:
    """Collects row-level issues with exact counts, storing at most N per (file, code)."""

    business_id: uuid.UUID
    dataset_id: uuid.UUID
    counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    pending: list[tuple[Any, ...]] = field(default_factory=list)

    def add(
        self,
        file_kind: str,
        code: str,
        *,
        row: int | None = None,
        column: str | None = None,
        message: str | None = None,
        value: str | None = None,
    ) -> None:
        self.counts[(file_kind, code)] += 1
        if self.counts[(file_kind, code)] > STORED_ISSUES_PER_CODE:
            return
        severity, description = ISSUE_CODES[code]
        self.pending.append(
            (
                self.business_id,
                self.dataset_id,
                file_kind,
                row,
                column,
                code,
                severity,
                (message or description)[:300],
                excerpt(value) if value else None,
            )
        )

    def count_bulk(self, file_kind: str, code: str, count: int) -> None:
        self.counts[(file_kind, code)] += count

    async def flush(self, pg: AsyncConnection[Any]) -> None:
        """COPY into an RLS-free temp table, then one INSERT…SELECT through the RLS check
        (PostgreSQL does not allow COPY FROM into tables with row-level security)."""
        if not self.pending:
            return
        async with pg.cursor() as cur:
            async with cur.copy(f"COPY stg_issues ({_ISSUE_COLUMNS}) FROM STDIN") as copy:
                for issue in self.pending:
                    await copy.write_row(issue)
            await cur.execute(
                f"INSERT INTO validation_issues ({_ISSUE_COLUMNS}) "
                f"SELECT {_ISSUE_COLUMNS} FROM stg_issues ORDER BY seq"
            )
            await cur.execute("TRUNCATE stg_issues")
        self.pending.clear()

    def as_json(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for (file_kind, code), count in sorted(self.counts.items()):
            result.setdefault(file_kind, {})[code] = count
        return result


@dataclass
class FileStats:
    total: int = 0
    parse_rejected: int = 0
    failed: bool = False  # a file-level error stopped processing


class _RowRejectedError(Exception):
    pass


def _parse_row(
    kind: FileKind,
    row: list[str],
    indices: dict[str, int],
    row_number: int,
    ctx: TimeContext,
    currency: str,
    sink: IssueSink,
) -> tuple[Any, ...]:
    values: dict[str, Any] = {}
    errors = 0
    for column in COLUMNS[kind]:
        index = indices.get(column.name)
        raw = row[index].strip() if index is not None else ""
        if not raw:
            if column.required:
                sink.add(kind, "V-011", row=row_number, column=column.name)
                errors += 1
            values[column.name] = None
            continue
        try:
            _parse_value(kind, column, raw, ctx, currency, values, sink, row_number)
        except InvalidValueError as problem:
            sink.add(
                kind,
                problem.code,
                row=row_number,
                column=column.name,
                message=problem.message,
                value=raw,
            )
            errors += 1
    if errors:
        raise _RowRejectedError
    if kind == "orders":
        values["discount_amount"] = values["discount_amount"] or Decimal(0)
        values["channel"] = values["channel"] or "unknown"
        values["region"] = values["region"] or "unknown"
    return (row_number, *(values.get(name) for name in STORED[kind]))


def _parse_value(
    kind: FileKind,
    column: Column,
    raw: str,
    ctx: TimeContext,
    currency: str,
    values: dict[str, Any],
    sink: IssueSink,
    row_number: int,
) -> None:
    match column.type:
        case "id":
            values[column.name] = parse_id(raw)
        case "text":
            values[column.name] = parse_text(raw, column) or None
        case "dimension":
            values[column.name] = parse_dimension(raw, column) or None
        case "decimal":
            values[column.name] = parse_decimal(raw, column)
        case "int":
            values[column.name] = parse_int(raw, column)
        case "enum":
            values[column.name] = parse_enum(raw)
        case "currency":
            parse_currency(raw, currency)  # validated only; the business currency is stored
        case "date" | "datetime":
            moment = parse_moment(raw, ctx)
            values[column.name] = moment.local_date
            if column.name in TIMESTAMP_COLUMNS:
                values[TIMESTAMP_COLUMNS[column.name]] = moment.at_utc
            if moment.ambiguous:
                sink.add(kind, "V-044", row=row_number, column=column.name, value=raw)


async def _stage_file(
    pg: AsyncConnection[Any],
    kind: FileKind,
    file: DatasetFile,
    ctx: TimeContext,
    currency: str,
    sink: IssueSink,
) -> FileStats:
    stats = FileStats()
    if file.raw is None:
        raise PermanentJobError("raw_file_missing")
    for header in file.mapping.get("ignored", []):
        sink.add(kind, "V-040", column=header)
    for header in file.mapping.get("pii_dropped", []):
        sink.add(kind, "V-041", column=header)

    indices = source_indices(file.headers, file.mapping)
    width = len(file.headers)
    # Decode incrementally: no second, fully decoded copy of the file is held in memory.
    stream = io.TextIOWrapper(io.BytesIO(file.raw), encoding="utf-8-sig", newline="")
    reader = csv.reader(stream, strict=True)
    columns = ", ".join(("source_row", *STORED[kind]))
    async with pg.cursor() as cur, cur.copy(f"COPY stg_{kind} ({columns}) FROM STDIN") as copy:
        try:
            next(reader)  # header (already validated at upload)
            for row_number, row in enumerate(reader, start=2):
                stats.total += 1
                if stats.total > MAX_ROWS[kind]:
                    sink.add(kind, "V-003", message=f"More than {MAX_ROWS[kind]} rows.")
                    stats.failed = True
                    break
                if len(row) != width:
                    sink.add(kind, "V-010", row=row_number)
                    stats.parse_rejected += 1
                    continue
                try:
                    staged = _parse_row(kind, row, indices, row_number, ctx, currency, sink)
                except _RowRejectedError:
                    stats.parse_rejected += 1
                    continue
                await copy.write_row(staged)
        except csv.Error:
            sink.add(
                kind,
                "V-002",
                row=reader.line_num,
                message=f"Malformed CSV near line {reader.line_num}.",
            )
            stats.failed = True
    return stats


# --------------------------------------------------------------------------------------------
# Set-based rules. Table and column names below are constants from this module, never input.
# --------------------------------------------------------------------------------------------

ACCEPTED = "NOT rejected AND NOT dropped"
ACCEPTED_O = "NOT o.rejected AND NOT o.dropped"


async def _flag(
    session: AsyncSession,
    sink: IssueSink,
    kind: FileKind,
    code: str,
    column: str,
    where: str,
    params: dict[str, Any] | None = None,
    *,
    drop: bool = False,
) -> None:
    """Mark matching staged rows rejected (or dropped) with `code` and record issues."""
    target = "dropped" if drop else "rejected"
    result = await session.execute(
        text(f"""
            UPDATE stg_{kind} s SET {target} = true, issue_code = :code
            WHERE NOT s.rejected AND NOT s.dropped AND ({where})
        """),
        {"code": code, **(params or {})},
    )
    count = int(result.rowcount)  # type: ignore[attr-defined]
    if count == 0:
        return
    sink.count_bulk(kind, code, count)
    severity, description = ISSUE_CODES[code]
    await session.execute(
        text(f"""
            INSERT INTO validation_issues (business_id, dataset_id, file_kind, row_number,
                column_name, code, severity, message, excerpt)
            SELECT :business_id, :dataset_id, :kind, source_row, :column, :code, :severity,
                   :message, left({KEYS[kind]}, 100)
            FROM stg_{kind} WHERE issue_code = :code
            ORDER BY source_row LIMIT :cap
        """),
        {
            "business_id": sink.business_id,
            "dataset_id": sink.dataset_id,
            "kind": kind,
            "column": column,
            "code": code,
            "severity": severity,
            "message": description,
            "cap": STORED_ISSUES_PER_CODE,
        },
    )


async def _duplicate_rules(session: AsyncSession, sink: IssueSink, kind: FileKind) -> None:
    key = KEYS[kind]
    content = ", ".join(c for c in STORED[kind] if c != key)
    await _flag(
        session,
        sink,
        kind,
        "V-020",
        key,
        f"""s.{key} IN (SELECT {key} FROM stg_{kind} WHERE {ACCEPTED} GROUP BY {key}
                       HAVING count(DISTINCT md5(row({content})::text)) > 1)""",
    )
    await _flag(
        session,
        sink,
        kind,
        "V-021",
        key,
        f"""s.source_row NOT IN (SELECT min(source_row) FROM stg_{kind} WHERE {ACCEPTED}
                                GROUP BY {key})""",
        drop=True,
    )


async def _reference_rules(session: AsyncSession, sink: IssueSink) -> None:
    await _flag(
        session,
        sink,
        "orders",
        "V-037",
        "order_id",
        f"""s.order_id IN (SELECT order_id FROM stg_orders WHERE {ACCEPTED} GROUP BY order_id
                          HAVING count(DISTINCT (order_date, customer_id)) > 1)""",
    )
    await _flag(
        session,
        sink,
        "orders",
        "V-030",
        "customer_id",
        "NOT EXISTS (SELECT 1 FROM stg_customers c WHERE c.customer_id = s.customer_id "
        "AND NOT c.rejected)",
    )
    await _flag(
        session,
        sink,
        "orders",
        "V-031",
        "product_id",
        "NOT EXISTS (SELECT 1 FROM stg_products p WHERE p.product_id = s.product_id "
        "AND NOT p.rejected)",
    )
    await _flag(
        session,
        sink,
        "orders",
        "V-033",
        "discount_amount",
        "s.discount_amount > s.quantity * s.unit_price",
    )
    await _flag(
        session,
        sink,
        "returns",
        "V-032",
        "order_line_id",
        f"NOT EXISTS (SELECT 1 FROM stg_orders o WHERE o.order_line_id = s.order_line_id "
        f"AND {ACCEPTED_O})",
    )
    await _flag(
        session,
        sink,
        "returns",
        "V-034",
        "return_date",
        f"EXISTS (SELECT 1 FROM stg_orders o WHERE o.order_line_id = s.order_line_id "
        f"AND {ACCEPTED_O} AND s.return_date < o.order_date)",
    )
    # Cumulative rules: running totals per line in (return_date, return_id) order, counting
    # every earlier valid return for the line (DATA_CONTRACTS AMD-8 note).
    for code, column, measure, limit in (
        ("V-035", "quantity_returned", "quantity_returned", "o.quantity"),
        (
            "V-036",
            "refund_amount",
            "refund_amount",
            "o.quantity * o.unit_price - o.discount_amount",
        ),
    ):
        await _flag(
            session,
            sink,
            "returns",
            code,
            column,
            f"""s.source_row IN (
                SELECT r.source_row FROM (
                    SELECT source_row, order_line_id, sum({measure}) OVER (
                        PARTITION BY order_line_id ORDER BY return_date, return_id
                        ROWS UNBOUNDED PRECEDING) AS running
                    FROM stg_returns WHERE {ACCEPTED}
                ) r JOIN stg_orders o ON o.order_line_id = r.order_line_id AND {ACCEPTED_O}
                WHERE r.running > {limit})""",
        )


_PRICE_OUTLIERS = f"""
    FROM stg_orders o JOIN stg_products p ON p.product_id = o.product_id
        AND NOT p.rejected AND NOT p.dropped
    WHERE {ACCEPTED_O}
      AND (o.unit_price > 3 * p.list_price OR o.unit_price < 0.1 * p.list_price)
"""


async def _price_warnings(session: AsyncSession, sink: IssueSink) -> None:
    total = await session.scalar(text(f"SELECT count(*) {_PRICE_OUTLIERS}"))
    if not total:
        return
    sink.count_bulk("orders", "V-043", int(total))
    await session.execute(
        text(f"""
            INSERT INTO validation_issues (business_id, dataset_id, file_kind, row_number,
                column_name, code, severity, message, excerpt)
            SELECT :business_id, :dataset_id, 'orders', o.source_row, 'unit_price', 'V-043',
                   'warning', :message, left(o.order_line_id, 100)
            {_PRICE_OUTLIERS}
            ORDER BY o.source_row LIMIT :cap
        """),
        {
            "business_id": sink.business_id,
            "dataset_id": sink.dataset_id,
            "message": ISSUE_CODES["V-043"][1],
            "cap": STORED_ISSUES_PER_CODE,
        },
    )


async def _profile(session: AsyncSession, kind: FileKind) -> dict[str, Any]:
    parts: list[str] = []
    for column in STORED[kind]:
        parts.append(f"count({column}) AS {column}__non_null")
        if column.endswith(("_id", "category", "region", "channel", "reason_code")):
            parts.append(f"count(DISTINCT {column}) AS {column}__distinct")
        if column.endswith(
            ("date", "_at", "quantity", "quantity_returned", "price", "cost", "amount")
        ):
            parts.append(f"min({column}) AS {column}__min, max({column}) AS {column}__max")
        if column.endswith(("quantity", "quantity_returned", "price", "cost", "amount")):
            parts.append(f"sum({column}) AS {column}__sum")
    row = (
        (
            await session.execute(
                text(
                    f"SELECT count(*) AS rows, {', '.join(parts)} FROM stg_{kind} WHERE {ACCEPTED}"
                )
            )
        )
        .one()
        ._asdict()
    )
    profile: dict[str, Any] = {"rows": row.pop("rows"), "columns": {}}
    for key, value in row.items():
        column, stat = key.split("__")
        profile["columns"].setdefault(column, {})[stat] = _json_value(value)
    return profile


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal | date | datetime):
        return str(value) if not isinstance(value, datetime) else value.isoformat()
    return value


def _complete_iso_weeks(first: date, last: date) -> int:
    start = first + timedelta(days=(7 - first.weekday()) % 7)  # first Monday on/after
    end = last - timedelta(days=(last.weekday() + 1) % 7)  # last Sunday on/before
    return max(0, ((end - start).days + 1) // 7)


@dataclass
class Outcome:
    rejected: bool
    accepted: dict[FileKind, int]


async def _validate_and_persist(
    session: AsyncSession, dataset: Dataset, files: dict[FileKind, DatasetFile], sink: IssueSink
) -> Outcome:
    connection = await session.connection()
    raw_connection = await connection.get_raw_connection()
    pg: AsyncConnection[Any] = raw_connection.driver_connection  # type: ignore[assignment]

    business_tz = ZoneInfo(dataset.business_timezone)
    ctx = TimeContext(
        source_tz=ZoneInfo(dataset.source_timezone),
        business_tz=business_tz,
        max_date=dataset.created_at.astimezone(business_tz).date() + timedelta(days=1),
    )
    await session.execute(
        text(
            "CREATE TEMP TABLE stg_issues (seq bigint GENERATED ALWAYS AS IDENTITY, "
            "business_id uuid, dataset_id uuid, file_kind text, row_number integer, "
            "column_name text, code text, severity text, message text, excerpt text) "
            "ON COMMIT DROP"
        )
    )
    for kind in FILE_KINDS:
        await session.execute(
            text(
                f"CREATE TEMP TABLE stg_{kind} (source_row integer PRIMARY KEY, "
                f"{STAGING_DDL[kind]}, rejected boolean NOT NULL DEFAULT false, "
                "dropped boolean NOT NULL DEFAULT false, issue_code text) ON COMMIT DROP"
            )
        )

    stats: dict[FileKind, FileStats] = {}
    for kind in FILE_KINDS:
        stats[kind] = await _stage_file(pg, kind, files[kind], ctx, dataset.currency, sink)
        await sink.flush(pg)
    for kind in FILE_KINDS:
        await session.execute(text(f"ANALYZE stg_{kind}"))

    rejected = any(s.failed for s in stats.values())
    if not rejected:
        for kind in FILE_KINDS:
            await _duplicate_rules(session, sink, kind)
        await _reference_rules(session, sink)
        await _price_warnings(session, sink)

    counts: dict[FileKind, tuple[int, int]] = {}
    for kind in FILE_KINDS:
        sql_rejected, dropped = (
            await session.execute(
                text(
                    f"SELECT count(*) FILTER (WHERE rejected), count(*) FILTER (WHERE dropped) "
                    f"FROM stg_{kind}"
                )
            )
        ).one()
        counts[kind] = (stats[kind].parse_rejected + int(sql_rejected), int(dropped))
        total = stats[kind].total
        if total and counts[kind][0] / total > REJECTED_ROW_THRESHOLD:
            sink.add(
                kind,
                "V-099",
                message=(
                    f"{counts[kind][0]} of {total} rows rejected (limit 5%); dataset not loaded."
                ),
            )
            rejected = True
        for column in DIMENSIONS[kind]:
            distinct = await session.scalar(
                text(f"SELECT count(DISTINCT {column}) FROM stg_{kind} WHERE {ACCEPTED}")
            )
            if distinct and distinct > MAX_DIMENSION_VALUES:
                sink.add(
                    kind,
                    "V-006",
                    column=column,
                    message=f"{distinct} distinct values (limit {MAX_DIMENSION_VALUES}).",
                )
                rejected = True

    accepted_orders = await session.scalar(
        text(f"SELECT count(*) FROM stg_orders WHERE {ACCEPTED}")
    )
    if not accepted_orders:
        sink.add("dataset", "V-007")
        rejected = True
    else:
        first, last = (
            await session.execute(
                text(f"SELECT min(order_date), max(order_date) FROM stg_orders WHERE {ACCEPTED}")
            )
        ).one()
        if _complete_iso_weeks(first, last) < MIN_HISTORY_WEEKS:
            sink.add("dataset", "V-042")
        dataset.date_min, dataset.date_max = first, last

    profiles = {kind: await _profile(session, kind) for kind in FILE_KINDS}
    await sink.flush(pg)

    if not rejected:
        for kind in FILE_KINDS:
            columns = ", ".join(("source_row", *STORED[kind]))
            await session.execute(
                text(f"""
                    INSERT INTO {TABLES[kind]} (business_id, dataset_id, {columns})
                    SELECT :business_id, :dataset_id, {columns} FROM stg_{kind}
                    WHERE {ACCEPTED} ORDER BY source_row
                """),
                {"business_id": dataset.business_id, "dataset_id": dataset.id},
            )

    accepted: dict[FileKind, int] = {}
    for kind in FILE_KINDS:
        rejected_rows, dropped = counts[kind]
        total = stats[kind].total
        accepted[kind] = max(0, total - rejected_rows - dropped)
        await session.execute(
            update(DatasetFile)
            .where(DatasetFile.id == files[kind].id)
            .values(
                rows_total=total,
                rows_rejected=rejected_rows,
                rows_duplicate=dropped,
                rows_accepted=accepted[kind],
                profile=profiles[kind],
            )
        )
    return Outcome(rejected=rejected, accepted=accepted)


async def ingest_dataset(sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob) -> None:
    """Job handler for `ingest_dataset`. Safe to re-run: it only acts on queued/validating."""
    if job.business_id is None:
        raise PermanentJobError("missing_business")
    dataset_id = uuid.UUID(job.payload["dataset_id"])

    async with sessionmaker() as session, session.begin():
        await set_business_context(session, job.business_id)
        status = await session.scalar(
            select(Dataset.status).where(Dataset.id == dataset_id).with_for_update()
        )
        if status not in ("queued", "validating"):
            logger.info("ingest_skipped", extra={"dataset_id": str(dataset_id), "status": status})
            return
        await session.execute(
            update(Dataset).where(Dataset.id == dataset_id).values(status="validating")
        )

    async with sessionmaker() as session, session.begin():
        await set_business_context(session, job.business_id)
        dataset = await session.scalar(
            select(Dataset).where(Dataset.id == dataset_id).with_for_update()
        )
        if dataset is None or dataset.status != "validating":
            return
        files = {
            cast(FileKind, f.kind): f
            for f in await session.scalars(
                select(DatasetFile)
                .where(DatasetFile.dataset_id == dataset_id)
                .options(undefer(DatasetFile.raw))
            )
        }
        sink = IssueSink(business_id=dataset.business_id, dataset_id=dataset.id)
        outcome = await _validate_and_persist(session, dataset, files, sink)
        file_rows = {kind: outcome.accepted[kind] for kind in FILE_KINDS}
        await session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)
            .values(
                status="rejected" if outcome.rejected else "ready",
                error_code="validation_failed" if outcome.rejected else None,
                products_rows=file_rows["products"],
                customers_rows=file_rows["customers"],
                orders_rows=file_rows["orders"],
                returns_rows=file_rows["returns"],
                date_min=dataset.date_min,
                date_max=dataset.date_max,
                issue_counts=sink.as_json(),
                completed_at=datetime.now(UTC),
            )
        )


async def mark_dataset_failed(
    sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob, code: str
) -> None:
    """Final-failure callback: the transaction rolled back, so no records exist."""
    if job.type != "ingest_dataset" or job.business_id is None:
        return
    async with sessionmaker() as session, session.begin():
        await set_business_context(session, job.business_id)
        await session.execute(
            update(Dataset)
            .where(
                Dataset.id == uuid.UUID(job.payload["dataset_id"]),
                Dataset.status.in_(("queued", "validating")),
            )
            .values(status="failed", error_code=code, completed_at=datetime.now(UTC))
        )
