"""Analytics benchmark: analysis-job latency and representative API queries, with statement
counts. Run explicitly: `pytest -m perf -s`. Results printed and appended to BENCHMARK_OUTPUT."""

import asyncio
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event

from scripts.generate_dataset import Options, generate
from tests.helpers import create_tenant, login, run_jobs, upload

pytestmark = pytest.mark.perf


@contextmanager
def counting(app: FastAPI) -> Iterator[dict[str, float]]:
    engine = app.state.sessionmaker.kw["bind"].sync_engine
    stats = {"statements": 0, "seconds": 0.0}

    def count(*_: Any) -> None:
        stats["statements"] += 1

    event.listen(engine, "before_cursor_execute", count)
    started = time.perf_counter()
    try:
        yield stats
    finally:
        stats["seconds"] = round(time.perf_counter() - started, 3)
        event.remove(engine, "before_cursor_execute", count)


def _append(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


@pytest.mark.parametrize("orders_per_day", [52, 260], ids=["~20k-lines", "~100k-lines"])
async def test_analytics_benchmark(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine, orders_per_day: int
) -> None:
    files = generate(
        Options(
            seed=2026,
            weeks=26,
            customers=3000,
            products=150,
            orders_per_day=orders_per_day,
            start=date(2026, 1, 5),
            noise=0.0,
            pii_column=False,
        )
    )
    tenant = create_tenant(owner_engine, f"abench{orders_per_day}")
    await login(client, tenant.email)
    response = await upload(
        client, tenant.business_id, {k.removesuffix(".csv"): v for k, v in files.items()}
    )
    await run_jobs(app, limit=1)  # ingestion (queues the analysis)
    with counting(app) as analysis:
        assert await run_jobs(app, limit=1) == 1
    base = f"/api/v1/businesses/{tenant.business_id}/datasets/{response.json()['id']}"
    results: dict[str, Any] = {"case": f"orders_per_day={orders_per_day}", "analysis_job": analysis}
    for name, path, params in (
        ("metrics_total", "/metrics", {}),
        ("metrics_weekly", "/metrics", {"granularity": "week"}),
        (
            "metrics_by_category_weekly",
            "/metrics",
            {"granularity": "week", "dimension": "category"},
        ),
        ("metrics_by_segment", "/metrics", {"dimension": "customer_segment"}),
        ("comparison_by_product", "/metrics/comparison", {"dimension": "product"}),
        ("products", "/products", {}),
        ("segments", "/segments", {}),
        ("analysis", "/analysis", {}),
    ):
        with counting(app) as stats:
            r = await client.get(base + path, params=params)
        assert r.status_code == 200, (name, r.text[:300])
        results[name] = stats
    analysis_body = (await client.get(base + "/analysis")).json()
    results["order_lines"] = analysis_body["summary"]["detection"]
    print("\nANALYTICS_BENCHMARK " + json.dumps(results))  # noqa: T201
    if output := os.environ.get("BENCHMARK_OUTPUT"):
        await asyncio.to_thread(_append, output, json.dumps(results))
    assert analysis_body["status"] == "completed"
    assert analysis["seconds"] < 30
    if orders_per_day == 260:
        assert analysis["seconds"] <= 15  # NFR-02: the 100k analysis budget is stricter.
