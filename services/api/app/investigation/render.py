"""Placeholder rendering (DATA_CONTRACTS §9.3) and code-generated observations.

Text is turned into segments: plain text, values (formatted from stored evidence, with the
evidence ID and field they came from) and entities (labels looked up server-side). The UI shows
segments as plain text, so nothing in a stored string is ever interpreted as markup.
"""

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

# Any is restricted to heterogeneous JSON payloads at validated schema/provider boundaries.
from app.investigation.critic import ENTITY_PLACEHOLDER, EVIDENCE_PLACEHOLDER, TOKEN
from app.investigation.facts import DIMENSION_ENTITY, Facts, entity_token, numeric

Segment = dict[str, Any]
METRIC_LABELS = {
    "net_revenue": "Net revenue",
    "gross_margin_pct": "Gross margin",
    "refund_rate": "Refund rate",
    "discount_rate": "Discount rate",
    "active_customers": "Weekly active customers",
    "repeat_customer_rate": "Repeat customer rate",
    "aov": "Average order value",
}
REASON_LABELS = {
    "damaged": "damaged",
    "defective": "defective",
    "wrong_item": "wrong item",
    "not_as_described": "not as described",
    "late_delivery": "late delivery",
    "changed_mind": "changed mind",
    "other": "other",
}


def display(f: Mapping[str, Any], currency: str) -> str:
    unit = f.get("unit")
    value = numeric(f)
    if value is None:
        return "n/a"
    if unit == "money":
        amount = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        return f"{'-' if amount < 0 else ''}{currency} {abs(amount):,.2f}"
    if unit == "ratio":
        return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)}%"
    if unit == "pp":
        pp = (value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
        return f"{'+' if pp > 0 else ''}{pp} pp"
    if unit == "count":
        return f"{int(value):,}"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):,}"


class Renderer:
    def __init__(
        self,
        evidence: Mapping[str, Facts],
        labels: Mapping[str, tuple[str, str]],  # entity token → (kind, label)
        currency: str,
    ) -> None:
        self.evidence = evidence
        self.labels = labels
        self.currency = currency

    def segments(self, text: str) -> list[Segment]:
        out: list[Segment] = []
        position = 0
        for match in TOKEN.finditer(text):
            if match.start() > position:
                out.append({"text": text[position : match.start()]})
            token = match.group(0)
            ev = EVIDENCE_PLACEHOLDER.fullmatch(token)
            entity = ENTITY_PLACEHOLDER.fullmatch(token)
            if ev is not None and (f := self.evidence.get(ev["id"], {}).get(ev["field"])):
                out.append(
                    {
                        "value": display(f, self.currency),
                        "evidence_id": ev["id"],
                        "field": ev["field"],
                    }
                )
            elif entity is not None and token in self.labels:
                kind, label = self.labels[token]
                out.append({"entity": label, "kind": kind})
            else:  # validated text never gets here; stored text is shown, not interpreted
                out.append({"text": token})
            position = match.end()
        if position < len(text):
            out.append({"text": text[position:]})
        return out

    def basis(self, basis: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for b in basis:
            f = {"value": b.get("value"), "unit": b.get("unit")}
            shown = (
                display(f, self.currency)
                if b.get("unit") not in ("entity", "code")
                else self._entity_label(b)
            )
            out.append({"evidence_id": b["evidence_id"], "field": b["field"], "value": shown})
        return out

    def _entity_label(self, b: Mapping[str, Any]) -> str:
        return str(b.get("value") or "n/a")


def ev(evidence_id: str, name: str) -> str:
    return "{{ev:" + evidence_id + "." + name + "}}"


def observations(
    metric: str,
    dimension: str,
    value: str | None,
    evidence: list[tuple[str, str, Facts]],  # (evidence id, kind, facts)
) -> list[str]:
    """Source observations for one incident, as placeholder text (no interpretation)."""
    label = METRIC_LABELS.get(metric, metric)
    scope = (
        "the whole business"
        if dimension == "overall" or value is None
        else entity_token(DIMENSION_ENTITY[dimension], value)
    )
    out: list[str] = []
    for evidence_id, kind, facts in evidence:
        if kind == "incident_detection":
            baseline = "baseline_aggregate" if "baseline_aggregate" in facts else "baseline_median"
            out.append(
                f"{label} for {scope} was {ev(evidence_id, 'current_value')} in the current "
                f"window, against {ev(evidence_id, baseline)} in the baseline."
            )
            out.append(
                f"Estimated financial impact in the current window: {ev(evidence_id, 'impact')} "
                f"(materiality threshold {ev(evidence_id, 'materiality')})."
            )
        elif kind == "source_records":
            parts = [
                f"{ev(evidence_id, f'counts.{name}')} {name.replace('_', ' ')}"
                for name in ("order_lines", "orders", "customers", "returns")
                if f"counts.{name}" in facts
            ]
            if parts:
                out.append("In scope in the current window: " + ", ".join(parts) + ".")
        elif kind == "return_reason_mix":
            changes = sorted(
                (
                    (numeric(f) or Decimal(0), name)
                    for name, f in facts.items()
                    if name.endswith(".share_change")
                ),
                reverse=True,
            )
            if changes and changes[0][0] > 0:
                code = changes[0][1].split(".")[1]
                share = ev(evidence_id, f"reasons.{code}.current_share")
                out.append(
                    f"The share of refunds with reason '{REASON_LABELS.get(code, code)}' changed "
                    f"by {ev(evidence_id, changes[0][1])} (now {share})."
                )
    return out
