"""Evidence payloads and reproducibility hashes (DATA_CONTRACTS §5, AMD-10)."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from app.analytics.detection import Flag
from app.analytics.metrics import RATIO_METRICS, fraction, money
from app.analytics.periods import Windows


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(type(value))


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, default=_default, sort_keys=True, separators=(",", ":"))


def sha256_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _metric_value(metric: str, value: Decimal | None) -> str | None:
    if value is None:
        return None
    if metric in RATIO_METRICS:
        return fraction(value)
    if metric == "active_customers":
        return format(value.normalize(), "f")
    return money(value)


def detection_evidence(
    flag: Flag, windows: Windows, materiality: Decimal, config_version: int
) -> dict[str, Any]:
    m = flag.metric
    return {
        "metric": m,
        "dimension": flag.dimension,
        "value": flag.value,
        "kind": flag.kind,
        "adverse_direction": flag.direction,
        "baseline_window": [windows.baseline[0], windows.baseline_end],
        "current_window": [windows.current[0], windows.end],
        "baseline_weekly": [
            {"week": w, "value": _metric_value(m, v)} for w, v in flag.baseline_weekly
        ],
        "current_weekly": [
            {"week": w, "value": _metric_value(m, v)} for w, v in flag.current_weekly
        ],
        "baseline_median": _metric_value(m, flag.baseline_median),
        "baseline_aggregate": _metric_value(m, flag.baseline_aggregate),
        "current_value": _metric_value(m, flag.current),
        "mad": _metric_value(m, flag.mad),
        "robust_sigma": _metric_value(m, flag.sigma),
        "z": flag.z,
        "delta_abs": _metric_value(m, flag.delta_abs),
        "delta_rel": fraction(flag.delta_rel) if flag.delta_rel is not None else None,
        "impact": money(flag.impact),
        "materiality": money(materiality),
        "onset_week": flag.onset_week,
        "support": flag.support,
        "config_version": config_version,
    }


def reason_mix_evidence(mix: dict[str, dict[str, Decimal]]) -> dict[str, Any]:
    total_b = sum((v["baseline"] for v in mix.values()), Decimal(0))
    total_w = sum((v["current"] for v in mix.values()), Decimal(0))
    rows = []
    for reason, v in sorted(mix.items()):
        share_b = v["baseline"] / total_b if total_b else None
        share_w = v["current"] / total_w if total_w else None
        rows.append(
            {
                "reason_code": reason,
                "baseline_refunds": money(v["baseline"]),
                "current_refunds": money(v["current"]),
                "baseline_share": fraction(share_b) if share_b is not None else None,
                "current_share": fraction(share_w) if share_w is not None else None,
                "share_change": fraction(share_w - share_b)
                if share_b is not None and share_w is not None
                else None,
            }
        )
    return {"reasons": rows, "baseline_total": money(total_b), "current_total": money(total_w)}
