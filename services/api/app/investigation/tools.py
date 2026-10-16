"""Read-only evidence tools (AMD-12). Strict parameter models, parameterised SQL, pure post-
processing. Scope (business, dataset, revision, incident slice and windows) is injected by the
runtime; tool arguments are enums and small integers only, so no model output can name a
tenant, a record or an SQL fragment. Every result carries typed `facts` (see facts.py).
"""

# Any is confined to JSON/SQL driver boundaries; tool arguments and rules are typed.

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, localcontext
from statistics import median
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import queries
from app.analytics.config import AnalysisConfig
from app.analytics.detection import MAD_SCALE
from app.analytics.metrics import (
    RATIO_METRICS,
    Totals,
    analytics_context,
    change,
    derive,
    present_value,
    ratio,
)
from app.analytics.periods import Period, Windows
from app.analytics.segmentation import SEGMENTS, segment_customers
from app.investigation.facts import Facts, Unit, entity_fact, fact

MetricName = Literal[
    "net_revenue",
    "gross_margin_pct",
    "refund_rate",
    "discount_rate",
    "active_customers",
    "repeat_customer_rate",
    "aov",
]
BreakdownDimension = Literal["none", "category", "product", "region", "channel", "customer_segment"]
ContributorDimension = Literal["product", "category", "region", "channel"]
QualityCheck = Literal[
    "zero_order_days",
    "weekly_volume",
    "near_duplicates",
    "cancellations",
    "missing_costs",
    "immature_returns",
]
ALL_CHECKS: tuple[QualityCheck, ...] = (
    "zero_order_days",
    "weekly_volume",
    "near_duplicates",
    "cancellations",
    "missing_costs",
    "immature_returns",
)
ToolName = Literal[
    "metric_breakdown",
    "period_comparison",
    "return_investigation",
    "segment_analysis",
    "customer_exposure",
    "evidence_lookup",
    "data_quality_check",
]
TOP_ROWS = 10  # rows kept in a breakdown's detail
TOP_FACTS = 5  # rows exposed as facts
QUALITY_REASONS = ("damaged", "defective")
LOGISTICS_REASONS = ("late_delivery", "wrong_item")
SEGMENT_JOIN = "LEFT JOIN analytics_segment_map sm ON sm.customer_id = ol.customer_id"


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MetricBreakdownParams(ToolParams):
    metric: MetricName
    by: BreakdownDimension


class PeriodComparisonParams(ToolParams):
    metrics: Annotated[list[MetricName], Field(min_length=1, max_length=7)]

    @field_validator("metrics")
    @classmethod
    def _unique(cls, value: list[MetricName]) -> list[MetricName]:
        if len(set(value)) != len(value):
            raise ValueError("metrics must be unique")
        return value


class ReturnInvestigationParams(ToolParams):
    by: ContributorDimension


class SegmentAnalysisParams(ToolParams):
    pass


class CustomerExposureParams(ToolParams):
    top_k: Annotated[int, Field(ge=1, le=20)]


class EvidenceLookupParams(ToolParams):
    evidence_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]


class DataQualityParams(ToolParams):
    checks: Annotated[list[QualityCheck], Field(min_length=1, max_length=6)]

    @field_validator("checks")
    @classmethod
    def _unique(cls, value: list[QualityCheck]) -> list[QualityCheck]:
        if len(set(value)) != len(value):
            raise ValueError("checks must be unique")
        return value


# ------------------------------------------------------------------------------ runtime scope


@dataclass(frozen=True)
class ToolScope:
    business_id: UUID
    dataset_id: UUID
    revision_id: UUID
    incident_id: UUID
    metric: str
    dimension: str
    value: str | None
    windows: Windows
    first_order: date
    as_of: date
    config: AnalysisConfig

    @property
    def where(self) -> tuple[str, str | None] | None:
        return None if self.dimension == "overall" else (self.dimension, self.value)

    def describe(self) -> dict[str, Any]:
        """The scope part of an evidence item's parameters (and of its cache key)."""
        return {
            "metric": self.metric,
            "dimension": self.dimension,
            "value": self.value,
            "baseline": [self.windows.baseline[0], self.windows.baseline_end],
            "current": [self.windows.current[0], self.windows.end],
        }


@dataclass
class ToolContext:
    """One transaction's view: the segment map temp table lives until commit."""

    session: AsyncSession
    scope: ToolScope
    _segments_loaded: bool = field(default=False)

    async def ensure_segments(self) -> None:
        """Customer → lifecycle segment as of the baseline end, exactly as detection used."""
        if self._segments_loaded:
            return
        end = self.scope.windows.baseline_end
        stats = await queries.customer_stats(
            self.session, self.scope.dataset_id, end, self.scope.config.rfm_lookback_days
        )
        profiles = segment_customers(stats, end, self.scope.config)
        await queries.load_segment_map(self.session, ((p.customer_id, p.segment) for p in profiles))
        self._segments_loaded = True

    async def slice_sql(self) -> tuple[str, dict[str, Any], str]:
        """(predicate, params, join) restricting aliases ol/p/sm to the incident slice."""
        scope = self.scope
        if scope.dimension == "overall":
            return "TRUE", {}, ""
        predicate, params = queries.slice_filter(scope.dimension, scope.value)
        if scope.dimension == "customer_segment":
            await self.ensure_segments()
            return predicate, params, SEGMENT_JOIN
        return predicate, params, ""


# --------------------------------------------------------------------------- metric families

VALUE_UNIT: dict[str, Unit] = {
    "net_revenue": "money",
    "aov": "money",
    "active_customers": "number",
    "gross_margin_pct": "ratio",
    "refund_rate": "ratio",
    "discount_rate": "ratio",
    "repeat_customer_rate": "ratio",
}
ADVERSE_UNIT: dict[str, Unit] = {
    "net_revenue": "money",
    "aov": "money",
    "active_customers": "number",
    "gross_margin_pct": "money",
    "refund_rate": "money",
    "discount_rate": "money",
    "repeat_customer_rate": "number",
}
# Additive numerator / denominator of the rate metrics: (numerator, denominator).
RATE_PARTS = {
    "refund_rate": ("refunds", "net_sales"),
    "discount_rate": ("discounts", "gross_sales"),
    "repeat_customer_rate": ("repeat_customers", "active_customers"),
    "aov": ("net_sales", "orders"),
}
# Values of these breakdowns overlap in customers, so customer counts don't add up across them.
NON_ADDITIVE = {("active_customers", "product"), ("active_customers", "category")}


def _part(t: Totals, name: str) -> Decimal:
    if name == "net_sales":
        return t.gross_sales - t.discounts
    return Decimal(getattr(t, name))


def _net_revenue(t: Totals) -> Decimal:
    return t.gross_sales - t.discounts - t.refunds


def window_value(metric: str, t: Totals, weeks: int) -> Decimal | None:
    """Weekly average for amounts and counts, aggregate ratio for rates (as detection)."""
    with localcontext(analytics_context()):
        if metric == "net_revenue":
            return _net_revenue(t) / weeks
        if metric == "active_customers":
            return Decimal(t.active_customers) / weeks
        value = derive(t)[metric]
        return None if value is None else Decimal(value)


def adverse_amount(
    metric: str, b: Totals, w: Totals, nb: int, nw: int, fallback_rate: Decimal | None
) -> Decimal | None:
    """How much worse the current window is than the baseline, in the metric's adverse
    direction (positive = adverse). Rates use their own baseline rate (or the slice's)."""
    with localcontext(analytics_context()):
        if metric == "net_revenue":
            return _net_revenue(b) / nb - _net_revenue(w) / nw
        if metric == "active_customers":
            return Decimal(b.active_customers) / nb - Decimal(w.active_customers) / nw
        if metric == "gross_margin_pct":
            mb, mw = derive(b)["gross_margin_pct"], derive(w)["gross_margin_pct"]
            if mb is None or mw is None:
                return None
            return (Decimal(mb) - Decimal(mw)) * _net_revenue(w)
        num, den = RATE_PARTS[metric]
        rate_b = ratio(_part(b, num), _part(b, den))
        rate_b = rate_b if rate_b is not None else fallback_rate
        if rate_b is None:
            return None
        if metric in ("refund_rate", "discount_rate"):
            return _part(w, num) - rate_b * _part(w, den)
        return rate_b * _part(w, den) - _part(w, num)


@dataclass(frozen=True)
class BreakdownRow:
    value: str | None
    baseline: Decimal | None
    current: Decimal | None
    adverse: Decimal | None
    share: Decimal | None


@dataclass(frozen=True)
class Breakdown:
    metric: str
    by: str | None
    total: BreakdownRow
    rows: list[BreakdownRow]  # ordered by adverse amount, largest first
    additive: bool

    @property
    def top_share(self) -> Decimal | None:
        return self.rows[0].share if self.rows else None


def _shares(adverse: dict[str | None, Decimal | None]) -> dict[str | None, Decimal | None]:
    """Each value's share of the total positive adverse amount (in [0, 1], sums to 1)."""
    positive = {k: max(v, Decimal(0)) for k, v in adverse.items() if v is not None}
    total = sum(positive.values(), Decimal(0))
    with localcontext(analytics_context()):
        return {k: (positive[k] / total if k in positive and total > 0 else None) for k in adverse}


async def breakdown(ctx: ToolContext, metric: str, by: str | None) -> Breakdown:
    scope = ctx.scope
    w = scope.windows
    if by == "customer_segment" or scope.dimension == "customer_segment":
        await ctx.ensure_segments()
    weekly = await queries.aggregate(
        ctx.session,
        scope.dataset_id,
        w.start,
        w.end,
        dimensions=(by,) if by else (),
        by_week=True,
        where=scope.where,
    )
    baseline_weeks, current_weeks = set(w.baseline), set(w.current)
    sums: dict[str | None, list[Totals]] = defaultdict(lambda: [Totals(), Totals()])
    total = [Totals(), Totals()]
    for (week, dimension, value), t in weekly.items():
        index = 0 if week in baseline_weeks else 1 if week in current_weeks else None
        if index is None:
            continue
        if dimension == "overall":
            total[index] = total[index] + t
        elif dimension == by:
            sums[value][index] = sums[value][index] + t
    nb, nw = len(w.baseline), len(w.current)
    rate_metric = metric in RATE_PARTS
    slice_rate = (
        ratio(_part(total[0], RATE_PARTS[metric][0]), _part(total[0], RATE_PARTS[metric][1]))
        if rate_metric
        else None
    )

    def row(value: str | None, b: Totals, cur: Totals) -> BreakdownRow:
        return BreakdownRow(
            value=value,
            baseline=window_value(metric, b, nb),
            current=window_value(metric, cur, nw),
            adverse=adverse_amount(metric, b, cur, nb, nw, slice_rate),
            share=None,
        )

    rows = [row(value, b, cur) for value, (b, cur) in sums.items()]
    shares = _shares({r.value: r.adverse for r in rows})
    rows = [
        BreakdownRow(r.value, r.baseline, r.current, r.adverse, shares.get(r.value)) for r in rows
    ]
    rows.sort(
        key=lambda r: (-(r.adverse if r.adverse is not None else Decimal("-1e30")), r.value or "")
    )
    return Breakdown(
        metric=metric,
        by=by,
        total=row(None, total[0], total[1]),
        rows=rows,
        additive=(metric, by) not in NON_ADDITIVE,
    )


def _change_unit(metric: str) -> Unit:
    return "pp" if metric in RATIO_METRICS else VALUE_UNIT[metric]


def _diff(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    with localcontext(analytics_context()):
        return None if a is None or b is None else a - b


def breakdown_facts(bd: Breakdown, prefix: str = "") -> Facts:
    m = bd.metric
    facts: Facts = {
        f"{prefix}total.baseline": fact(bd.total.baseline, VALUE_UNIT[m]),
        f"{prefix}total.current": fact(bd.total.current, VALUE_UNIT[m]),
        f"{prefix}total.change": fact(_diff(bd.total.current, bd.total.baseline), _change_unit(m)),
        f"{prefix}total.adverse": fact(bd.total.adverse, ADVERSE_UNIT[m]),
    }
    if bd.by is None:
        return facts
    facts[f"{prefix}values"] = fact(len(bd.rows), "count")
    facts[f"{prefix}additive"] = {"value": "yes" if bd.additive else "no", "unit": "code"}
    if bd.additive:
        facts[f"{prefix}top_share"] = fact(bd.top_share, "ratio")
    for i, r in enumerate(bd.rows[:TOP_FACTS], start=1):
        facts[f"{prefix}top.{i}.entity"] = entity_fact(bd.by, r.value)
        facts[f"{prefix}top.{i}.adverse"] = fact(r.adverse, ADVERSE_UNIT[m])
        facts[f"{prefix}top.{i}.baseline"] = fact(r.baseline, VALUE_UNIT[m])
        facts[f"{prefix}top.{i}.current"] = fact(r.current, VALUE_UNIT[m])
        if bd.additive:
            facts[f"{prefix}top.{i}.share"] = fact(r.share, "ratio")
    return facts


def _detail_rows(bd: Breakdown) -> list[dict[str, Any]]:
    def show(value: Decimal | None) -> str | None:
        with localcontext(analytics_context()):
            return None if value is None else str(value.quantize(Decimal("0.000001")))

    rows: list[dict[str, Any]] = [
        {
            "value": r.value,
            "baseline": show(r.baseline),
            "current": show(r.current),
            "adverse": show(r.adverse),
            "share": show(r.share),
        }
        for r in bd.rows[:TOP_ROWS]
    ]
    rest = bd.rows[TOP_ROWS:]
    if rest:
        rows.append(
            {
                "value": None,
                "other_values": len(rest),
                "adverse": show(sum((r.adverse or Decimal(0) for r in rest), Decimal(0))),
                "share": show(sum((r.share or Decimal(0) for r in rest), Decimal(0))),
            }
        )
    return rows


# ------------------------------------------------------------------------------------- tools


async def metric_breakdown(ctx: ToolContext, params: MetricBreakdownParams) -> dict[str, Any]:
    by = None if params.by == "none" else params.by
    bd = await breakdown(ctx, params.metric, by)
    return {
        "facts": breakdown_facts(bd),
        "detail": {
            "weeks": {
                "baseline": len(ctx.scope.windows.baseline),
                "current": len(ctx.scope.windows.current),
            },
            "rows": _detail_rows(bd),
        },
    }


async def period_comparison(ctx: ToolContext, params: PeriodComparisonParams) -> dict[str, Any]:
    scope = ctx.scope
    current = Period(scope.windows.current[0], scope.windows.end)
    previous = current.previous()
    restrictions = []
    if scope.dimension == "customer_segment":
        await ctx.ensure_segments()

    async def totals(period: Period) -> Totals:
        rows = await queries.aggregate(
            ctx.session, scope.dataset_id, period.start, period.end, where=scope.where
        )
        return rows.get((None, "overall", None), Totals())

    cur = derive(await totals(current))
    prev = derive(await totals(previous)) if previous.start >= scope.first_order else None
    if prev is None:
        restrictions.append("comparison_unavailable")
    facts: Facts = {}
    for metric in params.metrics:
        unit: Unit = "count" if metric == "active_customers" else VALUE_UNIT[metric]
        facts[f"{metric}.current"] = _presented(metric, cur[metric], unit)
        if prev is None:
            continue
        facts[f"{metric}.previous"] = _presented(metric, prev[metric], unit)
        delta = change(metric, cur[metric], prev[metric])
        facts[f"{metric}.change_abs"] = {
            "value": delta["abs"],
            "unit": "pp" if metric in RATIO_METRICS else unit,
        }
        facts[f"{metric}.change_rel"] = {"value": delta["rel"], "unit": "ratio"}
    return {
        "facts": facts,
        "detail": {
            "current": [current.start, current.end],
            "previous": [previous.start, previous.end],
            "restrictions": restrictions,
        },
    }


def _presented(metric: str, value: Any, unit: Unit) -> dict[str, Any]:
    with localcontext(analytics_context()):
        return {"value": present_value(metric, value), "unit": unit}


async def return_investigation(
    ctx: ToolContext, params: ReturnInvestigationParams
) -> dict[str, Any]:
    scope = ctx.scope
    w = scope.windows
    predicate, slice_params, join = await ctx.slice_sql()
    mix = await queries.reason_mix(
        ctx.session,
        scope.dataset_id,
        scope.dimension,
        scope.value,
        (w.baseline[0], w.baseline_end),
        (w.current[0], w.end),
    )
    facts: Facts = {}
    with localcontext(analytics_context()):
        total_b = sum((v["baseline"] for v in mix.values()), Decimal(0))
        total_w = sum((v["current"] for v in mix.values()), Decimal(0))
        facts["refunds.baseline_total"] = fact(total_b, "money")
        facts["refunds.current_total"] = fact(total_w, "money")

        def share(codes: tuple[str, ...] | str, column: str) -> Decimal | None:
            group = (codes,) if isinstance(codes, str) else codes
            total = total_b if column == "baseline" else total_w
            amount = sum((mix[c][column] for c in group if c in mix), Decimal(0))
            return amount / total if total > 0 else None

        for code in sorted(mix):
            sb, sw = share(code, "baseline"), share(code, "current")
            facts[f"reasons.{code}.current_share"] = fact(sw, "ratio")
            facts[f"reasons.{code}.share_change"] = fact(_diff(sw, sb), "pp")
        for name, codes in (("quality", QUALITY_REASONS), ("logistics", LOGISTICS_REASONS)):
            sb, sw = share(codes, "baseline"), share(codes, "current")
            facts[f"{name}.baseline_share"] = fact(sb, "ratio")
            facts[f"{name}.current_share"] = fact(sw, "ratio")
            facts[f"{name}.share_change"] = fact(_diff(sw, sb), "pp")

    contributors = await breakdown(ctx, "refund_rate", params.by)
    facts.update(breakdown_facts(contributors, prefix="contributors."))

    maturity = scope.config.return_maturity_days
    cutoff = min(w.end, scope.as_of - timedelta(days=maturity))
    rows = await ctx.session.execute(
        text(f"""
            SELECT (ol.order_date >= :w0) AS current_cohort,
                coalesce(sum(ol.quantity * ol.unit_price - ol.discount_amount), 0) AS net_sales,
                coalesce(sum(rr.refunds), 0) AS refunds,
                count(*) AS lines
            FROM order_lines ol
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            {join}
            LEFT JOIN (
                SELECT r.order_line_id, sum(r.refund_amount) AS refunds
                FROM returns r JOIN order_lines l ON l.dataset_id = r.dataset_id
                    AND l.order_line_id = r.order_line_id
                WHERE r.dataset_id = :d AND r.return_date <= l.order_date + :maturity
                  AND r.return_date BETWEEN :b0 AND :limit
                GROUP BY r.order_line_id
            ) rr ON rr.order_line_id = ol.order_line_id
            WHERE ol.dataset_id = :d AND ol.status = 'completed'
              AND (ol.order_date BETWEEN :b0 AND :b1 OR ol.order_date BETWEEN :w0 AND :cutoff)
              AND {predicate}
            GROUP BY 1
        """),  # noqa: S608 - predicate and join come from the dimension allowlist
        {
            "d": scope.dataset_id,
            "b0": w.baseline[0],
            "b1": w.baseline_end,
            "w0": w.current[0],
            "cutoff": cutoff,
            "maturity": maturity,
            "limit": cutoff + timedelta(days=maturity),
            **slice_params,
        },
    )
    cohorts = {bool(r.current_cohort): r for r in rows}
    rates: dict[bool, Decimal | None] = {}
    for key in (False, True):
        r = cohorts.get(key)
        rates[key] = ratio(Decimal(r.refunds), Decimal(r.net_sales)) if r is not None else None
        facts[f"cohort.{'current' if key else 'baseline'}_lines"] = fact(
            int(r.lines) if r is not None else 0, "count"
        )
    facts["cohort.baseline_rate"] = fact(rates[False], "ratio")
    facts["cohort.current_rate"] = fact(rates[True], "ratio")
    facts["cohort.change"] = fact(_diff(rates[True], rates[False]), "pp")
    return {
        "facts": facts,
        "detail": {
            "contributors_by": params.by,
            "contributors": _detail_rows(contributors),
            "cohort_window": {
                "baseline": [w.baseline[0], w.baseline_end],
                "current": [w.current[0], cutoff] if cutoff >= w.current[0] else None,
                "maturity_days": maturity,
            },
            "restrictions": [] if cutoff >= w.current[0] else ["immature_returns"],
        },
    }


async def segment_analysis(ctx: ToolContext, params: SegmentAnalysisParams) -> dict[str, Any]:
    scope = ctx.scope
    w = scope.windows
    await ctx.ensure_segments()
    weekly = await queries.aggregate(
        ctx.session,
        scope.dataset_id,
        w.start,
        w.end,
        dimensions=("customer_segment",),
        by_week=True,
        where=scope.where,
    )
    baseline_weeks, current_weeks = set(w.baseline), set(w.current)
    sums: dict[str, list[Totals]] = {s: [Totals(), Totals()] for s in (*SEGMENTS, "new")}
    for (week, dimension, value), t in weekly.items():
        if dimension != "customer_segment":
            continue
        index = 0 if week in baseline_weeks else 1 if week in current_weeks else None
        if index is not None:
            key = value or "new"  # first order after the baseline end: no segment yet
            sums[key][index] = sums[key][index] + t
    nb, nw = len(w.baseline), len(w.current)
    facts: Facts = {}
    declines: dict[str | None, Decimal | None] = {}
    with localcontext(analytics_context()):
        for segment, (b, cur) in sums.items():
            active_b = Decimal(b.active_customers) / nb
            active_w = Decimal(cur.active_customers) / nw
            decline = _net_revenue(b) / nb - _net_revenue(cur) / nw
            declines[segment] = decline
            p = f"segments.{segment}."
            facts[p + "active_baseline"] = fact(active_b, "number")
            facts[p + "active_current"] = fact(active_w, "number")
            facts[p + "active_change_rel"] = fact(
                (active_w - active_b) / active_b if active_b > 0 else None, "ratio"
            )
            facts[p + "revenue_decline"] = fact(decline, "money")
        established = ("loyal", "at_risk")
        est_b = sum((Decimal(sums[s][0].active_customers) for s in established), Decimal(0)) / nb
        est_w = sum((Decimal(sums[s][1].active_customers) for s in established), Decimal(0)) / nw
        facts["established.active_baseline"] = fact(est_b, "number")
        facts["established.active_current"] = fact(est_w, "number")
        facts["established.active_change_rel"] = fact(
            (est_w - est_b) / est_b if est_b > 0 else None, "ratio"
        )
        shares = _shares(declines)
        established_share = sum(
            (share for s in established if (share := shares.get(s)) is not None), Decimal(0)
        )
        any_decline = any(v is not None for v in shares.values())
        facts["established.revenue_decline_share"] = fact(
            established_share if any_decline else None, "ratio"
        )
    return {"facts": facts, "detail": {"segments_as_of": w.baseline_end}}


async def customer_exposure(ctx: ToolContext, params: CustomerExposureParams) -> dict[str, Any]:
    scope = ctx.scope
    w = scope.windows
    predicate, slice_params, join = await ctx.slice_sql()
    bind = {"d": scope.dataset_id, "b0": w.baseline[0], "w0": w.current[0], "w1": w.end}
    lines = await ctx.session.execute(
        text(f"""
            SELECT ol.customer_id, (ol.order_date >= :w0) AS cur,
                sum(ol.quantity * ol.unit_price) AS gross, sum(ol.discount_amount) AS discounts,
                count(*) AS lines
            FROM order_lines ol
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            {join}
            WHERE ol.dataset_id = :d AND ol.status = 'completed'
              AND ol.order_date BETWEEN :b0 AND :w1 AND {predicate}
            GROUP BY 1, 2
        """),  # noqa: S608 - predicate and join come from the dimension allowlist
        {**bind, **slice_params},
    )
    refunds = await ctx.session.execute(
        text(f"""
            SELECT ol.customer_id, (r.return_date >= :w0) AS cur, sum(r.refund_amount) AS refunds
            FROM returns r
            JOIN order_lines ol ON ol.dataset_id = r.dataset_id
                AND ol.order_line_id = r.order_line_id
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            {join}
            WHERE r.dataset_id = :d AND r.return_date BETWEEN :b0 AND :w1 AND {predicate}
            GROUP BY 1, 2
        """),  # noqa: S608
        {**bind, **slice_params},
    )
    per: dict[str, list[Totals]] = defaultdict(lambda: [Totals(), Totals()])
    for r in lines:
        per[r.customer_id][1 if r.cur else 0] = per[r.customer_id][1 if r.cur else 0] + Totals(
            gross_sales=Decimal(r.gross), discounts=Decimal(r.discounts), lines=int(r.lines)
        )
    for r in refunds:
        per[r.customer_id][1 if r.cur else 0] = per[r.customer_id][1 if r.cur else 0] + Totals(
            refunds=Decimal(r.refunds)
        )
    b_total = sum((v[0] for v in per.values()), Totals())
    nb, nw = len(w.baseline), len(w.current)
    family = scope.metric if scope.metric in ("refund_rate", "discount_rate") else "net_revenue"
    slice_rate = (
        ratio(_part(b_total, RATE_PARTS[family][0]), _part(b_total, RATE_PARTS[family][1]))
        if family in RATE_PARTS
        else None
    )
    contributions: dict[str, Decimal] = {}
    with localcontext(analytics_context()):
        for customer, (b, cur) in per.items():
            if family == "net_revenue":
                value = _net_revenue(b) / nb - _net_revenue(cur) / nw
            elif slice_rate is None:
                continue
            else:
                num, den = RATE_PARTS[family]
                value = _part(cur, num) - slice_rate * _part(cur, den)
            if value > 0:
                contributions[customer] = value
        total = sum(contributions.values(), Decimal(0))
        ranked = sorted(contributions.items(), key=lambda kv: (-kv[1], kv[0]))
        top = ranked[: params.top_k]
        top_share = sum((v for _, v in top), Decimal(0)) / total if total > 0 else None
        top1_share = ranked[0][1] / total if total > 0 else None
    current_customers = [c for c, (_, cur) in per.items() if cur.lines > 0]
    segments = await ctx.session.execute(
        text("""
            SELECT segment, count(*) FILTER (WHERE lifetime_orders >= 2) AS repeat_buyers,
                count(*) AS customers
            FROM customer_segments
            WHERE revision_id = :rev AND customer_id = ANY(:ids)
            GROUP BY segment ORDER BY segment
        """),
        {"rev": scope.revision_id, "ids": current_customers},
    )
    facts: Facts = {
        "customers.current": fact(len(current_customers), "count"),
        "customers.adverse": fact(len(contributions), "count"),
        "adverse.total": fact(total, ADVERSE_UNIT[family]),
        "top_k": fact(params.top_k, "count"),
        "top_k.share": fact(top_share, "ratio"),
        "top1.share": fact(top1_share, "ratio"),
    }
    repeat_buyers = 0
    for row in segments:
        repeat_buyers += int(row.repeat_buyers)
        facts[f"exposed.{row.segment}"] = fact(int(row.customers), "count")
    facts["repeat_buyers.current"] = fact(repeat_buyers, "count")
    return {
        "facts": facts,
        "detail": {
            "basis": family,
            # Pseudonymous customer IDs for people; never part of an LLM input (facts only).
            "top_customers": [
                {"customer_id": c, "adverse": str(v.quantize(Decimal("0.0001")))} for c, v in top
            ],
        },
    }


async def data_quality_check(ctx: ToolContext, params: DataQualityParams) -> dict[str, Any]:
    scope = ctx.scope
    w = scope.windows
    nb, nw = len(w.baseline), len(w.current)
    predicate, slice_params, join = await ctx.slice_sql()
    bind = {"d": scope.dataset_id, "b0": w.baseline[0], "b1": w.baseline_end, "w0": w.current[0]}
    bind["w1"] = w.end
    facts: Facts = {}
    checks = set(params.checks)
    with localcontext(analytics_context()):
        if "zero_order_days" in checks:
            row = (
                await ctx.session.execute(
                    text(f"""
                        SELECT count(*) FILTER (WHERE d < :w0) AS baseline,
                               count(*) FILTER (WHERE d >= :w0) AS current
                        FROM generate_series(CAST(:b0 AS date), CAST(:w1 AS date),
                            interval '1 day') d
                        WHERE NOT EXISTS (
                            SELECT 1 FROM order_lines ol
                            JOIN products p ON p.dataset_id = ol.dataset_id
                                AND p.product_id = ol.product_id
                            {join}
                            WHERE ol.dataset_id = :d AND {predicate}
                              AND ol.status = 'completed' AND ol.order_date = d::date)
                    """),  # noqa: S608 - predicate and join are server-side allowlisted
                    {**bind, **slice_params},
                )
            ).one()
            per_b = Decimal(row.baseline) / nb
            per_w = Decimal(row.current) / nw
            facts["zero_days.baseline"] = fact(int(row.baseline), "count")
            facts["zero_days.current"] = fact(int(row.current), "count")
            facts["zero_days.excess_per_week"] = fact(per_w - per_b, "number")
        if "weekly_volume" in checks:
            bd_weekly = await queries.aggregate(
                ctx.session,
                scope.dataset_id,
                w.start,
                w.end,
                by_week=True,
                where=scope.where,
            )
            lines = {week: t.lines for (week, _, _), t in bd_weekly.items() if week is not None}
            baseline = [Decimal(lines.get(week, 0)) for week in w.baseline]
            m = Decimal(median(baseline))
            sigma = MAD_SCALE * Decimal(median(abs(v - m) for v in baseline))
            zs = [
                (Decimal(lines.get(week, 0)) - m) / sigma if sigma > 0 else None
                for week in w.current
            ]
            low = sum(
                1
                for week, z in zip(w.current, zs, strict=True)
                if (z is not None and z <= -3)
                or (z is None and Decimal(lines.get(week, 0)) < m / 2)
            )
            defined = [z for z in zs if z is not None]
            facts["volume.baseline_median"] = fact(m, "number")
            facts["volume.min_z"] = fact(min(defined) if defined else None, "number")
            facts["volume.low_weeks"] = fact(low, "count")
        if "near_duplicates" in checks or "cancellations" in checks:
            row = (
                await ctx.session.execute(
                    text(f"""
                        WITH lines AS (
                            SELECT ol.status, ol.quantity * ol.unit_price - ol.discount_amount
                                    AS amount,
                                row_number() OVER (PARTITION BY ol.status, ol.customer_id,
                                    ol.product_id, ol.quantity, ol.unit_price, ol.order_date
                                    ORDER BY ol.order_line_id) AS copy
                            FROM order_lines ol
                            JOIN products p ON p.dataset_id = ol.dataset_id
                                AND p.product_id = ol.product_id
                            {join}
                            WHERE ol.dataset_id = :d AND ol.order_date BETWEEN :w0 AND :w1
                              AND {predicate}
                        )
                        SELECT count(*) FILTER (WHERE status = 'completed') AS completed,
                            count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                            count(*) FILTER (WHERE status = 'completed' AND copy > 1) AS dups,
                            coalesce(sum(amount) FILTER (WHERE status = 'completed' AND copy > 1),
                                0) AS dup_value
                        FROM lines
                    """),  # noqa: S608 - predicate and join come from the dimension allowlist
                    {**bind, **slice_params},
                )
            ).one()
            completed, cancelled = int(row.completed), int(row.cancelled)
            if "near_duplicates" in checks:
                facts["lines.current"] = fact(completed, "count")
                facts["near_duplicates.lines"] = fact(int(row.dups), "count")
                facts["near_duplicates.value"] = fact(Decimal(row.dup_value), "money")
                facts["near_duplicates.share"] = fact(ratio(int(row.dups), completed), "ratio")
            if "cancellations" in checks:
                facts["cancelled.lines"] = fact(cancelled, "count")
                facts["cancelled.share"] = fact(ratio(cancelled, completed + cancelled), "ratio")
        if "missing_costs" in checks:
            count = await ctx.session.scalar(
                text(f"""
                    SELECT count(DISTINCT ol.product_id)
                    FROM order_lines ol
                    JOIN products p ON p.dataset_id = ol.dataset_id
                        AND p.product_id = ol.product_id
                    {join}
                    WHERE ol.dataset_id = :d AND ol.status = 'completed'
                      AND ol.order_date BETWEEN :w0 AND :w1 AND p.unit_cost IS NULL
                      AND {predicate}
                """),  # noqa: S608
                {**bind, **slice_params},
            )
            facts["missing_costs.products"] = fact(int(count or 0), "count")
        if "immature_returns" in checks:
            immature_from = scope.as_of - timedelta(days=scope.config.return_maturity_days)
            days = max(0, (w.end - max(immature_from, w.current[0] - timedelta(days=1))).days)
            days = min(days, nw * 7)
            facts["immature.days"] = fact(days, "count")
            facts["immature.share"] = fact(ratio(days, nw * 7), "ratio")
    return {"facts": facts, "detail": {"checks": sorted(checks)}}


# --------------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class ToolSpec:
    name: ToolName
    params: type[ToolParams]
    description: str
    query_version: str
    run: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]] | None


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "metric_breakdown",
            MetricBreakdownParams,
            "Baseline vs current value of one metric in the incident slice, optionally split "
            "by a dimension, with each value's share of the adverse change.",
            "1",
            metric_breakdown,
        ),
        ToolSpec(
            "period_comparison",
            PeriodComparisonParams,
            "Metrics of the current window against the immediately preceding period of equal "
            "length (unavailable when that period starts before the data).",
            "1",
            period_comparison,
        ),
        ToolSpec(
            "return_investigation",
            ReturnInvestigationParams,
            "Refund reason mix (baseline vs current), the values explaining the excess refunds, "
            "and matured cohort refund rates.",
            "1",
            return_investigation,
        ),
        ToolSpec(
            "segment_analysis",
            SegmentAnalysisParams,
            "Active customers and net revenue per lifecycle segment (as of the baseline end), "
            "baseline vs current, and the established segments' share of the revenue decline.",
            "1",
            segment_analysis,
        ),
        ToolSpec(
            "customer_exposure",
            CustomerExposureParams,
            "How concentrated the adverse change is among customers: top-k share, counts of "
            "customers in scope, repeat buyers exposed.",
            "1",
            customer_exposure,
        ),
        ToolSpec(
            "evidence_lookup",
            EvidenceLookupParams,
            "Full facts of evidence items already collected for this incident.",
            "1",
            None,
        ),
        ToolSpec(
            "data_quality_check",
            DataQualityParams,
            "Days without orders, weekly volume anomalies, near-duplicate and cancelled lines, "
            "products without cost and immature returns in the current window.",
            "1",
            data_quality_check,
        ),
    )
}
