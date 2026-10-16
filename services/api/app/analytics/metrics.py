"""Metric definitions (DATA_CONTRACTS §3.3 M01–M20, AMD-10). Pure functions over aggregates.

`Totals` holds additive sums for one scope (period × slice). Everything derived from it is
computed here, from unrounded sums, in exact decimal arithmetic. Undefined ratios are None.
"""

from dataclasses import dataclass, fields
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

ZERO = Decimal(0)
MONEY_PLACES = Decimal("0.0001")
RATIO_PLACES = Decimal("0.000001")
ANALYTICS_PRECISION = 60  # significant digits for intermediate decimal arithmetic

MetricValue = Decimal | int | None


def analytics_context() -> Context:
    ctx = Context(prec=ANALYTICS_PRECISION, rounding=ROUND_HALF_EVEN)
    return ctx


@dataclass(frozen=True)
class Totals:
    """Additive aggregates for a scope. Line sums are by order_date, return sums by return_date."""

    gross_sales: Decimal = ZERO
    discounts: Decimal = ZERO
    units_sold: int = 0
    cogs_sold_known: Decimal = ZERO  # Σ quantity × unit_cost over lines with a known cost
    lines_missing_cost: int = 0
    lines: int = 0
    orders: int = 0
    active_customers: int = 0
    repeat_customers: int = 0
    new_customers: int = 0
    refunds: Decimal = ZERO
    units_returned: int = 0
    cogs_restocked_known: Decimal = ZERO
    restockable_returns_missing_cost: int = 0
    returns: int = 0

    def __add__(self, other: Totals) -> Totals:
        """Combine disjoint scopes (e.g. weeks). Distinct counts add because each order and
        each customer-week belongs to exactly one week; that is how M19/M20 are defined."""
        return Totals(
            **{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)}
        )


def amount(metrics: dict[str, MetricValue], name: str) -> Decimal:
    """A metric that is always defined (sums, net revenue) as Decimal."""
    value = metrics[name]
    if value is None:
        raise ValueError(f"{name} is undefined")
    return Decimal(value)


def ratio(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext(analytics_context()):
        return Decimal(numerator) / Decimal(denominator)


def derive(t: Totals) -> dict[str, MetricValue]:
    """All per-scope metrics. COGS-based metrics need full cost coverage (AMD-10)."""
    with localcontext(analytics_context()):
        net_sales = t.gross_sales - t.discounts
        net_revenue = net_sales - t.refunds
        cogs_sold = t.cogs_sold_known if t.lines_missing_cost == 0 else None
        cogs_restocked = t.cogs_restocked_known if t.restockable_returns_missing_cost == 0 else None
        gross_profit = (
            net_revenue - cogs_sold + cogs_restocked
            if cogs_sold is not None and cogs_restocked is not None
            else None
        )
        margin = (
            ratio(gross_profit, net_revenue)
            if gross_profit is not None and net_revenue > 0
            else None
        )
        return {
            "gross_sales": t.gross_sales,  # M01
            "discounts": t.discounts,  # M02
            "net_sales": net_sales,  # M03
            "refunds": t.refunds,  # M04
            "net_revenue": net_revenue,  # M05
            "cogs_sold": cogs_sold,  # M06
            "cogs_restocked": cogs_restocked,  # M07
            "gross_profit": gross_profit,  # M08
            "gross_margin_pct": margin,  # M09
            "units_sold": t.units_sold,  # M10
            "units_returned": t.units_returned,  # M11
            "orders": t.orders,  # M12
            "aov": ratio(net_sales, t.orders),  # M13
            "discount_rate": ratio(t.discounts, t.gross_sales),  # M14
            "refund_rate": ratio(t.refunds, net_sales),  # M15
            "unit_return_rate": ratio(t.units_returned, t.units_sold),  # M16
            "active_customers": t.active_customers,  # M17
            "new_customers": t.new_customers,  # M18
            "repeat_customer_rate": ratio(t.repeat_customers, t.active_customers),  # M19
        }


RATIO_METRICS = frozenset(
    {
        "gross_margin_pct",
        "discount_rate",
        "refund_rate",
        "unit_return_rate",
        "repeat_customer_rate",
        "matured_cohort_refund_rate",
    }
)
COUNT_METRICS = frozenset(
    {"units_sold", "units_returned", "orders", "active_customers", "new_customers"}
)


def money(value: Decimal) -> str:
    return str(value.quantize(MONEY_PLACES, rounding=ROUND_HALF_EVEN))


def fraction(value: Decimal) -> str:
    return str(value.quantize(RATIO_PLACES, rounding=ROUND_HALF_EVEN))


def present_value(name: str, value: MetricValue) -> str | int | None:
    """JSON-safe value: counts as ints, ratios as 6-dp strings, money (incl. aov) as 4-dp."""
    if value is None or isinstance(value, int):
        return value
    return fraction(value) if name in RATIO_METRICS else money(value)


def present(metrics: dict[str, MetricValue]) -> dict[str, str | int | None]:
    return {name: present_value(name, value) for name, value in metrics.items()}


def change(name: str, current: MetricValue, previous: MetricValue) -> dict[str, Any]:
    """Absolute and relative change between equal periods; relative is null when prev = 0."""
    if current is None or previous is None:
        return {"abs": None, "rel": None}
    with localcontext(analytics_context()):
        delta = Decimal(current) - Decimal(previous)
        rel = delta / abs(Decimal(previous)) if previous != 0 else None
    abs_value: str | int | None = (
        int(delta)
        if isinstance(current, int) and isinstance(previous, int)
        else present_value(name, delta)
    )
    return {"abs": abs_value, "rel": fraction(rel) if rel is not None else None}
