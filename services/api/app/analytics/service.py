"""Analytics orchestration: analysis runs (segments, detection, evidence) and read models."""

import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import Any

from psycopg import AsyncConnection
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics import queries
from app.analytics.config import CODE_VERSION, CURRENT_CONFIG_VERSION, AnalysisConfig, load_config
from app.analytics.detection import (
    APPLICABLE,
    Flag,
    Incident,
    SliceKey,
    SliceSkip,
    consolidate,
    evaluate_slice,
    product_slices,
)
from app.analytics.evidence import (
    canonical_json,
    detection_evidence,
    reason_mix_evidence,
    sha256_of,
)
from app.analytics.metrics import Totals, amount, analytics_context, derive, money, present
from app.analytics.periods import Windows, detection_windows
from app.analytics.segmentation import CustomerProfile, distribution, segment_customers
from app.core.db import set_business_context
from app.jobs import queue
from app.jobs.queue import LeasedJob
from app.jobs.worker import PermanentJobError
from app.models import AnalysisRevision, EvidenceItem
from app.models import Incident as IncidentRow

DETECTION_DIMENSIONS = ("category", "product", "region", "channel", "customer_segment")
REFUND_METRICS = frozenset({"refund_rate"})


def restriction(code: str, message: str, affects: Sequence[str] = ()) -> dict[str, Any]:
    return {"code": code, "message": message, "affects": list(affects)}


# --------------------------------------------------------------------------- requesting runs


async def request_analysis(
    session: AsyncSession,
    business_id: uuid.UUID,
    dataset_id: uuid.UUID,
    requested_by: uuid.UUID | None,
) -> uuid.UUID:
    """Idempotent per (dataset, config version, code version); re-queues a failed run."""
    existing = await session.scalar(
        select(AnalysisRevision)
        .where(
            AnalysisRevision.dataset_id == dataset_id,
            AnalysisRevision.config_version == CURRENT_CONFIG_VERSION,
            AnalysisRevision.code_version == CODE_VERSION,
        )
        .with_for_update()
    )
    if existing is not None and existing.status != "failed":
        return existing.id
    if existing is None:
        revision_id = uuid.uuid7()
        await session.execute(
            insert(AnalysisRevision).values(
                id=revision_id,
                business_id=business_id,
                dataset_id=dataset_id,
                config_version=CURRENT_CONFIG_VERSION,
                code_version=CODE_VERSION,
                status="queued",
                requested_by=requested_by,
            )
        )
        dedupe = f"analyze_dataset:{revision_id}"
    else:
        revision_id = existing.id
        await session.execute(
            update(AnalysisRevision)
            .where(AnalysisRevision.id == revision_id)
            .values(status="queued", error_code=None)
        )
        dedupe = f"analyze_dataset:{revision_id}:{uuid.uuid7()}"
    await queue.enqueue(
        session,
        job_type="analyze_dataset",
        business_id=business_id,
        payload={"revision_id": str(revision_id)},
        dedupe_key=dedupe,
    )
    return revision_id


# --------------------------------------------------------------------------- analysis run


@dataclass
class AnalysisResult:
    status: str
    as_of: date | None
    windows: Windows | None
    profiles: list[CustomerProfile]
    incidents: list[Incident]
    evidence: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]]
    affected: dict[str, dict[str, int]]
    snapshot: list[dict[str, Any]]
    restrictions: list[dict[str, Any]]
    summary: dict[str, Any]
    materiality: Decimal | None = None
    skipped: dict[str, int] = field(default_factory=dict)


def _series_by_slice(weekly: dict[queries.SliceRow, Totals]) -> dict[SliceKey, dict[date, Totals]]:
    series: dict[SliceKey, dict[date, Totals]] = defaultdict(dict)
    for (week, dimension, value), totals in weekly.items():
        if week is not None:
            series[(dimension, value)][week] = totals
    return series


async def compute_analysis(
    session: AsyncSession, dataset_id: uuid.UUID, config: AnalysisConfig
) -> AnalysisResult:
    bounds = await queries.dataset_bounds(session, dataset_id)
    restrictions: list[dict[str, Any]] = []
    if bounds.cancelled_lines:
        restrictions.append(
            restriction(
                "cancelled_excluded",
                f"{bounds.cancelled_lines} cancelled order lines (value "
                f"{money(bounds.cancelled_value)})"
                " are excluded from every sales metric.",
            )
        )
    missing = await queries.products_missing_cost(session, dataset_id)
    if missing:
        restrictions.append(
            restriction(
                "missing_costs",
                f"{len(missing)} products have no unit cost; COGS, gross profit and margin are "
                "unavailable wherever they are sold or restocked.",
                ["cogs_sold", "cogs_restocked", "gross_profit", "gross_margin_pct"],
            )
        )
    if bounds.as_of is None or bounds.first_order is None:
        restrictions.append(restriction("insufficient_history", "No completed order lines."))
        return AnalysisResult(
            "insufficient_data", None, None, [], [], {}, {}, [], restrictions, {"customers": 0}
        )

    with localcontext(analytics_context()):
        as_of = bounds.as_of
        profiles = segment_customers(
            await queries.customer_stats(session, dataset_id, as_of, config.rfm_lookback_days),
            as_of,
            config,
        )
        if len(profiles) < config.low_sample_customers:
            restrictions.append(
                restriction(
                    "low_sample",
                    f"Only {len(profiles)} customers; RFM quintiles are coarse.",
                    ["segments"],
                )
            )

        overall_weekly = await queries.aggregate(
            session, dataset_id, bounds.first_order, as_of, by_week=True
        )
        snapshot = [
            {"week": week, **present(derive(totals))}
            for (week, _, _), totals in sorted(
                overall_weekly.items(), key=lambda kv: kv[0][0] or date.min
            )
        ]
        summary: dict[str, Any] = {
            "customers": len(profiles),
            "segments": distribution(profiles),
            "first_order_date": bounds.first_order,
            "as_of": as_of,
            "cancelled_lines": bounds.cancelled_lines,
            "products_missing_cost": missing[:50],
        }

        windows = detection_windows(
            bounds.first_order, as_of, config.current_weeks, config.baseline_weeks
        )
        active_baseline_weeks = 0
        if windows is not None:
            weeks_with_orders = {r[0] for r, t in overall_weekly.items() if t.orders > 0}
            active_baseline_weeks = sum(1 for w in windows.baseline if w in weeks_with_orders)
        if windows is None or active_baseline_weeks < config.min_baseline_weeks:
            restrictions.append(
                restriction(
                    "insufficient_history",
                    f"Detection needs {config.current_weeks + config.min_baseline_weeks} complete "
                    f"weeks with orders ({config.min_baseline_weeks} baseline + "
                    f"{config.current_weeks} current); found {active_baseline_weeks} baseline "
                    f"weeks.",
                    ["incidents"],
                )
            )
            return AnalysisResult(
                "insufficient_data",
                as_of,
                windows,
                profiles,
                [],
                {},
                {},
                snapshot,
                restrictions,
                summary,
            )

        # Customer segments as of the end of the baseline, so slices don't depend on W.
        b_profiles = segment_customers(
            await queries.customer_stats(
                session, dataset_id, windows.baseline_end, config.rfm_lookback_days
            ),
            windows.baseline_end,
            config,
        )
        await queries.load_segment_map(session, ((p.customer_id, p.segment) for p in b_profiles))
        weekly = await queries.aggregate(
            session,
            dataset_id,
            windows.start,
            windows.end,
            dimensions=DETECTION_DIMENSIONS,
            by_week=True,
        )
        series = _series_by_slice(weekly)
        overall = series.get(("overall", None), {})
        w_overall = sum((overall.get(w, Totals()) for w in windows.current), Totals())
        nr_w = amount(derive(w_overall), "net_revenue")
        materiality = max(config.materiality_pct * nr_w, config.min_impact_amount)

        baseline_product_sales = {
            value: amount(
                derive(sum((s.get(w, Totals()) for w in windows.baseline), Totals())), "net_sales"
            )
            for (dimension, value), s in series.items()
            if dimension == "product" and value is not None
        }
        tested_products = set(product_slices(baseline_product_sales, config))
        flags: list[Flag] = []
        skipped: dict[str, int] = defaultdict(int)
        tested = 0
        for slice_key in sorted(series, key=lambda k: (k[0], k[1] or "")):
            dimension, value = slice_key
            if dimension != "overall" and value is None:
                continue  # e.g. customers without a baseline segment (first order after B)
            if dimension == "product" and value not in tested_products:
                continue
            for metric in APPLICABLE[dimension]:
                outcome = evaluate_slice(
                    metric, slice_key, series[slice_key], windows, materiality, config
                )
                if isinstance(outcome, SliceSkip):
                    skipped[outcome.reason] += 1
                else:
                    tested += 1
                    if isinstance(outcome, Flag):
                        flags.append(outcome)
        summary["detection"] = {
            "slices_tested": tested,
            "slices_skipped": dict(skipped),
            "flags_before_consolidation": len(flags),
            "materiality": money(materiality),
            "current_net_revenue": money(nr_w),
        }

        incidents: list[Incident] = []
        affected: dict[str, dict[str, int]] = {}
        evidence: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}
        if flags:
            lines = await queries.window_lines(
                session, dataset_id, windows.current[0], windows.end, with_segments=True
            )
            order_sets: dict[SliceKey, set[str]] = defaultdict(set)
            line_counts: dict[SliceKey, int] = defaultdict(int)
            customers: dict[SliceKey, set[str]] = defaultdict(set)
            for line in lines:
                for s in line.slices:
                    order_sets[s].add(line.order_id)
                    line_counts[s] += 1
                    customers[s].add(line.customer_id)
            incidents = consolidate(
                flags, lambda s: frozenset(order_sets.get(s, set())), nr_w, config
            )
            for incident in incidents:
                f = incident.flag
                w_total = sum((series[f.slice].get(w, Totals()) for w in windows.current), Totals())
                affected[f.key] = {
                    "order_lines": line_counts.get(f.slice, 0),
                    "orders": len(order_sets.get(f.slice, set())),
                    "customers": len(customers.get(f.slice, set())),
                    "returns": w_total.returns,
                }
                evidence[f.key] = await _incident_evidence(
                    session, dataset_id, f, windows, materiality, config, affected[f.key]
                )
        return AnalysisResult(
            "completed",
            as_of,
            windows,
            profiles,
            incidents,
            evidence,
            affected,
            snapshot,
            restrictions,
            summary,
            materiality,
            dict(skipped),
        )


async def _incident_evidence(
    session: AsyncSession,
    dataset_id: uuid.UUID,
    flag: Flag,
    windows: Windows,
    materiality: Decimal,
    config: AnalysisConfig,
    counts: dict[str, int],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    params = {"metric": flag.metric, "dimension": flag.dimension, "value": flag.value}
    items.append(
        (
            "incident_detection",
            params,
            detection_evidence(flag, windows, materiality, config.version),
        )
    )

    is_refund = flag.metric in REFUND_METRICS
    order_by = (
        "r.refund_amount"
        if is_refund
        else ("ol.discount_amount" if flag.metric == "discount_rate" else "amount")
    )
    samples = await queries.source_samples(
        session,
        dataset_id,
        flag.dimension,
        flag.value,
        windows.current[0],
        windows.end,
        returns=is_refund,
        order_by=order_by,
        limit=config.evidence_sample_size,
    )
    items.append(
        (
            "source_records",
            {
                **params,
                "dataset_id": str(dataset_id),
                "table": "returns" if is_refund else "order_lines",
                "window": [windows.current[0].isoformat(), windows.end.isoformat()],
                "filter": {"status": "completed", flag.dimension: flag.value}
                if flag.dimension != "overall"
                else {"status": "completed"},
                "order_by": order_by,
            },
            {"counts": counts, "sample": samples, "sample_size": len(samples)},
        )
    )

    if is_refund:
        mix = await queries.reason_mix(
            session,
            dataset_id,
            flag.dimension,
            flag.value,
            (windows.baseline[0], windows.baseline_end),
            (windows.current[0], windows.end),
        )
        items.append(("return_reason_mix", params, reason_mix_evidence(mix)))
    return items


def _profile_row(p: CustomerProfile) -> dict[str, Any]:
    return {
        "customer_id": p.customer_id,
        "segment": p.segment,
        "r_score": p.r_score,
        "f_score": p.f_score,
        "m_score": p.m_score,
        "first_order_date": p.first_order_date,
        "last_order_date": p.last_order_date,
        "recency_days": p.recency_days,
        "lifetime_orders": p.lifetime_orders,
        "frequency_365": p.frequency,
        "monetary_365": p.monetary,
        "median_gap_days": p.median_gap_days,
        "flags": list(p.flags),
    }


async def analyze_dataset(sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob) -> None:
    """Job handler. Everything is written in one transaction; a failure leaves nothing behind."""
    if job.business_id is None:
        raise PermanentJobError("missing_business")
    revision_id = uuid.UUID(job.payload["revision_id"])
    async with sessionmaker() as session, session.begin():
        await set_business_context(session, job.business_id)
        status = await session.scalar(
            select(AnalysisRevision.status)
            .where(AnalysisRevision.id == revision_id)
            .with_for_update()
        )
        if status not in ("queued", "running"):
            return
        await session.execute(
            update(AnalysisRevision)
            .where(AnalysisRevision.id == revision_id)
            .values(status="running")
        )

    async with sessionmaker() as session, session.begin():
        await set_business_context(session, job.business_id)
        revision = await session.scalar(
            select(AnalysisRevision).where(AnalysisRevision.id == revision_id).with_for_update()
        )
        if revision is None or revision.status != "running":
            return
        config = await load_config(session, revision.config_version)
        result = await compute_analysis(session, revision.dataset_id, config)
        await _persist(session, revision, result)


async def _persist(
    session: AsyncSession, revision: AnalysisRevision, result: AnalysisResult
) -> None:
    profile_rows = [_profile_row(p) for p in result.profiles]
    if profile_rows:
        await _insert_segments(session, revision, profile_rows)
    incident_hashes = []
    for incident in result.incidents:
        f = incident.flag
        incident_id = uuid.uuid7()
        evidence_hashes = []
        stats = next(r for kind, _, r in result.evidence[f.key] if kind == "incident_detection")
        await session.execute(
            insert(IncidentRow).values(
                id=incident_id,
                business_id=revision.business_id,
                revision_id=revision.id,
                key=f.key,
                metric=f.metric,
                dimension=f.dimension,
                dimension_value=f.value,
                rank=incident.rank,
                status=incident.status,
                severity=incident.severity,
                impact=f.impact,
                stats=_jsonable(stats),
                affected=_jsonable(result.affected[f.key]),
                context=_jsonable(f.context),
            )
        )
        for kind, params, payload in result.evidence[f.key]:
            digest = sha256_of(payload)
            evidence_hashes.append(digest)
            await session.execute(
                insert(EvidenceItem).values(
                    id=uuid.uuid7(),
                    business_id=revision.business_id,
                    revision_id=revision.id,
                    incident_id=incident_id,
                    kind=kind,
                    params=_jsonable(params),
                    result=_jsonable(payload),
                    result_hash=digest,
                )
            )
        incident_hashes.append(
            {
                "key": f.key,
                "rank": incident.rank,
                "severity": incident.severity,
                "status": incident.status,
                "impact": money(f.impact),
                "context": f.context,
                "affected": result.affected[f.key],
                "evidence": evidence_hashes,
            }
        )

    metrics_hash = sha256_of(result.snapshot)
    segments_hash = sha256_of(profile_rows)
    incidents_hash = sha256_of(incident_hashes)
    w = result.windows
    await session.execute(
        update(AnalysisRevision)
        .where(AnalysisRevision.id == revision.id)
        .values(
            status=result.status,
            as_of_date=result.as_of,
            current_window=func.daterange(w.current[0], w.end, "[]") if w else None,
            baseline_window=func.daterange(w.baseline[0], w.baseline_end, "[]")
            if w and w.baseline
            else None,
            restrictions=_jsonable(result.restrictions),
            summary=_jsonable(result.summary),
            metrics_hash=metrics_hash,
            segments_hash=segments_hash,
            incidents_hash=incidents_hash,
            revision_hash=sha256_of([metrics_hash, segments_hash, incidents_hash]),
            completed_at=datetime.now(UTC),
        )
    )


SEGMENT_COLUMNS = (
    "customer_id",
    "segment",
    "r_score",
    "f_score",
    "m_score",
    "first_order_date",
    "last_order_date",
    "recency_days",
    "lifetime_orders",
    "frequency_365",
    "monetary_365",
    "median_gap_days",
    "flags",
)


async def _insert_segments(
    session: AsyncSession, revision: AnalysisRevision, rows: list[dict[str, Any]]
) -> None:
    """COPY into a temp table, then one INSERT…SELECT through RLS (COPY is not allowed into RLS
    tables, and row-by-row inserts would cost one round trip per few customers)."""
    columns = ", ".join(SEGMENT_COLUMNS)
    await session.execute(
        text(
            f"CREATE TEMP TABLE stg_segments ON COMMIT DROP AS "  # noqa: S608 - constant columns
            f"SELECT {columns} FROM customer_segments WITH NO DATA"
        )
    )
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    pg: AsyncConnection[Any] = raw.driver_connection  # type: ignore[assignment]  # SQLAlchemy types it loosely
    async with pg.cursor() as cur, cur.copy(f"COPY stg_segments ({columns}) FROM STDIN") as copy:
        for row in rows:
            await copy.write_row([row[c] for c in SEGMENT_COLUMNS])
    await session.execute(
        text(f"""
            INSERT INTO customer_segments (business_id, revision_id, {columns})
            SELECT :business_id, :revision_id, {columns} FROM stg_segments
        """),  # noqa: S608 - column list is a module constant
        {"business_id": revision.business_id, "revision_id": revision.id},
    )


def _jsonable(value: Any) -> Any:
    return json.loads(canonical_json(value))


async def mark_analysis_failed(
    sessionmaker: async_sessionmaker[AsyncSession], job: LeasedJob, code: str
) -> None:
    if job.business_id is None:
        return
    async with sessionmaker() as session, session.begin():
        await set_business_context(session, job.business_id)
        await session.execute(
            update(AnalysisRevision)
            .where(
                AnalysisRevision.id == uuid.UUID(job.payload["revision_id"]),
                AnalysisRevision.status.in_(("queued", "running")),
            )
            .values(status="failed", error_code=code, completed_at=datetime.now(UTC))
        )
