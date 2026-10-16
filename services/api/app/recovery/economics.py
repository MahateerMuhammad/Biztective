"""Read-only economics of an incident slice for recovery planning (AMD-17, DATA_CONTRACTS §14.2).

`slice_economics` (query version 1) stores the current-window volumes every estimate needs as
an immutable, hashed evidence item of the incident, cached by revision and scope exactly like
the investigation tools. Eligible customers for targeting are read from the same slice.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

from sqlalchemy import text

from app.analytics import queries
from app.analytics.evidence import sha256_of
from app.analytics.metrics import Totals, analytics_context, derive
from app.investigation.facts import Facts, fact, numeric
from app.investigation.store import cached_evidence, jsonable, store_tool_evidence
from app.investigation.tools import ToolContext
from app.recovery.estimator import Observed

KIND = "slice_economics"
QUERY_VERSION = "1"
MAX_ELIGIBLE = 5000


def _ratio(numerator: Decimal | None, denominator: Decimal | int | None) -> Decimal | None:
    if numerator is None or denominator is None or Decimal(denominator) <= 0:
        return None
    return numerator / Decimal(denominator)


async def slice_economics(ctx: ToolContext) -> dict[str, Any]:
    """Current-window volumes of the slice, plus its eligible customers (kept server-side:
    model inputs carry facts only, never this detail)."""
    scope = ctx.scope
    w = scope.windows
    if scope.dimension == "customer_segment":
        await ctx.ensure_segments()
    weekly = await queries.aggregate(
        ctx.session, scope.dataset_id, w.start, w.end, by_week=True, where=scope.where
    )
    baseline_weeks, current_weeks = set(w.baseline), set(w.current)
    b, cur = Totals(), Totals()
    for (week, _, _), t in weekly.items():
        if week in baseline_weeks:
            b = b + t
        elif week in current_weeks:
            cur = cur + t
    m = derive(cur)
    weeks = len(w.current)
    with localcontext(analytics_context()):
        net_revenue = Decimal(m["net_revenue"] or 0)
        gross_profit = m["gross_profit"]
        facts: Facts = {
            "weeks.current": fact(weeks, "count"),
            "gross_sales.current": fact(cur.gross_sales, "money"),
            "net_sales.current": fact(Decimal(m["net_sales"] or 0), "money"),
            "net_revenue.current": fact(net_revenue, "money"),
            "gross_profit.current": fact(
                Decimal(gross_profit) if gross_profit is not None else None, "money"
            ),
            "contribution_margin.current": fact(
                _ratio(Decimal(gross_profit), net_revenue) if gross_profit is not None else None,
                "ratio",
            ),
            "discount_rate.current": fact(_ratio(cur.discounts, cur.gross_sales), "ratio"),
            "discount_rate.baseline": fact(_ratio(b.discounts, b.gross_sales), "ratio"),
            "orders.current": fact(cur.orders, "count"),
            "customer_weeks.current": fact(cur.active_customers, "count"),
            "aov.current": fact(_ratio(Decimal(m["net_sales"] or 0), cur.orders), "money"),
        }
    return {
        "facts": facts,
        "detail": {
            "weeks": {"baseline": len(w.baseline), "current": weeks},
            "restrictions": [] if gross_profit is not None else ["missing_costs"],
            "eligible_customers": await eligible_customers(ctx),
        },
    }


async def economics_evidence(ctx: ToolContext) -> tuple[str, dict[str, Any]]:
    """(evidence id, result) of the slice's economics, computed once per revision and scope."""
    scope = ctx.scope
    params = jsonable({"arguments": {}, "scope": scope.describe()})
    params_hash = sha256_of({"tool": KIND, **params})
    existing = await cached_evidence(ctx.session, scope.revision_id, KIND, params_hash)
    if existing is None:
        result = await slice_economics(ctx)
        existing = await store_tool_evidence(
            ctx.session, scope, KIND, params, params_hash, QUERY_VERSION, result
        )
    row = (
        await ctx.session.execute(
            text("SELECT result FROM evidence_items WHERE id = :i"), {"i": existing}
        )
    ).one()
    return str(existing), dict(row.result)


@dataclass(frozen=True)
class Cited:
    """An observed value and where it was read from."""

    name: str
    value: Decimal | None
    unit: str
    evidence_id: str
    field: str


def observe(
    impact: Decimal, detection_id: str, economics_id: str, facts: Facts
) -> tuple[Observed, list[Cited]]:
    """Observed values for the estimator, each cited to its evidence field."""

    def get(name: str) -> Decimal | None:
        return numeric(facts.get(name))

    weeks = int(get("weeks.current") or 0) or 1
    with localcontext(analytics_context()):
        w = Decimal(weeks)
        net_revenue = get("net_revenue.current") or Decimal(0)
        gross_profit = get("gross_profit.current")
        current, baseline = get("discount_rate.current"), get("discount_rate.baseline")
        customer_weeks = get("customer_weeks.current") or Decimal(0)
        observed = Observed(
            weeks=weeks,
            weekly_excess_loss=impact / w,
            weekly_net_revenue=net_revenue / w,
            weekly_gross_sales=(get("gross_sales.current") or Decimal(0)) / w,
            excess_discount_rate=(current or Decimal(0)) - (baseline or Decimal(0)),
            contribution_margin=get("contribution_margin.current"),
            weekly_contribution=gross_profit / w if gross_profit is not None else None,
            aov=get("aov.current"),
            revenue_per_customer_week=net_revenue / customer_weeks if customer_weeks > 0 else None,
        )
    cited = [
        Cited("impact", impact, "money", detection_id, "impact"),
        *(
            Cited(name, get(name), str(facts[name]["unit"]), economics_id, name)
            for name in sorted(facts)
        ),
    ]
    return observed, cited


async def eligible_customers(ctx: ToolContext) -> list[dict[str, str | None]]:
    """Customers with completed purchases in the incident slice during the current window,
    with the region and acquisition channel used by exclusions (sorted, bounded)."""
    scope = ctx.scope
    predicate, params, join = await ctx.slice_sql()
    rows = await ctx.session.execute(
        text(f"""
            SELECT DISTINCT ol.customer_id, c.region, c.acquisition_channel
            FROM order_lines ol
            JOIN products p ON p.dataset_id = ol.dataset_id AND p.product_id = ol.product_id
            LEFT JOIN customers c
                ON c.dataset_id = ol.dataset_id AND c.customer_id = ol.customer_id
            {join}
            WHERE ol.dataset_id = :d AND ol.status = 'completed'
                AND ol.order_date BETWEEN :start AND :end AND {predicate}
            ORDER BY ol.customer_id
            LIMIT :limit
        """),  # noqa: S608 - the predicate and join come from the dimension allowlist
        {
            "d": scope.dataset_id,
            "start": scope.windows.current[0],
            "end": scope.windows.end,
            "limit": MAX_ELIGIBLE,
            **params,
        },
    )
    return [
        {"customer_id": r.customer_id, "region": r.region, "channel": r.acquisition_channel}
        for r in rows
    ]
