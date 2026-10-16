"""Facts: the flat, typed view of an evidence result that hypotheses, the critic, the renderer
and the LLM all read (AMD-12).

# Any is confined to JSON/SQL driver boundaries; tool arguments and rules are typed.

Every tool result stores `facts: {name: {"value", "unit"[, "kind"]}}`. Numeric facts are decimal
strings (money, ratio, pp, number) or ints (count). Entity facts carry the raw business value
for people; the LLM only ever sees an opaque `{{entity:<kind>:<ref>}}` token instead.
"""

import hashlib
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal

from app.analytics.metrics import RATIO_METRICS, analytics_context, fraction, money

Unit = Literal["money", "ratio", "pp", "count", "number", "entity", "code"]
NUMERIC_UNITS: frozenset[str] = frozenset({"money", "ratio", "pp", "count", "number"})
EntityKind = Literal["category", "product", "region", "channel", "segment"]
ENTITY_KINDS: frozenset[str] = frozenset({"category", "product", "region", "channel", "segment"})
DIMENSION_ENTITY: dict[str, EntityKind] = {
    "category": "category",
    "product": "product",
    "region": "region",
    "channel": "channel",
    "customer_segment": "segment",
}
Facts = dict[str, dict[str, Any]]


def fact(value: Decimal | int | str | None, unit: Unit) -> dict[str, Any]:
    if isinstance(value, Decimal):
        with localcontext(analytics_context()):
            rendered: str | int | None = (
                fraction(value) if unit in ("ratio", "pp") else money(value)
            )
    else:
        rendered = value
    return {"value": rendered, "unit": unit}


def entity_fact(dimension: str, value: str | None) -> dict[str, Any]:
    return {"value": value, "unit": "entity", "kind": DIMENSION_ENTITY[dimension]}


def entity_ref(kind: str, value: str) -> str:
    """Stable, opaque reference to a business value (never reversible by the model)."""
    return hashlib.sha256(f"{kind}\x00{value}".encode()).hexdigest()[:12]


def entity_token(kind: str, value: str) -> str:
    return "{{entity:" + kind + ":" + entity_ref(kind, value) + "}}"


def numeric(f: Mapping[str, Any] | None) -> Decimal | None:
    """The fact's value as a Decimal, or None when it is missing or not numeric."""
    if f is None or f.get("unit") not in NUMERIC_UNITS or f.get("value") is None:
        return None
    try:
        value = Decimal(str(f["value"]))
        return value if value.is_finite() else None
    except InvalidOperation:
        return None


def llm_fields(facts: Facts) -> tuple[dict[str, Any], dict[str, str]]:
    """(fields, units) for an LLM envelope: entity values become placeholder tokens."""
    fields: dict[str, Any] = {}
    units: dict[str, str] = {}
    for name, f in facts.items():
        value = f.get("value")
        if f.get("unit") == "entity":
            value = entity_token(f["kind"], value) if isinstance(value, str) else None
        fields[name] = value
        units[name] = f["unit"]
    return fields, units


def entities(facts: Facts) -> list[tuple[str, str]]:
    return [
        (f["kind"], f["value"])
        for f in facts.values()
        if f.get("unit") == "entity" and isinstance(f.get("value"), str)
    ]


# ------------------------------------------------- Phase 4 detection evidence (no facts key)


def _metric_unit(metric: str) -> tuple[Unit, Unit]:
    """(unit of values, unit of absolute changes) for a detection metric."""
    if metric in RATIO_METRICS:
        return "ratio", "pp"
    if metric == "active_customers":
        return "number", "number"
    return "money", "money"


def detection_facts(kind: str, result: Mapping[str, Any]) -> Facts:
    if "facts" in result:
        facts: Facts = result["facts"]
        return facts
    out: Facts = {}
    if kind == "incident_detection":
        value_unit, change_unit = _metric_unit(str(result["metric"]))
        out["metric"] = {"value": result["metric"], "unit": "code"}
        for name in ("baseline_median", "baseline_aggregate", "current_value"):
            if result.get(name) is not None:
                out[name] = {"value": result[name], "unit": value_unit}
        if result.get("delta_abs") is not None:
            out["delta_abs"] = {"value": result["delta_abs"], "unit": change_unit}
        if result.get("delta_rel") is not None:
            out["delta_rel"] = {"value": result["delta_rel"], "unit": "ratio"}
        if result.get("z") is not None:
            out["z"] = {"value": str(result["z"]), "unit": "number"}
        out["impact"] = {"value": result["impact"], "unit": "money"}
        out["materiality"] = {"value": result["materiality"], "unit": "money"}
        for name, count in sorted(result.get("support", {}).items()):
            out[f"support.{name}"] = {"value": int(count), "unit": "count"}
        if result.get("value") is not None and result.get("dimension") in DIMENSION_ENTITY:
            out["slice"] = entity_fact(str(result["dimension"]), str(result["value"]))
    elif kind == "return_reason_mix":
        out["refunds.baseline_total"] = {"value": result["baseline_total"], "unit": "money"}
        out["refunds.current_total"] = {"value": result["current_total"], "unit": "money"}
        for row in result.get("reasons", []):
            code = row["reason_code"]
            for name, unit in (
                ("baseline_share", "ratio"),
                ("current_share", "ratio"),
                ("share_change", "pp"),
                ("current_refunds", "money"),
            ):
                if row.get(name) is not None:
                    out[f"reasons.{code}.{name}"] = {"value": row[name], "unit": unit}
    elif kind == "source_records":
        for name, count in sorted(result.get("counts", {}).items()):
            out[f"counts.{name}"] = {"value": int(count), "unit": "count"}
        out["sample_size"] = {"value": int(result.get("sample_size", 0)), "unit": "count"}
    return out
