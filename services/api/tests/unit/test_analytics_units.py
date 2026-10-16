"""Pure analytics: metric definitions, property invariants, periods, segmentation, detection."""

import importlib.util
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.analytics.config import AnalysisConfig
from app.analytics.detection import (
    Flag,
    SliceSkip,
    consolidate,
    evaluate_slice,
    product_slices,
    severity,
)
from app.analytics.metrics import (
    Totals,
    amount,
    analytics_context,
    change,
    derive,
    present,
    ratio,
)
from app.analytics.periods import (
    Period,
    Windows,
    detection_windows,
    first_complete_week,
    last_complete_week,
    week_start,
)
from app.analytics.segmentation import (
    CustomerStats,
    lifecycle,
    median_gap_days,
    quintile_scores,
    segment_customers,
)


def _seeded_config() -> AnalysisConfig:
    """The v1 constants exactly as migration 0003 seeds them."""
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0003_analytics.py"
    spec = importlib.util.spec_from_file_location("migration_0003", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return AnalysisConfig.from_constants(1, module.CONFIG_V1)


CONFIG = _seeded_config()


# ------------------------------------------------------------------------- metric definitions


def test_derive_handles_zero_denominators_and_missing_costs() -> None:
    empty = derive(Totals())
    assert empty["net_revenue"] == 0
    for name in (
        "aov",
        "discount_rate",
        "refund_rate",
        "unit_return_rate",
        "repeat_customer_rate",
        "gross_margin_pct",
    ):
        assert empty[name] is None, name  # undefined, never 0
    missing = derive(Totals(gross_sales=Decimal(10), lines_missing_cost=1))
    assert (missing["cogs_sold"], missing["gross_profit"], missing["gross_margin_pct"]) == (
        None,
        None,
        None,
    )
    restock_missing = derive(Totals(gross_sales=Decimal(10), restockable_returns_missing_cost=1))
    assert restock_missing["cogs_restocked"] is None
    assert restock_missing["gross_profit"] is None


def test_fully_refunded_and_zero_revenue_margin_is_undefined() -> None:
    t = Totals(
        gross_sales=Decimal(30),
        refunds=Decimal(30),
        cogs_sold_known=Decimal(12),
        cogs_restocked_known=Decimal(12),
        units_sold=3,
        units_returned=3,
        orders=1,
    )
    m = derive(t)
    assert m["net_revenue"] == 0
    assert m["gross_profit"] == 0
    assert m["gross_margin_pct"] is None  # net revenue ≤ 0
    assert m["refund_rate"] == 1
    negative = derive(Totals(gross_sales=Decimal(10), refunds=Decimal(15)))
    assert negative["net_revenue"] == -5
    assert negative["gross_margin_pct"] is None


def test_extreme_values_keep_exact_precision() -> None:
    # 100,000 units × 1e9 price, a 4-dp discount: exact well beyond float precision.
    t = Totals(
        gross_sales=Decimal("100000000000000.0000"),
        discounts=Decimal("0.0001"),
        units_sold=100_000,
        orders=1,
        cogs_sold_known=Decimal("99999999999999.9999"),
    )
    m = derive(t)
    assert m["net_sales"] == Decimal("99999999999999.9999")
    assert m["gross_profit"] == Decimal("0")
    assert present(m)["net_sales"] == "99999999999999.9999"
    assert present({"discount_rate": ratio(1, 3)})["discount_rate"] == "0.333333"
    assert present({"refund_rate": ratio(2, 3)})["refund_rate"] == "0.666667"  # half-even


def test_change_between_periods() -> None:
    assert change("net_revenue", Decimal(3), Decimal("59.5")) == {
        "abs": "-56.5000",
        "rel": "-0.949580",
    }
    assert change("orders", 2, 0) == {"abs": 2, "rel": None}  # relative undefined when prev = 0
    assert change("gross_profit", None, Decimal(1)) == {"abs": None, "rel": None}


money = st.decimals(min_value=0, max_value=10**9, places=4, allow_nan=False, allow_infinity=False)
totals = st.builds(
    Totals,
    gross_sales=money,
    discounts=money,
    units_sold=st.integers(0, 10**6),
    cogs_sold_known=money,
    lines=st.integers(0, 10**5),
    orders=st.integers(0, 10**5),
    active_customers=st.integers(0, 10**5),
    refunds=money,
    units_returned=st.integers(0, 10**6),
    cogs_restocked_known=money,
    returns=st.integers(0, 10**5),
)


@settings(max_examples=200, deadline=None)
@given(totals)
def test_metric_identities(t: Totals) -> None:
    m = derive(t)
    assert amount(m, "net_sales") == t.gross_sales - t.discounts
    assert amount(m, "net_revenue") == amount(m, "net_sales") - t.refunds
    assert amount(m, "gross_profit") == (
        amount(m, "net_revenue") - t.cogs_sold_known + t.cogs_restocked_known
    )
    if t.gross_sales > 0:
        assert m["discount_rate"] == ratio(t.discounts, t.gross_sales)


@settings(max_examples=200, deadline=None)
@given(st.lists(totals, min_size=1, max_size=6))
def test_aggregation_is_additive_and_ratios_use_sums(parts: list[Totals]) -> None:
    combined = sum(parts, Totals())
    m = derive(combined)
    assert m["gross_sales"] == sum(p.gross_sales for p in parts)
    assert m["refunds"] == sum(p.refunds for p in parts)
    assert amount(m, "net_revenue") == sum(
        (amount(derive(p), "net_revenue") for p in parts), Decimal(0)
    )
    net_sales = sum((amount(derive(p), "net_sales") for p in parts), Decimal(0))
    # A combined rate is Σnum/Σden, which is not in general the mean of the part rates.
    assert m["refund_rate"] == ratio(sum(p.refunds for p in parts), net_sales)


def test_mean_of_rates_is_not_the_aggregate() -> None:
    a = Totals(gross_sales=Decimal(100), refunds=Decimal(50))  # rate 0.5 on 100
    b = Totals(gross_sales=Decimal(900), refunds=Decimal(0))  # rate 0.0 on 900
    assert derive(a + b)["refund_rate"] == Decimal("0.05")  # not 0.25


# ------------------------------------------------------------------------------- periods


def test_week_boundaries() -> None:
    assert week_start(date(2026, 1, 11)) == date(2026, 1, 5)  # Sunday → its Monday
    assert last_complete_week(date(2026, 1, 11)) == date(2026, 1, 5)  # Sunday completes it
    assert last_complete_week(date(2026, 1, 10)) == date(2025, 12, 29)  # Saturday does not
    assert first_complete_week(date(2026, 1, 5)) == date(2026, 1, 5)  # starts on a Monday
    assert first_complete_week(date(2026, 1, 6)) == date(2026, 1, 12)


def test_detection_windows() -> None:
    w = detection_windows(date(2026, 1, 5), date(2026, 7, 5), 4, 12)
    assert w is not None
    assert (w.current[0], w.end) == (date(2026, 6, 8), date(2026, 7, 5))
    assert (w.baseline[0], w.baseline_end) == (date(2026, 3, 16), date(2026, 6, 7))
    short = detection_windows(date(2026, 1, 5), date(2026, 2, 28), 4, 12)  # 7 complete weeks
    assert short is not None
    assert len(short.baseline) == 3
    assert detection_windows(date(2026, 1, 6), date(2026, 1, 25), 4, 12) is None


def test_equal_previous_period() -> None:
    p = Period(date(2026, 1, 12), date(2026, 1, 18))
    assert (p.days, p.previous()) == (7, Period(date(2026, 1, 5), date(2026, 1, 11)))
    feb = Period(date(2026, 2, 1), date(2026, 2, 28)).previous()
    assert (feb.start, feb.end) == (date(2026, 1, 4), date(2026, 1, 31))


# ----------------------------------------------------------------------------- segmentation

AS_OF = date(2026, 7, 1)


def customer(*days_ago: int, orders: int | None = None) -> CustomerStats:
    dates = tuple(sorted(AS_OF - timedelta(days=d) for d in days_ago))
    return CustomerStats("C", dates, orders or len(dates), len(dates), Decimal(100), 10, 0, 0)


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ((5,), "insufficient_history"),  # single order
        ((5, 40), "insufficient_history"),  # two orders: no cadence yet
        ((181,), "dormant"),  # absolute inactivity beats short history
        ((180,), "insufficient_history"),  # exactly 180 days is not dormant (> 180)
        ((10, 40, 70, 100), "loyal"),  # gap 30, recency 10
        ((60, 90, 120), "loyal"),  # gap 30, recency 60 = 2×30 is not > 2×30
        ((61, 91, 121), "at_risk"),  # 61 > 2×30 and > 30, ≤ 4×30
        ((120, 150, 180), "at_risk"),  # recency 120 = 4×30 → not > 4×gap
        ((121, 151, 181), "dormant"),  # recency 121 > 4×30
        ((59, 89, 119), "loyal"),
        ((25, 30, 35), "insufficient_history"),  # first order only 35 days ago (< 60)
        ((20, 22, 24, 100), "loyal"),  # median gap 2; recency 20 ≤ 30 minimum → on cadence
        ((31, 33, 35, 100), "dormant"),  # recency 31 > 4×2 and > 30
    ],
)
def test_lifecycle_thresholds(history: tuple[int, ...], expected: str) -> None:
    segment, reason, _ = lifecycle(customer(*history), AS_OF, CONFIG)
    assert segment == expected, reason
    assert "churn" not in reason


def test_same_day_orders_have_no_cadence() -> None:
    stats = CustomerStats("C", (AS_OF - timedelta(days=5),), 3, 3, Decimal(1), 3, 0, 0)
    assert lifecycle(stats, AS_OF, CONFIG)[0] == "insufficient_history"
    assert median_gap_days(stats.order_dates) is None


def test_quintiles_share_scores_on_ties() -> None:
    assert quintile_scores([Decimal(v) for v in (1, 2, 3, 4, 5)]) == [1, 2, 3, 4, 5]
    assert quintile_scores([Decimal(1)] * 4) == [5, 5, 5, 5]
    assert quintile_scores([Decimal(v) for v in (1, 1, 2, 9)]) == [3, 3, 4, 5]


def test_flags_return_risk_and_key_account() -> None:
    heavy = CustomerStats("A", (AS_OF,), 1, 1, Decimal(900), 10, 5, 3)
    light = CustomerStats("B", (AS_OF,), 1, 1, Decimal(100), 100, 1, 1)
    profiles = {p.customer_id: p for p in segment_customers([heavy, light], AS_OF, CONFIG)}
    assert set(profiles["A"].flags) == {"return_risk", "key_account"}
    assert profiles["B"].flags == ("key_account",)  # 100/1000 ≥ 5% share


# -------------------------------------------------------------------------------- detection


def _windows() -> Windows:
    windows = detection_windows(date(2026, 1, 5), date(2026, 4, 26), 4, 12)
    if windows is None:
        raise AssertionError("fixture windows must exist")
    return windows


WINDOWS = _windows()
WEEKS = (*WINDOWS.baseline, *WINDOWS.current)


def weekly(values: list[tuple[int, int]], orders: int = 40) -> dict[date, Totals]:
    """(net sales, refunds) per week; the last four entries are the current window."""
    return {
        w: Totals(
            gross_sales=Decimal(s),
            refunds=Decimal(r),
            orders=orders,
            units_sold=orders * 2,
            active_customers=orders,
            lines=orders,
        )
        for w, (s, r) in zip(WEEKS, values, strict=True)
    }


STABLE = [(10_000 + 100 * (i % 3), 400 + 10 * (i % 2)) for i in range(12)]


def test_stable_series_is_not_flagged() -> None:
    series = weekly(STABLE + STABLE[:4])
    for metric in ("net_revenue", "refund_rate"):
        assert (
            evaluate_slice(metric, ("overall", None), series, WINDOWS, Decimal(100), CONFIG) is None
        )


def test_refund_spike_is_flagged_with_statistics() -> None:
    series = weekly(STABLE + [(10_000, 2_000)] * 4)
    flag = evaluate_slice("refund_rate", ("product", "X"), series, WINDOWS, Decimal(100), CONFIG)
    assert isinstance(flag, Flag)
    assert flag.current == Decimal("0.2")  # 8000 / 40000 aggregated
    assert flag.z is not None
    assert flag.z >= 3
    assert flag.onset_week == WINDOWS.current[0]
    # impact = (0.2 − baseline aggregate 4860/121200) × 40,000 current net sales
    with localcontext(analytics_context()):  # the documented 60-digit analytics precision
        expected = (Decimal("0.2") - Decimal(4860) / Decimal(121200)) * 40_000
    assert flag.impact == expected
    assert str(expected).startswith("6396.0396039603960396")


def test_materiality_and_support_gate_flags() -> None:
    series = weekly(STABLE + [(10_000, 2_000)] * 4)
    assert (
        evaluate_slice("refund_rate", ("product", "X"), series, WINDOWS, Decimal(10**6), CONFIG)
        is None
    )
    thin = weekly(STABLE + [(10_000, 2_000)] * 4, orders=2)
    skip = evaluate_slice("refund_rate", ("product", "X"), thin, WINDOWS, Decimal(0), CONFIG)
    assert isinstance(skip, SliceSkip)
    assert skip.reason == "low_support"


def test_zero_mad_uses_change_thresholds() -> None:
    flat = [(10_000, 400)] * 12
    up = weekly(flat + [(10_000, 700)] * 4)  # 4% → 7%: +3pp and +75%
    assert isinstance(
        evaluate_slice("refund_rate", ("overall", None), up, WINDOWS, Decimal(0), CONFIG), Flag
    )
    tiny = weekly(flat + [(10_000, 500)] * 4)  # +1pp only
    assert (
        evaluate_slice("refund_rate", ("overall", None), tiny, WINDOWS, Decimal(0), CONFIG) is None
    )


def test_revenue_drop_and_rise() -> None:
    drop = weekly(STABLE + [(6_000, 400)] * 4)
    flag = evaluate_slice("net_revenue", ("overall", None), drop, WINDOWS, Decimal(100), CONFIG)
    assert isinstance(flag, Flag)
    assert flag.direction == "down"
    rise = weekly(STABLE + [(16_000, 400)] * 4)  # favourable: never an incident
    assert (
        evaluate_slice("net_revenue", ("overall", None), rise, WINDOWS, Decimal(100), CONFIG)
        is None
    )


def _flag(metric: str, dimension: str, value: str | None, impact: int, z: float = 5.0) -> Flag:
    return Flag(
        metric,
        dimension,
        value,
        "rate",
        "up",
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        z,
        Decimal(0),
        None,
        Decimal(impact),
        None,
        (),
        (),
        None,
        {},
    )


def test_consolidation_suppresses_parent_merges_overlap_and_ranks() -> None:
    flags = [
        _flag("refund_rate", "overall", None, 1000),
        _flag("refund_rate", "product", "A", 900),
        _flag("refund_rate", "category", "K", 800),  # same orders as product A → merged
        _flag("refund_rate", "region", "R", 300),
        _flag("net_revenue", "channel", "web", 950, z=4.0),
    ]
    sets: dict[tuple[str, str | None], frozenset[str]] = {
        ("product", "A"): frozenset({"o1", "o2", "o3"}),
        ("category", "K"): frozenset({"o1", "o2", "o3", "o4"}),
        ("region", "R"): frozenset({"o9"}),
        ("channel", "web"): frozenset({"o5"}),
    }
    incidents = consolidate(flags, lambda s: sets.get(s, frozenset()), Decimal(20_000), CONFIG)
    keys = [i.flag.key for i in incidents]
    assert keys == ["net_revenue|channel|web", "refund_rate|product|A", "refund_rate|region|R"]
    product = incidents[1].flag
    parent = product.context["parent"]
    assert isinstance(parent, dict)
    assert parent["key"] == "refund_rate|overall|*"
    assert product.context["related_slices"] == ["refund_rate|category|K"]
    assert [i.status for i in incidents] == ["investigated"] * 3
    # shares of 20,000: 4.75% and 4.5% → high (≥ 2.5%); 1.5% → medium (≥ 1%)
    assert [i.severity for i in incidents] == ["high", "high", "medium"]


def test_severity_bands() -> None:
    nr = Decimal(10_000)
    assert [severity(Decimal(v), nr, CONFIG) for v in (500, 250, 100, 99)] == [
        "critical",
        "high",
        "medium",
        "low",
    ]
    assert severity(Decimal(1), Decimal(0), CONFIG) == "critical"


def test_product_slice_coverage() -> None:
    sales = {"A": Decimal(50), "B": Decimal(30), "C": Decimal(15), "D": Decimal(5), "E": Decimal(0)}
    assert product_slices(sales, CONFIG) == ["A", "B"]  # 80% reached after B
    assert product_slices({}, CONFIG) == []


def _series(rows: list[Totals]) -> dict[date, Totals]:
    return dict(zip(WEEKS, rows, strict=True))


def _base(**overrides: object) -> Totals:
    values: dict[str, object] = {
        "gross_sales": Decimal(10_000),
        "discounts": Decimal(500),
        "refunds": Decimal(300),
        "cogs_sold_known": Decimal(4_000),
        "orders": 100,
        "units_sold": 200,
        "lines": 150,
        "active_customers": 80,
        "repeat_customers": 40,
    }
    values.update(overrides)
    return Totals(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("metric", "current"),
    [
        ("discount_rate", _base(discounts=Decimal(2_500))),  # 5% → 25%
        ("gross_margin_pct", _base(cogs_sold_known=Decimal(8_500))),  # margin collapses
        ("active_customers", _base(active_customers=40)),  # halved
        ("repeat_customer_rate", _base(repeat_customers=8)),  # 50% → 10%
        ("aov", _base(orders=200)),  # same sales, twice the orders → half the AOV
    ],
)
def test_impact_formulas_per_metric(metric: str, current: Totals) -> None:
    stable = [_base(gross_sales=Decimal(10_000 + 50 * (i % 3))) for i in range(12)]
    series = _series([*stable, *[current] * 4])
    flag = evaluate_slice(metric, ("overall", None), series, WINDOWS, Decimal(1), CONFIG)
    assert isinstance(flag, Flag), metric
    assert flag.impact > 0
    b_total = sum(stable, Totals())
    w_total = sum([current] * 4, Totals())
    b, w = derive(b_total), derive(w_total)
    with localcontext(analytics_context()):
        expected = {
            "discount_rate": lambda: (
                (amount(w, "discount_rate") - amount(b, "discount_rate")) * w_total.gross_sales
            ),
            "gross_margin_pct": lambda: (
                (amount(b, "gross_margin_pct") - amount(w, "gross_margin_pct"))
                * amount(w, "net_revenue")
            ),
            "active_customers": lambda: (
                (Decimal(80) - Decimal(40))
                * 4
                * (amount(b, "net_revenue") / b_total.active_customers)
            ),
            "repeat_customer_rate": lambda: (
                (amount(b, "repeat_customer_rate") - amount(w, "repeat_customer_rate"))
                * w_total.active_customers
                * (amount(b, "net_revenue") / b_total.active_customers)
            ),
            "aov": lambda: (amount(b, "aov") - amount(w, "aov")) * w_total.orders,
        }[metric]()
    assert flag.impact == expected


def test_evidence_payload_formats_and_hashes_deterministically() -> None:
    from app.analytics.evidence import canonical_json, detection_evidence, sha256_of

    stable = [_base() for _ in range(12)]
    flag = evaluate_slice(
        "active_customers",
        ("overall", None),
        _series([*stable, *[_base(active_customers=40)] * 4]),
        WINDOWS,
        Decimal(1),
        CONFIG,
    )
    assert isinstance(flag, Flag)
    payload = detection_evidence(flag, WINDOWS, Decimal("12.5"), 1)
    assert payload["current_value"] == "40"
    assert payload["baseline_median"] == "80"
    assert payload["materiality"] == "12.5000"
    assert (
        canonical_json({"b": Decimal("1.50"), "a": (date(2026, 1, 5),)})
        == '{"a":["2026-01-05"],"b":"1.50"}'
    )
    assert canonical_json(Decimal("2.000")) == '"2"'
    assert sha256_of(payload) == sha256_of(dict(reversed(list(payload.items()))))
    with pytest.raises(TypeError):
        canonical_json(object())


def test_unknown_config_and_dimension_are_rejected() -> None:
    from app.analytics.queries import slice_filter

    assert slice_filter("overall", None) == ("TRUE", {})
    with pytest.raises(ValueError):
        slice_filter("price; DROP TABLE x", "1")
