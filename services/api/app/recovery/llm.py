"""`explain_strategies` v1 (AMD-17): optional rationale text for strategies that already exist.

The model sees opaque strategy aliases, catalog codes and evidence facts (entities as
placeholder tokens; never customer IDs or names) and returns only rationale text under the
placeholder contract. It cannot add, remove, select, rank or parameterise strategies, cannot
produce numbers, and has no field that could express a decision.
"""

from typing import Annotated, Any

from pydantic import Field

from app.investigation.critic import Citations, check_refs, check_text
from app.investigation.facts import Facts, llm_fields
from app.investigation.llm_contracts import Rationale, Strict, _check_listed_citations
from app.llm.gateway import Task


class StrategyExplanation(Strict):
    strategy: Annotated[str, Field(pattern=r"^s[0-9]{1,3}$")]
    rationale: Annotated[list[Rationale], Field(min_length=1, max_length=2)]


class ExplainOutput(Strict):
    explanations: Annotated[list[StrategyExplanation], Field(max_length=40)]


EXPLAIN = Task("explain_strategies", 1, ExplainOutput)


def envelope(
    strategies: list[dict[str, Any]], evidence: dict[str, Facts], citations: Citations
) -> tuple[dict[str, Any], dict[str, str]]:
    """(envelope, alias → strategy instance id). Aliases keep business values out."""
    aliases = {f"s{i}": s["id"] for i, s in enumerate(strategies, start=1)}
    items = []
    for alias, s in zip(aliases, strategies, strict=True):
        cited = []
        for evidence_id in s["evidence"]:
            fields, units = llm_fields(evidence[evidence_id])
            cited.append({"id": evidence_id, "fields": fields, "units": units})
        items.append(
            {
                "strategy": alias,
                "code": s["strategy"],
                "addresses": s["hypothesis"]["template"],
                "summary": s["summary"],
                "action_types": s["action_types"],
                "evidence": cited,
            }
        )
    return (
        {
            "task": EXPLAIN.name,
            "prompt_version": EXPLAIN.prompt_version,
            "context": {"strategies": items},
            "untrusted_text": [],
            "allowed": {
                "strategies": sorted(aliases),
                "evidence_ids": sorted(citations.evidence),
                "entity_placeholders": sorted(citations.entity_tokens),
            },
        },
        aliases,
    )


def validate(
    output: ExplainOutput, aliases: dict[str, str], scoped: dict[str, Citations]
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(output.explanations):
        where = f"explanations[{i}]"
        instance = aliases.get(item.strategy)
        if instance is None:
            errors.append(f"unknown_strategy: {where} is not a strategy of this plan")
            continue
        if item.strategy in seen:
            errors.append(f"duplicate_strategy: {where} repeats a strategy")
        seen.add(item.strategy)
        for j, r in enumerate(item.rationale):
            errors += check_text(r.text, scoped[instance], f"{where}.rationale[{j}]")
            errors += check_refs(r.evidence_refs, scoped[instance], f"{where}.rationale[{j}]")
            errors += _check_listed_citations(r.text, r.evidence_refs, f"{where}.rationale[{j}]")
    return errors
