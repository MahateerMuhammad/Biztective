"""Recovery plan content: drafting, human edits, recalculation and validation (AMD-17).

A plan version is canonical JSON hashed with SHA-256. Everything here is a pure function of the
previous content, the edits and the stored economics evidence, so the same inputs always give
the same strategies, numbers and validation result, whatever an LLM wrote as rationale.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analytics.evidence import canonical_json, sha256_of
from app.analytics.metrics import analytics_context
from app.investigation.facts import Facts
from app.recovery.catalog import (
    ACTION_TYPES,
    BY_CODE,
    CATALOG_VERSION,
    DEFAULT_ALLOWED_ACTIONS,
    ORDER,
    ActionType,
    Strategy,
    strategies_for,
)
from app.recovery.economics import observe
from app.recovery.estimator import (
    ESTIMATOR_VERSION,
    SCENARIOS,
    Inputs,
    Observed,
    cents,
    estimate,
    ineligibility,
    present,
    ratio,
)

SCHEMA = "recovery-plan.v1"
MAX_TARGETS = 1000
DEFAULT_TARGETS = 500
DEFAULT_MAX_DISCOUNT = Decimal("0.10")
DEFAULT_HORIZON = 4
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9 _.\-]{1,64}$")]
InstanceId = Annotated[str, Field(pattern=r"^S_[A-Z_]{1,40}:[A-Za-z0-9_|:. \-]{1,200}$")]
CustomerId = Annotated[str, Field(min_length=1, max_length=64)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"), max_digits=16, decimal_places=2)]
Share = Annotated[Decimal, Field(ge=0, le=Decimal("0.5"), max_digits=6, decimal_places=4)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Range(Strict):
    """Low, mid and high values of one assumption."""

    low: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=6)]
    mid: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=6)]
    high: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=6)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.low <= self.mid <= self.high:
            raise ValueError("assumption values must satisfy low ≤ mid ≤ high")
        return self


class Constraints(Strict):
    budget_amount: Money | None = None
    max_discount_pct: Share = DEFAULT_MAX_DISCOUNT
    horizon_weeks: Annotated[int, Field(ge=1, le=12)] = DEFAULT_HORIZON
    allowed_action_types: Annotated[list[ActionType], Field(max_length=5)] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_ACTIONS)
    )
    excluded_regions: Annotated[list[Name], Field(max_length=50)] = Field(default_factory=list)
    excluded_channels: Annotated[list[Name], Field(max_length=50)] = Field(default_factory=list)


class PlanEdits(Strict):
    """A person's changes. Only fields that are present change; `budget_amount: null`
    removes the budget. Unknown fields are refused (422)."""

    budget_amount: Money | None = None
    max_discount_pct: Share | None = None
    horizon_weeks: Annotated[int | None, Field(ge=1, le=12)] = None
    allowed_action_types: Annotated[list[ActionType] | None, Field(max_length=5)] = None
    excluded_regions: Annotated[list[Name] | None, Field(max_length=50)] = None
    excluded_channels: Annotated[list[Name] | None, Field(max_length=50)] = None
    selected_strategies: Annotated[list[InstanceId] | None, Field(max_length=20)] = None
    assumptions: Annotated[
        dict[InstanceId, dict[Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")], Range]] | None,
        Field(max_length=20),
    ] = None
    targets: Annotated[
        dict[InstanceId, Annotated[list[CustomerId], Field(max_length=MAX_TARGETS)]] | None,
        Field(max_length=20),
    ] = None
    discounts: Annotated[dict[InstanceId, Share] | None, Field(max_length=20)] = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("edits must change at least one field")
        return self


class EditError(ValueError):
    """An edit that does not fit the plan it targets (422 with these field errors)."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("invalid edits")
        self.errors = errors


@dataclass(frozen=True)
class Candidate:
    """One investigated incident and a validated, actionable mechanism in it."""

    incident_id: str
    incident_key: str
    incident_rank: int
    dimension: str
    hypothesis_id: str
    template: str
    status: str
    confidence: str | None
    impact: Decimal
    detection_id: str
    economics_id: str
    economics: dict[str, Any]  # the stored evidence result (facts and detail)


def content_hash(content: dict[str, Any]) -> str:
    return sha256_of(content)


def _constraints(c: Constraints) -> dict[str, Any]:
    return {
        "budget_amount": cents(c.budget_amount) if c.budget_amount is not None else None,
        "max_discount_pct": ratio(c.max_discount_pct),
        "horizon_weeks": c.horizon_weeks,
        "allowed_action_types": sorted(set(c.allowed_action_types)),
        "excluded_regions": sorted(set(c.excluded_regions)),
        "excluded_channels": sorted(set(c.excluded_channels)),
    }


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _observed(entry: dict[str, Any]) -> Observed:
    o = entry["observed_inputs"]
    return Observed(
        weeks=int(o["weeks"]),
        weekly_excess_loss=Decimal(o["weekly_excess_loss"]),
        weekly_net_revenue=Decimal(o["weekly_net_revenue"]),
        weekly_gross_sales=Decimal(o["weekly_gross_sales"]),
        excess_discount_rate=Decimal(o["excess_discount_rate"]),
        contribution_margin=_decimal(o["contribution_margin"]),
        weekly_contribution=_decimal(o["weekly_contribution"]),
        aov=_decimal(o["aov"]),
        revenue_per_customer_week=_decimal(o["revenue_per_customer_week"]),
    )


def _observed_inputs(o: Observed) -> dict[str, Any]:
    def s(v: Decimal | None) -> str | None:
        return str(v) if v is not None else None

    return {
        "weeks": o.weeks,
        "weekly_excess_loss": str(o.weekly_excess_loss),
        "weekly_net_revenue": str(o.weekly_net_revenue),
        "weekly_gross_sales": str(o.weekly_gross_sales),
        "excess_discount_rate": str(o.excess_discount_rate),
        "contribution_margin": s(o.contribution_margin),
        "weekly_contribution": s(o.weekly_contribution),
        "aov": s(o.aov),
        "revenue_per_customer_week": s(o.revenue_per_customer_week),
    }


def _default_assumptions(strategy: Strategy) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for a in strategy.assumptions:
        given = a.mid is not None
        out[a.key] = {
            "unit": a.unit,
            "label": a.label,
            "low": str(a.low) if a.low is not None else None,
            "mid": str(a.mid) if a.mid is not None else None,
            "high": str(a.high) if a.high is not None else None,
            "source": "default" if given else "missing",
        }
    return out


def _eligible(economics: dict[str, Any]) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = economics["detail"].get("eligible_customers", [])
    return rows


def _excluded(customer: dict[str, str | None], constraints: dict[str, Any]) -> bool:
    return (customer.get("region") in constraints["excluded_regions"]) or (
        customer.get("channel") in constraints["excluded_channels"]
    )


def _default_targets(economics: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    ids = [str(c["customer_id"]) for c in _eligible(economics) if not _excluded(c, constraints)]
    return ids[:DEFAULT_TARGETS]


def draft(
    *,
    investigation_id: str,
    investigation_version: int,
    currency: str,
    constraints: Constraints,
    candidates: list[Candidate],
    rationale: dict[str, list[dict[str, Any]]] | None = None,
    rationale_source: Literal["deterministic", "llm"] = "deterministic",
) -> dict[str, Any]:
    """Version 1: every catalog strategy for every candidate, estimated and ranked, with the
    best permitted one selected so the person starts from a concrete proposal."""
    c = _constraints(constraints)
    strategies: list[dict[str, Any]] = []
    evidence: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        evidence[cand.economics_id] = cand.economics
        facts: Facts = cand.economics["facts"]
        observed, cited = observe(cand.impact, cand.detection_id, cand.economics_id, facts)
        for strategy in strategies_for(cand.template, cand.dimension):
            instance = f"{strategy.code}:{cand.incident_key}"
            entry: dict[str, Any] = {
                "id": instance,
                "strategy": strategy.code,
                "title": strategy.title,
                "summary": strategy.summary,
                "incident": {
                    "id": cand.incident_id,
                    "key": cand.incident_key,
                    "rank": cand.incident_rank,
                },
                "hypothesis": {
                    "id": cand.hypothesis_id,
                    "template": cand.template,
                    "status": cand.status,
                    "confidence": cand.confidence,
                },
                "action_types": list(strategy.action_types),
                "risky_action_types": [
                    t for t in strategy.action_types if ACTION_TYPES[t]["risky"]
                ],
                "requirements": list(strategy.requirements),
                "tradeoffs": list(strategy.tradeoffs),
                "evidence": sorted({cand.detection_id, cand.economics_id}),
                "observed": [
                    {
                        "name": x.name,
                        "value": str(x.value) if x.value is not None else None,
                        "unit": x.unit,
                        "evidence_id": x.evidence_id,
                        "field": x.field,
                        "kind": "observed",
                    }
                    for x in cited
                ],
                "observed_inputs": _observed_inputs(observed),
                "eligibility": {"eligible": True, "reasons": []},
                "assumptions": _default_assumptions(strategy),
                "rationale": {
                    "source": rationale_source
                    if rationale and instance in rationale
                    else "deterministic",
                    "items": (rationale or {}).get(instance) or default_rationale(strategy),
                },
            }
            reasons = ineligibility(strategy, observed)
            entry["eligibility"] = {"eligible": not reasons, "reasons": reasons}
            if strategy.targets_customers:
                entry["targeting"] = {
                    "eligible_customers": len(_eligible(cand.economics)),
                    "source": "default",
                    "selected": _default_targets(cand.economics, c),
                }
            if strategy.uses_discount:
                entry["discount"] = ratio(min(DEFAULT_MAX_DISCOUNT, constraints.max_discount_pct))
            strategies.append(entry)
    content: dict[str, Any] = {
        "schema": SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "estimator_version": ESTIMATOR_VERSION,
        "investigation": {"id": investigation_id, "version": investigation_version},
        "currency": currency,
        "constraints": c,
        "strategies": strategies,
        "selected": [],
    }
    _recalculate(content, evidence)
    allowed = set(c["allowed_action_types"])
    first = next(
        (
            s["id"]
            for s in content["strategies"]
            if s["eligibility"]["eligible"] and set(s["action_types"]) <= allowed
        ),
        None,
    )
    content["selected"] = [first] if first else []
    _totals(content)
    return content


def default_rationale(strategy: Strategy) -> list[dict[str, Any]]:
    return [{"text": strategy.summary, "evidence_refs": []}]


def _assumption_values(entry: dict[str, Any]) -> dict[str, dict[str, Decimal]] | None:
    values: dict[str, dict[str, Decimal]] = {}
    for key, a in entry["assumptions"].items():
        if a["mid"] is None:
            return None
        values[key] = {s: Decimal(a[s]) for s in SCENARIOS}
    return values


def _recalculate(content: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> None:
    """Recompute every strategy's estimate and the ranking from the content's inputs."""
    c = content["constraints"]
    for entry in content["strategies"]:
        strategy = BY_CODE[entry["strategy"]]
        if "targeting" in entry and entry["targeting"]["source"] == "default":
            economics = evidence[economics_id(entry)]
            entry["targeting"]["selected"] = _default_targets(economics, c)
        warnings: list[str] = []
        values = _assumption_values(entry)
        if strategy.formula == "none":
            entry["estimate_status"], entry["estimate"] = "not_estimated", None
        elif not entry["eligibility"]["eligible"]:
            entry["estimate_status"], entry["estimate"] = "ineligible", None
        elif values is None:
            entry["estimate_status"], entry["estimate"] = "missing_assumptions", None
        else:
            result = estimate(
                strategy,
                _observed(entry),
                Inputs(
                    horizon_weeks=int(c["horizon_weeks"]),
                    assumptions=values,
                    targets=len(entry.get("targeting", {}).get("selected", [])),
                    discount=Decimal(entry.get("discount", "0")),
                ),
            )
            entry["estimate_status"], entry["estimate"] = "computed", present(result)
            warnings += result.warnings
        entry["warnings"] = sorted(set(warnings))
    allowed = set(c["allowed_action_types"])

    def rank(entry: dict[str, Any]) -> tuple[int, Decimal, int, int]:
        usable = entry["eligibility"]["eligible"] and set(entry["action_types"]) <= allowed
        net = (
            Decimal(entry["estimate"]["scenarios"]["mid"]["net"])
            if entry["estimate"] is not None
            else None
        )
        return (
            0 if usable else 1,
            -net if net is not None else Decimal("Infinity"),
            ORDER[entry["strategy"]],
            int(entry["incident"]["rank"]),
        )

    content["strategies"].sort(key=rank)
    for i, entry in enumerate(content["strategies"], start=1):
        entry["rank"] = i


def economics_id(entry: dict[str, Any]) -> str:
    found: str = next(o["evidence_id"] for o in entry["observed"] if o["name"] != "impact")
    return found


def _totals(content: dict[str, Any]) -> None:
    chosen = [s for s in content["strategies"] if s["id"] in set(content["selected"])]
    computed = [s["estimate"] for s in chosen if s["estimate"] is not None]
    if not computed:
        content["totals"] = None
        return
    with localcontext(analytics_context()):
        content["totals"] = {
            "kind": "estimate",
            "scenarios": {
                s: {
                    k: cents(sum((Decimal(e["scenarios"][s][k]) for e in computed), Decimal(0)))
                    for k in ("benefit", "cost", "net")
                }
                for s in SCENARIOS
            },
            "strategies_without_estimate": len(chosen) - len(computed),
        }


def revise(
    content: dict[str, Any], edits: PlanEdits, evidence: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """A new version's content: the previous one with the person's edits applied, then every
    affected estimate, the ranking and the totals recalculated. Raises EditError for edits
    that do not fit this plan."""
    new: dict[str, Any] = json.loads(canonical_json(content))  # deep copy, stored form
    by_id = {s["id"]: s for s in new["strategies"]}
    errors: list[dict[str, str]] = []
    fields = edits.model_fields_set
    c = new["constraints"]
    if "budget_amount" in fields:
        c["budget_amount"] = cents(edits.budget_amount) if edits.budget_amount is not None else None
    if edits.max_discount_pct is not None:
        c["max_discount_pct"] = ratio(edits.max_discount_pct)
    if edits.horizon_weeks is not None:
        c["horizon_weeks"] = edits.horizon_weeks
    if edits.allowed_action_types is not None:
        c["allowed_action_types"] = sorted(set(edits.allowed_action_types))
    if edits.excluded_regions is not None:
        c["excluded_regions"] = sorted(set(edits.excluded_regions))
    if edits.excluded_channels is not None:
        c["excluded_channels"] = sorted(set(edits.excluded_channels))
    for instance, values in (edits.assumptions or {}).items():
        entry = by_id.get(instance)
        if entry is None:
            errors.append(_err(f"assumptions.{instance}", "unknown_strategy"))
            continue
        strategy = BY_CODE[entry["strategy"]]
        specs = {a.key: a for a in strategy.assumptions}
        for key, r in values.items():
            spec = specs.get(key)
            if spec is None:
                errors.append(_err(f"assumptions.{instance}.{key}", "unknown_assumption"))
                continue
            if r.low < spec.minimum or r.high > spec.maximum:
                errors.append(_err(f"assumptions.{instance}.{key}", "out_of_range"))
                continue
            entry["assumptions"][key] |= {
                "low": str(r.low),
                "mid": str(r.mid),
                "high": str(r.high),
                "source": "human",
            }
    for instance, ids in (edits.targets or {}).items():
        entry = by_id.get(instance)
        if entry is None or "targeting" not in entry:
            errors.append(_err(f"targets.{instance}", "not_a_targeting_strategy"))
            continue
        entry["targeting"]["selected"] = sorted(set(ids))
        entry["targeting"]["source"] = "human"
    for instance, share in (edits.discounts or {}).items():
        entry = by_id.get(instance)
        if entry is None or "discount" not in entry:
            errors.append(_err(f"discounts.{instance}", "not_a_discount_strategy"))
            continue
        entry["discount"] = ratio(share)
    if edits.selected_strategies is not None:
        if any(i not in by_id for i in edits.selected_strategies):
            errors.append(_err("selected_strategies", "unknown_strategy"))
        new["selected"] = list(edits.selected_strategies)
    if errors:
        raise EditError(errors)
    _recalculate(new, evidence)
    order = [s["id"] for s in new["strategies"]]
    new["selected"] = sorted(set(new["selected"]), key=order.index)
    _totals(new)
    return new


def _err(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": code.replace("_", " ")}


def validate(content: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Every constraint, checked before a version can be presented for approval."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    c = content["constraints"]
    allowed = set(c["allowed_action_types"])
    by_id = {s["id"]: s for s in content["strategies"]}
    selected = content["selected"]
    if not selected:
        errors.append({"code": "no_strategy_selected"})
    total_high_cost = Decimal(0)
    templates: dict[str, int] = {}
    for instance in selected:
        entry = by_id.get(instance)
        if entry is None:
            errors.append({"code": "unknown_strategy", "strategy": instance})
            continue
        if not entry["eligibility"]["eligible"]:
            errors.append({"code": "strategy_ineligible", "strategy": instance})
        for action in entry["action_types"]:
            if action not in allowed:
                errors.append(
                    {"code": "action_type_not_allowed", "strategy": instance, "action": action}
                )
        missing = [k for k, a in entry["assumptions"].items() if a["mid"] is None]
        for key in missing:
            errors.append({"code": "missing_assumption", "strategy": instance, "field": key})
        if "discount" in entry and Decimal(entry["discount"]) > Decimal(c["max_discount_pct"]):
            errors.append({"code": "discount_exceeds_max", "strategy": instance})
        if "targeting" in entry:
            selected_ids = entry["targeting"]["selected"]
            eligible = {str(x["customer_id"]): x for x in _eligible(evidence[economics_id(entry)])}
            invalid = [i for i in selected_ids if i not in eligible or _excluded(eligible[i], c)]
            if invalid:
                errors.append(
                    {
                        "code": "invalid_customer_targeting",
                        "strategy": instance,
                        "count": str(len(invalid)),
                    }
                )
            if not selected_ids:
                errors.append({"code": "no_targets", "strategy": instance})
        if entry["estimate"] is not None:
            with localcontext(analytics_context()):
                total_high_cost += Decimal(entry["estimate"]["scenarios"]["high"]["cost"])
        for w in entry["warnings"]:
            warnings.append({"code": w, "strategy": instance})
        template = entry["hypothesis"]["template"]
        templates[template] = templates.get(template, 0) + 1
    if total_high_cost > 0 and c["budget_amount"] is None:
        errors.append({"code": "budget_missing"})
    elif c["budget_amount"] is not None and total_high_cost > Decimal(c["budget_amount"]):
        errors.append({"code": "budget_exceeded", "cost": cents(total_high_cost)})
    for template, count in sorted(templates.items()):
        if count > 1:
            warnings.append({"code": "overlapping_scope", "template": template})
    return {"valid": not errors, "errors": errors, "warnings": warnings}
