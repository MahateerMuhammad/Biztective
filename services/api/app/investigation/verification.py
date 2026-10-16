"""Deterministic hypothesis verification (ARCHITECTURE §6.4 as amended by AMD-12).

# Any is confined to JSON/SQL driver boundaries; tool arguments and rules are typed.

Inputs are the template and the facts of its required evidence; the output is a status,
confidences and the per-criterion outcome with the exact facts each rule read. No LLM output
is an input here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from app.investigation.catalog import Evidence, Lookup, Template

Status = Literal["supported", "refuted", "inconclusive", "needs_human_context"]
SUPPORTED_AT = Decimal("0.7")
REFUTED_BELOW = Decimal("0.3")
CONFIDENCE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class CriterionOutcome:
    key: str
    polarity: str
    source: str
    weight: Decimal
    description: str
    satisfied: bool | None  # None: unanswered (human) or evidence missing (data)
    basis: tuple[dict[str, Any], ...]
    question: dict[str, Any] | None

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "polarity": self.polarity,
            "source": self.source,
            "weight": str(self.weight),
            "description": self.description,
            "satisfied": self.satisfied,
            "basis": list(self.basis),
        }


@dataclass(frozen=True)
class Evaluation:
    status: Status
    confidence: Decimal
    data_confidence: Decimal | None
    human_confidence: Decimal | None
    criteria: tuple[CriterionOutcome, ...]
    missing_evidence: tuple[str, ...]

    @property
    def unknown_data(self) -> tuple[CriterionOutcome, ...]:
        return tuple(c for c in self.criteria if c.source == "data" and c.satisfied is None)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CONFIDENCE_PLACES, rounding=ROUND_HALF_EVEN)


def evaluate(template: Template, evidence: Mapping[str, Evidence | None]) -> Evaluation:
    outcomes = []
    for criterion in template.criteria:
        lookup = Lookup(evidence)
        satisfied = criterion.rule(lookup) if criterion.rule is not None else None
        outcomes.append(
            CriterionOutcome(
                key=criterion.key,
                polarity=criterion.polarity,
                source=criterion.source,
                weight=criterion.weight,
                description=criterion.description,
                satisfied=satisfied,
                basis=tuple(
                    {
                        "evidence_id": b.evidence_id,
                        "field": b.field,
                        "value": b.value,
                        "unit": b.unit,
                    }
                    for b in lookup.basis
                ),
                question=criterion.question.as_json() if criterion.question else None,
            )
        )
    missing = tuple(r.key for r in template.requirements if evidence.get(r.key) is None)

    support_data = [o for o in outcomes if o.polarity == "support" and o.source == "data"]
    total = sum((o.weight for o in support_data), Decimal(0))
    data_confidence = (
        _q(sum((o.weight for o in support_data if o.satisfied), Decimal(0)) / total)
        if total > 0
        else None
    )
    human = [o for o in outcomes if o.source == "human"]
    confidence = data_confidence if data_confidence is not None else Decimal(0)
    unknown = any(o.satisfied is None for o in support_data)

    status: Status
    if any(o.polarity == "refute" and o.satisfied for o in outcomes):
        status = "refuted"
    elif template.human_only:
        status = "needs_human_context"
    elif human and REFUTED_BELOW <= confidence < SUPPORTED_AT:
        status = "needs_human_context"  # every human criterion is unanswered in Phase 5
    elif confidence >= SUPPORTED_AT:
        status = "supported"
    elif confidence < REFUTED_BELOW and not missing and not unknown:
        status = "refuted"
    else:
        status = "inconclusive"
    return Evaluation(
        status=status,
        confidence=confidence,
        data_confidence=data_confidence,
        human_confidence=None,
        criteria=tuple(outcomes),
        missing_evidence=missing,
    )
