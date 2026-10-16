"""Deterministic strategy estimates (AMD-17, DATA_CONTRACTS §14.3; AGENTS.md R-10).

Inputs are observed values taken from stored evidence and human-confirmed or labelled default
assumptions. Outputs are estimates per scenario (low, mid, high): benefit, cost and net, plus
the break-even value of the strategy's main uncertain rate. Arithmetic is exact Decimal in the
analytics context; money is rounded half-even to cents only when presented.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from app.analytics.metrics import analytics_context
from app.recovery.catalog import Strategy

ESTIMATOR_VERSION = "1"
SCENARIOS = ("low", "mid", "high")
CENTS = Decimal("0.01")
RATIO = Decimal("0.000001")
ZERO = Decimal(0)


@dataclass(frozen=True)
class Observed:
    """Per-slice values over the current window, each read from one evidence item."""

    weeks: int
    weekly_excess_loss: Decimal  # incident impact / weeks
    weekly_net_revenue: Decimal
    weekly_gross_sales: Decimal
    excess_discount_rate: Decimal  # current − baseline discount rate (can be ≤ 0)
    contribution_margin: Decimal | None  # gross profit / net revenue; None if costs incomplete
    weekly_contribution: Decimal | None
    aov: Decimal | None
    revenue_per_customer_week: Decimal | None


@dataclass(frozen=True)
class Inputs:
    horizon_weeks: int
    assumptions: dict[str, dict[str, Decimal]]  # key → scenario → value (complete)
    targets: int = 0
    discount: Decimal = ZERO


@dataclass(frozen=True)
class Scenario:
    benefit: Decimal
    cost: Decimal

    @property
    def net(self) -> Decimal:
        with localcontext(analytics_context()):
            return self.benefit - self.cost


@dataclass(frozen=True)
class Estimate:
    scenarios: dict[str, Scenario]
    break_even: dict[str, object]  # {variable, value | None, reason | None}
    warnings: tuple[str, ...]


def cents(value: Decimal) -> str:
    return str(value.quantize(CENTS, rounding=ROUND_HALF_EVEN))


def ratio(value: Decimal) -> str:
    return str(value.quantize(RATIO, rounding=ROUND_HALF_EVEN))


def ineligibility(strategy: Strategy, observed: Observed) -> list[str]:
    """Why the evidence cannot support an estimate for this strategy (empty: it can)."""
    reasons: list[str] = []
    if strategy.needs_margin and observed.contribution_margin is None:
        reasons.append("missing_cost_data")
    if (
        strategy.formula in ("discount_cap", "discount_review")
        and observed.excess_discount_rate <= 0
    ):
        reasons.append("no_excess_discount")
    if strategy.formula == "customer" and (
        observed.aov is None or observed.revenue_per_customer_week is None
    ):
        reasons.append("no_customer_activity")
    return reasons


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator > 0 else None


def estimate(strategy: Strategy, observed: Observed, inputs: Inputs) -> Estimate:
    """Benefit, cost and net per scenario. The caller guarantees eligibility and complete
    assumptions (`ineligibility` empty, every assumption present)."""
    h = Decimal(inputs.horizon_weeks)
    a = inputs.assumptions
    warnings: list[str] = []
    scenarios: dict[str, Scenario] = {}
    with localcontext(analytics_context()):
        contribution = observed.weekly_contribution
        margin = observed.contribution_margin
        if strategy.needs_margin and margin is not None and margin <= 0:
            warnings.append("non_positive_contribution_margin")
        excess_discount = max(observed.excess_discount_rate, ZERO) * observed.weekly_gross_sales
        mid = {k: v["mid"] for k, v in a.items()}
        variable: str
        point: Decimal | None
        reason: str | None = None
        if strategy.formula in ("loss_recovery", "sales_hold"):
            gross = observed.weekly_excess_loss * h
            for s in SCENARIOS:
                benefit = gross * a["effectiveness"][s]
                if strategy.formula == "loss_recovery":
                    cost = a["review_cost"][s]
                else:  # a slice that sells at a loss forgoes no contribution when paused
                    cost = max(contribution or ZERO, ZERO) * h * a["hold_share"][s]
                scenarios[s] = Scenario(benefit, cost)
            variable = "effectiveness"
            cost_mid = (
                mid["review_cost"]
                if strategy.formula == "loss_recovery"
                else max(contribution or ZERO, ZERO) * h * mid["hold_share"]
            )
            point = _divide(cost_mid, gross)
        elif strategy.formula in ("discount_cap", "discount_review"):
            gross = excess_discount * h
            for s in SCENARIOS:
                benefit = gross * a["compliance_rate"][s]
                if strategy.formula == "discount_review":
                    cost = a["review_cost"][s]
                else:
                    cost = a["volume_loss"][s] * max(contribution or ZERO, ZERO) * h
                scenarios[s] = Scenario(benefit, cost)
            variable = "compliance_rate"
            cost_mid = (
                mid["review_cost"]
                if strategy.formula == "discount_review"
                else mid["volume_loss"] * max(contribution or ZERO, ZERO) * h
            )
            point = _divide(cost_mid, gross)
        elif strategy.formula == "customer":
            n = Decimal(inputs.targets)
            per_customer = (observed.revenue_per_customer_week or ZERO) * h * (margin or ZERO)
            offer = (observed.aov or ZERO) * inputs.discount
            for s in SCENARIOS:
                responders = n * a["response_rate"][s]
                benefit = responders * per_customer
                cost = n * a["contact_cost"][s] + responders * offer
                scenarios[s] = Scenario(benefit, cost)
            variable = "response_rate"
            point = _divide(mid["contact_cost"], per_customer - offer)
            if point is None:
                reason = "offer_exceeds_contribution"
        else:
            return Estimate({}, {"variable": None, "value": None, "reason": "not_estimated"}, ())
        if point is None and reason is None:
            reason = "no_recoverable_amount"
        if point is not None and point > 1:
            reason = "above_one"  # even full success does not pay back the mid cost
        return Estimate(
            scenarios,
            {
                "variable": variable,
                "value": ratio(point) if point is not None else None,
                "reason": reason,
            },
            tuple(warnings),
        )


def present(result: Estimate) -> dict[str, object]:
    return {
        "kind": "estimate",
        "estimator_version": ESTIMATOR_VERSION,
        "scenarios": {
            s: {"benefit": cents(v.benefit), "cost": cents(v.cost), "net": cents(v.net)}
            for s, v in result.scenarios.items()
        },
        "break_even": result.break_even,
    }
