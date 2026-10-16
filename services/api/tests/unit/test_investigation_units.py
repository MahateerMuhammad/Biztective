"""Phase 5 unit tests: catalog (T-06), tool parameter models (T-07), verification, the
Evidence Critic and placeholder validation (T-23), rendering and provider schemas."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.analytics.evidence import sha256_of
from app.investigation.catalog import CATALOG, TEMPLATES, Evidence, candidates
from app.investigation.critic import Citations, check_refs, check_text, check_tool_result, intact
from app.investigation.facts import detection_facts, entity_token, fact, llm_fields
from app.investigation.llm_contracts import (
    PrioritizeOutput,
    SelectToolsOutput,
    SynthesisOutput,
    validate_prioritize,
    validate_select_tools,
    validate_synthesis,
)
from app.investigation.render import Renderer, display, observations
from app.investigation.tools import (
    TOOLS,
    CustomerExposureParams,
    DataQualityParams,
    EvidenceLookupParams,
    MetricBreakdownParams,
    PeriodComparisonParams,
    ReturnInvestigationParams,
)
from app.investigation.verification import evaluate
from app.llm.schema import provider_schema

# ------------------------------------------------------------------------------- catalog


@pytest.mark.parametrize(
    "metric",
    [
        "net_revenue",
        "gross_margin_pct",
        "refund_rate",
        "discount_rate",
        "active_customers",
        "repeat_customer_rate",
        "aov",
    ],
)
def test_every_metric_has_competing_candidates(metric: str) -> None:
    names = [t.name for t in candidates(metric)]
    assert {"H_DATA_QUALITY", "H_EXTERNAL_CONTEXT"} <= set(names)
    assert len(names) >= 3  # at least one business explanation competes with the two above
    assert names == [t.name for t in TEMPLATES if t.name in names]  # catalog order


def test_requirements_build_valid_parameters_for_every_metric() -> None:
    for template in TEMPLATES:
        for requirement in template.requirements:
            for metric in template.metrics:
                params = requirement.params(metric)
                assert isinstance(params, TOOLS[requirement.tool].params)


def test_catalog_shape() -> None:
    for template in TEMPLATES:
        keys = [c.key for c in template.criteria]
        assert len(keys) == len(set(keys)), template.name
        for criterion in template.criteria:
            if criterion.source == "human":
                assert criterion.question is not None
                assert criterion.rule is None
                assert criterion.question.options
            else:
                assert criterion.rule is not None
                assert criterion.question is None
        if template.name != "H_EXTERNAL_CONTEXT":
            assert any(c.source == "data" and c.polarity == "support" for c in template.criteria)
    assert CATALOG["H_EXTERNAL_CONTEXT"].human_only
    assert "churn" not in " ".join(
        t.claim.lower() for t in TEMPLATES if "not a churn" not in t.claim
    )


# --------------------------------------------------------------------------- verification


def ev(**facts: tuple[Any, str]) -> Evidence:
    return Evidence(uuid.uuid4(), {name: {"value": v, "unit": u} for name, (v, u) in facts.items()})


def returns_evidence(quality: str, top: str, cohort: str | None) -> Evidence:
    return ev(
        **{
            "quality.share_change": (quality, "pp"),
            "contributors.top_share": (top, "ratio"),
            "cohort.change": (cohort, "pp"),
        }
    )


@pytest.mark.parametrize(
    ("quality", "top", "cohort", "status", "confidence"),
    [
        ("0.21", "0.80", "0.05", "supported", "1.0000"),  # all three support criteria
        ("0.15", "0.30", "0.00", "needs_human_context", "0.4000"),  # C1 only: ask a human
        ("0.00", "0.20", "0.00", "refuted", "0.0000"),  # R1: no reason shift, no product
        ("0.05", "0.60", "0.00", "needs_human_context", "0.3000"),  # C2 only: at the floor
        ("0.05", "0.20", "0.00", "refuted", "0.0000"),  # nothing met, all evidence present
    ],
)
def test_returns_quality_statuses(
    quality: str, top: str, cohort: str, status: str, confidence: str
) -> None:
    result = evaluate(
        CATALOG["H_RETURNS_QUALITY"], {"returns_by_product": returns_evidence(quality, top, cohort)}
    )
    assert result.status == status
    assert result.confidence == Decimal(confidence)
    assert result.human_confidence is None


def test_missing_evidence_is_inconclusive_not_refuted() -> None:
    result = evaluate(CATALOG["H_RETURNS_QUALITY"], {"returns_by_product": None})
    assert result.status == "inconclusive"
    assert result.missing_evidence == ("returns_by_product",)
    assert {c.key for c in result.unknown_data} == {"C1", "C2", "C3", "R1"}


def test_unknown_criterion_blocks_low_confidence_refutation() -> None:
    # Cohort unknown (immature returns), the rest weak: inconclusive, not refuted.
    result = evaluate(
        CATALOG["H_RETURNS_QUALITY"],
        {"returns_by_product": returns_evidence("0.03", "0.40", None)},
    )
    assert result.status == "inconclusive"


def test_every_template_reaches_its_statuses() -> None:
    reached: dict[str, set[str]] = {t.name: set() for t in TEMPLATES}

    def record(name: str, evidence: dict[str, Evidence | None]) -> None:
        reached[name].add(evaluate(CATALOG[name], evidence).status)

    for quality_shift, top, cohort in [
        ("0.21", "0.8", "0.05"),
        ("0.15", "0.3", "0.0"),
        ("0.0", "0.2", "0.0"),
    ]:
        record(
            "H_RETURNS_QUALITY",
            {"returns_by_product": returns_evidence(quality_shift, top, cohort)},
        )
    record("H_RETURNS_QUALITY", {"returns_by_product": None})

    def fulfil(logistics: str | None, region: str, channel: str) -> dict[str, Evidence | None]:
        return {
            "returns_by_region": ev(
                **{
                    "logistics.share_change": (logistics, "pp"),
                    "contributors.top_share": (region, "ratio"),
                }
            ),
            "returns_by_channel": ev(**{"contributors.top_share": (channel, "ratio")}),
        }

    record("H_FULFILLMENT", fulfil("0.2", "0.7", "0.4"))  # supported
    record("H_FULFILLMENT", fulfil("0.2", "0.3", "0.3"))  # needs human context
    record("H_FULFILLMENT", fulfil("0.0", "0.3", "0.3"))  # refuted
    record("H_FULFILLMENT", fulfil(None, "0.3", "0.3"))  # inconclusive (no refunds)

    def discount(change: str, share: str) -> dict[str, Evidence | None]:
        return {
            "discount_by_product": ev(
                **{"total.change": (change, "pp"), "top_share": (share, "ratio")}
            )
        }

    record("H_PRICE_DISCOUNT", discount("0.05", "0.9"))
    record("H_PRICE_DISCOUNT", discount("0.05", "0.1"))
    record("H_PRICE_DISCOUNT", discount("0.001", "0.1"))
    record("H_PRICE_DISCOUNT", {"discount_by_product": None})

    def segments(active: str, share: str | None) -> dict[str, Evidence | None]:
        return {
            "segments": ev(
                **{
                    "established.active_change_rel": (active, "ratio"),
                    "established.revenue_decline_share": (share, "ratio"),
                }
            )
        }

    record("H_SEGMENT_SHIFT", segments("-0.3", "0.8"))
    record("H_SEGMENT_SHIFT", segments("-0.3", "0.2"))
    record("H_SEGMENT_SHIFT", segments("0.0", "0.2"))
    record("H_SEGMENT_SHIFT", segments("-0.1", None))

    def exposure(share: str | None, top1: str | None) -> dict[str, Evidence | None]:
        return {"exposure": ev(**{"top_k.share": (share, "ratio"), "top1.share": (top1, "ratio")})}

    record("H_CONCENTRATION", exposure("0.7", "0.3"))
    record("H_CONCENTRATION", exposure("0.6", "0.1"))
    record("H_CONCENTRATION", exposure("0.1", "0.05"))
    record("H_CONCENTRATION", exposure("0.3", None))

    def channel(values: int, share: str) -> dict[str, Evidence | None]:
        return {"by_channel": ev(**{"values": (values, "count"), "top_share": (share, "ratio")})}

    record("H_CHANNEL", channel(3, "0.8"))
    record("H_CHANNEL", channel(3, "0.3"))
    record("H_CHANNEL", channel(1, "1.0"))  # a channel slice: not informative

    def quality(zero: str, low: int, dups: str, top1: str) -> dict[str, Evidence | None]:
        return {
            "quality": ev(
                **{
                    "zero_days.excess_per_week": (zero, "number"),
                    "volume.low_weeks": (low, "count"),
                    "near_duplicates.share": (dups, "ratio"),
                }
            ),
            "exposure": ev(**{"top1.share": (top1, "ratio")}),
        }

    record("H_DATA_QUALITY", quality("2", 1, "0.05", "0.4"))
    record("H_DATA_QUALITY", quality("2", 0, "0.0", "0.15"))
    record("H_DATA_QUALITY", quality("0", 0, "0.0", "0.05"))
    record("H_DATA_QUALITY", {"quality": None, "exposure": None})
    record("H_EXTERNAL_CONTEXT", {})

    full = {"supported", "refuted", "inconclusive", "needs_human_context"}
    for name, statuses in reached.items():
        expected = {
            "H_CHANNEL": {"supported", "refuted", "inconclusive"},  # no human criterion
            "H_EXTERNAL_CONTEXT": {"needs_human_context"},  # human-only
        }.get(name, full)
        assert statuses == expected, name


def test_criteria_record_the_facts_they_read() -> None:
    evidence = returns_evidence("0.21", "0.8", "0.05")
    result = evaluate(CATALOG["H_RETURNS_QUALITY"], {"returns_by_product": evidence})
    c1 = next(c for c in result.criteria if c.key == "C1")
    assert c1.basis == (
        {
            "evidence_id": str(evidence.id),
            "field": "quality.share_change",
            "value": "0.21",
            "unit": "pp",
        },
    )


# ------------------------------------------------------------------- tool parameter models


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (MetricBreakdownParams, {"metric": "net_revenue", "by": "price; DROP TABLE x"}),
        (MetricBreakdownParams, {"metric": "profit", "by": "none"}),
        (MetricBreakdownParams, {"metric": "net_revenue", "by": "none", "business_id": "x"}),
        (MetricBreakdownParams, {"metric": "net_revenue"}),
        (PeriodComparisonParams, {"metrics": []}),
        (PeriodComparisonParams, {"metrics": ["aov", "aov"]}),
        (ReturnInvestigationParams, {"by": "customer"}),
        (CustomerExposureParams, {"top_k": 0}),
        (CustomerExposureParams, {"top_k": 21}),
        (CustomerExposureParams, {"top_k": "5"}),
        (EvidenceLookupParams, {"evidence_ids": ["not-a-uuid"]}),
        (EvidenceLookupParams, {"evidence_ids": [str(uuid.uuid4()) for _ in range(11)]}),
        (DataQualityParams, {"checks": ["drop_tables"]}),
        (DataQualityParams, {"checks": ["near_duplicates", "near_duplicates"]}),
    ],
)
def test_tool_parameters_reject_invalid_input(model: type[BaseModel], payload: Any) -> None:
    import json

    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


def test_tool_call_union_rejects_unknown_tools() -> None:
    import json

    bad = {
        "calls": [
            {
                "tool": "delete_dataset",
                "arguments": {},
                "hypothesis": "H_CHANNEL",
                "purpose": "x",
            }
        ],
        "done": True,
    }
    with pytest.raises(ValidationError):
        SelectToolsOutput.model_validate_json(json.dumps(bad))
    too_many = {
        "calls": [
            {
                "tool": "segment_analysis",
                "arguments": {},
                "hypothesis": "H_SEGMENT_SHIFT",
                "purpose": "x",
            }
        ]
        * 7,
        "done": False,
    }
    with pytest.raises(ValidationError):
        SelectToolsOutput.model_validate_json(json.dumps(too_many))


def test_every_tool_is_registered_read_only() -> None:
    assert set(TOOLS) == {
        "metric_breakdown",
        "period_comparison",
        "return_investigation",
        "segment_analysis",
        "customer_exposure",
        "evidence_lookup",
        "data_quality_check",
    }
    for spec in TOOLS.values():
        fields = set(spec.params.model_fields)
        assert not fields & {"business_id", "dataset_id", "revision_id", "sql", "query", "value"}


# ------------------------------------------------------------------------- evidence critic


def citations_with(evidence_id: str) -> Citations:
    c = Citations()
    c.add_evidence(
        evidence_id,
        {
            "impact": {"value": "1250.5000", "unit": "money"},
            "quality.share_change": {"value": "0.213000", "unit": "pp"},
            "total.change": {"value": "-0.030000", "unit": "pp"},
            "metric": {"value": "refund_rate", "unit": "code"},
            "top.1.entity": {"value": "Velvet Lamp", "unit": "entity", "kind": "product"},
        },
    )
    return c


def test_valid_text_passes() -> None:
    eid = str(uuid.uuid4())
    c = citations_with(eid)
    token = entity_token("product", "Velvet Lamp")
    text = (
        f"Damaged and defective returns rose by {{{{ev:{eid}.quality.share_change}}}} for "
        f"{token}, costing {{{{ev:{eid}.impact}}}}."
    )
    assert check_text(text, c, "t") == []


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Refunds rose by 12%.", "digit_outside_placeholder"),
        ("Refunds rose by {{ev:%s.impact}} and 3 more.", "digit_outside_placeholder"),
        ("See {{ev:00000000-0000-0000-0000-000000000000.impact}}.", "unknown_evidence"),
        ("See {{ev:%s.nope}}.", "unknown_field"),
        ("See {{ev:%s.metric}}.", "non_numeric_field"),
        ("See {{entity:product:abcdefabcdef}}.", "unknown_entity"),
        ("See {{sql:drop}}.", "malformed_placeholder"),
        ("See {{ev:%s.impact}.", "malformed_placeholder"),
        ("The Velvet Lamp is to blame.", "raw_entity_name"),
        ("<script>alert(document.cookie)</script>", "markup"),
        ("The discount rate rose by {{ev:%s.total.change}}.", "direction_contradiction"),
        ("Damaged returns fell by {{ev:%s.quality.share_change}}.", "direction_contradiction"),
    ],
)
def test_critic_rejects(text: str, code: str) -> None:
    eid = str(uuid.uuid4())
    errors = check_text(text.replace("%s", eid), citations_with(eid), "t")
    assert any(e.startswith(code) for e in errors), errors


def test_direction_check_ignores_ambiguous_sentences() -> None:
    eid = str(uuid.uuid4())
    c = citations_with(eid)
    two = f"It rose by {{{{ev:{eid}.total.change}}}} and {{{{ev:{eid}.quality.share_change}}}}."
    neutral = f"The discount rate changed by {{{{ev:{eid}.total.change}}}}."
    assert check_text(two, c, "t") == []
    assert check_text(neutral, c, "t") == []


def test_refs_and_scoping() -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    c = citations_with(a)
    c.add_evidence(b, {"impact": {"value": "1", "unit": "money"}})
    assert check_refs([a, b], c, "t") == []
    scoped = c.restricted_to([a])
    assert check_refs([b], scoped, "t") != []
    assert check_text(f"{{{{ev:{b}.impact}}}}", scoped, "t") != []


@pytest.mark.parametrize(
    ("facts", "detail", "problem"),
    [
        ({"top_share": {"value": "1.2", "unit": "ratio"}}, {}, "share_out_of_range"),
        ({"x.share_change": {"value": "-1.5", "unit": "pp"}}, {}, "share_change_out_of_range"),
        ({"lines": {"value": -1, "unit": "count"}}, {}, "count_invalid"),
        ({"lines": {"value": 1.5, "unit": "count"}}, {}, "count_invalid"),
        ({"impact": {"value": "NaN", "unit": "money"}}, {}, "not_finite"),
        ({"impact": {"value": "abc", "unit": "money"}}, {}, "not_a_number"),
        (
            {
                "top.1.share": {"value": "0.7", "unit": "ratio"},
                "top.2.share": {"value": "0.6", "unit": "ratio"},
            },
            {},
            "shares_exceed_total",
        ),
        (
            {
                "top_k.share": {"value": "0.3", "unit": "ratio"},
                "top1.share": {"value": "0.5", "unit": "ratio"},
            },
            {},
            "top1_exceeds_top_k",
        ),
        (
            {
                "near_duplicates.lines": {"value": 9, "unit": "count"},
                "lines.current": {"value": 3, "unit": "count"},
            },
            {},
            "duplicates_exceed_lines",
        ),
        ({}, {"rows": [{"share": "0.5"}, {"share": "0.2"}]}, "decomposition_does_not_sum"),
    ],
)
def test_impossible_tool_results_are_rejected(
    facts: dict[str, Any], detail: dict[str, Any], problem: str
) -> None:
    problems = check_tool_result({"facts": facts, "detail": detail})
    assert any(p.startswith(problem) for p in problems), problems


def test_plausible_tool_result_passes_and_integrity() -> None:
    result: dict[str, Any] = {
        "facts": {
            "top_share": {"value": "0.6", "unit": "ratio"},
            "top.1.share": {"value": "0.6", "unit": "ratio"},
            "top.2.share": {"value": "0.4", "unit": "ratio"},
            "total.change": {"value": "-0.4", "unit": "pp"},
            "lines": {"value": 4, "unit": "count"},
        },
        "detail": {"rows": [{"share": "0.6"}, {"share": "0.4"}]},
    }
    assert check_tool_result(result) == []
    digest = sha256_of(result)
    assert intact(result, digest)
    tampered = {**result, "facts": {**result["facts"], "lines": {"value": 5, "unit": "count"}}}
    assert not intact(tampered, digest)


# ------------------------------------------------------------------------------- rendering


@pytest.mark.parametrize(
    ("value", "unit", "shown"),
    [
        ("1234.5678", "money", "USD 1,234.57"),
        ("-12.004", "money", "-USD 12.00"),
        ("0.123456", "ratio", "12.3%"),
        ("0.021", "pp", "+2.1 pp"),
        ("-0.05", "pp", "-5.0 pp"),
        (1234, "count", "1,234"),
        ("3.14159", "number", "3.14"),
        (None, "money", "n/a"),
    ],
)
def test_display(value: Any, unit: str, shown: str) -> None:
    assert display({"value": value, "unit": unit}, "USD") == shown


def test_renderer_segments_values_and_entities() -> None:
    eid = str(uuid.uuid4())
    token = entity_token("category", "<b>kitchen</b>")
    renderer = Renderer(
        {eid: {"impact": {"value": "10", "unit": "money"}}},
        {token: ("category", "<b>kitchen</b>")},
        "EUR",
    )
    segments = renderer.segments(f"Impact {{{{ev:{eid}.impact}}}} in {token}.")
    assert segments == [
        {"text": "Impact "},
        {"value": "EUR 10.00", "evidence_id": eid, "field": "impact"},
        {"text": " in "},
        {"entity": "<b>kitchen</b>", "kind": "category"},  # shown as text by the UI
        {"text": "."},
    ]


def test_observations_cite_detection_evidence_only_through_placeholders() -> None:
    det, rec, mix = (str(uuid.uuid4()) for _ in range(3))
    detection = detection_facts(
        "incident_detection",
        {
            "metric": "refund_rate",
            "dimension": "category",
            "value": "kitchen",
            "baseline_aggregate": "0.05",
            "current_value": "0.12",
            "delta_abs": "0.07",
            "delta_rel": "1.4",
            "z": 5.2,
            "impact": "900.0000",
            "materiality": "100.0000",
            "support": {"baseline_orders": 300, "current_orders": 120},
        },
    )
    records = detection_facts("source_records", {"counts": {"orders": 5}, "sample_size": 5})
    reasons = detection_facts(
        "return_reason_mix",
        {
            "baseline_total": "10",
            "current_total": "30",
            "reasons": [
                {
                    "reason_code": "defective",
                    "baseline_share": "0.1",
                    "current_share": "0.5",
                    "share_change": "0.4",
                    "current_refunds": "15",
                }
            ],
        },
    )
    lines = observations(
        "refund_rate",
        "category",
        "kitchen",
        [
            (det, "incident_detection", detection),
            (rec, "source_records", records),
            (mix, "return_reason_mix", reasons),
        ],
    )
    citations = Citations()
    for eid, facts in ((det, detection), (rec, records), (mix, reasons)):
        citations.add_evidence(eid, facts)
    for line in lines:
        assert check_text(line, citations, "observation") == []
    assert "kitchen" not in " ".join(lines)
    fields, units = llm_fields(detection)
    assert fields["slice"] == entity_token("category", "kitchen")
    assert units["impact"] == "money"


# ----------------------------------------------------------------------- provider schemas


def _walk(node: Any) -> list[dict[str, Any]]:
    out = []
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            out.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(_walk(value))
    return out


@pytest.mark.parametrize("model", [SelectToolsOutput, SynthesisOutput])
def test_provider_schema_is_supported_subset(model: type[BaseModel]) -> None:
    schema = provider_schema(model)
    unsupported = {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "oneOf"}
    for node in _walk(schema):
        assert not (set(node) & unsupported), node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False


def test_provider_schema_keeps_property_named_like_a_keyword() -> None:
    class Odd(BaseModel):
        title: str
        default: int

    schema = provider_schema(Odd)
    assert set(schema["properties"]) == {"title", "default"}


@pytest.mark.parametrize(
    "text", ["Refunds rose by \uff11\uff12%.", "Refunds rose by \u0661\u0662%."]
)
def test_critic_rejects_unicode_numerical_claims(text: str) -> None:
    assert any(
        e.startswith("digit_outside_placeholder") for e in check_text(text, Citations(), "t")
    )


@pytest.mark.parametrize("text", ["See \x009999\x00.", "See \x000\x00."])
def test_critic_rejects_forged_internal_markers(text: str) -> None:
    assert check_text(text, Citations(), "t")[0].startswith("control_character")


def test_entity_citations_are_scoped_to_incident_evidence() -> None:
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    citations = citations_with(first)
    citations.add_evidence(
        second, {"entity": {"value": "Foreign Product", "unit": "entity", "kind": "product"}}
    )
    scoped = citations.restricted_to([first])
    assert check_text(entity_token("product", "Velvet Lamp"), scoped, "t") == []
    assert check_text(entity_token("product", "Foreign Product"), scoped, "t")[0].startswith(
        "unknown_entity"
    )


@pytest.mark.parametrize("share", ["NaN", "Infinity", "invalid"])
def test_malformed_decomposition_shares_are_rejected_without_crashing(share: str) -> None:
    assert check_tool_result({"facts": {}, "detail": {"rows": [{"share": share}]}})


def test_numeric_claim_citation_must_be_listed_in_evidence_refs() -> None:
    eid = str(uuid.uuid4())
    output = PrioritizeOutput.model_validate(
        {
            "ranking": [
                {
                    "template": "H_DATA_QUALITY",
                    "priority": 1,
                    "rationale": [
                        {"text": "Check " + "{{ev:" + eid + ".impact}}.", "evidence_refs": []}
                    ],
                }
            ]
        }
    )
    errors = validate_prioritize(output, {"H_DATA_QUALITY"}, citations_with(eid))
    assert any(error.startswith("unlisted_citation") for error in errors)


def test_prioritization_rejects_unknown_and_duplicate_templates() -> None:
    output = PrioritizeOutput.model_validate(
        {
            "ranking": [
                {"template": "H_CHANNEL", "priority": 1, "rationale": []},
                {"template": "H_CHANNEL", "priority": 2, "rationale": []},
            ]
        }
    )
    errors = validate_prioritize(output, {"H_DATA_QUALITY"}, Citations())
    assert any(error.startswith("unknown_template") for error in errors)
    assert any(error.startswith("duplicate_template") for error in errors)


def test_tool_selection_rejects_foreign_hypotheses_and_evidence() -> None:
    import json

    output = SelectToolsOutput.model_validate_json(
        json.dumps(
            {
                "calls": [
                    {
                        "tool": "evidence_lookup",
                        "arguments": {"evidence_ids": [str(uuid.uuid4())]},
                        "hypothesis": "H_CHANNEL",
                        "purpose": "Check evidence.",
                    }
                ],
                "done": False,
            }
        )
    )
    errors = validate_select_tools(output, {"H_DATA_QUALITY"}, Citations())
    assert any(error.startswith("unknown_hypothesis") for error in errors)
    assert any(error.startswith("unknown_evidence") for error in errors)


@pytest.mark.parametrize("status", ["supported", "refuted", "inconclusive", "needs_human_context"])
def test_synthesis_causes_must_be_supported_and_consistent(status: str) -> None:
    hid, eid = str(uuid.uuid4()), str(uuid.uuid4())
    cause = {"hypothesis_id": hid, "statement": "Evidence supports this.", "evidence_refs": [eid]}
    output = SynthesisOutput.model_validate(
        {
            "summary": "The evidence is available.",
            "root_causes": [cause, cause],
            "alternatives_considered": [
                {"hypothesis_id": hid, "why_not": "Other explanation.", "evidence_refs": [eid]}
            ],
            "caveats": ["Context remains unresolved."],
        }
    )
    errors = validate_synthesis(
        output, {hid: status}, {hid: "incident"}, citations_with(eid), {"incident": {eid}}
    )
    assert any(error.startswith("duplicate_hypothesis") for error in errors)
    assert any(error.startswith("contradiction") for error in errors)
    assert any(error.startswith("unsupported_root_cause") for error in errors) == (
        status != "supported"
    )


def test_synthesis_rejects_unknown_hypotheses_without_crashing() -> None:
    hid, eid = str(uuid.uuid4()), str(uuid.uuid4())
    output = SynthesisOutput.model_validate(
        {
            "summary": "Unresolved.",
            "root_causes": [{"hypothesis_id": hid, "statement": "Claim.", "evidence_refs": [eid]}],
            "alternatives_considered": [
                {"hypothesis_id": hid, "why_not": "Unknown.", "evidence_refs": []}
            ],
            "caveats": [],
        }
    )
    errors = validate_synthesis(output, {}, {}, Citations(), {})
    assert len(errors) == 2
    assert all(error.startswith("unknown_hypothesis") for error in errors)


def test_criterion_serialization_preserves_exact_evidence_basis() -> None:
    evidence = returns_evidence("0.21", "0.8", "0.05")
    evaluation = evaluate(CATALOG["H_RETURNS_QUALITY"], {"returns_by_product": evidence})
    outcome = next(criterion for criterion in evaluation.criteria if criterion.key == "C1")
    rendered = outcome.as_json()
    assert rendered["satisfied"] is True
    assert rendered["weight"] == str(outcome.weight)
    assert rendered["basis"] == list(outcome.basis)
    assert rendered["basis"][0]["evidence_id"] == str(evidence.id)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_derived_shares_are_rejected_without_crashing(value: str) -> None:
    assert check_tool_result(
        {"facts": {"top.1.share": {"value": value, "unit": "ratio"}}, "detail": {}}
    )


def test_extreme_valid_period_ratio_preserves_precision() -> None:
    # Refunds from earlier sales can dwarf a tiny current-period net-sales denominator.
    value = Decimal("500000000000000000000000.123456")
    assert fact(value, "ratio") == {"value": str(value), "unit": "ratio"}
