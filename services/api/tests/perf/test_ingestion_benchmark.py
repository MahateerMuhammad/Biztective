"""Ingestion benchmark (NFR-02). Run explicitly: `pytest -m perf -s`.

Measures, on seeded synthetic data uploaded through the real API: request latency (upload,
inspection, storage), job latency (parse → stage → validate → persist), Python peak traced
allocation during the job, and process peak RSS. Results are printed and, if BENCHMARK_OUTPUT
is set, appended as JSON lines. Thresholds are the NFR-02 budgets, not tuned to the results.
"""

import asyncio
import json
import os
import resource
import sys
import time
import tracemalloc
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from scripts.generate_dataset import Options, generate
from tests.helpers import Tenant, create_tenant, login, run_jobs, upload

pytestmark = pytest.mark.perf


def _append_line(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes vs KiB


async def _ingest(
    app: FastAPI,
    client: httpx.AsyncClient,
    tenant: Tenant,
    seed: int,
    orders_per_day: int,
    *,
    traced: bool,
) -> dict[str, Any]:
    files = generate(
        Options(
            seed=seed,
            weeks=26,
            customers=3000,
            products=150,
            orders_per_day=orders_per_day,
            start=date(2026, 1, 5),
            noise=0.0,
            pii_column=False,
        )
    )
    payload = {name.removesuffix(".csv"): content for name, content in files.items()}
    started = time.perf_counter()
    response = await upload(client, tenant.business_id, payload)
    upload_seconds = time.perf_counter() - started
    assert response.status_code == 202, response.text
    if traced:
        tracemalloc.start()
    started = time.perf_counter()
    assert await run_jobs(app) == 1
    job_seconds = time.perf_counter() - started
    traced_peak = tracemalloc.get_traced_memory()[1] if traced else 0
    if traced:
        tracemalloc.stop()
    return {
        "id": response.json()["id"],
        "upload_bytes": sum(len(c) for c in files.values()),
        "upload_s": upload_seconds,
        "job_s": job_seconds,
        "traced_peak": traced_peak,
    }


@pytest.mark.parametrize(
    ("orders_per_day", "budget_seconds"),
    [(52, 30.0), (260, 30.0)],
    ids=["~20k-lines", "~100k-lines"],
)
async def test_ingestion_benchmark(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner_engine: Engine,
    orders_per_day: int,
    budget_seconds: float,
) -> None:
    tenant = create_tenant(owner_engine, f"bench{orders_per_day}")
    await login(client, tenant.email)
    rss_before = _peak_rss_mb()
    timed = await _ingest(app, client, tenant, 2026, orders_per_day, traced=False)
    rss_after_timed = _peak_rss_mb()
    traced = await _ingest(app, client, tenant, 2027, orders_per_day, traced=True)

    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, orders_rows, products_rows, customers_rows, returns_rows "
                "FROM datasets WHERE id = :id"
            ),
            {"id": timed["id"]},
        ).one()
    result: dict[str, Any] = {
        "case": f"orders_per_day={orders_per_day}",
        "status": row.status,
        "order_lines": row.orders_rows,
        "returns": row.returns_rows,
        "customers": row.customers_rows,
        "products": row.products_rows,
        "upload_mb": round(timed["upload_bytes"] / (1024 * 1024), 2),
        "upload_request_s": round(timed["upload_s"], 3),
        "ingest_job_s": round(timed["job_s"], 3),
        "total_s": round(timed["upload_s"] + timed["job_s"], 3),
        "order_lines_per_s": round(row.orders_rows / timed["job_s"]),
        "job_python_peak_traced_mb": round(traced["traced_peak"] / (1024 * 1024), 1),
        "process_peak_rss_mb_before": round(rss_before, 1),
        "process_peak_rss_mb_after_timed_run": round(rss_after_timed, 1),
    }
    print("\nBENCHMARK " + json.dumps(result))  # noqa: T201 - benchmark report
    if output := os.environ.get("BENCHMARK_OUTPUT"):
        await asyncio.to_thread(_append_line, output, json.dumps(result))
    assert row.status == "ready"
    assert timed["upload_s"] + timed["job_s"] < budget_seconds
