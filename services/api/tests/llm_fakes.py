"""Scripted LLM providers for CI (AMD-12). No network; every request is recorded.

`CooperativeModel` reads the input envelope like a well-behaved model and answers within the
contract, with selectable phrasing so tests can prove that analytical facts do not depend on
wording. Other fakes return whatever a test scripts, including hostile or broken output.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from app.llm.provider import LlmRequest, LlmResponse, ProviderError

Script = Callable[[LlmRequest], dict[str, Any] | str | ProviderError]


class ScriptedProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, script: Script) -> None:
        self.script = script
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        answer = self.script(request)
        if isinstance(answer, ProviderError):
            raise answer
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return LlmResponse(
            text=text,
            model=self.model,
            stop_reason="end_turn",
            tokens_in=len(request.system + request.user) // 4,
            tokens_out=len(text) // 4,
        )

    def tasks(self) -> list[str]:
        return [r.task for r in self.requests]


def envelope(request: LlmRequest) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.user)
    result: dict[str, Any] = body["input"]
    return result


def _numeric_field(evidence: dict[str, Any], prefer: tuple[str, ...]) -> str | None:
    units: dict[str, str] = evidence["units"]
    fields: dict[str, Any] = evidence["fields"]
    for name in prefer:
        if name in units and fields.get(name) is not None:
            return name
    for name, unit in units.items():
        if unit in ("money", "count") and fields.get(name) is not None:
            return name
    return None


def placeholder(evidence: dict[str, Any], prefer: tuple[str, ...] = ("impact",)) -> str:
    name = _numeric_field(evidence, prefer)
    return "{{ev:" + evidence["id"] + "." + name + "}}" if name else "the evidence"


PHRASES = (
    ("Worth checking first given", "This is supported by", "Overall the investigation found"),
    ("A strong lead because of", "The evidence shows", "In short, the analysis identified"),
    ("Plausible in light of", "Data points to this:", "Summing up, we saw"),
)


class CooperativeModel:
    """Answers each task correctly; `style` changes only the wording."""

    def __init__(self, style: int = 0, *, extra_tools: bool = True) -> None:
        self.style = style
        self.extra_tools = extra_tools

    def __call__(self, request: LlmRequest) -> dict[str, Any]:
        data = envelope(request)
        context = data["context"]
        lead, support, summary = PHRASES[self.style % len(PHRASES)]
        if request.task == "prioritize_hypotheses":
            detection = context["evidence"][0]
            names = [c["template"] for c in context["candidates"]]
            if self.style % 2:
                names = list(reversed(names))
            return {
                "ranking": [
                    {
                        "template": name,
                        "priority": min(9, i + 1),
                        "rationale": [
                            {
                                "text": f"{lead} {placeholder(detection)}.",
                                "evidence_refs": [detection["id"]],
                            }
                        ],
                    }
                    for i, name in enumerate(names)
                ]
            }
        if request.task == "select_tools":
            hypothesis = context["hypotheses"][0]["template"]
            calls: list[dict[str, Any]] = []
            if self.extra_tools and context["round"] == 1:
                calls = [
                    {
                        "tool": "period_comparison",
                        "arguments": {"metrics": [context["incident"]["metric"]]},
                        "hypothesis": hypothesis,
                        "purpose": "Compare with the preceding period of equal length.",
                    },
                    {
                        "tool": "evidence_lookup",
                        "arguments": {"evidence_ids": [context["evidence"][0]["id"]]},
                        "hypothesis": hypothesis,
                        "purpose": "Re-read the detection statistics.",
                    },
                ]
            elif self.extra_tools:
                calls = [
                    {
                        "tool": "metric_breakdown",
                        "arguments": {"metric": context["incident"]["metric"], "by": "region"},
                        "hypothesis": hypothesis,
                        "purpose": "See whether one region stands out.",
                    }
                ]
            return {"calls": calls, "done": context["round"] >= 2}
        if request.task == "synthesize_findings":
            causes, alternatives = [], []
            first_evidence = None
            for incident in context["incidents"]:
                evidence = {e["id"]: e for e in incident["evidence"]}
                first_evidence = first_evidence or incident["evidence"][0]
                for h in incident["hypotheses"]:
                    refs = sorted({r for c in h["criteria"] for r in c["evidence_refs"]})[:6]
                    cited = evidence[refs[0]] if refs else incident["evidence"][0]
                    text = f"{support} {placeholder(cited, ('cohort.current_rate',))}."
                    if h["status"] == "supported" and h.get("interpretation") != "intended":
                        causes.append(
                            {
                                "hypothesis_id": h["id"],
                                "statement": text,
                                "evidence_refs": refs or [cited["id"]],
                            }
                        )
                    else:
                        alternatives.append(
                            {
                                "hypothesis_id": h["id"],
                                "why_not": f"Status is {h['status'].replace('_', ' ')}.",
                                "evidence_refs": refs,
                            }
                        )
            cite = placeholder(first_evidence) if first_evidence else "the evidence"
            return {
                "summary": f"{summary} {cite}.",
                "root_causes": causes,
                "alternatives_considered": alternatives[:24],
                "caveats": [],
            }
        if request.task == "explain_strategies":
            explanations = []
            for s in context["strategies"]:
                cited = s["evidence"][0]
                explanations.append(
                    {
                        "strategy": s["strategy"],
                        "rationale": [
                            {
                                "text": f"{support} {placeholder(cited)}.",
                                "evidence_refs": [cited["id"]],
                            }
                        ],
                    }
                )
            return {"explanations": explanations}
        raise AssertionError(f"unexpected task {request.task}")


def contains_any(requests: list[LlmRequest], needles: list[str]) -> list[str]:
    """Needles that appear anywhere in what was sent to the model."""
    sent = "\n".join(r.system + "\n" + r.user for r in requests)
    return [n for n in needles if re.search(re.escape(n), sent, re.IGNORECASE)]
