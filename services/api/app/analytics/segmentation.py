"""Customer purchase cadence, RFM scores and lifecycle segments (DATA_CONTRACTS §4 + AMD-10).

Classification is deterministic and explainable. "dormant" and "at_risk" describe inactivity
relative to fixed thresholds or the customer's own cadence. Nothing here asserts that a
customer has churned.
"""

import math
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise
from statistics import median
from typing import Literal

from app.analytics.config import AnalysisConfig
from app.analytics.metrics import ratio

Segment = Literal["loyal", "at_risk", "dormant", "insufficient_history"]
SEGMENTS: tuple[Segment, ...] = ("loyal", "at_risk", "dormant", "insufficient_history")


@dataclass(frozen=True)
class CustomerStats:
    customer_id: str
    order_dates: tuple[date, ...]  # distinct completed order dates ≤ as-of, ascending
    lifetime_orders: int
    frequency: int  # distinct orders in (as_of − lookback, as_of]
    monetary: Decimal  # net revenue of lines in the lookback window, refunds through the line
    units_sold: int
    units_returned: int
    returned_lines: int


@dataclass(frozen=True)
class CustomerProfile:
    customer_id: str
    segment: Segment
    reason: str
    r_score: int
    f_score: int
    m_score: int
    first_order_date: date
    last_order_date: date
    recency_days: int
    lifetime_orders: int
    frequency: int
    monetary: Decimal
    median_gap_days: Decimal | None
    flags: tuple[str, ...] = field(default_factory=tuple)


def median_gap_days(order_dates: tuple[date, ...]) -> Decimal | None:
    """Median gap between consecutive distinct order dates; None with fewer than 2 dates."""
    gaps = [(b - a).days for a, b in pairwise(order_dates)]
    return Decimal(str(median(gaps))) if gaps else None


def quintile_scores(values: list[Decimal]) -> list[int]:
    """score = clamp(ceil(5 × rank_max / n), 1, 5); ties share the higher rank (§4)."""
    n = len(values)
    ordered = sorted(values)
    return [min(5, max(1, math.ceil(5 * bisect_right(ordered, v) / n))) for v in values]


def lifecycle(
    stats: CustomerStats, as_of: date, config: AnalysisConfig
) -> tuple[Segment, str, Decimal | None]:
    """AMD-10 rules, first match wins. Returns (segment, human-readable reason, median gap)."""
    first, last = stats.order_dates[0], stats.order_dates[-1]
    recency = (as_of - last).days
    gap = median_gap_days(stats.order_dates)
    if recency > config.dormant_days:
        return "dormant", f"no purchase for {recency} days (> {config.dormant_days})", gap
    if (
        stats.lifetime_orders >= 3
        and gap is not None
        and recency > config.dormant_gap_multiplier * gap
        and recency > config.churn_min_recency_days
    ):
        return (
            "dormant",
            f"{recency} days since last order, more than {config.dormant_gap_multiplier}x "
            f"the usual {gap}-day gap",
            gap,
        )
    if stats.lifetime_orders < 3 or gap is None:
        return "insufficient_history", "fewer than 3 orders: no purchase cadence yet", gap
    if (as_of - first).days < config.min_history_days:
        return (
            "insufficient_history",
            f"first order only {(as_of - first).days} days ago (< {config.min_history_days})",
            gap,
        )
    if recency > config.churn_gap_multiplier * gap and recency > config.churn_min_recency_days:
        return (
            "at_risk",
            f"overdue: {recency} days since last order vs a usual {gap}-day gap",
            gap,
        )
    return "loyal", f"repeat customer on cadence ({gap}-day median gap)", gap


def segment_customers(
    customers: list[CustomerStats], as_of: date, config: AnalysisConfig
) -> list[CustomerProfile]:
    if not customers:
        return []
    r_scores = quintile_scores([-Decimal((as_of - c.order_dates[-1]).days) for c in customers])
    f_scores = quintile_scores([Decimal(c.frequency) for c in customers])
    m_scores = quintile_scores([c.monetary for c in customers])

    total_sold = sum(c.units_sold for c in customers)
    business_return_rate = ratio(sum(c.units_returned for c in customers), total_sold)
    return_threshold = max(
        config.return_risk_min_rate,
        config.return_risk_multiplier * (business_return_rate or Decimal(0)),
    )
    total_monetary = sum((c.monetary for c in customers), Decimal(0))

    profiles = []
    for c, r, f, m in zip(customers, r_scores, f_scores, m_scores, strict=True):
        segment, reason, gap = lifecycle(c, as_of, config)
        flags = []
        rate = ratio(c.units_returned, c.units_sold)
        if c.returned_lines >= 2 and rate is not None and rate >= return_threshold:
            flags.append("return_risk")
        if total_monetary > 0 and c.monetary >= config.key_account_share * total_monetary:
            flags.append("key_account")
        profiles.append(
            CustomerProfile(
                customer_id=c.customer_id,
                segment=segment,
                reason=reason,
                r_score=r,
                f_score=f,
                m_score=m,
                first_order_date=c.order_dates[0],
                last_order_date=c.order_dates[-1],
                recency_days=(as_of - c.order_dates[-1]).days,
                lifetime_orders=c.lifetime_orders,
                frequency=c.frequency,
                monetary=c.monetary,
                median_gap_days=gap,
                flags=tuple(flags),
            )
        )
    return profiles


def distribution(profiles: list[CustomerProfile]) -> dict[Segment, int]:
    counts = Counter(p.segment for p in profiles)
    return {s: counts.get(s, 0) for s in SEGMENTS}
