"""Deterministic hypothesis verification (ARCHITECTURE §6.4 as amended by AMD-12 and AMD-15).

# Any is confined to JSON/SQL driver boundaries; tool arguments and rules are typed.

Inputs are the template, the facts of its required evidence and the structured human answers
to its criteria; the output is a status, confidences, the interpretation and the per-criterion
outcome with the exact facts or answers each rule read. No LLM output is an input here, and a
person's free text never is: only the catalog effect of the option they chose.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from app.investigation.catalog import Criterion, Evidence, Lookup, Template

Status = Literal["supported", "refuted", "inconclusive", "needs_human_context"]
Interpretation = Literal["intended", "unintended"]
AnswerEffect = Literal["confirm", "deny", "intended", "unsure", "unstructured"]
SUPPORTED_AT = Decimal("0.7")
REFUTED_BELOW = Decimal("0.3")
HUMAN_FACTOR = Decimal("0.5")
CONFIDENCE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class Answer:
    """A person's structured answer to a human criterion, as stored human-context evidence."""

    effect: AnswerEffect
    option_id: str | None
    evidence_id: str

    @property
    def satisfied(self) -> bool | None:
        if self.effect in ("confirm", "intended"):
            return True
        return False if self.effect == "deny" else None


@dataclass(frozen=True)
class CriterionOutcome:
    key: str
    polarity: str
    source: str
    weight: Decimal
    description: str
    satisfied: bool | None  # None: unanswered or unsure (human) or evidence missing (data)
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
    interpretation: Interpretation | None = None
    conflicts: tuple[str, ...] = ()

    @property
    def unknown_data(self) -> tuple[CriterionOutcome, ...]:
        return tuple(c for c in self.criteria if c.source == "data" and c.satisfied is None)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CONFIDENCE_PLACES, rounding=ROUND_HALF_EVEN)


def _outcome(
    criterion: Criterion, evidence: Mapping[str, Evidence | None], answer: Answer | None
) -> CriterionOutcome:
    if criterion.source == "human":
        satisfied = answer.satisfied if answer is not None else None
        basis: tuple[dict[str, Any], ...] = (
            (
                {
                    "evidence_id": answer.evidence_id,
                    "field": "answer.option",
                    "value": answer.option_id,
                    "unit": "code",
                },
            )
            if answer is not None
            else ()
        )
    else:
        lookup = Lookup(evidence)
        satisfied = criterion.rule(lookup) if criterion.rule is not None else None
        basis = tuple(
            {"evidence_id": b.evidence_id, "field": b.field, "value": b.value, "unit": b.unit}
            for b in lookup.basis
        )
    return CriterionOutcome(
        key=criterion.key,
        polarity=criterion.polarity,
        source=criterion.source,
        weight=criterion.weight,
        description=criterion.description,
        satisfied=satisfied,
        basis=basis,
        question=criterion.question.as_json() if criterion.question else None,
    )


def evaluate(
    template: Template,
    evidence: Mapping[str, Evidence | None],
    answers: Mapping[str, Answer] | None = None,
) -> Evaluation:
    answers = answers or {}
    outcomes = [_outcome(c, evidence, answers.get(c.key)) for c in template.criteria]
    missing = tuple(r.key for r in template.requirements if evidence.get(r.key) is None)

    support_data = [o for o in outcomes if o.polarity == "support" and o.source == "data"]
    total = sum((o.weight for o in support_data), Decimal(0))
    data_confidence = (
        _q(sum((o.weight for o in support_data if o.satisfied), Decimal(0)) / total)
        if total > 0
        else None
    )
    human = [o for o in outcomes if o.source == "human"]
    human_total = sum((o.weight for o in human), Decimal(0))
    human_satisfied = sum((o.weight for o in human if o.satisfied), Decimal(0))
    answered = any(o.satisfied is not None for o in human)
    human_confidence = _q(human_satisfied / human_total) if answered and human_total else None
    pending_human = any(o.satisfied is None for o in human)
    unknown = any(o.satisfied is None for o in support_data)
    refuted_by_data = any(o.polarity == "refute" and o.satisfied for o in outcomes)

    status: Status
    if template.human_only:
        confidence = human_confidence if human_confidence is not None else Decimal(0)
        if refuted_by_data:
            status = "refuted"
        elif human_confidence is None:
            status = "needs_human_context"
        elif confidence >= SUPPORTED_AT:
            status = "supported"
        elif confidence < REFUTED_BELOW:
            status = "refuted"
        else:
            status = "needs_human_context"
    else:
        base = data_confidence if data_confidence is not None else Decimal(0)
        confidence = _q(min(Decimal(1), base + HUMAN_FACTOR * human_satisfied))
        if refuted_by_data:
            status = "refuted"
        elif pending_human and REFUTED_BELOW <= confidence < SUPPORTED_AT:
            status = "needs_human_context"
        elif confidence >= SUPPORTED_AT:
            status = "supported"
        elif confidence < REFUTED_BELOW and not missing and not unknown:
            status = "refuted"
        else:
            status = "inconclusive"

    interpretation: Interpretation | None = None
    conflicts: list[str] = []
    for criterion, outcome in zip(template.criteria, outcomes, strict=True):
        answer = answers.get(criterion.key) if criterion.source == "human" else None
        if answer is None:
            continue
        if answer.effect == "intended":
            interpretation = "intended"
        elif (
            answer.effect == "confirm"
            and interpretation is None
            and criterion.question is not None
            and criterion.question.asks_intent
        ):
            interpretation = "unintended"
        # A statement contradicting the measured data is recorded, never applied to the data.
        strong_data = data_confidence is not None and data_confidence >= SUPPORTED_AT
        if (outcome.satisfied is False and strong_data) or (outcome.satisfied and refuted_by_data):
            conflicts.append(criterion.key)
    return Evaluation(
        status=status,
        confidence=confidence,
        data_confidence=data_confidence,
        human_confidence=human_confidence,
        criteria=tuple(outcomes),
        missing_evidence=missing,
        interpretation=interpretation,
        conflicts=tuple(conflicts),
    )
