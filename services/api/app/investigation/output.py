"""The structured investigation output: loaded from the database, never from graph state.

Observations (code-rendered source facts), hypotheses with their deterministic evaluations,
open context requests, data gaps and the narrative are kept apart, and every number shown
carries the evidence ID and field it was read from.

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

from app.investigation.catalog import CATALOG, ORDER
from app.investigation.critic import Citations
from app.investigation.facts import DIMENSION_ENTITY, entity_token, llm_fields
from app.investigation.render import METRIC_LABELS, Renderer, observations
from app.investigation.store import LinkedEvidence, linked_evidence
from app.models import (
    AnalysisRevision,
    Clarification,
    Dataset,
    Finding,
    Hypothesis,
    HypothesisEvaluation,
    Incident,
    Investigation,
    Product,
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
    rows = (
        await session.execute(
            select(Hypothesis, HypothesisEvaluation)
            .outerjoin(HypothesisEvaluation, HypothesisEvaluation.hypothesis_id == Hypothesis.id)
            .where(Hypothesis.investigation_id == investigation.id)
            .order_by(Hypothesis.priority, Hypothesis.created_at)
        )
    ).all()
    hypotheses: dict[UUID, list[HypothesisView]] = {}
    for hypothesis, evaluation in rows:
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
        select(Finding).where(Finding.investigation_id == investigation.id, Finding.round == 1)
    )
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
    )


def ranked(views: list[HypothesisView]) -> list[HypothesisView]:
    """Supported first, then by confidence, then catalog order (presentation and root cause)."""
    order = {"supported": 0, "needs_human_context": 1, "inconclusive": 2, "refuted": 3}
    return sorted(
        views,
        key=lambda v: (order.get(v.status, 4), -v.confidence, ORDER.get(v.row.template, 99)),
    )


def root_cause(views: list[HypothesisView]) -> HypothesisView | None:
    supported = [v for v in ranked(views) if v.status == "supported"]
    return supported[0] if supported else None


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
                "open_questions": [
                    c.question_spec["question"]
                    for c in loaded.clarifications
                    if c.incident_id == incident.id
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
    supported = needs_context = 0
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
                        "options": c.question_spec["options"],
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
            "source": f.source,
            "label": "AI interpretation; source facts are listed in observations."
            if f.source == "llm"
            else "Evidence-based summary.",
            "rendered": f.rendered,
            "created_at": f.created_at.isoformat(),
        }
    return {"incidents": incidents, "findings": findings}


def render_findings(loaded: Loaded, content: dict[str, Any]) -> dict[str, Any]:
    renderer = loaded.renderer()
    return {
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
