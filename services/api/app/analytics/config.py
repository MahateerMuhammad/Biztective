"""Versioned analysis constants (DATA_CONTRACTS §3.5 + AMD-10). Read-only at runtime."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Bump whenever a formula or algorithm changes: results are keyed by (config, code) version.
CODE_VERSION = "analytics-1.0.0"
CURRENT_CONFIG_VERSION = 1


@dataclass(frozen=True)
class AnalysisConfig:
    version: int
    current_weeks: int
    baseline_weeks: int
    min_baseline_weeks: int
    z_threshold: Decimal
    rel_change_threshold: Decimal
    rate_abs_threshold_pp: Decimal
    rate_rel_threshold: Decimal
    materiality_pct: Decimal
    min_impact_amount: Decimal
    min_slice_orders_baseline: int
    min_slice_orders_current: int
    min_units_current_refund_rate: int
    product_slice_coverage: Decimal
    max_product_slices: int
    child_share_threshold: Decimal
    overlap_jaccard_threshold: Decimal
    max_incidents: int
    max_investigated_incidents: int
    return_maturity_days: int
    rfm_lookback_days: int
    churn_gap_multiplier: Decimal
    churn_min_recency_days: int
    dormant_days: int
    dormant_gap_multiplier: Decimal
    min_history_days: int
    return_risk_min_rate: Decimal
    return_risk_multiplier: Decimal
    key_account_share: Decimal
    low_sample_customers: int
    severity_critical_share: Decimal
    severity_high_share: Decimal
    severity_medium_share: Decimal
    evidence_sample_size: int

    @classmethod
    def from_constants(cls, version: int, constants: dict[str, Any]) -> AnalysisConfig:
        values: dict[str, Any] = {"version": version}
        for name, field in cls.__dataclass_fields__.items():
            if name == "version":
                continue
            raw = constants[name]
            values[name] = Decimal(str(raw)) if field.type in (Decimal, "Decimal") else int(raw)
        return cls(**values)


async def load_config(
    session: AsyncSession, version: int = CURRENT_CONFIG_VERSION
) -> AnalysisConfig:
    constants = await session.scalar(
        text("SELECT constants FROM analysis_configs WHERE version = :v"), {"v": version}
    )
    if constants is None:
        raise LookupError(f"analysis_config version {version} is missing")
    return AnalysisConfig.from_constants(version, constants)
