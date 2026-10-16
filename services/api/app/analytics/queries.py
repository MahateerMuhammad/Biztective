"""Raw data access for analytics: parameterised SQL over ready datasets (completed lines only).

Dimension expressions come from a fixed allowlist; values are always bound parameters. One
scan of order lines and one of returns produce every requested slice via GROUPING SETS.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import Totals
from app.analytics.segmentation import CustomerStats

# Allowlist (DATA_CONTRACTS §3.4). Keys are API names; values are SQL over aliases ol/p/c/sm.
DIMENSION_SQL: dict[str, str] = {
    "category": "p.category",
    "product": "ol.product_id",
    "region": "ol.region",
    "channel": "ol.channel",
    "customer_segment": "sm.segment",
    "acquisition_channel": "c.acquisition_channel",
}
SliceRow = tuple[date | None, str, str | None]  # (week or None, dimension, value)


@dataclass(frozen=True)
class DatasetBounds:
    first_order: date | None
    as_of: date | None  # T: last completed order date (detection, segmentation)
    last_activity: date | None  # last completed order or return date (default API ranges)
    cancelled_lines: int
    cancelled_value: Decimal


async def dataset_bounds(session: AsyncSession, dataset_id: UUID) -> DatasetBounds:
    row = (
        await session.execute(
            text("""
                SELECT min(order_date) FILTER (WHERE status = 'completed'),
                       max(order_date) FILTER (WHERE status = 'completed'),
                       greatest(max(order_date) FILTER (WHERE status = 'completed'),
                                (SELECT max(return_date) FROM returns WHERE dataset_id = :d)),
                       count(*) FILTER (WHERE status = 'cancelled'),
                       coalesce(sum(quantity * unit_price - discount_amount)
                                FILTER (WHERE status = 'cancelled'), 0)
                FROM order_lines WHERE dataset_id = :d
            """),
            {"d": dataset_id},
        )
    ).one()
    return DatasetBounds(row[0], row[1], row[2], int(row[3]), Decimal(row[4]))


async def load_segment_map(session: AsyncSession, segments: Iterable[tuple[str, str]]) -> None:
    """Customer → segment for `customer_segment` slicing, in a transaction-scoped temp table.

    Temp tables have no RLS, so COPY is allowed; rows hold pseudonymous IDs only."""
    await session.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS analytics_segment_map "
            "(customer_id text PRIMARY KEY, segment text NOT NULL) ON COMMIT DROP"
        )
    )
    await session.execute(text("TRUNCATE analytics_segment_map"))
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    pg: AsyncConnection[Any] = raw.driver_connection  # type: ignore[assignment]  # SQLAlchemy types it loosely
    async with pg.cursor() as cur, cur.copy("COPY analytics_segment_map FROM STDIN") as copy:
        for row in segments:
            await copy.write_row(row)
    # Temp tables are never auto-analysed; give the planner real row counts.
    await session.execute(text("ANALYZE analytics_segment_map"))


def _grouping(
    dimensions: Sequence[str], week_expr: str | None, where_dimension: str | None = None
) -> tuple[str, str, str]:
    """SELECT fragment, GROUPING SETS clause and the extra joins needed."""
    base = [week_expr] if week_expr else []
    sets = [f"({', '.join(base)})"] + [
        f"({', '.join([*base, DIMENSION_SQL[d]])})" for d in dimensions
    ]
    select = [f"{week_expr} AS week" if week_expr else "NULL::date AS week"]
    for d in dimensions:
        select.append(f"{DIMENSION_SQL[d]} AS dim_{d}")
        select.append(f"GROUPING({DIMENSION_SQL[d]}) AS g_{d}")
    joins = []
    needed = {*dimensions, where_dimension}
    if "customer_segment" in needed:
        joins.append("LEFT JOIN analytics_segment_map sm ON sm.customer_id = ol.customer_id")
    if "acquisition_channel" in needed:
        joins.append(
            "JOIN customers c ON c.dataset_id = ol.dataset_id AND c.customer_id = ol.customer_id"
        )
    return ", ".join(select), ", ".join(sets), " ".join(joins)


def _slice_of(row: Any, dimensions: Sequence[str]) -> SliceRow:
    for d in dimensions:
        if getattr(row, f"g_{d}") == 0:
            value = getattr(row, f"dim_{d}")
            return (row.week, d, None if value is None else str(value))
    return (row.week, "overall", None)


async def aggregate(
    session: AsyncSession,
    dataset_id: UUID,
    start: date,
    end: date,
    *,
    dimensions: Sequence[str] = (),
    by_week: bool = False,
    where: tuple[str, str | None] | None = None,
) -> dict[SliceRow, Totals]:
    """Totals per (week|None, dimension, value) for lines and returns in [start, end],
    optionally restricted to one slice (`where` = (dimension, value)).

    Two statements regardless of how many dimensions are requested."""
    unknown = set(dimensions) - DIMENSION_SQL.keys()
    if unknown:
        raise ValueError(f"unsupported dimensions: {sorted(unknown)}")
    predicate, extra = slice_filter(*where) if where else ("TRUE", {})
    where_dimension = where[0] if where else None
    params = {"d": dataset_id, "start": start, "end": end, **extra}

    select, sets, joins = _grouping(
        dimensions,
        "date_trunc('week', ol.order_date)::date" if by_week else None,
        where_dimension,
    )
    period_start = "date_trunc('week', ol.order_date)::date" if by_week else "CAST(:start AS date)"
    # Each customer's first order comes from a window over the same scan (no aggregate join
    # whose plan depends on fresh statistics after an ingestion).
    lines = await session.execute(
        text(f"""
            WITH ol AS (
                SELECT l.*, min(l.order_date) OVER (PARTITION BY l.customer_id) AS first_order
                FROM order_lines l
                WHERE l.dataset_id = :d AND l.status = 'completed' AND l.order_date <= :end
            )
            SELECT {select},
                coalesce(sum(ol.quantity * ol.unit_price), 0) AS gross_sales,
                coalesce(sum(ol.discount_amount), 0) AS discounts,
                coalesce(sum(ol.quantity), 0) AS units_sold,
                coalesce(sum(ol.quantity * p.unit_cost), 0) AS cogs_sold_known,
                count(*) FILTER (WHERE p.unit_cost IS NULL) AS lines_missing_cost,
                count(*) AS lines,
                count(DISTINCT ol.order_id) AS orders,
                count(DISTINCT ol.customer_id) AS active_customers,
                count(DISTINCT ol.customer_id)
                    FILTER (WHERE ol.first_order < {period_start}) AS repeat_customers,
                count(DISTINCT ol.customer_id)
                    FILTER (WHERE ol.first_order >= {period_start}) AS new_customers
            FROM ol
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            {joins}
            WHERE ol.order_date BETWEEN :start AND :end AND {predicate}
            GROUP BY GROUPING SETS ({sets})
        """),  # noqa: S608 - fragments come from DIMENSION_SQL, values are bound
        params,
    )
    result: dict[SliceRow, Totals] = {}
    for row in lines:
        result[_slice_of(row, dimensions)] = Totals(
            gross_sales=Decimal(row.gross_sales),
            discounts=Decimal(row.discounts),
            units_sold=int(row.units_sold),
            cogs_sold_known=Decimal(row.cogs_sold_known),
            lines_missing_cost=int(row.lines_missing_cost),
            lines=int(row.lines),
            orders=int(row.orders),
            active_customers=int(row.active_customers),
            repeat_customers=int(row.repeat_customers),
            new_customers=int(row.new_customers),
        )

    select, sets, joins = _grouping(
        dimensions,
        "date_trunc('week', r.return_date)::date" if by_week else None,
        where_dimension,
    )
    returns = await session.execute(
        text(f"""
            SELECT {select},
                coalesce(sum(r.refund_amount), 0) AS refunds,
                coalesce(sum(r.quantity_returned), 0) AS units_returned,
                coalesce(sum(r.quantity_returned * p.unit_cost)
                    FILTER (WHERE r.reason_code NOT IN ('damaged', 'defective')), 0)
                    AS cogs_restocked_known,
                count(*) FILTER (WHERE p.unit_cost IS NULL
                    AND r.reason_code NOT IN ('damaged', 'defective'))
                    AS restockable_returns_missing_cost,
                count(*) AS returns
            FROM returns r
            JOIN order_lines ol ON ol.dataset_id = r.dataset_id
                AND ol.order_line_id = r.order_line_id
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            {joins}
            WHERE r.dataset_id = :d AND r.return_date BETWEEN :start AND :end AND {predicate}
            GROUP BY GROUPING SETS ({sets})
        """),  # noqa: S608
        params,
    )
    for row in returns:
        key = _slice_of(row, dimensions)
        result[key] = result.get(key, Totals()) + Totals(
            refunds=Decimal(row.refunds),
            units_returned=int(row.units_returned),
            cogs_restocked_known=Decimal(row.cogs_restocked_known),
            restockable_returns_missing_cost=int(row.restockable_returns_missing_cost),
            returns=int(row.returns),
        )
    return result


async def matured_cohort_refund_rates(
    session: AsyncSession,
    dataset_id: UUID,
    start: date,
    end: date,
    as_of: date,
    maturity_days: int,
    *,
    by_product: bool,
) -> dict[str | None, tuple[Decimal, Decimal]]:
    """M21 numerator/denominator: lines ordered in [start, min(end, T − maturity)], refunds
    within `maturity_days` of the order date. Keyed by product_id (or None for overall)."""
    cutoff = min(end, as_of - timedelta(days=maturity_days))
    if cutoff < start:
        return {}
    group = "ol.product_id" if by_product else "NULL::text"
    rows = await session.execute(
        text(f"""
            SELECT {group} AS key,
                sum(ol.quantity * ol.unit_price - ol.discount_amount) AS net_sales,
                coalesce(sum(rr.refunds), 0) AS refunds
            FROM order_lines ol
            LEFT JOIN (
                SELECT r.order_line_id, sum(r.refund_amount) AS refunds
                FROM returns r JOIN order_lines l ON l.dataset_id = r.dataset_id
                    AND l.order_line_id = r.order_line_id
                WHERE r.dataset_id = :d AND r.return_date <= l.order_date + :maturity
                GROUP BY r.order_line_id
            ) rr ON rr.order_line_id = ol.order_line_id
            WHERE ol.dataset_id = :d AND ol.status = 'completed'
              AND ol.order_date BETWEEN :start AND :cutoff
            GROUP BY 1
        """),  # noqa: S608 - `group` is one of two constants
        {"d": dataset_id, "start": start, "cutoff": cutoff, "maturity": maturity_days},
    )
    return {row.key: (Decimal(row.refunds), Decimal(row.net_sales)) for row in rows}


async def customer_stats(
    session: AsyncSession, dataset_id: UUID, as_of: date, lookback_days: int
) -> list[CustomerStats]:
    """Per-customer purchase history as of `as_of` (completed lines; returns ≤ as_of)."""
    window_start = as_of - timedelta(days=lookback_days)
    params = {"d": dataset_id, "as_of": as_of, "window_start": window_start}
    lines = await session.execute(
        text("""
            SELECT customer_id,
                array_agg(DISTINCT order_date ORDER BY order_date) AS order_dates,
                count(DISTINCT order_id) AS lifetime_orders,
                count(DISTINCT order_id) FILTER (WHERE order_date > :window_start) AS frequency,
                coalesce(sum(quantity * unit_price - discount_amount)
                    FILTER (WHERE order_date > :window_start), 0) AS window_net_sales,
                sum(quantity) AS units_sold
            FROM order_lines
            WHERE dataset_id = :d AND status = 'completed' AND order_date <= :as_of
            GROUP BY customer_id ORDER BY customer_id
        """),
        params,
    )
    returns = await session.execute(
        text("""
            SELECT ol.customer_id,
                coalesce(sum(r.refund_amount) FILTER (WHERE ol.order_date > :window_start), 0)
                    AS window_refunds,
                coalesce(sum(r.quantity_returned), 0) AS units_returned,
                count(DISTINCT r.order_line_id) AS returned_lines
            FROM returns r JOIN order_lines ol ON ol.dataset_id = r.dataset_id
                AND ol.order_line_id = r.order_line_id
            WHERE r.dataset_id = :d AND r.return_date <= :as_of AND ol.order_date <= :as_of
            GROUP BY ol.customer_id
        """),
        params,
    )
    by_customer = {row.customer_id: row for row in returns}
    stats = []
    for row in lines:
        ret = by_customer.get(row.customer_id)
        stats.append(
            CustomerStats(
                customer_id=row.customer_id,
                order_dates=tuple(row.order_dates),
                lifetime_orders=int(row.lifetime_orders),
                frequency=int(row.frequency),
                monetary=Decimal(row.window_net_sales)
                - (Decimal(ret.window_refunds) if ret else 0),
                units_sold=int(row.units_sold),
                units_returned=int(ret.units_returned) if ret else 0,
                returned_lines=int(ret.returned_lines) if ret else 0,
            )
        )
    return stats


@dataclass(frozen=True)
class WindowLine:
    order_line_id: str
    order_id: str
    customer_id: str
    slices: tuple[tuple[str, str | None], ...]


async def window_lines(
    session: AsyncSession, dataset_id: UUID, start: date, end: date, *, with_segments: bool
) -> list[WindowLine]:
    """Completed lines of a window with their slice memberships (order sets, record counts)."""
    segment = "sm.segment" if with_segments else "NULL::text"
    join = (
        "LEFT JOIN analytics_segment_map sm ON sm.customer_id = ol.customer_id"
        if with_segments
        else ""
    )
    rows = await session.execute(
        text(f"""
            SELECT ol.order_line_id, ol.order_id, ol.customer_id, p.category, ol.product_id,
                   ol.region, ol.channel, {segment} AS segment
            FROM order_lines ol
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            {join}
            WHERE ol.dataset_id = :d AND ol.status = 'completed'
              AND ol.order_date BETWEEN :start AND :end
        """),  # noqa: S608
        {"d": dataset_id, "start": start, "end": end},
    )
    return [
        WindowLine(
            r.order_line_id,
            r.order_id,
            r.customer_id,
            (
                ("overall", None),
                ("category", r.category),
                ("product", r.product_id),
                ("region", r.region),
                ("channel", r.channel),
                ("customer_segment", r.segment),
            ),
        )
        for r in rows
    ]


def slice_filter(dimension: str, value: str | None) -> tuple[str, dict[str, Any]]:
    """SQL predicate over aliases ol/p/sm for one slice (dimension from the allowlist)."""
    if dimension == "overall":
        return "TRUE", {}
    if dimension not in DIMENSION_SQL:
        raise ValueError(dimension)
    return f"{DIMENSION_SQL[dimension]} = :slice_value", {"slice_value": value}


async def source_samples(
    session: AsyncSession,
    dataset_id: UUID,
    dimension: str,
    value: str | None,
    start: date,
    end: date,
    *,
    returns: bool,
    order_by: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Top contributing source records of a slice in a window, with file and source_row."""
    predicate, extra = slice_filter(dimension, value)
    join = (
        "LEFT JOIN analytics_segment_map sm ON sm.customer_id = ol.customer_id"
        if dimension == "customer_segment"
        else ""
    )
    if returns:
        sql = f"""
            SELECT 'returns' AS file, r.source_row, r.return_id AS key, r.refund_amount AS amount,
                   r.reason_code AS detail
            FROM returns r
            JOIN order_lines ol ON ol.dataset_id = r.dataset_id
                AND ol.order_line_id = r.order_line_id
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id {join}
            WHERE r.dataset_id = :d AND r.return_date BETWEEN :start AND :end AND {predicate}
            ORDER BY {order_by} DESC, r.source_row LIMIT :limit
        """  # noqa: S608
    else:
        sql = f"""
            SELECT 'orders' AS file, ol.source_row, ol.order_line_id AS key,
                   ol.quantity * ol.unit_price - ol.discount_amount AS amount,
                   ol.product_id AS detail
            FROM order_lines ol
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id {join}
            WHERE ol.dataset_id = :d AND ol.status = 'completed'
              AND ol.order_date BETWEEN :start AND :end AND {predicate}
            ORDER BY {order_by} DESC, ol.source_row LIMIT :limit
        """  # noqa: S608
    rows = await session.execute(
        text(sql), {"d": dataset_id, "start": start, "end": end, "limit": limit, **extra}
    )
    return [
        {
            "file": r.file,
            "source_row": r.source_row,
            "key": r.key,
            "amount": str(r.amount),
            "detail": r.detail,
        }
        for r in rows
    ]


async def reason_mix(
    session: AsyncSession,
    dataset_id: UUID,
    dimension: str,
    value: str | None,
    baseline: tuple[date, date],
    current: tuple[date, date],
) -> dict[str, dict[str, Decimal]]:
    """Refunds by reason code in baseline vs current window for one slice."""
    predicate, extra = slice_filter(dimension, value)
    join = (
        "LEFT JOIN analytics_segment_map sm ON sm.customer_id = ol.customer_id"
        if dimension == "customer_segment"
        else ""
    )
    rows = await session.execute(
        text(f"""
            SELECT r.reason_code,
                coalesce(sum(r.refund_amount)
                    FILTER (WHERE r.return_date BETWEEN :bs AND :be), 0) AS b,
                coalesce(sum(r.refund_amount)
                    FILTER (WHERE r.return_date BETWEEN :cs AND :ce), 0) AS w
            FROM returns r
            JOIN order_lines ol ON ol.dataset_id = r.dataset_id
                AND ol.order_line_id = r.order_line_id
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id {join}
            WHERE r.dataset_id = :d AND r.return_date BETWEEN :bs AND :ce AND {predicate}
            GROUP BY r.reason_code ORDER BY r.reason_code
        """),  # noqa: S608
        {
            "d": dataset_id,
            "bs": baseline[0],
            "be": baseline[1],
            "cs": current[0],
            "ce": current[1],
            **extra,
        },
    )
    return {r.reason_code: {"baseline": Decimal(r.b), "current": Decimal(r.w)} for r in rows}


async def products_missing_cost(session: AsyncSession, dataset_id: UUID) -> list[str]:
    rows = await session.scalars(
        text(
            "SELECT product_id FROM products WHERE dataset_id = :d AND unit_cost IS NULL "
            "ORDER BY product_id"
        ),
        {"d": dataset_id},
    )
    return list(rows)


async def product_names(session: AsyncSession, dataset_id: UUID) -> dict[str, tuple[str, str]]:
    rows = await session.execute(
        text("SELECT product_id, product_name, category FROM products WHERE dataset_id = :d"),
        {"d": dataset_id},
    )
    return {r.product_id: (r.product_name, r.category) for r in rows}
