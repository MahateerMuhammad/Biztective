"""Analytics against hand-computed reference values, plus API behaviour and restrictions."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from tests import analytics_reference as ref
from tests.helpers import Tenant, create_tenant, ingest_files, login


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


@pytest.fixture
async def reference(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> str:
    detail = await ingest_files(app, client, owner.business_id, ref.files())
    assert detail["status"] == "ready", detail
    return f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}"


def by_week(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["week"]: r["metrics"] for r in rows}


def codes(body: dict[str, Any]) -> set[str]:
    return {r["code"] for r in body["restrictions"]}


async def test_weekly_metrics_match_hand_computation(
    client: httpx.AsyncClient, reference: str
) -> None:
    body = (await client.get(f"{reference}/metrics", params={"granularity": "week"})).json()
    weeks = by_week(body["data"])
    w1 = weeks["2026-01-05"]
    # Lines L1, L2, L3. gross = 2*25 + 12 + 25 = 87; discounts 5; net sales 82.
    # Return R1 (damaged, written off): refunds 22.50, no COGS reversal.
    # COGS sold = 2*10 + 1*4 + 1*10 = 34; gross profit = 59.50 - 34 = 25.50.
    assert w1 == {
        "gross_sales": "87.0000",
        "discounts": "5.0000",
        "net_sales": "82.0000",
        "refunds": "22.5000",
        "net_revenue": "59.5000",
        "cogs_sold": "34.0000",
        "cogs_restocked": "0.0000",
        "gross_profit": "25.5000",
        "gross_margin_pct": "0.428571",  # 25.5 / 59.5 = 0.4285714…
        "units_sold": 4,
        "units_returned": 1,
        "orders": 2,
        "aov": "41.0000",  # 82 / 2
        "discount_rate": "0.057471",  # 5 / 87 = 0.0574712…
        "refund_rate": "0.274390",  # 22.5 / 82 = 0.2743902…
        "unit_return_rate": "0.250000",  # 1 / 4
        "active_customers": 2,
        "new_customers": 2,
        "repeat_customer_rate": "0.000000",
    }
    w2 = weeks["2026-01-12"]
    # Lines L4 (36 - 6) and L6 (13); L5 is cancelled and excluded. P3 (L6) has no cost.
    # Returns R2 (30.00, restock 3*4) and R3 (10.00 partial, restock 1*10): refunds 40.
    assert w2 == {
        "gross_sales": "49.0000",
        "discounts": "6.0000",
        "net_sales": "43.0000",
        "refunds": "40.0000",
        "net_revenue": "3.0000",
        "cogs_sold": None,  # L6 has no known cost → not estimated
        "cogs_restocked": "22.0000",
        "gross_profit": None,
        "gross_margin_pct": None,
        "units_sold": 5,
        "units_returned": 4,
        "orders": 2,
        "aov": "21.5000",
        "discount_rate": "0.122449",  # 6 / 49 = 0.1224489…
        "refund_rate": "0.930233",  # 40 / 43 = 0.9302325…
        "unit_return_rate": "0.800000",
        "active_customers": 2,
        "new_customers": 0,
        "repeat_customer_rate": "1.000000",  # both ordered before 2026-01-12
    }
    assert {"missing_costs", "cancelled_excluded"} <= codes(body)


async def test_period_totals_are_sums_not_averages(
    client: httpx.AsyncClient, reference: str
) -> None:
    body = (
        await client.get(f"{reference}/metrics", params={"from": "2026-01-05", "to": "2026-01-18"})
    ).json()
    total = body["data"][0]["metrics"]
    assert (total["gross_sales"], total["net_sales"], total["refunds"], total["net_revenue"]) == (
        "136.0000",
        "125.0000",
        "62.5000",
        "62.5000",
    )
    # Aggregate ratio: 62.5 / 125 = 0.5, not the mean of the weekly rates (0.274 and 0.930).
    assert total["refund_rate"] == "0.500000"
    assert total["aov"] == "31.2500"  # 125 / 4 orders
    assert total["active_customers"] == 2  # C3 only has a cancelled line
    assert total["gross_margin_pct"] is None


async def test_product_performance_and_return_rates(
    client: httpx.AsyncClient, reference: str
) -> None:
    body = (await client.get(f"{reference}/products")).json()
    products = {p["product_id"]: p for p in body["data"]}
    p1 = products["P1"]["metrics"]
    # P1: L1 + L3 (L5 cancelled). net sales 45 + 25 = 70; refunds 22.50 + 10 = 32.50;
    # COGS 30; restocked 10 (R3 only; R1 damaged) → gross profit 37.50 - 30 + 10 = 17.50.
    assert (p1["net_sales"], p1["refunds"], p1["net_revenue"], p1["gross_profit"]) == (
        "70.0000",
        "32.5000",
        "37.5000",
        "17.5000",
    )
    assert p1["gross_margin_pct"] == "0.466667"  # 17.5 / 37.5
    assert p1["unit_return_rate"] == "0.666667"  # 2 / 3
    assert p1["refund_rate"] == "0.464286"  # 32.5 / 70
    p2 = products["P2"]["metrics"]
    assert (p2["net_revenue"], p2["gross_profit"], p2["unit_return_rate"]) == (
        "12.0000",
        "8.0000",
        "0.750000",
    )  # 42 - 30; 12 - 16 + 12; 3 / 4
    p3 = products["P3"]["metrics"]
    assert (p3["net_revenue"], p3["cogs_sold"], p3["gross_margin_pct"]) == ("13.0000", None, None)
    assert [p["product_id"] for p in body["data"]] == ["P1", "P3", "P2"]  # by net revenue
    assert products["P1"]["product_name"] == "Lamp"
    # Matured cohort refund rate needs orders ≥ 14 days before the as-of date (2026-01-14).
    assert p1["matured_cohort_refund_rate"] is None
    assert "immature_returns" in codes(body)


async def test_category_dimension(client: httpx.AsyncClient, reference: str) -> None:
    body = (await client.get(f"{reference}/metrics", params={"dimension": "category"})).json()
    rows = {r["value"]: r["metrics"] for r in body["data"]}
    assert set(rows) == {"home", "fitness", "kitchen"}
    assert rows["home"]["net_revenue"] == "37.5000"
    assert all(r["dimension"] == "category" for r in body["data"])


async def test_equal_period_comparison(client: httpx.AsyncClient, reference: str) -> None:
    body = (
        await client.get(
            f"{reference}/metrics/comparison", params={"from": "2026-01-12", "to": "2026-01-18"}
        )
    ).json()
    assert body["period"]["previous"] == {
        "from": "2026-01-05",
        "to": "2026-01-11",
        "available": True,
    }
    row = body["data"][0]
    assert row["current"]["net_revenue"] == "3.0000"
    assert row["previous"]["net_revenue"] == "59.5000"
    # -56.50 / 59.50 = -0.9495798…
    assert row["change"]["net_revenue"] == {"abs": "-56.5000", "rel": "-0.949580"}
    assert row["change"]["orders"] == {"abs": 0, "rel": "0.000000"}
    assert row["change"]["gross_profit"] == {"abs": None, "rel": None}  # no cost for P3

    early = (
        await client.get(
            f"{reference}/metrics/comparison", params={"from": "2026-01-05", "to": "2026-01-11"}
        )
    ).json()
    assert early["period"]["previous"]["available"] is False
    assert early["data"][0]["previous"] is None
    assert "comparison_unavailable" in codes(early)


async def test_segments_and_customers(client: httpx.AsyncClient, reference: str) -> None:
    analysis = (await client.get(f"{reference}/analysis")).json()
    assert analysis["status"] == "insufficient_data"  # 2 weeks of data cannot support detection
    assert analysis["incidents"] == []
    assert {"insufficient_history", "low_sample", "missing_costs", "cancelled_excluded"} <= {
        r["code"] for r in analysis["restrictions"]
    }
    seg = (await client.get(f"{reference}/segments")).json()["data"]
    counts = {s["segment"]: s["customers"] for s in seg["segments"]}
    assert counts == {"loyal": 0, "at_risk": 0, "dormant": 0, "insufficient_history": 2}
    assert "churned" not in str(seg["rules"]).replace(
        "none of them asserts that a customer churned", ""
    )
    customers = (await client.get(f"{reference}/customers")).json()["items"]
    c1 = next(c for c in customers if c["customer_id"] == "C1")
    # C1: orders on 01-05 and 01-12; as-of (last completed order) 01-14 → recency 2, gap 7.
    assert (c1["lifetime_orders"], c1["recency_days"], c1["median_gap_days"]) == (2, 2, "7.0")
    # Net sales 45 + 12 + 30 = 87. Refunds count only up to the as-of date: R1 (22.50, 01-07)
    # yes, R2 (01-15) no → 87 - 22.50 = 64.50.
    assert c1["monetary_365"] == "64.5000"
    assert c1["segment"] == "insufficient_history"
    assert all(c["customer_id"] != "C3" for c in customers)  # cancelled-only customer


async def test_period_validation_and_states(
    app: FastAPI, client: httpx.AsyncClient, reference: str, owner: Tenant
) -> None:
    bad = await client.get(
        f"{reference}/metrics", params={"from": "2026-02-01", "to": "2026-01-01"}
    )
    assert bad.status_code == 422
    huge = await client.get(
        f"{reference}/metrics", params={"from": "2020-01-01", "to": "2026-01-01"}
    )
    assert huge.status_code == 422
    unknown = await client.get(f"{reference}/metrics", params={"dimension": "price"})
    assert unknown.status_code == 422
    outside = (
        await client.get(f"{reference}/metrics", params={"from": "2025-12-29", "to": "2026-01-25"})
    ).json()
    assert "incomplete_period" in codes(outside)
    segments = (
        await client.get(f"{reference}/metrics", params={"dimension": "customer_segment"})
    ).json()
    assert {r["value"] for r in segments["data"]} == {"insufficient_history"}


async def test_matured_cohort_refund_rate(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    detail = await ingest_files(app, client, owner.business_id, ref.matured_files())
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}"
    products = {
        p["product_id"]: p["metrics"] for p in (await client.get(f"{base}/products")).json()["data"]
    }
    assert products["P1"]["matured_cohort_refund_rate"] == "0.500000"  # 22.50 / 45
    assert products["P2"]["matured_cohort_refund_rate"] == "0.000000"  # L2 matured, not returned
    # The period view still counts both refunds of P1 (22.50 + 10.00) against its net sales 45.
    assert products["P1"]["refund_rate"] == "0.722222"


async def test_unknown_incident_and_failed_analysis_rerun(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import text

    from app.analytics import service
    from tests.helpers import csrf_headers, run_jobs

    async def crash(*_: object, **__: object) -> None:
        raise RuntimeError("simulated analysis crash")

    monkeypatch.setattr(service, "compute_analysis", crash)
    detail = await ingest_files(app, client, owner.business_id, ref.files())
    base = f"/api/v1/businesses/{owner.business_id}/datasets/{detail['id']}"
    failed = (await client.get(f"{base}/analysis")).json()
    assert (failed["status"], failed["error_code"]) == ("failed", "unexpected_error")
    assert (await client.get(f"{base}/segments")).status_code == 409  # nothing half-written

    monkeypatch.undo()
    missing = await client.get(f"{base}/incidents/00000000-0000-0000-0000-000000000001")
    assert missing.status_code == 404
    rerun = await client.post(f"{base}/analysis", headers=await csrf_headers(client))
    assert rerun.status_code == 202
    assert rerun.json() == {"id": failed["id"], "status": "queued"}
    await run_jobs(app)
    again = (await client.get(f"{base}/analysis")).json()
    assert again["status"] == "insufficient_data"
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM jobs WHERE type = 'analyze_dataset'")) == 2


@pytest.mark.parametrize(
    ("start", "end"),
    [("0001-01-01", "0001-01-01"), ("0001-01-04", "0001-01-10")],
)
async def test_comparison_rejects_previous_period_date_underflow(
    client: httpx.AsyncClient, reference: str, start: str, end: str
) -> None:
    response = await client.get(
        f"{reference}/metrics/comparison", params={"from": start, "to": end}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_period"
