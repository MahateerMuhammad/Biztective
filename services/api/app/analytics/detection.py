"""Deterministic incident detection (ARCHITECTURE §5, AMD-10). Pure functions only.

Input is weekly `Totals` per slice; output is ranked incidents with explicit statistics.
No entity names, dates or expected outcomes are referenced anywhere in this module.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, localcontext
from statistics import median
from typing import Literal

from app.analytics.config import AnalysisConfig
from app.analytics.metrics import MetricValue, Totals, amount, analytics_context, derive
from app.analytics.periods import Windows

Kind = Literal["amount", "count", "rate"]
Direction = Literal["down", "up"]
Severity = Literal["critical", "high", "medium", "low"]
SliceKey = tuple[str, str | None]  # (dimension, value); ("overall", None) for the whole business

# ARCHITECTURE §5.2: (kind, adverse direction, window value is an aggregate ratio)
METRICS: dict[str, tuple[Kind, Direction, bool]] = {
    "net_revenue": ("amount", "down", False),
    "gross_margin_pct": ("rate", "down", True),
    "refund_rate": ("rate", "up", True),
    "discount_rate": ("rate", "up", True),
    "active_customers": ("count", "down", False),
    "repeat_customer_rate": ("rate", "down", True),
    "aov": ("amount", "down", True),  # a ratio of sums (A11), thresholds as an amount
}
APPLICABLE: dict[str, tuple[str, ...]] = {
    "overall": tuple(METRICS),
    "category": tuple(METRICS),
    "region": tuple(METRICS),
    "channel": tuple(METRICS),
    "product": ("net_revenue", "gross_margin_pct", "refund_rate", "discount_rate"),
    "customer_segment": ("net_revenue", "active_customers", "aov", "refund_rate"),
}
MAD_SCALE = Decimal("1.4826")


def weekly_value(metric: str, totals: Totals) -> Decimal | None:
    value = derive(totals)[metric]
    return None if value is None else Decimal(value)


@dataclass(frozen=True)
class Flag:
    metric: str
    dimension: str
    value: str | None
    kind: Kind
    direction: Direction
    baseline_median: Decimal
    mad: Decimal
    sigma: Decimal
    current: Decimal
    z: float | None
    delta_abs: Decimal
    delta_rel: Decimal | None
    impact: Decimal
    onset_week: date | None
    baseline_weekly: tuple[tuple[date, Decimal | None], ...]
    current_weekly: tuple[tuple[date, Decimal | None], ...]
    baseline_aggregate: Decimal | None
    support: dict[str, int]
    context: dict[str, object] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.metric}|{self.dimension}|{self.value if self.value is not None else '*'}"

    @property
    def slice(self) -> SliceKey:
        return (self.dimension, self.value)


@dataclass(frozen=True)
class SliceSkip:
    metric: str
    dimension: str
    value: str | None
    reason: Literal["low_support", "insufficient_baseline", "undefined_current"]


@dataclass(frozen=True)
class Incident:
    flag: Flag
    rank: int
    severity: Severity
    status: Literal["investigated", "not_investigated"]


def _sum(weeks: tuple[date, ...], series: Mapping[date, Totals]) -> Totals:
    total = Totals()
    for week in weeks:
        total = total + series.get(week, Totals())
    return total


def _impact(
    metric: str,
    m: Decimal,
    c: Decimal,
    w_total: Totals,
    b_total: Totals,
    n_w: int,
) -> Decimal | None:
    """ARCHITECTURE §5.5 impact formulas; None when a needed ratio is undefined."""
    w, b = derive(w_total), derive(b_total)
    nrpac_b = (
        amount(b, "net_revenue") / b_total.active_customers if b_total.active_customers else None
    )

    def rate(metrics: dict[str, MetricValue], name: str) -> Decimal | None:
        value = metrics[name]
        return None if value is None else Decimal(value)

    match metric:
        case "net_revenue":
            return n_w * m - amount(w, "net_revenue")
        case "gross_margin_pct":
            mb, mw = rate(b, "gross_margin_pct"), rate(w, "gross_margin_pct")
            return None if mb is None or mw is None else (mb - mw) * amount(w, "net_revenue")
        case "refund_rate":
            rb, rw = rate(b, "refund_rate"), rate(w, "refund_rate")
            return None if rb is None or rw is None else (rw - rb) * amount(w, "net_sales")
        case "discount_rate":
            db, dw = rate(b, "discount_rate"), rate(w, "discount_rate")
            return None if db is None or dw is None else (dw - db) * w_total.gross_sales
        case "active_customers":
            return None if nrpac_b is None else (m - c) * n_w * nrpac_b
        case "repeat_customer_rate":
            pb, pw = rate(b, "repeat_customer_rate"), rate(w, "repeat_customer_rate")
            if nrpac_b is None or pb is None or pw is None:
                return None
            return (pb - pw) * w_total.active_customers * nrpac_b
        case "aov":
            ab, aw = rate(b, "aov"), rate(w, "aov")
            return None if ab is None or aw is None else (ab - aw) * w_total.orders
    raise ValueError(metric)


def _passes_change(
    kind: Kind, m: Decimal, delta_abs: Decimal, delta_rel: Decimal | None, config: AnalysisConfig
) -> bool:
    if kind == "rate":
        if abs(delta_abs) < config.rate_abs_threshold_pp:
            return False
        # m = 0: any qualifying absolute change is an infinite relative change (AMD-10).
        return delta_rel is None or abs(delta_rel) >= config.rate_rel_threshold
    return delta_rel is not None and abs(delta_rel) >= config.rel_change_threshold


def evaluate_slice(
    metric: str,
    slice_key: SliceKey,
    series: Mapping[date, Totals],
    windows: Windows,
    materiality: Decimal,
    config: AnalysisConfig,
) -> Flag | SliceSkip | None:
    """Return a Flag if the slice breaches every condition, a SliceSkip if it cannot be
    tested, or None when it was tested and is normal."""
    dimension, value = slice_key
    kind, direction, aggregate = METRICS[metric]
    b_total = _sum(windows.baseline, series)
    w_total = _sum(windows.current, series)
    if (
        b_total.orders < config.min_slice_orders_baseline
        or w_total.orders < config.min_slice_orders_current
        or (metric == "refund_rate" and w_total.units_sold < config.min_units_current_refund_rate)
    ):
        return SliceSkip(metric, dimension, value, "low_support")

    with localcontext(analytics_context()):
        b_weekly = tuple(
            (wk, weekly_value(metric, series.get(wk, Totals()))) for wk in windows.baseline
        )
        w_weekly = tuple(
            (wk, weekly_value(metric, series.get(wk, Totals()))) for wk in windows.current
        )
        defined = [v for _, v in b_weekly if v is not None]
        if len(defined) < config.min_baseline_weeks:
            return SliceSkip(metric, dimension, value, "insufficient_baseline")
        if aggregate:
            current = weekly_value(metric, w_total)
        else:
            w_values = [v for _, v in w_weekly if v is not None]
            current = sum(w_values, Decimal(0)) / len(w_values) if w_values else None
        if current is None:
            return SliceSkip(metric, dimension, value, "undefined_current")

        m = Decimal(median(defined))
        mad = Decimal(median(abs(v - m) for v in defined))
        sigma = MAD_SCALE * mad
        delta_abs = current - m
        delta_rel = delta_abs / abs(m) if m != 0 else None
        adverse = delta_abs < 0 if direction == "down" else delta_abs > 0
        z = float(delta_abs / sigma) if sigma > 0 else None
        if not adverse:
            return None
        if z is not None and abs(z) < float(config.z_threshold):
            return None
        if not _passes_change(kind, m, delta_abs, delta_rel, config):
            return None
        impact = _impact(metric, m, current, w_total, b_total, len(windows.current))
        if impact is None or impact <= 0 or impact < materiality:
            return None
        onset = _onset(w_weekly, m, sigma, kind, direction, config)
        b_aggregate = weekly_value(metric, b_total) if aggregate else None

    return Flag(
        metric=metric,
        dimension=dimension,
        value=value,
        kind=kind,
        direction=direction,
        baseline_median=m,
        mad=mad,
        sigma=sigma,
        current=current,
        z=round(z, 4) if z is not None else None,
        delta_abs=delta_abs,
        delta_rel=delta_rel,
        impact=impact,
        onset_week=onset,
        baseline_weekly=b_weekly,
        current_weekly=w_weekly,
        baseline_aggregate=b_aggregate,
        support={
            "baseline_orders": b_total.orders,
            "current_orders": w_total.orders,
            "current_units": w_total.units_sold,
        },
    )


def _onset(
    weekly: tuple[tuple[date, Decimal | None], ...],
    m: Decimal,
    sigma: Decimal,
    kind: Kind,
    direction: Direction,
    config: AnalysisConfig,
) -> date | None:
    sign = -1 if direction == "down" else 1
    for week, v in weekly:
        if v is None:
            continue
        delta = v - m
        if sign * delta <= 0:
            continue
        if sigma > 0:
            if abs(delta) >= 3 * sigma:
                return week
        elif _passes_change(kind, m, delta, delta / abs(m) if m != 0 else None, config):
            return week
    return None


def severity(impact: Decimal, overall_net_revenue_w: Decimal, config: AnalysisConfig) -> Severity:
    if overall_net_revenue_w <= 0:
        return "critical"
    with localcontext(analytics_context()):
        share = impact / overall_net_revenue_w
    if share >= config.severity_critical_share:
        return "critical"
    if share >= config.severity_high_share:
        return "high"
    if share >= config.severity_medium_share:
        return "medium"
    return "low"


def _rank_key(flag: Flag) -> tuple[Decimal, float, str]:
    return (-flag.impact, -abs(flag.z or 0.0), flag.key)


def jaccard(a: frozenset[str], b: frozenset[str]) -> Decimal:
    union = len(a | b)
    return Decimal(len(a & b)) / Decimal(union) if union else Decimal(0)


def consolidate(
    flags: list[Flag],
    order_sets: Callable[[SliceKey], frozenset[str]],
    overall_net_revenue_w: Decimal,
    config: AnalysisConfig,
) -> list[Incident]:
    """Parent suppression, overlap merge, ranking and severity (ARCHITECTURE §5.6)."""
    kept: list[Flag] = []
    for metric in sorted({f.metric for f in flags}):
        group = sorted((f for f in flags if f.metric == metric), key=_rank_key)
        parent = next((f for f in group if f.dimension == "overall"), None)
        if parent is not None:
            children = [
                f
                for f in group
                if f.dimension != "overall"
                and f.impact >= config.child_share_threshold * parent.impact
            ]
            if children:
                parent_info = {"key": parent.key, "impact": str(parent.impact)}
                group = [
                    replace(f, context={**f.context, "parent": parent_info}) if f in children else f
                    for f in group
                    if f is not parent
                ]
        merged: list[Flag] = []
        for flag in group:
            target = next(
                (
                    i
                    for i, k in enumerate(merged)
                    if k.dimension != flag.dimension
                    and k.dimension != "overall"
                    and flag.dimension != "overall"
                    and jaccard(order_sets(k.slice), order_sets(flag.slice))
                    >= config.overlap_jaccard_threshold
                ),
                None,
            )
            if target is None:
                merged.append(flag)
                continue
            primary = merged[target]
            previous = primary.context.get("related_slices", [])
            related = [*(previous if isinstance(previous, list) else []), flag.key]
            merged[target] = replace(
                primary, context={**primary.context, "related_slices": related}
            )
        kept.extend(merged)

    ranked = sorted(kept, key=_rank_key)[: config.max_incidents]
    return [
        Incident(
            flag=flag,
            rank=i + 1,
            severity=severity(flag.impact, overall_net_revenue_w, config),
            status="investigated" if i < config.max_investigated_incidents else "not_investigated",
        )
        for i, flag in enumerate(ranked)
    ]


def product_slices(baseline_net_sales: dict[str, Decimal], config: AnalysisConfig) -> list[str]:
    """Products making up the top `product_slice_coverage` of baseline net sales (≤ max)."""
    total = sum((v for v in baseline_net_sales.values() if v > 0), Decimal(0))
    if total <= 0:
        return []
    chosen: list[str] = []
    covered = Decimal(0)
    for product, value in sorted(baseline_net_sales.items(), key=lambda kv: (-kv[1], kv[0])):
        if (
            covered >= config.product_slice_coverage * total
            or len(chosen) >= config.max_product_slices
        ):
            break
        if value <= 0:
            break
        chosen.append(product)
        covered += value
    return chosen
