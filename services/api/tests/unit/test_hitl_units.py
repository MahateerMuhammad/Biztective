"""Phase 6 unit tests: verification with human answers, the margin bridge, the CT-5 intent
trigger and question selection, response schemas and the version comparison (AMD-15)."""

import copy
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.analytics.metrics import Totals
from app.investigation import output
from app.investigation.catalog import CATALOG, TEMPLATES, Evidence, human_criterion
from app.investigation.critic import check_tool_result
from app.investigation.graph import _context_requests, response_schema
from app.investigation.replay import compare, plain
from app.investigation.tools import MarginDrivers, margin_effects, margin_of
from app.investigation.verification import Answer, evaluate
from app.models import Hypothesis, HypothesisEvaluation

Q6 = Decimal("0.000001")


def ev(**facts: tuple[Any, str]) -> Evidence:
    return Evidence(uuid.uuid4(), {name: {"value": v, "unit": u} for name, (v, u) in facts.items()})


def discount(change: str, share: str) -> dict[str, Evidence | None]:
    return {
        "discount_by_product": ev(**{"total.change": (change, "pp"), "top_share": (share, "ratio")})
    }


def returns(quality: str, top: str, cohort: str) -> dict[str, Evidence | None]:
    return {
        "returns_by_product": ev(
            **{
                "quality.share_change": (quality, "pp"),
                "contributors.top_share": (top, "ratio"),
                "cohort.change": (cohort, "pp"),
            }
        )
    }


def answer(effect: str, option: str | None = None) -> Answer:
    return Answer(effect, option, str(uuid.uuid4()))  # type: ignore[arg-type]  # test values


# -------------------------------------------------------------------------- catalog 1.1.0


def test_every_human_criterion_offers_unsure_and_valid_follow_ups() -> None:
    names = {t.name for t in TEMPLATES}
    for template in TEMPLATES:
        for criterion in template.criteria:
            if criterion.question is None:
                continue
            ids = [o.id for o in criterion.question.options]
            assert len(ids) == len(set(ids)), template.name
            assert "unsure" in ids
            assert {o.effect for o in criterion.question.options} >= {"unsure"}
            for option in criterion.question.options:
                assert set(option.follow_up) <= names
                assert template.name not in option.follow_up


def test_discount_question_asks_intent_and_names_the_alternatives() -> None:
    criterion = human_criterion("H_PRICE_DISCOUNT")
    assert criterion is not None
    assert criterion.question is not None
    assert criterion.question.asks_intent
    planned = criterion.question.option("planned_campaign")
    assert planned is not None
    assert planned.effect == "intended"
    assert set(planned.follow_up) == {"H_RETURNS_QUALITY", "H_COST_PRICE"}
    assert criterion.question.option("missing") is None
    returns_question = human_criterion("H_RETURNS_QUALITY")
    assert returns_question is not None
    assert returns_question.question is not None
    assert not returns_question.question.asks_intent
    assert human_criterion("H_CHANNEL") is None


def test_response_schema_lists_offered_options_and_bounds_text() -> None:
    spec = CATALOG["H_PRICE_DISCOUNT"].criteria[2].question.as_json()  # type: ignore[union-attr]
    schema = response_schema(spec)
    assert schema["required"] == ["checkpoint_id"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["option_id"]["enum"] == [
        "planned_campaign",
        "unplanned",
        "no_known_change",
        "unsure",
    ]
    assert schema["properties"]["free_text"]["maxLength"] == 1000
    assert schema["anyOf"] == [{"required": ["option_id"]}, {"required": ["free_text"]}]


# -------------------------------------------------------------------- verification v2


def test_no_answer_is_exactly_the_phase_5_evaluation() -> None:
    for evidence in (discount("0.05", "0.9"), discount("0.05", "0.1"), discount("0.0", "0.1")):
        assert evaluate(CATALOG["H_PRICE_DISCOUNT"], evidence) == evaluate(
            CATALOG["H_PRICE_DISCOUNT"], evidence, {}
        )
    result = evaluate(CATALOG["H_PRICE_DISCOUNT"], discount("0.05", "0.9"))
    assert result.interpretation is None
    assert result.conflicts == ()
    assert result.human_confidence is None


def test_planned_campaign_keeps_the_verdict_and_marks_it_intended() -> None:
    result = evaluate(
        CATALOG["H_PRICE_DISCOUNT"],
        discount("0.05", "0.9"),
        {"C3": answer("intended", "planned_campaign")},
    )
    assert result.status == "supported"
    assert result.confidence == Decimal("1.0000")  # min(1, 1.0 + 0.5 × 0.3)
    assert result.data_confidence == Decimal("1.0000")
    assert result.human_confidence == Decimal("1.0000")
    assert result.interpretation == "intended"
    assert result.conflicts == ()
    human = next(c for c in result.criteria if c.key == "C3")
    assert human.satisfied is True
    assert human.basis[0]["field"] == "answer.option"
    assert human.basis[0]["value"] == "planned_campaign"


def test_unplanned_confirms_an_unintended_problem() -> None:
    result = evaluate(
        CATALOG["H_PRICE_DISCOUNT"], discount("0.05", "0.9"), {"C3": answer("confirm", "unplanned")}
    )
    assert (result.status, result.interpretation) == ("supported", "unintended")


def test_confirm_on_a_question_without_intent_has_no_interpretation() -> None:
    result = evaluate(
        CATALOG["H_RETURNS_QUALITY"],
        returns("0.15", "0.3", "0.0"),
        {"C4": answer("confirm", "yes")},
    )
    assert result.interpretation is None
    # 0.4 data + 0.5 × 0.3 human: answered, so no longer waiting for context, but not enough.
    assert (result.status, result.confidence) == ("inconclusive", Decimal("0.5500"))


def test_human_support_can_lift_a_hypothesis_over_the_threshold() -> None:
    evidence = returns("0.15", "0.6", "0.0")  # C1 + C2 = 0.7 data confidence
    assert evaluate(CATALOG["H_RETURNS_QUALITY"], evidence).status == "supported"
    weaker = returns("0.0", "0.6", "0.05")  # C2 + C3 = 0.6: waits for context
    assert evaluate(CATALOG["H_RETURNS_QUALITY"], weaker).status == "needs_human_context"
    lifted = evaluate(CATALOG["H_RETURNS_QUALITY"], weaker, {"C4": answer("confirm", "yes")})
    assert (lifted.status, lifted.confidence) == ("supported", Decimal("0.7500"))


@pytest.mark.parametrize(
    ("effect", "status"), [("deny", "inconclusive"), ("unsure", "needs_human_context")]
)
def test_deny_and_unsure_on_mixed_evidence(effect: str, status: str) -> None:
    result = evaluate(
        CATALOG["H_RETURNS_QUALITY"], returns("0.15", "0.3", "0.0"), {"C4": answer(effect, "no")}
    )
    assert result.status == status
    assert result.confidence == Decimal("0.4000")
    assert result.conflicts == ()


def test_unstructured_answer_changes_nothing() -> None:
    evidence = returns("0.15", "0.3", "0.0")
    before = evaluate(CATALOG["H_RETURNS_QUALITY"], evidence)
    after = evaluate(CATALOG["H_RETURNS_QUALITY"], evidence, {"C4": answer("unstructured")})
    assert (after.status, after.confidence, after.human_confidence) == (
        before.status,
        before.confidence,
        None,
    )


def test_contradicting_strong_data_is_a_recorded_conflict_not_a_change() -> None:
    result = evaluate(
        CATALOG["H_PRICE_DISCOUNT"],
        discount("0.05", "0.9"),
        {"C3": answer("deny", "no_known_change")},
    )
    assert result.status == "supported"  # the measured discount rise stands
    assert result.confidence == Decimal("1.0000")
    assert result.human_confidence == Decimal("0.0000")
    assert result.conflicts == ("C3",)


def test_confirming_a_refuted_mechanism_is_a_conflict() -> None:
    result = evaluate(
        CATALOG["H_PRICE_DISCOUNT"],
        discount("0.001", "0.1"),
        {"C3": answer("confirm", "unplanned")},
    )
    assert result.status == "refuted"  # a data refutation always wins
    assert result.conflicts == ("C3",)


@pytest.mark.parametrize(
    ("effect", "status", "confidence"),
    [
        ("confirm", "supported", "1.0000"),
        ("deny", "refuted", "0.0000"),
        ("unsure", "needs_human_context", "0"),
    ],
)
def test_human_only_template_follows_the_answer(effect: str, status: str, confidence: str) -> None:
    result = evaluate(CATALOG["H_EXTERNAL_CONTEXT"], {}, {"C1": answer(effect, "stockout")})
    assert result.status == status
    assert result.confidence == Decimal(confidence)


# ------------------------------------------------------------------------ margin bridge


def test_margin_formula_matches_hand_computation() -> None:
    # gross 1000, discounts 100, refunds 90 (of 900 net sales), COGS 400 → profit 410 / 810.
    m = margin_of(Decimal("0.1"), Decimal("0.1"), Decimal("0.4"))
    assert m is not None
    assert m.quantize(Q6) == (Decimal(410) / Decimal(810)).quantize(Q6) == Decimal("0.506173")
    assert margin_of(Decimal(1), Decimal(0), Decimal("0.4")) is None  # no net revenue


def test_drivers_from_totals() -> None:
    totals = Totals(
        gross_sales=Decimal(1000),
        discounts=Decimal(100),
        refunds=Decimal(90),
        cogs_sold_known=Decimal(400),
    )
    drivers = MarginDrivers.of(totals)
    assert drivers == MarginDrivers(Decimal("0.1"), Decimal("0.1"), Decimal("0.4"))
    assert MarginDrivers.of(Totals(gross_sales=Decimal(1000), lines_missing_cost=1)) is None
    assert MarginDrivers.of(Totals()) is None


def test_effects_are_sequential_and_sum_exactly() -> None:
    before = MarginDrivers(Decimal("0.1"), Decimal("0.1"), Decimal("0.4"))
    after = MarginDrivers(Decimal("0.2"), Decimal("0.15"), Decimal("0.45"))
    effects = margin_effects(before, after)
    assert effects is not None
    # Hand: 0.506173 → 0.444444 (discount) → 0.411765 (refunds) → 0.338235 (price/cost).
    assert {k: v.quantize(Q6) for k, v in effects.items()} == {
        "discount": Decimal("-0.061728"),
        "refunds": Decimal("-0.032680"),
        "price_cost": Decimal("-0.073529"),
    }
    m0 = margin_of(before.d, before.r, before.c)
    m3 = margin_of(after.d, after.r, after.c)
    assert m0 is not None
    assert m3 is not None
    assert sum(effects.values(), Decimal(0)) == m3 - m0
    pure_discount = margin_effects(before, MarginDrivers(Decimal("0.2"), before.r, before.c))
    assert pure_discount is not None
    assert pure_discount["refunds"] == pure_discount["price_cost"] == 0
    assert margin_effects(before, MarginDrivers(Decimal(1), before.r, before.c)) is None


def test_critic_rejects_a_margin_bridge_that_does_not_add_up() -> None:
    def result(change: str) -> dict[str, Any]:
        return {
            "facts": {
                "margin.change": {"value": change, "unit": "pp"},
                "effect.discount": {"value": "-0.05", "unit": "pp"},
                "effect.refunds": {"value": "-0.01", "unit": "pp"},
                "effect.price_cost": {"value": "0.0", "unit": "pp"},
                "adverse.discount_share": {"value": "0.833333", "unit": "ratio"},
            }
        }

    assert check_tool_result(result("-0.06")) == []
    assert check_tool_result(result("-0.02")) == ["decomposition_does_not_sum: margin"]


# ----------------------------------------------------------------- question selection


def view(
    template: str, evidence: dict[str, Evidence | None], answers: dict[str, Answer] | None = None
) -> output.HypothesisView:
    result = evaluate(CATALOG[template], evidence, answers)
    row = Hypothesis(
        id=uuid.uuid4(),
        template=template,
        priority=1,
        source="catalog",
        rationale=[],
    )
    evaluation = HypothesisEvaluation(
        status=result.status,
        confidence=result.confidence,
        data_confidence=result.data_confidence,
        human_confidence=result.human_confidence,
        criteria=[c.as_json() for c in result.criteria],
        interpretation=result.interpretation,
        conflicts=list(result.conflicts),
    )
    return output.HypothesisView(row, evaluation)


def test_supported_mechanism_with_unknown_intent_triggers_ct5_first() -> None:
    views = [
        view("H_PRICE_DISCOUNT", discount("0.05", "0.9")),
        view("H_EXTERNAL_CONTEXT", {}),
    ]
    asked = _context_requests(views)
    assert [(v.row.template, key, trigger) for v, key, trigger in asked] == [
        ("H_PRICE_DISCOUNT", "C3", "CT-5")
    ]


def test_after_a_planned_answer_the_alternatives_are_asked_about() -> None:
    planned = {"C3": answer("intended", "planned_campaign")}
    views = [
        view("H_PRICE_DISCOUNT", discount("0.05", "0.9"), planned),
        view("H_RETURNS_QUALITY", returns("0.0", "0.6", "0.0")),  # 0.3: needs context
        view("H_EXTERNAL_CONTEXT", {}),
    ]
    assert views[0].explained
    assert output.root_cause(views) is None
    asked = [(v.row.template, trigger) for v, _, trigger in _context_requests(views)]
    assert asked[0] == ("H_RETURNS_QUALITY", "CT-2")
    assert ("H_PRICE_DISCOUNT", "CT-5") not in asked  # answered: never asked again
    recommended = output.recommendations(views, open_question=True)
    assert [r["kind"] for r in recommended] == ["monitor", "answer"]
    assert output.recommendations(views, open_question=False)[0]["kind"] == "monitor"


def test_unplanned_answer_keeps_the_root_cause_and_its_fix() -> None:
    views = [
        view("H_PRICE_DISCOUNT", discount("0.05", "0.9"), {"C3": answer("confirm", "unplanned")})
    ]
    cause = output.root_cause(views)
    assert cause is not None
    assert cause.row.template == "H_PRICE_DISCOUNT"
    assert _context_requests(views) == []
    assert output.recommendations(views, open_question=False)[0]["kind"] == "fix"


# ------------------------------------------------------------------- version comparison


def snapshot(version: int) -> dict[str, Any]:
    return {
        "version": version,
        "caused_by": None if version == 1 else {"human_input_id": "h1", "effect": "intended"},
        "incidents": {
            "discount_rate|product|P1": {
                "measurements": {"impact": "100.0000", "evidence": {"e1": "a" * 64}},
                "hypotheses": {
                    "H_PRICE_DISCOUNT": {
                        "status": "supported",
                        "confidence": "1.0000",
                        "human_confidence": None,
                        "interpretation": None,
                        "conflicts": [],
                    }
                },
                "root_cause": "H_PRICE_DISCOUNT",
                "explained_by_context": [],
                "recommendations": ["Review discounts."],
                "human_context": [],
            }
        },
        "findings": {
            "summary": "One problem.",
            "root_causes": ["Discounting."],
            "explained_by_context": [],
            "conflicts": [],
            "caveats": [],
        },
    }


def test_compare_lists_what_the_answer_changed() -> None:
    a = snapshot(1)
    b = snapshot(2)
    incident = b["incidents"]["discount_rate|product|P1"]
    incident["hypotheses"]["H_PRICE_DISCOUNT"].update(
        interpretation="intended", human_confidence="1.0000"
    )
    incident["hypotheses"]["H_COST_PRICE"] = {"status": "refuted", "confidence": "0.0000"}
    incident["measurements"]["evidence"]["e2"] = "b" * 64
    incident.update(
        root_cause=None,
        explained_by_context=["H_PRICE_DISCOUNT"],
        recommendations=["Track the campaign."],
        human_context=[{"human_input_id": "h1", "effect": "intended"}],
    )
    b["findings"].update(
        summary="Explained by a campaign.",
        root_causes=[],
        explained_by_context=["Discounting."],
        caveats=["Note."],
    )
    diff = compare(a, b)
    assert diff["measurements_preserved"] is True
    assert diff["caused_by"] == {"human_input_id": "h1", "effect": "intended"}
    (changed,) = diff["incidents"]
    assert changed["root_cause"] == {"from": "H_PRICE_DISCOUNT", "to": None}
    assert changed["explained_by_context"] == {"added": ["H_PRICE_DISCOUNT"], "removed": []}
    assert changed["new_evidence"] == ["e2"]
    assert changed["recommendations"] == {
        "added": ["Track the campaign."],
        "removed": ["Review discounts."],
    }
    hypotheses = {h["template"]: h for h in changed["hypotheses"]}
    assert hypotheses["H_COST_PRICE"]["added"] is True
    assert hypotheses["H_PRICE_DISCOUNT"]["changes"] == {
        "human_confidence": {"from": None, "to": "1.0000"},
        "interpretation": {"from": None, "to": "intended"},
    }
    assert changed["human_context_added"] == [{"human_input_id": "h1", "effect": "intended"}]
    assert diff["findings"]["summary"] == {"from": "One problem.", "to": "Explained by a campaign."}
    assert diff["findings"]["root_causes"] == {"added": [], "removed": ["Discounting."]}
    assert diff["findings"]["explained_by_context"]["added"] == ["Discounting."]
    assert diff["findings"]["caveats"]["added"] == ["Note."]


def test_compare_detects_changed_or_missing_measurements() -> None:
    a = snapshot(1)
    tampered = copy.deepcopy(a)
    tampered["version"] = 2
    tampered["incidents"]["discount_rate|product|P1"]["measurements"]["evidence"]["e1"] = "c" * 64
    diff = compare(a, tampered)
    assert diff["measurements_preserved"] is False
    assert diff["incidents"][0]["changed_evidence"] == ["e1"]

    impact = copy.deepcopy(a)
    impact["incidents"]["discount_rate|product|P1"]["measurements"]["impact"] = "99.0000"
    assert compare(a, impact)["measurements_preserved"] is False

    gone = copy.deepcopy(a)
    gone["incidents"] = {}
    removed = compare(a, gone)
    assert removed["measurements_preserved"] is False
    assert removed["incidents"] == [
        {"key": "discount_rate|product|P1", "added": False, "removed": True}
    ]
    identical = compare(a, a)
    assert identical["measurements_preserved"] is True
    assert identical["incidents"][0]["hypotheses"] == []
    assert identical["findings"]["summary"] is None


def test_plain_text_of_rendered_segments() -> None:
    segments = [
        {"text": "Rate "},
        {"value": "20.2%", "evidence_id": "e", "field": "f"},
        {"entity": "Mat (P1)", "kind": "product"},
    ]
    assert plain(segments) == "Rate 20.2%Mat (P1)"
