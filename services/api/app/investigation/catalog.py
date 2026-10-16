"""Hypothesis catalog 1.1.0 (ARCHITECTURE §6.2 as amended by AMD-12 and AMD-15). Pure data
and rules.

# Any is confined to JSON/SQL driver boundaries; tool arguments and rules are typed.

A template states a claim, the metrics it can explain, the evidence tool calls it needs (fixed
parameters, so verdicts never depend on which extra tools an LLM chose), and its criteria.
Thresholds are part of the catalog version: any change is a version bump.

Human criteria ask one catalog question whose options carry their effect on the criterion
(confirm, deny, intended, unsure) and the templates worth investigating next (AMD-15). Effects
are catalog data: nothing is ever derived from a person's free text.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from app.investigation.facts import Facts, numeric
from app.investigation.tools import (
    ALL_CHECKS,
    CustomerExposureParams,
    DataQualityParams,
    MarginBridgeParams,
    MetricBreakdownParams,
    ReturnInvestigationParams,
    SegmentAnalysisParams,
    ToolName,
    ToolParams,
)

CATALOG_VERSION = "1.1.0"
ALL_METRICS = (
    "net_revenue",
    "gross_margin_pct",
    "refund_rate",
    "discount_rate",
    "active_customers",
    "repeat_customer_rate",
    "aov",
)
TemplateName = Literal[
    "H_RETURNS_QUALITY",
    "H_FULFILLMENT",
    "H_PRICE_DISCOUNT",
    "H_SEGMENT_SHIFT",
    "H_CONCENTRATION",
    "H_CHANNEL",
    "H_DATA_QUALITY",
    "H_EXTERNAL_CONTEXT",
    "H_COST_PRICE",
]
Effect = Literal["confirm", "deny", "intended", "unsure"]
ALWAYS = ("H_DATA_QUALITY", "H_EXTERNAL_CONTEXT")


@dataclass(frozen=True)
class Requirement:
    key: str
    tool: ToolName
    params: Callable[[str], ToolParams]  # incident metric → parameters


@dataclass(frozen=True)
class Evidence:
    id: UUID
    facts: Facts


@dataclass(frozen=True)
class Basis:
    evidence_id: str
    field: str
    value: Any
    unit: str


class Lookup:
    """Reads facts of the template's required evidence and remembers what was read."""

    def __init__(self, evidence: Mapping[str, Evidence | None]) -> None:
        self._evidence = evidence
        self.basis: list[Basis] = []

    def get(self, requirement: str, name: str) -> Decimal | None:
        item = self._evidence.get(requirement)
        if item is None:
            return None
        f = item.facts.get(name)
        if f is not None:
            self.basis.append(Basis(str(item.id), name, f.get("value"), str(f.get("unit"))))
        return numeric(f)


Rule = Callable[[Lookup], bool | None]  # None: the evidence needed is missing


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    effect: Effect
    follow_up: tuple[TemplateName, ...] = ()


@dataclass(frozen=True)
class QuestionSpec:
    question: str
    options: tuple[Option, ...]

    @property
    def asks_intent(self) -> bool:
        """The data can show the mechanism but not whether it was a deliberate decision."""
        return any(o.effect == "intended" for o in self.options)

    def option(self, option_id: str) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)

    def as_json(self) -> dict[str, Any]:
        return {
            "type": "single_choice",
            "question": self.question,
            "options": [
                {"id": o.id, "label": o.label, "effect": o.effect, "follow_up": list(o.follow_up)}
                for o in self.options
            ],
        }


@dataclass(frozen=True)
class Criterion:
    key: str
    polarity: Literal["support", "refute"]
    source: Literal["data", "human"]
    weight: Decimal
    description: str
    rule: Rule | None = None
    question: QuestionSpec | None = None


@dataclass(frozen=True)
class Template:
    name: TemplateName
    claim: str
    metrics: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)

    @property
    def human_only(self) -> bool:
        return all(c.source == "human" for c in self.criteria)


def _ge(value: Decimal | None, threshold: str) -> bool | None:
    return None if value is None else value >= Decimal(threshold)


def _lt(value: Decimal | None, threshold: str) -> bool | None:
    return None if value is None else value < Decimal(threshold)


def _all(*values: bool | None) -> bool | None:
    """Three-valued AND: False wins, otherwise unknown wins."""
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


def _any(*values: bool | None) -> bool | None:
    if any(v is True for v in values):
        return True
    return None if any(v is None for v in values) else False


UNSURE = Option("unsure", "Not sure", "unsure")
YES_NO = (Option("yes", "Yes", "confirm"), Option("no", "No", "deny"), UNSURE)


def _yes_no(question: str) -> QuestionSpec:
    return QuestionSpec(question, YES_NO)


def _distinct_top_share(lookup: Lookup, requirement: str, threshold: str) -> bool | None:
    """A value explains ≥ threshold of the adverse change (needs at least two values)."""
    values = lookup.get(requirement, "values")
    if values is None or values < 2:
        return None
    return _ge(lookup.get(requirement, "top_share"), threshold)


RETURNS_BY_PRODUCT = Requirement(
    "returns_by_product", "return_investigation", lambda _: ReturnInvestigationParams(by="product")
)
RETURNS_BY_REGION = Requirement(
    "returns_by_region", "return_investigation", lambda _: ReturnInvestigationParams(by="region")
)
RETURNS_BY_CHANNEL = Requirement(
    "returns_by_channel", "return_investigation", lambda _: ReturnInvestigationParams(by="channel")
)
DISCOUNT_BY_PRODUCT = Requirement(
    "discount_by_product",
    "metric_breakdown",
    lambda _: MetricBreakdownParams(metric="discount_rate", by="product"),
)
SEGMENTS = Requirement("segments", "segment_analysis", lambda _: SegmentAnalysisParams())
EXPOSURE = Requirement("exposure", "customer_exposure", lambda _: CustomerExposureParams(top_k=5))
BY_CHANNEL = Requirement(
    "by_channel",
    "metric_breakdown",
    lambda metric: MetricBreakdownParams.model_validate({"metric": metric, "by": "channel"}),
)
QUALITY = Requirement(
    "quality", "data_quality_check", lambda _: DataQualityParams(checks=list(ALL_CHECKS))
)
MARGIN = Requirement("margin", "margin_bridge", lambda _: MarginBridgeParams())


def _returns_c2(lookup: Lookup) -> bool | None:
    return _ge(lookup.get("returns_by_product", "contributors.top_share"), "0.50")


def _fulfillment_c2(lookup: Lookup) -> bool | None:
    return _any(
        _ge(lookup.get("returns_by_region", "contributors.top_share"), "0.50"),
        _ge(lookup.get("returns_by_channel", "contributors.top_share"), "0.50"),
    )


TEMPLATES: tuple[Template, ...] = (
    Template(
        "H_RETURNS_QUALITY",
        "A product or category quality problem is driving returns.",
        ("refund_rate", "gross_margin_pct", "net_revenue"),
        (RETURNS_BY_PRODUCT,),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.4"),
                "Damaged and defective returns' share of refunds rose by at least 10 pp.",
                lambda q: _ge(q.get("returns_by_product", "quality.share_change"), "0.10"),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.3"),
                "One product explains at least half of the excess refunds.",
                _returns_c2,
            ),
            Criterion(
                "C3",
                "support",
                "data",
                Decimal("0.3"),
                "Orders placed in the current window are refunded at least 2 pp more often "
                "(matured cohorts) than baseline orders.",
                lambda q: _ge(q.get("returns_by_product", "cohort.change"), "0.02"),
            ),
            Criterion(
                "C4",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms a product, supplier or batch change in the period.",
                question=_yes_no(
                    "Did anything change for these products in the period, such as a new "
                    "supplier, batch or specification?"
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "The damaged and defective share barely moved (under 2 pp) and no single "
                "product explains the excess refunds.",
                lambda q: _all(
                    _lt(q.get("returns_by_product", "quality.share_change"), "0.02"),
                    None if (c2 := _returns_c2(q)) is None else not c2,
                ),
            ),
        ),
    ),
    Template(
        "H_FULFILLMENT",
        "Delivery problems (late or wrong items) in a region or channel are driving the change.",
        ("refund_rate", "active_customers", "repeat_customer_rate"),
        (RETURNS_BY_REGION, RETURNS_BY_CHANNEL),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.5"),
                "Late-delivery and wrong-item returns' share of refunds rose by at least 10 pp.",
                lambda q: _ge(q.get("returns_by_region", "logistics.share_change"), "0.10"),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.5"),
                "One region or channel explains at least half of the excess refunds.",
                _fulfillment_c2,
            ),
            Criterion(
                "C3",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms a carrier, warehouse or fulfilment change.",
                question=_yes_no(
                    "Did a carrier, warehouse or fulfilment process change in the period?"
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "The late-delivery and wrong-item share barely moved (under 2 pp) and no "
                "region or channel explains the excess refunds.",
                lambda q: _all(
                    _lt(q.get("returns_by_region", "logistics.share_change"), "0.02"),
                    None if (c2 := _fulfillment_c2(q)) is None else not c2,
                ),
            ),
        ),
    ),
    Template(
        "H_PRICE_DISCOUNT",
        "Heavier discounting or weaker price realisation is driving the change.",
        ("discount_rate", "gross_margin_pct", "aov", "net_revenue"),
        (DISCOUNT_BY_PRODUCT,),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.6"),
                "The discount rate rose by at least 2 pp.",
                lambda q: _ge(q.get("discount_by_product", "total.change"), "0.02"),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.4"),
                "One product explains at least half of the extra discounts.",
                lambda q: _ge(q.get("discount_by_product", "top_share"), "0.50"),
            ),
            Criterion(
                "C3",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms the higher discounting in the period (planned or not).",
                question=QuestionSpec(
                    "Was the higher discounting in the period planned, for example a clearance "
                    "or promotion campaign?",
                    (
                        Option(
                            "planned_campaign",
                            "Yes, it was a planned clearance or promotion campaign",
                            "intended",
                            ("H_RETURNS_QUALITY", "H_COST_PRICE"),
                        ),
                        Option("unplanned", "No, discounts were not meant to rise", "confirm"),
                        Option(
                            "no_known_change",
                            "No discount change happened as far as we know",
                            "deny",
                        ),
                        UNSURE,
                    ),
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "The discount rate rose by less than 0.5 pp.",
                lambda q: _lt(q.get("discount_by_product", "total.change"), "0.005"),
            ),
        ),
    ),
    Template(
        "H_SEGMENT_SHIFT",
        "Previously established customers are buying less (this is not a churn claim).",
        ("net_revenue", "active_customers", "repeat_customer_rate", "aov"),
        (SEGMENTS,),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.5"),
                "Weekly active customers from the loyal and at-risk segments fell by at least 15%.",
                lambda q: (
                    None
                    if (v := q.get("segments", "established.active_change_rel")) is None
                    else v <= Decimal("-0.15")
                ),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.5"),
                "Loyal and at-risk customers explain at least half of the revenue decline.",
                lambda q: _ge(q.get("segments", "established.revenue_decline_share"), "0.50"),
            ),
            Criterion(
                "C3",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms a change affecting regular customers.",
                question=_yes_no(
                    "Did anything change for regular customers in the period, such as a "
                    "competitor offer, a lost account or a service problem?"
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "Established customers' weekly activity fell by less than 5% and they explain "
                "under half of any revenue decline.",
                lambda q: _all(
                    None
                    if (v := q.get("segments", "established.active_change_rel")) is None
                    else v >= Decimal("-0.05"),
                    _lt(q.get("segments", "established.revenue_decline_share"), "0.50"),
                ),
            ),
        ),
    ),
    Template(
        "H_CONCENTRATION",
        "A few customers explain most of the change.",
        ("net_revenue", "aov", "refund_rate"),
        (EXPOSURE,),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.6"),
                "The five most affected customers explain at least half of the adverse change.",
                lambda q: _ge(q.get("exposure", "top_k.share"), "0.50"),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.4"),
                "The single most affected customer explains at least a quarter of it.",
                lambda q: _ge(q.get("exposure", "top1.share"), "0.25"),
            ),
            Criterion(
                "C3",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms a key account changed its ordering or returns.",
                question=_yes_no(
                    "Did one of your largest customers change how much they ordered or returned?"
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "The five most affected customers explain under a fifth of the change.",
                lambda q: _lt(q.get("exposure", "top_k.share"), "0.20"),
            ),
        ),
    ),
    Template(
        "H_CHANNEL",
        "A specific sales channel is underperforming.",
        ("net_revenue", "active_customers"),
        (BY_CHANNEL,),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("1.0"),
                "One channel explains at least 60% of the adverse change.",
                lambda q: _distinct_top_share(q, "by_channel", "0.60"),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "No channel explains even 40% of the adverse change.",
                lambda q: (
                    None
                    if (q.get("by_channel", "values") or 0) < 2
                    else _lt(q.get("by_channel", "top_share"), "0.40")
                ),
            ),
        ),
    ),
    Template(
        "H_DATA_QUALITY",
        "The change is an artefact of the data (missing days, duplicates or one unusual "
        "customer) rather than a business change.",
        ALL_METRICS,
        (QUALITY, EXPOSURE),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.4"),
                "The current window has more days without orders, or a week with unusually "
                "few order lines.",
                lambda q: _any(
                    _ge(q.get("quality", "zero_days.excess_per_week"), "1"),
                    _ge(q.get("quality", "volume.low_weeks"), "1"),
                ),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.3"),
                "Near-duplicate order lines make up at least 1% of the current window.",
                lambda q: _ge(q.get("quality", "near_duplicates.share"), "0.01"),
            ),
            Criterion(
                "C3",
                "support",
                "data",
                Decimal("0.3"),
                "A single customer explains more than 30% of the adverse change.",
                lambda q: (
                    None if (v := q.get("exposure", "top1.share")) is None else v > Decimal("0.30")
                ),
            ),
            Criterion(
                "C4",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms a recording or export problem in the period.",
                question=_yes_no(
                    "Was there a known problem recording or exporting orders in the period?"
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "No excess days without orders, no low-volume week, near-duplicates under "
                "0.2% and no customer above 10% of the change.",
                lambda q: _all(
                    _lt(q.get("quality", "zero_days.excess_per_week"), "0.5"),
                    _lt(q.get("quality", "volume.low_weeks"), "1"),
                    _lt(q.get("quality", "near_duplicates.share"), "0.002"),
                    _lt(q.get("exposure", "top1.share"), "0.10"),
                ),
            ),
        ),
    ),
    Template(
        "H_EXTERNAL_CONTEXT",
        "A cause the data cannot show (a stockout, supplier change, promotion or seasonal event).",
        ALL_METRICS,
        (),
        (
            Criterion(
                "C1",
                "support",
                "human",
                Decimal("1.0"),
                "Someone confirms an event outside the data.",
                question=QuestionSpec(
                    "Was there an event in the period that the data cannot show?",
                    (
                        Option("stockout", "A stockout", "confirm"),
                        Option("supplier_change", "A supplier or product-cost change", "confirm"),
                        Option("promotion", "A promotion or campaign", "confirm"),
                        Option("seasonal_event", "A seasonal or one-off event", "confirm"),
                        Option("none", "None of these", "deny"),
                        UNSURE,
                    ),
                ),
            ),
        ),
    ),
    Template(
        "H_COST_PRICE",
        "Selling prices fell relative to product cost, or sales moved to lower-margin products, "
        "compressing the margin before discounts and refunds.",
        ("gross_margin_pct",),
        (MARGIN,),
        (
            Criterion(
                "C1",
                "support",
                "data",
                Decimal("0.6"),
                "Prices relative to cost (or the product mix) lowered the margin by at least 2 pp.",
                lambda q: (
                    None
                    if (v := q.get("margin", "effect.price_cost")) is None
                    else v <= Decimal("-0.02")
                ),
            ),
            Criterion(
                "C2",
                "support",
                "data",
                Decimal("0.4"),
                "Price and cost explain at least half of the margin decline.",
                lambda q: _ge(q.get("margin", "adverse.price_cost_share"), "0.50"),
            ),
            Criterion(
                "C3",
                "support",
                "human",
                Decimal("0.3"),
                "Someone confirms a list-price or product-range change in the period.",
                question=QuestionSpec(
                    "Did you lower list prices or change the product range in the period?",
                    (
                        Option(
                            "lowered_prices",
                            "Yes, we lowered list prices on purpose",
                            "intended",
                            ("H_RETURNS_QUALITY",),
                        ),
                        Option(
                            "range_change",
                            "Yes, sales shifted to cheaper or lower-margin products",
                            "confirm",
                        ),
                        Option("no", "No", "deny"),
                        UNSURE,
                    ),
                ),
            ),
            Criterion(
                "R1",
                "refute",
                "data",
                Decimal("0"),
                "Price and cost moved the margin by less than 0.5 pp.",
                lambda q: (
                    None
                    if (v := q.get("margin", "effect.price_cost")) is None
                    else v > Decimal("-0.005")
                ),
            ),
        ),
    ),
)
CATALOG: dict[str, Template] = {t.name: t for t in TEMPLATES}
ORDER: dict[str, int] = {t.name: i for i, t in enumerate(TEMPLATES)}


def candidates(metric: str) -> list[Template]:
    """Applicable templates in catalog order; data quality and external context always."""
    return [t for t in TEMPLATES if metric in t.metrics or t.name in ALWAYS]


def human_criterion(template: str) -> Criterion | None:
    return next((c for c in CATALOG[template].criteria if c.question is not None), None)


# Recommended next checks for a root cause, or for a mechanism explained by a deliberate
# business decision. Investigation steps for people, never executable actions (AMD-15).
RECOMMENDATIONS: dict[str, tuple[str, str]] = {
    "H_RETURNS_QUALITY": (
        "Inspect recent batches, specifications and supplier quality for the affected products.",
        "Check the affected products' returns once the planned change has settled.",
    ),
    "H_FULFILLMENT": (
        "Review carrier and warehouse performance for the affected region or channel.",
        "Confirm the planned fulfilment change is performing as expected.",
    ),
    "H_PRICE_DISCOUNT": (
        "Review discount rules, coupon codes and price overrides for the affected products.",
        "Track the planned campaign against its intended depth and end date; no pricing "
        "correction is suggested while it runs as planned.",
    ),
    "H_SEGMENT_SHIFT": (
        "Contact regular customers whose purchases fell to learn what changed.",
        "Watch whether regular customers return after the planned change.",
    ),
    "H_CONCENTRATION": (
        "Review the largest affected accounts individually.",
        "Confirm the planned change with the largest affected accounts.",
    ),
    "H_CHANNEL": (
        "Review the underperforming channel's listings, pricing and traffic.",
        "Confirm the planned channel change is performing as expected.",
    ),
    "H_DATA_QUALITY": (
        "Verify order recording and exports for the period before acting on the figures.",
        "Verify order recording and exports for the period before acting on the figures.",
    ),
    "H_EXTERNAL_CONTEXT": (
        "Record the external event and re-check the figures once it has passed.",
        "Record the external event and re-check the figures once it has passed.",
    ),
    "H_COST_PRICE": (
        "Compare current list prices, product costs and the sales mix with the baseline period.",
        "Track margins on the repriced products against the planned price change.",
    ),
}
ANSWER_NEEDED = "Answer the open question so ROOT can separate the competing explanations."
