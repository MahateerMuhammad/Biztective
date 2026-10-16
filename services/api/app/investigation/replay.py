"""Interpretation versions and their comparison (AMD-15).

A version is a canonical snapshot of what ROOT concluded at a stable point: version 1 before
any human input, version k+1 after the k-th answer. `compare` is a pure function of two stored
snapshots: it never calls the LLM or re-runs analytics, so replay cannot change what it shows.

Any annotations represent heterogeneous JSON snapshot payloads.
"""

from typing import Any

from app.investigation import output
from app.models import Finding, HumanInput


def plain(segments: list[dict[str, Any]]) -> str:
    """Rendered segments as the text a person reads (values and entity labels inline)."""
    return "".join(str(s.get("text", s.get("value", s.get("entity", "")))) for s in segments)


def snapshot(
    loaded: output.Loaded, version: int, findings: Finding, caused_by: HumanInput | None
) -> dict[str, Any]:
    incidents: dict[str, Any] = {}
    for incident in loaded.investigated:
        views = output.ranked(loaded.hypotheses.get(incident.id, []))
        cause = output.root_cause(views)
        items = loaded.evidence.get(incident.id, [])
        incidents[incident.key] = {
            "incident_id": str(incident.id),
            "metric": incident.metric,
            "dimension": incident.dimension,
            "value": incident.dimension_value,
            "severity": incident.severity,
            "measurements": {
                "impact": str(incident.impact),
                # Every calculated result the interpretation rests on, by evidence ID.
                "evidence": {str(e.id): e.result_hash for e in items if e.kind != "human_context"},
            },
            "hypotheses": {
                v.row.template: {
                    "hypothesis_id": str(v.row.id),
                    "source": v.row.source,
                    "status": v.status,
                    "confidence": str(v.confidence),
                    "data_confidence": None
                    if v.evaluation is None or v.evaluation.data_confidence is None
                    else str(v.evaluation.data_confidence),
                    "human_confidence": None
                    if v.evaluation is None or v.evaluation.human_confidence is None
                    else str(v.evaluation.human_confidence),
                    "interpretation": v.interpretation,
                    "conflicts": list(v.evaluation.conflicts) if v.evaluation else [],
                }
                for v in views
            },
            "root_cause": cause.row.template if cause else None,
            "explained_by_context": [v.row.template for v in views if v.explained],
            "recommendations": [
                r["text"] for r in output.recommendations(views, open_question=False)
            ],
            "human_context": [
                {
                    "human_input_id": h["human_input_id"],
                    "criterion": h["criterion"],
                    "option_id": h["option_id"],
                    "effect": h["effect"],
                }
                for h in output.human_context(loaded, incident)
            ],
        }
    rendered = findings.rendered
    return {
        "version": version,
        "caused_by": None
        if caused_by is None
        else {
            "human_input_id": str(caused_by.id),
            "clarification_id": str(caused_by.clarification_id),
            "criterion": caused_by.criterion_key,
            "option_id": caused_by.option_id,
            "effect": caused_by.effect,
            "answered_by": str(caused_by.answered_by),
            "answered_at": caused_by.answered_at.isoformat(),
        },
        "incidents": incidents,
        "findings": {
            "findings_id": str(findings.id),
            "source": findings.source,
            "summary": plain(rendered["summary"]),
            "root_causes": [plain(c["statement"]) for c in rendered["root_causes"]],
            "explained_by_context": [
                plain(e["statement"]) for e in rendered.get("explained_by_context", [])
            ],
            "conflicts": [plain(c["statement"]) for c in rendered.get("conflicts", [])],
            "caveats": [plain(c) for c in rendered["caveats"]],
        },
    }


def _changes(before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]) -> Any:
    return {
        f: {"from": before.get(f), "to": after.get(f)}
        for f in fields
        if before.get(f) != after.get(f)
    }


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What changed from snapshot `a` to snapshot `b`, and whether every measurement of `a`
    is still present and identical in `b`."""
    preserved = True
    incidents = []
    for key in sorted(set(a["incidents"]) | set(b["incidents"])):
        before, after = a["incidents"].get(key), b["incidents"].get(key)
        if before is None or after is None:
            incidents.append({"key": key, "added": before is None, "removed": after is None})
            preserved = preserved and before is None
            continue
        mb, ma = before["measurements"], after["measurements"]
        changed_evidence = sorted(
            e for e, h in mb["evidence"].items() if ma["evidence"].get(e) != h
        )
        same = mb["impact"] == ma["impact"] and not changed_evidence
        preserved = preserved and same
        hypotheses = []
        for template in sorted(set(before["hypotheses"]) | set(after["hypotheses"])):
            hb, ha = before["hypotheses"].get(template), after["hypotheses"].get(template)
            if hb is None:
                hypotheses.append({"template": template, "added": True, "now": ha})
                continue
            if ha is None:  # pragma: no cover - hypotheses are insert-only
                hypotheses.append({"template": template, "removed": True})
                continue
            fields = ("status", "confidence", "human_confidence", "interpretation", "conflicts")
            diff = _changes(hb, ha, fields)
            if diff:
                hypotheses.append({"template": template, "changes": diff})
        recs_b, recs_a = before["recommendations"], after["recommendations"]
        incidents.append(
            {
                "key": key,
                "measurements_preserved": same,
                "changed_evidence": changed_evidence,
                "new_evidence": sorted(set(ma["evidence"]) - set(mb["evidence"])),
                "impact": ma["impact"],
                "root_cause": None
                if before["root_cause"] == after["root_cause"]
                else {"from": before["root_cause"], "to": after["root_cause"]},
                "explained_by_context": {
                    "added": [
                        t
                        for t in after["explained_by_context"]
                        if t not in before["explained_by_context"]
                    ],
                    "removed": [
                        t
                        for t in before["explained_by_context"]
                        if t not in after["explained_by_context"]
                    ],
                },
                "hypotheses": hypotheses,
                "recommendations": {
                    "added": [r for r in recs_a if r not in recs_b],
                    "removed": [r for r in recs_b if r not in recs_a],
                },
                "human_context_added": [
                    h for h in after["human_context"] if h not in before["human_context"]
                ],
            }
        )
    fb, fa = a["findings"], b["findings"]
    return {
        "from_version": a["version"],
        "to_version": b["version"],
        "caused_by": b["caused_by"],
        "measurements_preserved": preserved,
        "incidents": incidents,
        "findings": {
            "summary": None
            if fb["summary"] == fa["summary"]
            else {"from": fb["summary"], "to": fa["summary"]},
            "root_causes": {
                "added": [c for c in fa["root_causes"] if c not in fb["root_causes"]],
                "removed": [c for c in fb["root_causes"] if c not in fa["root_causes"]],
            },
            "explained_by_context": {
                "added": [
                    c for c in fa["explained_by_context"] if c not in fb["explained_by_context"]
                ],
                "removed": [
                    c for c in fb["explained_by_context"] if c not in fa["explained_by_context"]
                ],
            },
            "conflicts": {
                "added": [c for c in fa["conflicts"] if c not in fb["conflicts"]],
                "removed": [c for c in fb["conflicts"] if c not in fa["conflicts"]],
            },
            "caveats": {
                "added": [c for c in fa["caveats"] if c not in fb["caveats"]],
                "removed": [c for c in fb["caveats"] if c not in fa["caveats"]],
            },
        },
    }
