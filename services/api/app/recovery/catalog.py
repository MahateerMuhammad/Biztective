"""Recovery strategy catalog `recovery-1.0.0` (AMD-17, DATA_CONTRACTS §14).

Strategies are catalog data: which validated mechanism each addresses, the action types it
needs, the assumptions its estimate depends on, and what it costs operationally. Nothing here
is derived from model output or free text.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

CATALOG_VERSION = "recovery-1.0.0"

ActionType = Literal[
    "review_task", "sales_hold", "discount_policy", "customer_contact", "customer_offer"
]
ACTION_TYPES: dict[str, dict[str, object]] = {
    "review_task": {
        "risky": False,
        "label": "Internal review task for the team",
    },
    "sales_hold": {
        "risky": True,
        "label": "Pause selling the affected products (customers can no longer buy them)",
    },
    "discount_policy": {
        "risky": True,
        "label": "Change the discount policy customers see",
    },
    "customer_contact": {
        "risky": True,
        "label": "Contact individual customers",
    },
    "customer_offer": {
        "risky": True,
        "label": "Give individual customers a discount or credit (costs money)",
    },
}
DEFAULT_ALLOWED_ACTIONS: tuple[ActionType, ...] = ("review_task",)

Unit = Literal["ratio", "money"]


@dataclass(frozen=True)
class Assumption:
    key: str
    unit: Unit
    label: str
    low: Decimal | None  # None: no default, a person must provide it
    mid: Decimal | None
    high: Decimal | None
    minimum: Decimal = Decimal(0)
    maximum: Decimal = Decimal(1)


def rate(key: str, label: str, low: str, mid: str, high: str) -> Assumption:
    return Assumption(key, "ratio", label, Decimal(low), Decimal(mid), Decimal(high))


def money(key: str, label: str) -> Assumption:
    return Assumption(
        key, "money", label, None, None, None, minimum=Decimal(0), maximum=Decimal("1000000000")
    )


Formula = Literal[
    "loss_recovery",  # benefit = weekly excess loss × h × rate; cost = a fixed amount
    "sales_hold",  # benefit = weekly excess loss × h × rate; cost = contribution forgone
    "discount_cap",  # benefit = excess discount × h × compliance; cost = contribution forgone
    "discount_review",  # benefit = excess discount × h × compliance; cost = a fixed amount
    "customer",  # benefit = targets × rate × revenue per customer-week × h × margin
    "none",  # not estimated
]


@dataclass(frozen=True)
class Strategy:
    code: str
    templates: tuple[str, ...]
    title: str
    summary: str
    action_types: tuple[str, ...]
    formula: Formula
    assumptions: tuple[Assumption, ...]
    requirements: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    targets_customers: bool = False
    uses_discount: bool = False
    needs_margin: bool = False
    # Slice dimensions the strategy can act on (None: any).
    dimensions: tuple[str, ...] | None = None


EFFECTIVENESS = "effectiveness"
REVIEW_COST = money("review_cost", "One-off cost of the review (staff time, inspection fees)")
CONTACT_COST = money("contact_cost", "Cost of contacting one customer")

STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        "S_QUALITY_REVIEW",
        ("H_RETURNS_QUALITY",),
        "Quality review with the supplier",
        "Inspect the affected stock and agree corrective action with the supplier.",
        ("review_task",),
        "loss_recovery",
        (
            rate(
                EFFECTIVENESS,
                "Share of the excess refunds the review prevents",
                "0.2",
                "0.4",
                "0.6",
            ),
            REVIEW_COST,
        ),
        ("Someone owns the supplier conversation", "Returned items are available to inspect"),
        ("Slower: refunds continue until the fix reaches stock",),
    ),
    Strategy(
        "S_QUALITY_HOLD",
        ("H_RETURNS_QUALITY",),
        "Pause sales of the affected products",
        "Stop selling the affected products until the quality problem is fixed.",
        ("review_task", "sales_hold"),
        "sales_hold",
        (
            rate(
                EFFECTIVENESS, "Share of the excess refunds the pause prevents", "0.7", "0.85", "1"
            ),
            rate("hold_share", "Share of the slice's sales that is paused", "1", "1", "1"),
        ),
        ("Ability to delist or block the products in every sales channel",),
        (
            "Forgoes the contribution of every paused sale",
            "Customers who wanted the product may buy elsewhere",
        ),
        needs_margin=True,
        dimensions=("product", "category"),
    ),
    Strategy(
        "S_GOODWILL_OUTREACH",
        ("H_RETURNS_QUALITY",),
        "Goodwill outreach to affected customers",
        "Contact customers who bought the affected products and offer a credit on the next order.",
        ("customer_contact", "customer_offer"),
        "customer",
        (
            rate(
                "response_rate",
                "Share of contacted customers who order again",
                "0.05",
                "0.1",
                "0.2",
            ),
            CONTACT_COST,
        ),
        ("Customer contact consent for the selected customers",),
        ("Costs money on every redeemed credit", "Does not fix the product itself"),
        targets_customers=True,
        uses_discount=True,
        needs_margin=True,
    ),
    Strategy(
        "S_DISCOUNT_GUARDRAIL",
        ("H_PRICE_DISCOUNT",),
        "Cap discounts on the affected slice",
        "Apply a maximum discount to the affected products or category.",
        ("review_task", "discount_policy"),
        "discount_cap",
        (
            rate(
                "compliance_rate",
                "Share of the excess discount the cap removes",
                "0.5",
                "0.7",
                "0.9",
            ),
            rate(
                "volume_loss",
                "Share of the slice's sales lost because prices rise",
                "0",
                "0.05",
                "0.15",
            ),
        ),
        ("Price or promotion settings can be changed in every sales channel",),
        ("Higher prices can reduce sales volume",),
        needs_margin=True,
        dimensions=("product", "category", "channel", "region"),
    ),
    Strategy(
        "S_DISCOUNT_REVIEW",
        ("H_PRICE_DISCOUNT",),
        "Review discount approvals",
        "Audit who grants the discounts and tighten approvals without changing list prices.",
        ("review_task",),
        "discount_review",
        (
            rate(
                "compliance_rate",
                "Share of the excess discount the review removes",
                "0.2",
                "0.35",
                "0.5",
            ),
            REVIEW_COST,
        ),
        ("Discount approvals are recorded and attributable",),
        ("Slower and less certain than a hard cap",),
    ),
    Strategy(
        "S_FULFILLMENT_ESCALATION",
        ("H_FULFILLMENT",),
        "Escalate with the carrier or warehouse",
        "Raise the late or damaged deliveries with the logistics provider and agree a fix.",
        ("review_task",),
        "loss_recovery",
        (
            rate(
                EFFECTIVENESS, "Share of the excess refunds the fix prevents", "0.2", "0.4", "0.6"
            ),
            REVIEW_COST,
        ),
        ("Delivery records to share with the provider",),
        ("Depends on the provider's response",),
    ),
    Strategy(
        "S_TARGETED_OFFER",
        ("H_SEGMENT_SHIFT", "H_CONCENTRATION"),
        "Targeted win-back offer",
        "Offer the selected customers a discount on their next order.",
        ("customer_contact", "customer_offer"),
        "customer",
        (
            rate(
                "response_rate",
                "Share of targeted customers who order again",
                "0.05",
                "0.12",
                "0.25",
            ),
            CONTACT_COST,
        ),
        ("Customer contact consent for the selected customers",),
        ("Discounts reduce the margin of every redeemed order",),
        targets_customers=True,
        uses_discount=True,
        needs_margin=True,
    ),
    Strategy(
        "S_CUSTOMER_OUTREACH",
        ("H_SEGMENT_SHIFT", "H_CONCENTRATION"),
        "Personal outreach without a discount",
        "Contact the selected customers personally, without a price incentive.",
        ("customer_contact",),
        "customer",
        (
            rate(
                "response_rate",
                "Share of contacted customers who order again",
                "0.02",
                "0.06",
                "0.12",
            ),
            CONTACT_COST,
        ),
        ("Customer contact consent for the selected customers",),
        ("Lower response than an offer",),
        targets_customers=True,
        needs_margin=True,
    ),
    Strategy(
        "S_PRICE_REVIEW",
        ("H_COST_PRICE",),
        "Review prices against product cost",
        "Re-check list prices and the product mix against current product costs.",
        ("review_task",),
        "loss_recovery",
        (
            rate(
                EFFECTIVENESS, "Share of the margin loss the review recovers", "0.2", "0.4", "0.6"
            ),
            REVIEW_COST,
        ),
        ("Current supplier costs are known",),
        ("Price increases can reduce volume",),
    ),
    Strategy(
        "S_CHANNEL_REVIEW",
        ("H_CHANNEL",),
        "Review the affected sales channel",
        "Investigate the channel's terms, listings and fees with the channel owner.",
        ("review_task",),
        "loss_recovery",
        (
            rate(EFFECTIVENESS, "Share of the loss the review recovers", "0.2", "0.35", "0.5"),
            REVIEW_COST,
        ),
        ("Access to the channel's reports",),
        ("Results depend on the channel partner",),
    ),
    Strategy(
        "S_DATA_FIX",
        ("H_DATA_QUALITY",),
        "Fix the data before acting",
        "Correct the suspicious records at the source before choosing a business response.",
        ("review_task",),
        "none",
        (),
        ("Access to the system that produced the data",),
        ("Delays any business response until the data is corrected",),
        limitations=("impact_not_estimated",),
    ),
)
BY_CODE: dict[str, Strategy] = {s.code: s for s in STRATEGIES}
ORDER: dict[str, int] = {s.code: i for i, s in enumerate(STRATEGIES)}


def strategies_for(template: str, dimension: str | None = None) -> list[Strategy]:
    return [
        s
        for s in STRATEGIES
        if template in s.templates
        and (dimension is None or s.dimensions is None or dimension in s.dimensions)
    ]
