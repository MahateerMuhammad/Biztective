"""The structured investigation output: loaded from the database, never from graph state.

Observations (code-rendered source facts), hypotheses with their latest deterministic
evaluations, human context, clarifications, data gaps, recommendations and the narrative are
kept apart, and every number shown carries the evidence ID and field it was read from. A
person's free text is returned as plain data only: it never passes through the placeholder
renderer (AMD-15).

Any annotations represent heterogeneous JSON response fields and evidence payloads; facts
are verified by the critic and rendered through server-owned templates.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.catalog import ANSWER_NEEDED, CATALOG, ORDER, RECOMMENDATIONS
from app.investigation.critic import Citations
from app.investigation.facts import DIMENSION_ENTITY, entity_token, llm_fields
from app.investigation.render import METRIC_LABELS, Renderer, observations
from app.investigation.store import LinkedEvidence, linked_evidence
from app.models import (
    AnalysisRevision,
    Clarification,
    Dataset,
    Finding,
    HumanInput,
    Hypothesis,
    HypothesisEvaluation,
    Incident,
    Investigation,
    Product,
    User,
)


@dataclass
class HypothesisView:
    row: Hypothesis
    evaluation: HypothesisEvaluation | None

    @property
    def status(self) -> str:
        return self.evaluation.status if self.evaluation else "pending"

    @property
    def confidence(self) -> Decimal:
        return self.evaluation.confidence if self.evaluation else Decimal(0)

    @property
    def interpretation(self) -> str | None:
        return self.evaluation.interpretation if self.evaluation else None

    @property
    def explained(self) -> bool:
        """Supported, but the business confirmed the mechanism was a deliberate decision."""
        return self.status == "supported" and self.interpretation == "intended"


@dataclass
class Loaded:
    investigation: Investigation
    revision: AnalysisRevision
    currency: str
    incidents: list[Incident]
    evidence: dict[UUID, list[LinkedEvidence]]
    hypotheses: dict[UUID, list[HypothesisView]]
    clarifications: list[Clarification]
    findings: Finding | None
    product_names: dict[str, str] = field(default_factory=dict)
    human_inputs: list[HumanInput] = field(default_factory=list)
    user_names: dict[UUID, str] = field(default_factory=dict)

    @property
    def investigated(self) -> list[Incident]:
        return [i for i in self.incidents if i.id in self.hypotheses or i.id in self.evidence]

    def citations(self, incident_ids: Iterable[UUID] | None = None) -> Citations:
        wanted = set(incident_ids) if incident_ids is not None else None
        citations = Citations()
        for incident_id, items in self.evidence.items():
            if wanted is not None and incident_id not in wanted:
                continue
            for item in items:
                citations.add_evidence(str(item.id), item.facts)
        for incident in self.incidents:
            citations.add_name(incident.dimension_value)
            if incident.dimension in DIMENSION_ENTITY and incident.dimension_value is not None:
                citations.entity_tokens.add(
                    entity_token(DIMENSION_ENTITY[incident.dimension], incident.dimension_value)
                )
        for name in self.product_names.values():
            citations.add_name(name)
        return citations

    def renderer(self) -> Renderer:
        facts = {str(i.id): i.facts for items in self.evidence.values() for i in items}
        labels: dict[str, tuple[str, str]] = {}

        def label(kind: str, value: str) -> None:
            shown = (
                f"{self.product_names[value]} ({value})"
                if kind == "product" and value in self.product_names
                else value
            )
            labels[entity_token(kind, value)] = (kind, shown)

        for items in self.evidence.values():
            for item in items:
                for f in item.facts.values():
                    if f.get("unit") == "entity" and isinstance(f.get("value"), str):
                        label(f["kind"], f["value"])
        for incident in self.incidents:
            if incident.dimension in DIMENSION_ENTITY and incident.dimension_value is not None:
                label(DIMENSION_ENTITY[incident.dimension], incident.dimension_value)
        return Renderer(facts, labels, self.currency)


async def load(session: AsyncSession, investigation: Investigation) -> Loaded:
    revision = await session.get_one(AnalysisRevision, investigation.revision_id)
    currency = await session.scalar(
        select(Dataset.currency).where(Dataset.id == investigation.dataset_id)
    )
    incidents = list(
        await session.scalars(
            select(Incident).where(Incident.revision_id == revision.id).order_by(Incident.rank)
        )
    )
    evidence = await linked_evidence(session, investigation.id)
    hypotheses: dict[UUID, list[HypothesisView]] = {}
    for hypothesis, evaluation in await latest_evaluations(session, investigation.id):
        hypotheses.setdefault(hypothesis.incident_id, []).append(
            HypothesisView(hypothesis, evaluation)
        )
    clarifications = list(
        await session.scalars(
            select(Clarification)
            .where(Clarification.investigation_id == investigation.id)
            .order_by(Clarification.created_at, Clarification.id)
        )
    )
    findings = await session.scalar(
        select(Finding)
        .where(Finding.investigation_id == investigation.id)
        .order_by(Finding.round.desc())
        .limit(1)
    )
    human_inputs = list(
        await session.scalars(
            select(HumanInput)
            .where(HumanInput.investigation_id == investigation.id)
            .order_by(HumanInput.answered_at, HumanInput.id)
        )
    )
    user_names: dict[UUID, str] = {}
    if human_inputs:
        user_names = {
            u.id: u.display_name
            for u in await session.scalars(
                select(User).where(User.id.in_({h.answered_by for h in human_inputs}))
            )
        }
    product_ids = {
        f["value"]
        for items in evidence.values()
        for item in items
        for f in item.facts.values()
        if f.get("unit") == "entity" and f.get("kind") == "product"
    } | {i.dimension_value for i in incidents if i.dimension == "product" and i.dimension_value}
    names = {}
    if product_ids:
        names = {
            p.product_id: p.product_name
            for p in await session.scalars(
                select(Product).where(
                    Product.dataset_id == investigation.dataset_id,
                    Product.product_id.in_(product_ids),
                )
            )
        }
    return Loaded(
        investigation,
        revision,
        currency or "",
        incidents,
        evidence,
        hypotheses,
        clarifications,
        findings,
        names,
        human_inputs,
        user_names,
    )


async def latest_evaluations(
    session: AsyncSession, investigation_id: UUID, incident_id: UUID | None = None
) -> list[tuple[Hypothesis, HypothesisEvaluation | None]]:
    """Each hypothesis with its current (latest) evaluation; earlier ones stay as history."""
    query = (
        select(Hypothesis, HypothesisEvaluation)
        .outerjoin(HypothesisEvaluation, HypothesisEvaluation.hypothesis_id == Hypothesis.id)
        .where(Hypothesis.investigation_id == investigation_id)
        .order_by(
            Hypothesis.priority,
            Hypothesis.created_at,
            Hypothesis.id,
            HypothesisEvaluation.evaluated_at,
            HypothesisEvaluation.id,
        )
    )
    if incident_id is not None:
        query = query.where(Hypothesis.incident_id == incident_id)
    latest: dict[UUID, tuple[Hypothesis, HypothesisEvaluation | None]] = {}
    for hypothesis, evaluation in (await session.execute(query)).all():
        latest[hypothesis.id] = (hypothesis, evaluation)  # later rows overwrite earlier ones
    return list(latest.values())


def ranked(views: list[HypothesisView]) -> list[HypothesisView]:
    """Supported problems first, then mechanisms explained by business context, then by
    status, confidence and catalog order (presentation and root cause)."""
    order = {"supported": 0, "needs_human_context": 2, "inconclusive": 3, "refuted": 4}
    return sorted(
        views,
        key=lambda v: (
            1 if v.explained else order.get(v.status, 5),
            -v.confidence,
            ORDER.get(v.row.template, 99),
        ),
    )


def root_cause(views: list[HypothesisView]) -> HypothesisView | None:
    """The highest-ranked supported hypothesis that is a problem, not a deliberate decision."""
    supported = [v for v in ranked(views) if v.status == "supported" and not v.explained]
    return supported[0] if supported else None


def recommendations(views: list[HypothesisView], open_question: bool) -> list[dict[str, Any]]:
    """Recommended next checks (catalog text, never executable actions)."""
    out: list[dict[str, Any]] = []
    cause = root_cause(views)
    if cause is not None:
        out.append(
            {
                "template": cause.row.template,
                "kind": "fix",
                "text": RECOMMENDATIONS[cause.row.template][0],
            }
        )
    for v in ranked(views):
        if v.explained:
            out.append(
                {
                    "template": v.row.template,
                    "kind": "monitor",
                    "text": RECOMMENDATIONS[v.row.template][1],
                }
            )
    if cause is None and open_question:
        out.append({"template": None, "kind": "answer", "text": ANSWER_NEEDED})
    return out


def human_context(loaded: Loaded, incident: Incident) -> list[dict[str, Any]]:
    """Answers applied to this incident, attributed; free text is plain data (never rendered)."""
    applied = {
        str(item.result["detail"]["human_input_id"])
        for item in loaded.evidence.get(incident.id, [])
        if item.kind == "human_context"
    }
    clarifications = {c.id: c for c in loaded.clarifications}
    out = []
    for h in loaded.human_inputs:
        if str(h.id) not in applied:
            continue
        clarification = clarifications.get(h.clarification_id)
        spec = clarification.question_spec if clarification else {}
        label = next((o["label"] for o in spec.get("options", []) if o["id"] == h.option_id), None)
        out.append(
            {
                "human_input_id": str(h.id),
                "clarification_id": str(h.clarification_id),
                "criterion": h.criterion_key,
                "question": spec.get("question"),
                "option_id": h.option_id,
                "option_label": label,
                "effect": h.effect,
                "free_text": h.free_text,
                "answered_by": {
                    "id": str(h.answered_by),
                    "display_name": loaded.user_names.get(h.answered_by),
                },
                "answered_at": h.answered_at.isoformat(),
            }
        )
    return out


def data_gaps(views: list[HypothesisView]) -> list[dict[str, Any]]:
    gaps = []
    for v in views:
        if v.evaluation is None:
            continue
        for c in v.evaluation.criteria:
            if c["source"] == "data" and c["satisfied"] is None:
                gaps.append(
                    {
                        "template": v.row.template,
                        "criterion": c["key"],
                        "reason": "evidence_missing",
                    }
                )
    return gaps


def evidence_view(item: LinkedEvidence) -> dict[str, Any]:
    fields, units = llm_fields(item.facts)
    return {"id": str(item.id), "tool": item.kind, "fields": fields, "units": units}


def incident_view(incident: Incident) -> dict[str, Any]:
    entity = (
        entity_token(DIMENSION_ENTITY[incident.dimension], incident.dimension_value)
        if incident.dimension in DIMENSION_ENTITY and incident.dimension_value is not None
        else None
    )
    return {
        "id": str(incident.id),
        "metric": incident.metric,
        "dimension": incident.dimension,
        "entity": entity,
        "severity": incident.severity,
        "rank": incident.rank,
    }


def synthesis_context(loaded: Loaded) -> dict[str, Any]:
    incidents = []
    for incident in loaded.investigated:
        views = ranked(loaded.hypotheses.get(incident.id, []))
        incidents.append(
            {
                **incident_view(incident),
                "evidence": [evidence_view(e) for e in loaded.evidence.get(incident.id, [])],
                "hypotheses": [
                    {
                        "id": str(v.row.id),
                        "template": v.row.template,
                        "claim": CATALOG[v.row.template].claim,
                        "status": v.status,
                        "confidence": str(v.confidence),
                        "interpretation": v.interpretation,
                        "conflicts": list(v.evaluation.conflicts) if v.evaluation else [],
                        "criteria": [
                            {
                                "key": c["key"],
                                "description": c["description"],
                                "source": c["source"],
                                "satisfied": c["satisfied"],
                                "evidence_refs": sorted({b["evidence_id"] for b in c["basis"]}),
                            }
                            for c in (v.evaluation.criteria if v.evaluation else [])
                        ],
                    }
                    for v in views
                ],
                # Catalog codes only: a person's free text never reaches the model.
                "human_context": [
                    {"criterion": h["criterion"], "option": h["option_id"], "effect": h["effect"]}
                    for h in human_context(loaded, incident)
                ],
                "open_questions": [
                    c.question_spec["question"]
                    for c in loaded.clarifications
                    if c.incident_id == incident.id and c.status in ("open", "awaiting")
                ],
                "data_gaps": data_gaps(views),
            }
        )
    return {
        "currency": loaded.currency,
        "incidents": incidents,
        "restrictions": [r["code"] for r in loaded.revision.restrictions],
    }


def fallback_synthesis(loaded: Loaded) -> dict[str, Any]:
    """Deterministic narrative used without an LLM (template text, code-generated)."""
    causes, alternatives, caveats = [], [], []
    supported = needs_context = explained = 0
    for incident in loaded.investigated:
        views = ranked(loaded.hypotheses.get(incident.id, []))
        cause = root_cause(views)
        if cause is not None and cause.evaluation is not None:
            supported += 1
            cited = [
                b
                for c in cause.evaluation.criteria
                if c["satisfied"] and c["polarity"] == "support"
                for b in c["basis"][:1]
            ]
            refs = sorted({b["evidence_id"] for b in cited})
            met = "; ".join(
                f"{c['description']} ({_placeholder(b)})"
                for c in cause.evaluation.criteria
                if c["satisfied"] and c["polarity"] == "support"
                for b in c["basis"][:1]
            )
            causes.append(
                {
                    "hypothesis_id": str(cause.row.id),
                    "statement": f"{CATALOG[cause.row.template].claim} Criteria met: {met}",
                    "evidence_refs": refs,
                }
            )
        elif any(v.explained for v in views):
            explained += 1
        elif any(v.status == "needs_human_context" for v in views):
            needs_context += 1
        for v in views:
            if v is cause or v.evaluation is None:
                continue
            refs = sorted({b["evidence_id"] for c in v.evaluation.criteria for b in c["basis"]})
            alternatives.append(
                {
                    "hypothesis_id": str(v.row.id),
                    "why_not": _why_not(v),
                    "evidence_refs": refs[:6],
                }
            )
    for restriction in loaded.revision.restrictions:
        if restriction["code"] in ("immature_returns", "missing_costs", "low_sample"):
            caveats.append(restriction["message"])
    if any(data_gaps(loaded.hypotheses.get(i.id, [])) for i in loaded.investigated):
        caveats.append("Some criteria could not be checked because evidence was unavailable.")
    total = len(loaded.investigated)
    summary = (
        f"Investigated {total} incident{'s' if total != 1 else ''}: {supported} with an "
        f"evidence-backed explanation, {needs_context} that need context only the business "
        "can provide."
    )
    if explained:
        summary += (
            f" {explained} explained by a deliberate business decision you confirmed, with no "
            "other supported cause."
        )
    return {
        "summary": summary,
        "root_causes": causes,
        "alternatives_considered": alternatives,
        "caveats": caveats[:5],
    }


def _placeholder(basis: dict[str, Any]) -> str:
    return "{{ev:" + str(basis["evidence_id"]) + "." + str(basis["field"]) + "}}"


def _why_not(view: HypothesisView) -> str:
    criteria = view.evaluation.criteria if view.evaluation else []
    if view.status == "refuted":
        refute = next((c for c in criteria if c["polarity"] == "refute" and c["satisfied"]), None)
        return f"Refuted: {refute['description']}" if refute else "Refuted: no criterion was met."
    if view.status == "needs_human_context":
        question = next((c for c in criteria if c["source"] == "human"), None)
        return f"Needs your context: {question['description']}" if question else "Needs context."
    if view.explained:
        return (
            "Confirmed as a deliberate business decision by your answer, so it is not "
            "treated as a problem to fix; the measured impact is unchanged."
        )
    if view.status == "supported":
        return "Supported, but with lower confidence than the leading explanation."
    return "Inconclusive: the evidence was mixed or incomplete."


def report(loaded: Loaded) -> dict[str, Any]:
    """The API's structured investigation output."""
    renderer = loaded.renderer()
    incidents = []
    for incident in loaded.incidents:
        items = loaded.evidence.get(incident.id, [])
        views = ranked(loaded.hypotheses.get(incident.id, []))
        cause = root_cause(views)
        detection = [(str(e.id), e.kind, e.facts) for e in items if e.requested_by == "detection"]
        incidents.append(
            {
                "id": str(incident.id),
                "rank": incident.rank,
                "key": incident.key,
                "metric": incident.metric,
                "metric_label": METRIC_LABELS.get(incident.metric, incident.metric),
                "dimension": incident.dimension,
                "value": incident.dimension_value,
                "severity": incident.severity,
                "investigated": incident.id in loaded.hypotheses,
                "observations": [
                    renderer.segments(text)
                    for text in observations(
                        incident.metric, incident.dimension, incident.dimension_value, detection
                    )
                ],
                "evidence": [
                    {"id": str(e.id), "kind": e.kind, "requested_by": e.requested_by} for e in items
                ],
                "root_cause_hypothesis_id": str(cause.row.id) if cause else None,
                "explained_by_context": [str(v.row.id) for v in views if v.explained],
                "recommendations": recommendations(
                    views,
                    any(
                        c.incident_id == incident.id and c.status in ("open", "awaiting")
                        for c in loaded.clarifications
                    ),
                ),
                "human_context": human_context(loaded, incident),
                "hypotheses": [
                    {
                        "id": str(v.row.id),
                        "template": v.row.template,
                        "claim": CATALOG[v.row.template].claim,
                        "priority": v.row.priority,
                        "source": v.row.source,
                        "rationale": [renderer.segments(r["text"]) for r in v.row.rationale],
                        "status": v.status,
                        "confidence": str(v.confidence),
                        "data_confidence": str(v.evaluation.data_confidence)
                        if v.evaluation and v.evaluation.data_confidence is not None
                        else None,
                        "human_confidence": str(v.evaluation.human_confidence)
                        if v.evaluation and v.evaluation.human_confidence is not None
                        else None,
                        "interpretation": v.interpretation,
                        "conflicts": list(v.evaluation.conflicts) if v.evaluation else [],
                        "criteria": [
                            {
                                "key": c["key"],
                                "description": c["description"],
                                "polarity": c["polarity"],
                                "source": c["source"],
                                "satisfied": c["satisfied"],
                                "basis": renderer.basis(c["basis"]),
                            }
                            for c in (v.evaluation.criteria if v.evaluation else [])
                        ],
                    }
                    for v in views
                ],
                "context_requests": [
                    {
                        "id": str(c.id),
                        "trigger": c.trigger,
                        "criterion": c.criterion_key,
                        "question": c.question_spec["question"],
                        "type": c.question_spec["type"],
                        "options": [
                            {"id": o["id"], "label": o["label"]} for o in c.question_spec["options"]
                        ],
                        "status": c.status,
                    }
                    for c in loaded.clarifications
                    if c.incident_id == incident.id
                ],
                "data_gaps": data_gaps(views),
            }
        )
    findings = None
    if loaded.findings is not None:
        f = loaded.findings
        findings = {
            "id": str(f.id),
            "version": f.round,
            "source": f.source,
            "label": "AI interpretation; source facts are listed in observations."
            if f.source == "llm"
            else "Evidence-based summary.",
            "rendered": f.rendered,
            "created_at": f.created_at.isoformat(),
        }
    return {"incidents": incidents, "findings": findings}


def render_findings(loaded: Loaded, content: dict[str, Any]) -> dict[str, Any]:
    """The narrative (LLM or template) plus deterministic sections: mechanisms explained by
    business context, answers that contradict the measured data, and the recommended next
    checks per incident."""
    renderer = loaded.renderer()
    explained = []
    conflicts = []
    recommended = []
    for incident in loaded.investigated:
        views = loaded.hypotheses.get(incident.id, [])
        for v in ranked(views):
            if v.explained:
                explained.append(
                    {
                        "hypothesis_id": str(v.row.id),
                        "incident_id": str(incident.id),
                        "statement": [{"text": CATALOG[v.row.template].claim}],
                    }
                )
            for key in v.evaluation.conflicts if v.evaluation else []:
                criterion = next(c for c in CATALOG[v.row.template].criteria if c.key == key)
                conflicts.append(
                    {
                        "hypothesis_id": str(v.row.id),
                        "incident_id": str(incident.id),
                        "statement": [
                            {
                                "text": f"The answer to “{criterion.description}” conflicts "
                                "with the measured data, so the measurements were kept and "
                                "the verdict stands: " + CATALOG[v.row.template].claim
                            }
                        ],
                    }
                )
        open_question = any(
            c.incident_id == incident.id and c.status in ("open", "awaiting")
            for c in loaded.clarifications
        )
        for r in recommendations(views, open_question):
            recommended.append({"incident_id": str(incident.id), **r})
    return {
        "explained_by_context": explained,
        "conflicts": conflicts,
        "recommendations": recommended,
        "summary": renderer.segments(content["summary"]),
        "root_causes": [
            {
                "hypothesis_id": c["hypothesis_id"],
                "statement": renderer.segments(c["statement"]),
                "evidence_refs": c["evidence_refs"],
            }
            for c in content["root_causes"]
        ],
        "alternatives_considered": [
            {
                "hypothesis_id": a["hypothesis_id"],
                "why_not": renderer.segments(a["why_not"]),
                "evidence_refs": a["evidence_refs"],
            }
            for a in content["alternatives_considered"]
        ],
        "caveats": [renderer.segments(c) for c in content["caveats"]],
    }
