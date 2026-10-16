"""LLM task contracts for investigations (DATA_CONTRACTS §9 as amended by AMD-12).

Each task has a strict output model (JSON-schema structured output), a semantic validator
(referential checks and the Evidence Critic's text rules) and a deterministic fallback in the
graph. The LLM orders, selects and phrases; it never computes, decides a status or executes.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.investigation.catalog import TemplateName
from app.investigation.critic import EVIDENCE_PLACEHOLDER, Citations, check_refs, check_text
from app.investigation.tools import (
    CustomerExposureParams,
    DataQualityParams,
    EvidenceLookupParams,
    MetricBreakdownParams,
    PeriodComparisonParams,
    ReturnInvestigationParams,
    SegmentAnalysisParams,
)
from app.llm.gateway import Task


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


Text300 = Annotated[str, Field(min_length=1, max_length=300)]
EvidenceRefs = Annotated[list[str], Field(max_length=6)]


# ------------------------------------------------------------------ prioritize_hypotheses


def _check_listed_citations(text: str, refs: list[str], where: str) -> list[str]:
    cited = {match["id"] for match in EVIDENCE_PLACEHOLDER.finditer(text)}
    if cited - set(refs):
        return [f"unlisted_citation: {where} cites evidence absent from evidence_refs"]
    return []


class Rationale(Strict):
    text: Text300
    evidence_refs: EvidenceRefs


class RankedHypothesis(Strict):
    template: TemplateName
    priority: Annotated[int, Field(ge=1, le=9)]
    rationale: Annotated[list[Rationale], Field(max_length=3)]


class PrioritizeOutput(Strict):
    ranking: Annotated[list[RankedHypothesis], Field(min_length=1, max_length=8)]


PRIORITIZE = Task("prioritize_hypotheses", 1, PrioritizeOutput)


def validate_prioritize(
    output: PrioritizeOutput, candidates: set[str], citations: Citations
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, ranked in enumerate(output.ranking):
        if ranked.template not in candidates:
            errors.append(f"unknown_template: ranking[{i}] is not a candidate for this incident")
        if ranked.template in seen:
            errors.append(f"duplicate_template: ranking[{i}] repeats a template")
        seen.add(ranked.template)
        for j, r in enumerate(ranked.rationale):
            where = f"ranking[{i}].rationale[{j}]"
            errors += check_text(r.text, citations, where)
            errors += check_refs(r.evidence_refs, citations, where)
            errors += _check_listed_citations(r.text, r.evidence_refs, where)
    return errors


# --------------------------------------------------------------------------- select_tools

Purpose = Annotated[str, Field(min_length=1, max_length=200)]


class MetricBreakdownCall(Strict):
    tool: Literal["metric_breakdown"]
    arguments: MetricBreakdownParams
    hypothesis: TemplateName
    purpose: Purpose


class PeriodComparisonCall(Strict):
    tool: Literal["period_comparison"]
    arguments: PeriodComparisonParams
    hypothesis: TemplateName
    purpose: Purpose


class ReturnInvestigationCall(Strict):
    tool: Literal["return_investigation"]
    arguments: ReturnInvestigationParams
    hypothesis: TemplateName
    purpose: Purpose


class SegmentAnalysisCall(Strict):
    tool: Literal["segment_analysis"]
    arguments: SegmentAnalysisParams
    hypothesis: TemplateName
    purpose: Purpose


class CustomerExposureCall(Strict):
    tool: Literal["customer_exposure"]
    arguments: CustomerExposureParams
    hypothesis: TemplateName
    purpose: Purpose


class EvidenceLookupCall(Strict):
    tool: Literal["evidence_lookup"]
    arguments: EvidenceLookupParams
    hypothesis: TemplateName
    purpose: Purpose


class DataQualityCall(Strict):
    tool: Literal["data_quality_check"]
    arguments: DataQualityParams
    hypothesis: TemplateName
    purpose: Purpose


ToolCall = Annotated[
    MetricBreakdownCall
    | PeriodComparisonCall
    | ReturnInvestigationCall
    | SegmentAnalysisCall
    | CustomerExposureCall
    | EvidenceLookupCall
    | DataQualityCall,
    Field(discriminator="tool"),
]


class SelectToolsOutput(Strict):
    calls: Annotated[list[ToolCall], Field(max_length=6)]
    done: bool


SELECT_TOOLS = Task("select_tools", 1, SelectToolsOutput)


def validate_select_tools(
    output: SelectToolsOutput, hypotheses: set[str], citations: Citations
) -> list[str]:
    errors: list[str] = []
    for i, call in enumerate(output.calls):
        if call.hypothesis not in hypotheses:
            errors.append(f"unknown_hypothesis: calls[{i}] serves no hypothesis of this incident")
        errors += check_text(call.purpose, citations, f"calls[{i}].purpose")
        if isinstance(call, EvidenceLookupCall):
            errors += check_refs(
                (str(e) for e in call.arguments.evidence_ids), citations, f"calls[{i}].arguments"
            )
    return errors


# ----------------------------------------------------------------------- synthesize_findings


class RootCause(Strict):
    hypothesis_id: Annotated[str, Field(min_length=36, max_length=36)]
    statement: Annotated[str, Field(min_length=1, max_length=400)]
    evidence_refs: Annotated[list[str], Field(min_length=1, max_length=6)]


class Alternative(Strict):
    hypothesis_id: Annotated[str, Field(min_length=36, max_length=36)]
    why_not: Text300
    evidence_refs: EvidenceRefs


class SynthesisOutput(Strict):
    summary: Annotated[str, Field(min_length=1, max_length=600)]
    root_causes: Annotated[list[RootCause], Field(max_length=5)]
    alternatives_considered: Annotated[list[Alternative], Field(max_length=24)]
    caveats: Annotated[list[Text300], Field(max_length=5)]


SYNTHESIZE = Task("synthesize_findings", 2, SynthesisOutput)


def validate_synthesis(
    output: SynthesisOutput,
    statuses: dict[str, str],  # hypothesis id → status
    incident_of: dict[str, str],  # hypothesis id → incident id
    citations: Citations,  # every evidence item of the investigation
    incident_evidence: dict[str, set[str]],  # incident id → its evidence ids
) -> list[str]:
    errors = check_text(output.summary, citations, "summary")
    causes: set[str] = set()
    for i, cause in enumerate(output.root_causes):
        where = f"root_causes[{i}]"
        status = statuses.get(cause.hypothesis_id)
        if status is None:
            errors.append(f"unknown_hypothesis: {where} is not a hypothesis of this investigation")
            continue
        if status != "supported":
            errors.append(
                f"unsupported_root_cause: {where} is {status}; only supported hypotheses can be "
                "stated as causes"
            )
        if cause.hypothesis_id in causes:
            errors.append(f"duplicate_hypothesis: {where} repeats a root cause")
        causes.add(cause.hypothesis_id)
        scoped = citations.restricted_to(incident_evidence[incident_of[cause.hypothesis_id]])
        errors += check_text(cause.statement, scoped, f"{where}.statement")
        errors += check_refs(cause.evidence_refs, scoped, where)
        errors += _check_listed_citations(cause.statement, cause.evidence_refs, where)
    for i, alt in enumerate(output.alternatives_considered):
        where = f"alternatives_considered[{i}]"
        if alt.hypothesis_id not in statuses:
            errors.append(f"unknown_hypothesis: {where} is not a hypothesis of this investigation")
            continue
        if alt.hypothesis_id in causes:
            errors.append(f"contradiction: {where} is also stated as a root cause")
        scoped = citations.restricted_to(incident_evidence[incident_of[alt.hypothesis_id]])
        errors += check_text(alt.why_not, scoped, f"{where}.why_not")
        errors += check_refs(alt.evidence_refs, scoped, where)
        errors += _check_listed_citations(alt.why_not, alt.evidence_refs, where)
    for i, caveat in enumerate(output.caveats):
        errors += check_text(caveat, citations, f"caveats[{i}]")
    return errors
