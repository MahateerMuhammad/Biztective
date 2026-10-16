"""Recovery estimator golden values, edge cases and plan validation (AMD-17, T-31).

Every expected number below is computed by hand from the documented formulas (DATA_CONTRACTS
§14.3) with horizon h = 4 weeks.
"""

from decimal import Decimal
from typing import Any

import pytest

from app.investigation.facts import fact
from app.recovery import plan
from app.recovery.catalog import BY_CODE, STRATEGIES, strategies_for
from app.recovery.estimator import Inputs, Observed, estimate, ineligibility, present

D = Decimal
BASE = Observed(
    weeks=4,
    weekly_excess_loss=D(1000),
    weekly_net_revenue=D(5000),
    weekly_gross_sales=D(6000),
    excess_discount_rate=D("0.2"),
    contribution_margin=D("0.4"),
    weekly_contribution=D(2000),
    aov=D(50),
    revenue_per_customer_week=D(25),
)


def scenario(low: str, mid: str, high: str) -> dict[str, Decimal]:
    return {"low": D(low), "mid": D(mid), "high": D(high)}


def numbers(result: Any) -> dict[str, tuple[str, str, str]]:
    shown = present(result)["scenarios"]
    assert isinstance(shown, dict)
    return {s: (v["benefit"], v["cost"], v["net"]) for s, v in shown.items()}


def test_loss_recovery_golden() -> None:
    result = estimate(
        BY_CODE["S_QUALITY_REVIEW"],
        BASE,
        Inputs(
            4,
            {
                "effectiveness": scenario("0.2", "0.4", "0.6"),
                "review_cost": scenario("500", "800", "1200"),
            },
        ),
    )
    assert numbers(result) == {
        "low": ("800.00", "500.00", "300.00"),
        "mid": ("1600.00", "800.00", "800.00"),
        "high": ("2400.00", "1200.00", "1200.00"),
    }
    assert result.break_even == {"variable": "effectiveness", "value": "0.200000", "reason": None}


def test_sales_hold_golden_and_break_even_above_one() -> None:
    result = estimate(
        BY_CODE["S_QUALITY_HOLD"],
        BASE,
        Inputs(
            4,
            {
                "effectiveness": scenario("0.7", "0.85", "1"),
                "hold_share": scenario("1", "1", "1"),
            },
        ),
    )
    assert numbers(result) == {
        "low": ("2800.00", "8000.00", "-5200.00"),
        "mid": ("3400.00", "8000.00", "-4600.00"),
        "high": ("4000.00", "8000.00", "-4000.00"),
    }
    # Even preventing every excess refund does not pay for the forgone contribution.
    assert result.break_even == {
        "variable": "effectiveness",
        "value": "2.000000",
        "reason": "above_one",
    }


def test_discount_cap_and_review_golden() -> None:
    cap = estimate(
        BY_CODE["S_DISCOUNT_GUARDRAIL"],
        BASE,
        Inputs(
            4,
            {
                "compliance_rate": scenario("0.5", "0.7", "0.9"),
                "volume_loss": scenario("0", "0.05", "0.15"),
            },
        ),
    )
    assert numbers(cap) == {
        "low": ("2400.00", "0.00", "2400.00"),
        "mid": ("3360.00", "400.00", "2960.00"),
        "high": ("4320.00", "1200.00", "3120.00"),
    }
    assert cap.break_even["value"] == "0.083333"
    review = estimate(
        BY_CODE["S_DISCOUNT_REVIEW"],
        BASE,
        Inputs(
            4,
            {
                "compliance_rate": scenario("0.2", "0.35", "0.5"),
                "review_cost": scenario("300", "300", "300"),
            },
        ),
    )
    assert numbers(review) == {
        "low": ("960.00", "300.00", "660.00"),
        "mid": ("1680.00", "300.00", "1380.00"),
        "high": ("2400.00", "300.00", "2100.00"),
    }
    assert review.break_even["value"] == "0.062500"


def test_customer_offer_golden() -> None:
    result = estimate(
        BY_CODE["S_TARGETED_OFFER"],
        BASE,
        Inputs(
            4,
            {
                "response_rate": scenario("0.05", "0.12", "0.25"),
                "contact_cost": scenario("1", "1", "2"),
            },
            targets=100,
            discount=D("0.1"),
        ),
    )
    # Per responder: 25 × 4 × 0.4 = 40 contribution; offer cost 50 × 0.1 = 5.
    assert numbers(result) == {
        "low": ("200.00", "125.00", "75.00"),
        "mid": ("480.00", "160.00", "320.00"),
        "high": ("1000.00", "325.00", "675.00"),
    }
    assert result.break_even == {
        "variable": "response_rate",
        "value": "0.028571",
        "reason": None,
    }


@pytest.mark.parametrize("margin", ["0", "-0.1"])
def test_zero_or_negative_contribution_margin(margin: str) -> None:
    observed = Observed(
        **{
            **BASE.__dict__,
            "contribution_margin": D(margin),
            "weekly_contribution": D(5000) * D(margin),
        }
    )
    hold = estimate(
        BY_CODE["S_QUALITY_HOLD"],
        observed,
        Inputs(
            4, {"effectiveness": scenario("1", "1", "1"), "hold_share": scenario("1", "1", "1")}
        ),
    )
    # A slice that earns nothing (or loses money) forgoes no contribution when paused.
    assert {s.cost for s in hold.scenarios.values()} == {D(0)}
    assert hold.break_even["value"] == "0.000000"
    assert "non_positive_contribution_margin" in hold.warnings
    offer = estimate(
        BY_CODE["S_TARGETED_OFFER"],
        observed,
        Inputs(
            4,
            {
                "response_rate": scenario("0.1", "0.1", "0.1"),
                "contact_cost": scenario("1", "1", "1"),
            },
            targets=10,
            discount=D("0.1"),
        ),
    )
    # Extra orders add no contribution: the offer can never pay back; no division by zero.
    assert offer.break_even == {
        "variable": "response_rate",
        "value": None,
        "reason": "offer_exceeds_contribution",
    }
    assert all(s.net < 0 for s in offer.scenarios.values())
    assert "non_positive_contribution_margin" in offer.warnings


def test_missing_cost_data_and_no_excess_discount_make_strategies_ineligible() -> None:
    no_costs = Observed(
        **{**BASE.__dict__, "contribution_margin": None, "weekly_contribution": None}
    )
    assert ineligibility(BY_CODE["S_QUALITY_HOLD"], no_costs) == ["missing_cost_data"]
    assert ineligibility(BY_CODE["S_QUALITY_REVIEW"], no_costs) == []
    no_excess = Observed(**{**BASE.__dict__, "excess_discount_rate": D("-0.01")})
    assert ineligibility(BY_CODE["S_DISCOUNT_REVIEW"], no_excess) == ["no_excess_discount"]


def test_catalog_shape() -> None:
    codes = [s.code for s in STRATEGIES]
    assert len(codes) == len(set(codes))
    assert {s.code for s in strategies_for("H_RETURNS_QUALITY")} == {
        "S_QUALITY_REVIEW",
        "S_QUALITY_HOLD",
        "S_GOODWILL_OUTREACH",
    }
    # Pausing sales of a customer segment is not an action anyone can take.
    assert "S_QUALITY_HOLD" not in {
        s.code for s in strategies_for("H_RETURNS_QUALITY", "customer_segment")
    }
    for s in STRATEGIES:
        for a in s.assumptions:
            assert (a.low is None) == (a.mid is None) == (a.high is None)
            if a.unit == "money":
                assert a.mid is None  # money is always a person's input, never a default
            else:
                assert a.low is not None
                assert a.high is not None
                assert a.minimum <= a.low <= a.high <= a.maximum


# --------------------------------------------------------------------------- plan content

ECONOMICS = {
    "facts": {
        "weeks.current": fact(4, "count"),
        "gross_sales.current": fact(D(24000), "money"),
        "net_sales.current": fact(D(21000), "money"),
        "net_revenue.current": fact(D(20000), "money"),
        "gross_profit.current": fact(D(8000), "money"),
        "contribution_margin.current": fact(D("0.4"), "ratio"),
        "discount_rate.current": fact(D("0.25"), "ratio"),
        "discount_rate.baseline": fact(D("0.05"), "ratio"),
        "orders.current": fact(420, "count"),
        "customer_weeks.current": fact(800, "count"),
        "aov.current": fact(D(50), "money"),
    },
    "detail": {
        "eligible_customers": [
            {"customer_id": "C1", "region": "east", "channel": "web"},
            {"customer_id": "C2", "region": "west", "channel": "social"},
            {"customer_id": "C3", "region": "east", "channel": "social"},
        ]
    },
}


def candidate(template: str = "H_RETURNS_QUALITY", dimension: str = "product") -> plan.Candidate:
    return plan.Candidate(
        incident_id="00000000-0000-0000-0000-000000000001",
        incident_key=f"refund_rate|{dimension}|X1",
        incident_rank=1,
        dimension=dimension,
        hypothesis_id="00000000-0000-0000-0000-000000000002",
        template=template,
        status="supported",
        confidence="0.9",
        impact=D(4000),
        detection_id="00000000-0000-0000-0000-00000000000d",
        economics_id="00000000-0000-0000-0000-00000000000e",
        economics=ECONOMICS,
    )


EVIDENCE = {"00000000-0000-0000-0000-00000000000e": ECONOMICS}


def drafted(**constraints: Any) -> dict[str, Any]:
    return plan.draft(
        investigation_id="i",
        investigation_version=1,
        currency="USD",
        constraints=plan.Constraints(**constraints),
        candidates=[candidate()],
    )


def edited(content: dict[str, Any], **edits: Any) -> dict[str, Any]:
    return plan.revise(content, plan.PlanEdits(**edits), EVIDENCE)


def codes(content: dict[str, Any]) -> list[str]:
    return [e["code"] for e in plan.validate(content, EVIDENCE)["errors"]]


def test_draft_is_deterministic_and_observed_values_cite_evidence() -> None:
    first, second = drafted(), drafted()
    assert plan.content_hash(first) == plan.content_hash(second)
    review = next(s for s in first["strategies"] if s["strategy"] == "S_QUALITY_REVIEW")
    observed = {o["name"]: o for o in review["observed"]}
    assert observed["impact"] == {
        "name": "impact",
        "value": "4000",
        "unit": "money",
        "evidence_id": "00000000-0000-0000-0000-00000000000d",
        "field": "impact",
        "kind": "observed",
    }
    assert observed["net_revenue.current"]["evidence_id"] == "00000000-0000-0000-0000-00000000000e"
    # Only the permitted, internal strategy is preselected; money must come from a person.
    assert first["selected"] == ["S_QUALITY_REVIEW:refund_rate|product|X1"]
    assert codes(first) == ["missing_assumption"]


def test_missing_assumption_then_budget_rules() -> None:
    instance = "S_QUALITY_REVIEW:refund_rate|product|X1"
    costed = edited(
        drafted(),
        assumptions={instance: {"review_cost": {"low": "400", "mid": "500", "high": "700"}}},
    )
    review = next(s for s in costed["strategies"] if s["id"] == instance)
    assert review["assumptions"]["review_cost"]["source"] == "human"
    assert review["estimate"]["scenarios"]["mid"] == {
        "benefit": "1600.00",  # 4000 / 4 weeks × 4 weeks × 0.4
        "cost": "500.00",
        "net": "1100.00",
    }
    assert codes(costed) == ["budget_missing"]
    assert codes(edited(costed, budget_amount="700")) == []
    assert codes(edited(costed, budget_amount="699.99")) == ["budget_exceeded"]
    assert plan.validate(edited(costed, budget_amount="700"), EVIDENCE)["valid"] is True


def test_risky_actions_are_opt_in() -> None:
    hold = "S_QUALITY_HOLD:refund_rate|product|X1"
    content = edited(drafted(budget_amount="1000000"), selected_strategies=[hold])
    assert codes(content) == ["action_type_not_allowed"]
    allowed = edited(content, allowed_action_types=["review_task", "sales_hold"])
    assert codes(allowed) == []


def test_customer_targeting_discount_and_exclusions() -> None:
    offer = "S_GOODWILL_OUTREACH:refund_rate|product|X1"
    base = edited(
        drafted(budget_amount="100000"),
        selected_strategies=[offer],
        allowed_action_types=["review_task", "customer_contact", "customer_offer"],
        assumptions={offer: {"contact_cost": {"low": "1", "mid": "1", "high": "1"}}},
    )
    entry = next(s for s in base["strategies"] if s["id"] == offer)
    assert entry["targeting"] == {
        "eligible_customers": 3,
        "source": "default",
        "selected": ["C1", "C2", "C3"],
    }
    assert codes(base) == []
    assert codes(edited(base, targets={offer: ["C1", "NOT_A_CUSTOMER"]})) == [
        "invalid_customer_targeting"
    ]
    assert codes(edited(base, targets={offer: []})) == ["no_targets"]
    # Default targets follow exclusions; a person's own list is never silently changed.
    excluded = edited(base, excluded_regions=["east"])
    assert next(s for s in excluded["strategies"] if s["id"] == offer)["targeting"]["selected"] == [
        "C2"
    ]
    chosen = edited(base, targets={offer: ["C1", "C2"]})
    assert codes(edited(chosen, excluded_channels=["web"])) == ["invalid_customer_targeting"]
    assert codes(edited(base, discounts={offer: "0.2"})) == ["discount_exceeds_max"]
    assert codes(edited(base, max_discount_pct="0.05")) == ["discount_exceeds_max"]


def test_changed_budget_and_horizon_recalculate_estimates() -> None:
    instance = "S_QUALITY_REVIEW:refund_rate|product|X1"
    content = edited(
        drafted(budget_amount="1000"),
        assumptions={instance: {"review_cost": {"low": "500", "mid": "500", "high": "500"}}},
    )
    longer = edited(content, horizon_weeks=8)
    mid = next(s for s in longer["strategies"] if s["id"] == instance)["estimate"]["scenarios"][
        "mid"
    ]
    assert mid == {"benefit": "3200.00", "cost": "500.00", "net": "2700.00"}
    assert longer["totals"]["scenarios"]["mid"]["net"] == "2700.00"
    assert plan.content_hash(longer) != plan.content_hash(content)


@pytest.mark.parametrize(
    ("edits", "code"),
    [
        (
            {"assumptions": {"S_NOPE:x": {"effectiveness": {"low": 0, "mid": 0, "high": 0}}}},
            "unknown_strategy",
        ),
        (
            {
                "assumptions": {
                    "S_QUALITY_REVIEW:refund_rate|product|X1": {
                        "bogus": {"low": 0, "mid": 0, "high": 0}
                    }
                }
            },
            "unknown_assumption",
        ),
        (
            {
                "assumptions": {
                    "S_QUALITY_REVIEW:refund_rate|product|X1": {
                        "effectiveness": {"low": 0, "mid": 1, "high": 2}
                    }
                }
            },
            "out_of_range",
        ),
        (
            {"targets": {"S_QUALITY_REVIEW:refund_rate|product|X1": ["C1"]}},
            "not_a_targeting_strategy",
        ),
        ({"selected_strategies": ["S_QUALITY_REVIEW:somewhere|else|X"]}, "unknown_strategy"),
    ],
)
def test_edits_that_do_not_fit_the_plan_are_refused(edits: dict[str, Any], code: str) -> None:
    with pytest.raises(plan.EditError) as exc:
        edited(drafted(), **edits)
    assert exc.value.errors[0]["code"] == code


def test_edit_models_refuse_unknown_fields_and_bad_values() -> None:
    with pytest.raises(ValueError, match="extra"):
        plan.PlanEdits.model_validate({"approved": True})
    with pytest.raises(ValueError, match="low ≤ mid ≤ high"):
        plan.Range(low=D(1), mid=D(0), high=D(2))
    with pytest.raises(ValueError, match="at least one"):
        plan.PlanEdits()
    with pytest.raises(ValueError, match=r"Input should be"):
        plan.PlanEdits.model_validate({"allowed_action_types": ["wire_transfer"]})
    with pytest.raises(ValueError, match="less than or equal"):
        plan.PlanEdits(max_discount_pct=D("0.9"))
