"""Analytics API (AMD-10): metrics, equal-period comparison, products, customers, incidents."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import queries
from app.analytics.config import CODE_VERSION, load_config
from app.analytics.metrics import (
    Totals,
    amount,
    analytics_context,
    change,
    derive,
    fraction,
    money,
    present,
    ratio,
)
from app.analytics.periods import Period, detection_windows
from app.analytics.segmentation import SEGMENTS
from app.analytics.service import request_analysis, restriction
from app.audit.writer import record_audit
from app.auth.dependencies import client_host
from app.core.db import DbSession
from app.core.errors import ApiError
from app.core.pagination import Cursor, Limit, decode_cursor, encode_cursor
from app.models import AnalysisRevision, CustomerSegment, Dataset, EvidenceItem, Incident
from app.tenancy.dependencies import RequirePermission, TenantContext
from app.tenancy.permissions import Permission

router = APIRouter(prefix="/businesses/{business_id}/datasets/{dataset_id}", tags=["analytics"])

Reader = Annotated[TenantContext, Depends(RequirePermission(Permission.ANALYTICS_READ))]
Runner = Annotated[TenantContext, Depends(RequirePermission(Permission.ANALYSIS_RUN))]
Dimension = Literal[
    "overall", "category", "product", "region", "channel", "customer_segment", "acquisition_channel"
]
FromDate = Annotated[date | None, Query(alias="from")]
ToDate = Annotated[date | None, Query(alias="to")]
MAX_RANGE_DAYS = 1100


class AnalyticsResponse(BaseModel):
    """Common envelope: every number is computed from stored records, never estimated."""

    dataset_id: UUID
    currency: str
    as_of: date | None
    period: dict[str, Any] | None
    restrictions: list[dict[str, Any]]
    data: Any


async def _dataset(db: AsyncSession, tenant: TenantContext, dataset_id: UUID) -> Dataset:
    dataset = await db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.business_id == tenant.business_id)
    )
    if dataset is None:
        raise ApiError(404, "not_found", "Resource not found.")
    if dataset.status != "ready":
        raise ApiError(409, "dataset_not_ready", "Analytics are available only for ready datasets.")
    return dataset


async def _latest_revision(db: AsyncSession, dataset_id: UUID) -> AnalysisRevision | None:
    return await db.scalar(
        select(AnalysisRevision)
        .where(AnalysisRevision.dataset_id == dataset_id)
        .order_by(
            (AnalysisRevision.code_version == CODE_VERSION).desc(),
            AnalysisRevision.created_at.desc(),
        )
        .limit(1)
    )


async def _completed_revision(db: AsyncSession, dataset_id: UUID) -> AnalysisRevision:
    revision = await _latest_revision(db, dataset_id)
    if revision is None or revision.status not in ("completed", "insufficient_data"):
        raise ApiError(409, "analysis_not_ready", "The analysis for this dataset has not finished.")
    return revision


def _period(start: date | None, end: date | None, default: Period) -> Period:
    period = Period(start or default.start, end or default.end)
    if period.end < period.start:
        raise ApiError(422, "invalid_period", "'from' must be on or before 'to'.")
    if period.days > MAX_RANGE_DAYS:
        raise ApiError(422, "invalid_period", f"Periods are limited to {MAX_RANGE_DAYS} days.")
    return period


def _period_restrictions(
    period: Period, bounds: KnownBounds, maturity_days: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if period.start < bounds.first_order or period.end > bounds.last_activity:
        out.append(
            restriction(
                "incomplete_period",
                f"The period extends outside the data ({bounds.first_order} to "
                f"{bounds.last_activity}); "
                "days without data count as zero sales.",
                ["all"],
            )
        )
    if period.end > bounds.as_of - timedelta(days=maturity_days):
        out.append(
            restriction(
                "immature_returns",
                f"Orders from the last {maturity_days} days before {bounds.as_of} may still be "
                "returned; refund metrics for this period can rise.",
                ["refunds", "refund_rate", "unit_return_rate", "net_revenue"],
            )
        )
    if bounds.cancelled_lines:
        out.append(
            restriction(
                "cancelled_excluded",
                f"{bounds.cancelled_lines} cancelled order lines are excluded from all sales "
                f"metrics.",
            )
        )
    return out


def _cost_restriction(rows: list[Totals], missing: list[str]) -> list[dict[str, Any]]:
    if any(t.lines_missing_cost or t.restockable_returns_missing_cost for t in rows):
        shown = ", ".join(missing[:10]) + ("…" if len(missing) > 10 else "")
        return [
            restriction(
                "missing_costs",
                f"Products without unit cost ({shown}) appear in this scope; COGS, gross profit "
                f"and "
                "margin are null where they are needed.",
                ["cogs_sold", "cogs_restocked", "gross_profit", "gross_margin_pct"],
            )
        ]
    return []


async def _prepare_dimension(db: AsyncSession, dataset_id: UUID, dimension: str) -> tuple[str, ...]:
    if dimension == "overall":
        return ()
    if dimension == "customer_segment":
        revision = await _completed_revision(db, dataset_id)
        await db.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS analytics_segment_map "
                "(customer_id text PRIMARY KEY, segment text NOT NULL) ON COMMIT DROP"
            )
        )
        await db.execute(text("TRUNCATE analytics_segment_map"))
        await db.execute(
            text(
                "INSERT INTO analytics_segment_map SELECT customer_id, segment FROM "
                "customer_segments "
                "WHERE revision_id = :r"
            ),
            {"r": revision.id},
        )
        await db.execute(text("ANALYZE analytics_segment_map"))
    return (dimension,)


@dataclass(frozen=True)
class KnownBounds:
    """Dataset bounds for a dataset that has completed orders (all dates defined)."""

    first_order: date
    as_of: date
    last_activity: date
    cancelled_lines: int


async def _bounds(db: AsyncSession, dataset_id: UUID) -> KnownBounds:
    b = await queries.dataset_bounds(db, dataset_id)
    if b.as_of is None or b.first_order is None or b.last_activity is None:
        raise ApiError(409, "no_completed_orders", "The dataset has no completed order lines.")
    return KnownBounds(b.first_order, b.as_of, b.last_activity, b.cancelled_lines)


def _rows(aggregates: dict[queries.SliceRow, Totals], dimension: str) -> list[dict[str, Any]]:
    rows = []
    for (week, dim, value), totals in sorted(
        aggregates.items(),
        key=lambda kv: (kv[0][0] or date.min, kv[0][1] != "overall", kv[0][2] or ""),
    ):
        if dimension != "overall" and dim == "overall":
            continue
        rows.append(
            {"week": week, "dimension": dim, "value": value, "metrics": present(derive(totals))}
        )
    return rows


@router.get("/metrics")
async def get_metrics(
    db: DbSession,
    tenant: Reader,
    dataset_id: UUID,
    start: FromDate = None,
    end: ToDate = None,
    granularity: Literal["total", "week"] = "total",
    dimension: Dimension = "overall",
) -> AnalyticsResponse:
    dataset = await _dataset(db, tenant, dataset_id)
    bounds = await _bounds(db, dataset_id)
    config = await load_config(db)
    period = _period(start, end, Period(bounds.first_order, bounds.last_activity))
    dims = await _prepare_dimension(db, dataset_id, dimension)
    aggregates = await queries.aggregate(
        db, dataset_id, period.start, period.end, dimensions=dims, by_week=granularity == "week"
    )
    restrictions = _period_restrictions(period, bounds, config.return_maturity_days)
    restrictions += _cost_restriction(
        list(aggregates.values()), await queries.products_missing_cost(db, dataset_id)
    )
    return AnalyticsResponse(
        dataset_id=dataset.id,
        currency=dataset.currency,
        as_of=bounds.as_of,
        period={
            "from": period.start,
            "to": period.end,
            "granularity": granularity,
            "dimension": dimension,
        },
        restrictions=restrictions,
        data=_rows(aggregates, dimension),
    )


@router.get("/metrics/comparison")
async def compare_periods(
    db: DbSession,
    tenant: Reader,
    dataset_id: UUID,
    start: FromDate = None,
    end: ToDate = None,
    dimension: Dimension = "overall",
) -> AnalyticsResponse:
    """Current period vs the immediately preceding period of equal length (AMD-10)."""
    dataset = await _dataset(db, tenant, dataset_id)
    bounds = await _bounds(db, dataset_id)
    config = await load_config(db)
    windows = detection_windows(bounds.first_order, bounds.as_of, config.current_weeks, 0)
    default = (
        Period(windows.current[0], windows.end)
        if windows
        else Period(bounds.first_order, bounds.last_activity)
    )
    current = _period(start, end, default)
    previous = current.previous()
    dims = await _prepare_dimension(db, dataset_id, dimension)
    restrictions = _period_restrictions(current, bounds, config.return_maturity_days)
    cur = await queries.aggregate(db, dataset_id, current.start, current.end, dimensions=dims)
    prev: dict[queries.SliceRow, Totals] | None = None
    if previous.start < bounds.first_order:
        restrictions.append(
            restriction(
                "comparison_unavailable",
                f"The comparison period {previous.start} to {previous.end} starts before the first "
                f"order ({bounds.first_order}); it is not compared on partial data.",
                ["change"],
            )
        )
    else:
        prev = await queries.aggregate(
            db, dataset_id, previous.start, previous.end, dimensions=dims
        )
    restrictions += _cost_restriction(
        [*cur.values(), *(prev or {}).values()], await queries.products_missing_cost(db, dataset_id)
    )
    rows = []
    keys = sorted(
        {(d, v) for (_, d, v) in [*cur, *(prev or {})]},
        key=lambda k: (k[0] != "overall", k[1] or ""),
    )
    for dim, value in keys:
        if dimension != "overall" and dim == "overall":
            continue
        c = derive(cur.get((None, dim, value), Totals()))
        p = derive(prev.get((None, dim, value), Totals())) if prev is not None else None
        rows.append(
            {
                "dimension": dim,
                "value": value,
                "current": present(c),
                "previous": present(p) if p is not None else None,
                "change": {name: change(name, c[name], p[name]) for name in c}
                if p is not None
                else None,
            }
        )
    return AnalyticsResponse(
        dataset_id=dataset.id,
        currency=dataset.currency,
        as_of=bounds.as_of,
        period={
            "current": {"from": current.start, "to": current.end},
            "previous": {"from": previous.start, "to": previous.end, "available": prev is not None},
            "dimension": dimension,
        },
        restrictions=restrictions,
        data=rows,
    )


@router.get("/products")
async def product_performance(
    db: DbSession,
    tenant: Reader,
    dataset_id: UUID,
    start: FromDate = None,
    end: ToDate = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AnalyticsResponse:
    """Per-product sales, margin and return rates (M15, M16 period view; M21 matured cohort)."""
    dataset = await _dataset(db, tenant, dataset_id)
    bounds = await _bounds(db, dataset_id)
    config = await load_config(db)
    period = _period(start, end, Period(bounds.first_order, bounds.last_activity))
    aggregates = await queries.aggregate(
        db, dataset_id, period.start, period.end, dimensions=("product",)
    )
    names = await queries.product_names(db, dataset_id)
    matured = await queries.matured_cohort_refund_rates(
        db,
        dataset_id,
        period.start,
        period.end,
        bounds.as_of,
        config.return_maturity_days,
        by_product=True,
    )
    ranked: list[tuple[Decimal, str, dict[str, Any]]] = []
    for (_, dim, value), totals in aggregates.items():
        if dim != "product" or value is None:
            continue
        refunds, net_sales = matured.get(value, (Decimal(0), Decimal(0)))
        cohort = ratio(refunds, net_sales)
        metrics = derive(totals)
        name, category = names.get(value, ("", ""))
        ranked.append(
            (
                amount(metrics, "net_revenue"),
                value,
                {
                    "product_id": value,
                    "product_name": name,
                    "category": category,
                    "metrics": {
                        **present(metrics),
                        "matured_cohort_refund_rate": fraction(cohort)
                        if cohort is not None
                        else None,
                    },
                },
            )
        )
    ranked.sort(key=lambda r: (-r[0], r[1]))
    products = [row for _, _, row in ranked]
    restrictions = _period_restrictions(period, bounds, config.return_maturity_days)
    restrictions += _cost_restriction(
        list(aggregates.values()), await queries.products_missing_cost(db, dataset_id)
    )
    return AnalyticsResponse(
        dataset_id=dataset.id,
        currency=dataset.currency,
        as_of=bounds.as_of,
        period={"from": period.start, "to": period.end, "products": len(products)},
        restrictions=restrictions,
        data=products[:limit],
    )


@router.get("/segments")
async def segment_summary(db: DbSession, tenant: Reader, dataset_id: UUID) -> AnalyticsResponse:
    dataset = await _dataset(db, tenant, dataset_id)
    revision = await _completed_revision(db, dataset_id)
    config = await load_config(db, revision.config_version)
    rows = await db.execute(
        select(CustomerSegment.segment, func.count(), func.sum(CustomerSegment.monetary_365))
        .where(CustomerSegment.revision_id == revision.id)
        .group_by(CustomerSegment.segment)
    )
    by_segment = {s: (int(n), Decimal(m)) for s, n, m in rows}
    total = sum(n for n, _ in by_segment.values())
    with localcontext(analytics_context()):
        data = [
            {
                "segment": s,
                "customers": by_segment.get(s, (0, Decimal(0)))[0],
                "share": fraction(Decimal(by_segment.get(s, (0, 0))[0]) / total) if total else None,
                "monetary_365": money(by_segment.get(s, (0, Decimal(0)))[1]),
            }
            for s in SEGMENTS
        ]
    return AnalyticsResponse(
        dataset_id=dataset.id,
        currency=dataset.currency,
        as_of=revision.as_of_date,
        period=None,
        restrictions=[
            r for r in revision.restrictions if r["code"] in ("low_sample", "cancelled_excluded")
        ],
        data={
            "revision_id": revision.id,
            "customers": total,
            "segments": data,
            "rules": {
                "dormant": f"no purchase for > {config.dormant_days} days, or > "
                f"{config.dormant_gap_multiplier}x the customer's median gap (≥ 3 orders)",
                "insufficient_history": f"< 3 orders, or first order < {config.min_history_days} "
                "days ago",
                "at_risk": f"> {config.churn_gap_multiplier}x the customer's median gap and > "
                f"{config.churn_min_recency_days} days since the last order",
                "loyal": "3+ orders and purchasing on their usual cadence",
                "note": "Segments describe purchase timing; none of them asserts that a "
                "customer churned.",
            },
        },
    )


class CustomerRow(BaseModel):
    customer_id: str
    segment: str
    r_score: int
    f_score: int
    m_score: int
    first_order_date: date
    last_order_date: date
    recency_days: int
    lifetime_orders: int
    frequency_365: int
    monetary_365: str
    median_gap_days: str | None
    flags: list[str]


@router.get("/customers")
async def list_customers(
    db: DbSession,
    tenant: Reader,
    dataset_id: UUID,
    limit: Limit = 50,
    cursor: Cursor = None,
    segment: Literal["loyal", "at_risk", "dormant", "insufficient_history"] | None = None,
) -> dict[str, Any]:
    await _dataset(db, tenant, dataset_id)
    revision = await _completed_revision(db, dataset_id)
    query = (
        select(CustomerSegment)
        .where(CustomerSegment.revision_id == revision.id)
        .order_by(CustomerSegment.customer_id)
        .limit(limit + 1)
    )
    if segment:
        query = query.where(CustomerSegment.segment == segment)
    if cursor:
        query = query.where(CustomerSegment.customer_id > decode_cursor(cursor))
    rows = list((await db.scalars(query)).all())
    page = rows[:limit]
    return {
        "revision_id": revision.id,
        "items": [
            CustomerRow(
                customer_id=r.customer_id,
                segment=r.segment,
                r_score=r.r_score,
                f_score=r.f_score,
                m_score=r.m_score,
                first_order_date=r.first_order_date,
                last_order_date=r.last_order_date,
                recency_days=r.recency_days,
                lifetime_orders=r.lifetime_orders,
                frequency_365=r.frequency_365,
                monetary_365=money(r.monetary_365),
                median_gap_days=str(r.median_gap_days) if r.median_gap_days is not None else None,
                flags=list(r.flags),
            ).model_dump(mode="json")
            for r in page
        ],
        "next_cursor": encode_cursor(page[-1].customer_id) if len(rows) > limit else None,
    }


def _revision_out(
    revision: AnalysisRevision, incidents: list[Incident], evidence: list[EvidenceItem]
) -> dict[str, Any]:
    by_incident: dict[UUID, list[dict[str, Any]]] = {}
    for e in evidence:
        by_incident.setdefault(e.incident_id, []).append(
            {"id": e.id, "kind": e.kind, "result_hash": e.result_hash}
        )
    return {
        "id": revision.id,
        "status": revision.status,
        "config_version": revision.config_version,
        "code_version": revision.code_version,
        "as_of": revision.as_of_date,
        "current_window": [revision.current_window.lower, revision.current_window.upper]
        if revision.current_window
        else None,
        "baseline_window": [revision.baseline_window.lower, revision.baseline_window.upper]
        if revision.baseline_window
        else None,
        "restrictions": revision.restrictions,
        "summary": revision.summary,
        "revision_hash": revision.revision_hash,
        "error_code": revision.error_code,
        "completed_at": revision.completed_at,
        "incidents": [
            {
                "id": i.id,
                "key": i.key,
                "rank": i.rank,
                "severity": i.severity,
                "status": i.status,
                "metric": i.metric,
                "dimension": i.dimension,
                "value": i.dimension_value,
                "impact": money(i.impact),
                "affected": i.affected,
                "context": i.context,
                "current_value": i.stats.get("current_value"),
                "baseline_median": i.stats.get("baseline_median"),
                "z": i.stats.get("z"),
                "onset_week": i.stats.get("onset_week"),
                "evidence": by_incident.get(i.id, []),
            }
            for i in incidents
        ],
    }


@router.get("/analysis")
async def get_analysis(db: DbSession, tenant: Reader, dataset_id: UUID) -> dict[str, Any]:
    await _dataset(db, tenant, dataset_id)
    revision = await _latest_revision(db, dataset_id)
    if revision is None:
        raise ApiError(404, "not_found", "No analysis has been requested for this dataset.")
    incidents = list(
        (
            await db.scalars(
                select(Incident).where(Incident.revision_id == revision.id).order_by(Incident.rank)
            )
        ).all()
    )
    evidence = list(
        (
            await db.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.revision_id == revision.id)
                .order_by(EvidenceItem.created_at, EvidenceItem.id)
            )
        ).all()
    )
    return _revision_out(revision, incidents, evidence)


@router.post("/analysis", status_code=202)
async def run_analysis(
    request: Request, db: DbSession, tenant: Runner, dataset_id: UUID
) -> JSONResponse:
    await _dataset(db, tenant, dataset_id)
    revision_id = await request_analysis(db, tenant.business_id, dataset_id, tenant.user.id)
    await record_audit(
        db,
        action="analysis.requested",
        actor_user_id=tenant.user.id,
        business_id=tenant.business_id,
        target_type="analysis_revision",
        target_id=str(revision_id),
        client_host=client_host(request),
    )
    status = await db.scalar(
        select(AnalysisRevision.status).where(AnalysisRevision.id == revision_id)
    )
    await db.commit()
    return JSONResponse({"id": str(revision_id), "status": status}, status_code=202)


@router.get("/incidents/{incident_id}")
async def get_incident(
    db: DbSession, tenant: Reader, dataset_id: UUID, incident_id: UUID
) -> dict[str, Any]:
    await _dataset(db, tenant, dataset_id)
    incident = await db.scalar(
        select(Incident)
        .join(AnalysisRevision, AnalysisRevision.id == Incident.revision_id)
        .where(Incident.id == incident_id, AnalysisRevision.dataset_id == dataset_id)
    )
    if incident is None:
        raise ApiError(404, "not_found", "Resource not found.")
    evidence = (
        await db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.incident_id == incident.id)
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
        )
    ).all()
    return {
        "id": incident.id,
        "key": incident.key,
        "rank": incident.rank,
        "severity": incident.severity,
        "status": incident.status,
        "metric": incident.metric,
        "dimension": incident.dimension,
        "value": incident.dimension_value,
        "impact": money(incident.impact),
        "affected": incident.affected,
        "context": incident.context,
        "stats": incident.stats,
        "evidence": [
            {
                "id": e.id,
                "kind": e.kind,
                "params": e.params,
                "result": e.result,
                "result_hash": e.result_hash,
            }
            for e in evidence
        ],
    }
