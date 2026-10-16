"""Evidence Critic (AMD-12): deterministic checks between computation, storage and the LLM.

1. Tool output sanity: impossible numbers are rejected before a result is stored.
2. Integrity: stored results must still match their SHA-256 before they are used.
3. LLM text: citations and placeholders must point at this investigation's evidence and at
   numeric facts that exist; no digits or business names outside placeholders; no stated
   direction that contradicts the sign of the change it cites.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Any is restricted to heterogeneous JSON payloads at validated schema/provider boundaries.
from app.analytics.evidence import sha256_of
from app.investigation.facts import NUMERIC_UNITS, Facts, entity_token, numeric

TOKEN = re.compile(r"\{\{[^{}]*\}\}")
EVIDENCE_PLACEHOLDER = re.compile(
    r"\{\{ev:(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.(?P<field>[A-Za-z0-9_.]+)\}\}"
)
ENTITY_PLACEHOLDER = re.compile(
    r"\{\{entity:(?P<kind>category|product|region|channel|segment):(?P<ref>[0-9a-f]{12})\}\}"
)
DIGIT = re.compile(r"\d")
SENTENCE_END = re.compile(r"[.!?;:]+(?:\s|$)")
WORD = re.compile(r"[a-z]+")
UP_WORDS = frozenset(
    {
        *("rose", "rise", "rises", "risen", "rising", "increase", "increased", "increases"),
        *("increasing", "higher", "up", "grew", "grow", "grows", "growing", "jumped", "jump"),
        *("climbed", "climb", "climbs", "surged"),
    }
)
DOWN_WORDS = frozenset(
    {
        *("fell", "fall", "falls", "fallen", "falling", "decrease", "decreased", "decreases"),
        *("decreasing", "decline", "declined", "declines", "declining", "lower", "down"),
        *("dropped", "drop", "drops", "shrank", "shrink", "shrinks", "slipped"),
    }
)
SIGNED_SUFFIXES = ("change", "change_abs", "change_rel", "delta_abs", "delta_rel")
GENERIC_NAMES = frozenset({"unknown", "other", "none", "web", "app", "online", "store"})
MIN_NAME_LENGTH = 4
TOLERANCE = Decimal("0.0001")


# --------------------------------------------------------------------- 1. tool output sanity


def _is_share(name: str) -> bool:
    last = name.rsplit(".", 1)[-1]
    return last in {"share", "top_share"} or last.endswith("_share")


def check_tool_result(result: Mapping[str, Any]) -> list[str]:
    """Problems that make a computed result impossible; an empty list means plausible."""
    problems: list[str] = []
    facts: Facts = result.get("facts", {})
    for name, f in facts.items():
        unit, value = f.get("unit"), f.get("value")
        if unit not in NUMERIC_UNITS or value is None:
            continue
        if unit == "count":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"count_invalid: {name}")
            continue
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            problems.append(f"not_a_number: {name}")
            continue
        if not number.is_finite():
            problems.append(f"not_finite: {name}")
        elif unit == "ratio" and _is_share(name) and not (-TOLERANCE <= number <= 1 + TOLERANCE):
            problems.append(f"share_out_of_range: {name}")
        elif name.endswith("share_change") and not (-1 - TOLERANCE <= number <= 1 + TOLERANCE):
            problems.append(f"share_change_out_of_range: {name}")
    top_shares = [numeric(f) for n, f in facts.items() if _is_top_row_share(n)]
    if sum((s for s in top_shares if s is not None), Decimal(0)) > 1 + TOLERANCE:
        problems.append("shares_exceed_total")
    k_share, one_share = numeric(facts.get("top_k.share")), numeric(facts.get("top1.share"))
    if k_share is not None and one_share is not None and one_share > k_share + TOLERANCE:
        problems.append("top1_exceeds_top_k")
    dups, lines = numeric(facts.get("near_duplicates.lines")), numeric(facts.get("lines.current"))
    if dups is not None and lines is not None and dups > lines:
        problems.append("duplicates_exceed_lines")
    margin_change = numeric(facts.get("margin.change"))
    effects = [numeric(facts.get(f"effect.{n}")) for n in ("discount", "refunds", "price_cost")]
    if (
        margin_change is not None
        and all(e is not None for e in effects)
        and abs(sum((e for e in effects if e is not None), Decimal(0)) - margin_change) > TOLERANCE
    ):
        problems.append("decomposition_does_not_sum: margin")
    for key in ("rows", "contributors"):
        rows = result.get("detail", {}).get(key)
        if isinstance(rows, list):
            shares: list[Decimal] = []
            for row in rows:
                if not isinstance(row, dict) or row.get("share") is None:
                    continue
                try:
                    share = Decimal(str(row["share"]))
                except InvalidOperation:
                    problems.append(f"not_a_number: detail.{key}.share")
                    continue
                if not share.is_finite():
                    problems.append(f"not_finite: detail.{key}.share")
                    continue
                if not (-TOLERANCE <= share <= 1 + TOLERANCE):
                    problems.append(f"share_out_of_range: detail.{key}.share")
                shares.append(share)
            if shares and abs(sum(shares, Decimal(0)) - 1) > TOLERANCE:
                problems.append(f"decomposition_does_not_sum: {key}")
    return problems


def _is_top_row_share(name: str) -> bool:
    parts = name.split(".")
    return len(parts) >= 3 and parts[-3] == "top" and parts[-1] == "share"


# ------------------------------------------------------------------------------ 2. integrity


def intact(result: Mapping[str, Any], result_hash: str) -> bool:
    return sha256_of(result) == result_hash


# ---------------------------------------------------------------------------- 3. LLM text


@dataclass
class Citations:
    """What a piece of LLM text may cite: evidence facts, entity tokens, forbidden names."""

    evidence: dict[str, Facts] = field(default_factory=dict)
    entity_tokens: set[str] = field(default_factory=set)
    raw_names: set[str] = field(default_factory=set)

    def add_evidence(self, evidence_id: str, facts: Facts) -> None:
        self.evidence[evidence_id] = facts
        for f in facts.values():
            if f.get("unit") == "entity" and isinstance(f.get("value"), str):
                self.entity_tokens.add(entity_token(f["kind"], f["value"]))
                self.add_name(f["value"])

    def add_name(self, name: str | None) -> None:
        if name and len(name) >= MIN_NAME_LENGTH and name.lower() not in GENERIC_NAMES:
            self.raw_names.add(name)

    def restricted_to(self, evidence_ids: Iterable[str]) -> Citations:
        allowed = set(evidence_ids)
        scoped = Citations(raw_names=self.raw_names.copy())
        for evidence_id, facts in self.evidence.items():
            if evidence_id in allowed:
                scoped.add_evidence(evidence_id, facts)
        return scoped


def _signed(name: str, f: Mapping[str, Any]) -> bool:
    return f.get("unit") == "pp" or name.rsplit(".", 1)[-1].endswith(SIGNED_SUFFIXES)


def check_text(text: str, citations: Citations, where: str) -> list[str]:
    errors: list[str] = []
    # The critic's internal numeric markers must never be forgeable by model text.
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        return [f"control_character: {where} contains a forbidden control character"]
    markers: list[tuple[str, Mapping[str, Any]] | None] = []

    def mark(match: re.Match[str]) -> str:
        token = match.group(0)
        ev = EVIDENCE_PLACEHOLDER.fullmatch(token)
        if ev is not None:
            facts = citations.evidence.get(ev["id"])
            if facts is None:
                errors.append(f"unknown_evidence: {where} cites evidence that is not allowed here")
            elif (f := facts.get(ev["field"])) is None:
                errors.append(f"unknown_field: {where} cites a field the evidence does not have")
            elif numeric(f) is None:
                errors.append(f"non_numeric_field: {where} cites a field without a number")
            else:
                markers.append((ev["field"], f))
                return f" \x00{len(markers) - 1}\x00 "
        elif ENTITY_PLACEHOLDER.fullmatch(token) is not None:
            if token not in citations.entity_tokens:
                errors.append(f"unknown_entity: {where} uses an entity reference not provided")
        else:
            errors.append(f"malformed_placeholder: {where} has an invalid placeholder")
        markers.append(None)
        return " "

    stripped = TOKEN.sub(mark, text)
    plain = re.sub(r"\x00\d+\x00", " ", stripped)
    if DIGIT.search(plain):
        errors.append(f"digit_outside_placeholder: {where} contains a digit outside placeholders")
    if "{{" in plain or "}}" in plain:
        errors.append(f"malformed_placeholder: {where} has an unclosed placeholder")
    if "<" in plain or ">" in plain:
        errors.append(f"markup: {where} contains markup characters")
    lowered = plain.lower()
    for name in citations.raw_names:
        if re.search(rf"(?<![\w]){re.escape(name.lower())}(?![\w])", lowered):
            errors.append(f"raw_entity_name: {where} names a business entity; use its placeholder")
            break
    errors.extend(_direction_errors(stripped, markers, where))
    return errors


def _direction_errors(
    stripped: str, markers: list[tuple[str, Mapping[str, Any]] | None], where: str
) -> list[str]:
    errors = []
    for sentence in SENTENCE_END.split(stripped):
        cited = [markers[int(i)] for i in re.findall(r"\x00(\d+)\x00", sentence)]
        signed = [(n, f) for item in cited if item is not None for n, f in [item] if _signed(n, f)]
        if len(signed) != 1:
            continue
        value = numeric(signed[0][1])
        words = set(WORD.findall(re.sub(r"\x00\d+\x00", " ", sentence).lower()))
        up, down = bool(words & UP_WORDS), bool(words & DOWN_WORDS)
        if value is None or value == 0 or up == down:
            continue
        if (up and value < 0) or (down and value > 0):
            errors.append(
                f"direction_contradiction: {where} states a direction opposite to the cited change"
            )
    return errors


def check_refs(refs: Iterable[str], citations: Citations, where: str) -> list[str]:
    return [
        f"unknown_evidence: {where} lists evidence that is not allowed here"
        for ref in refs
        if ref not in citations.evidence
    ]
