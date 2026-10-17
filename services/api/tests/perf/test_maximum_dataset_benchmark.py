"""NFR-02 at the actual 500,000-line upload limit, through the public API.

Compact IDs and omitted optional CSV columns keep this realistic maximum-row fixture
inside the existing 25 MiB per-file contract. Fixture generation is outside timings;
request inspection/storage, validation/persistence and analysis remain inside them.
"""

import asyncio
import io
import json
import os
import resource
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.ingestion.contract import MAX_FILE_BYTES, MAX_UPLOAD_BYTES
from tests.helpers import create_tenant, login, run_jobs, upload
from tests.perf.test_analytics_benchmark import counting

# Any is restricted to JSON benchmark output assembled from SQL driver values.
pytestmark = pytest.mark.perf
LINES = 500_000
DAYS = 182


def maximum_files() -> dict[str, bytes]:
    dates = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(DAYS)]
    orders = io.StringIO()
    orders.write("order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price\n")
    returns = io.StringIO()
    returns.write(
        "return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code\n"
    )
    for i in range(LINES):
        day = i * DAYS // LINES
        product = i % 150
        # A measurable current-window decline exercises detection and its evidence writes.
        price = 10 if day >= DAYS - 28 else 20
        orders.write(f"{i},{i},{dates[day]},{i % 3000},{product},1,{price}\n")
        if i % 20 == 0:
            returned = dates[min(day + 2, DAYS - 1)]
            reason = "damaged" if i % 40 == 0 else "changed_mind"
            returns.write(f"{i},{i},{returned},1,{price},{reason}\n")
    products = "product_id,product_name,category,unit_cost,list_price\n" + "".join(
        f"{i},Item {i},category{i % 8},5,20\n" for i in range(150)
    )
    customers = "customer_id\n" + "".join(f"{i}\n" for i in range(3000))
    return {
        "orders": orders.getvalue().encode(),
        "returns": returns.getvalue().encode(),
        "products": products.encode(),
        "customers": customers.encode(),
    }


def append_result(path: str, line: str) -> None:
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


async def test_maximum_dataset_ingestion_and_analysis(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    files = maximum_files()
    assert all(len(content) <= MAX_FILE_BYTES for content in files.values())
    assert sum(map(len, files.values())) <= MAX_UPLOAD_BYTES
    tenant = create_tenant(owner_engine, "maximum500k")
    assert (await login(client, tenant.email)).status_code == 200
    with counting(app) as request:
        response = await upload(client, tenant.business_id, files)
    assert response.status_code == 202, response.text
    dataset_id = response.json()["id"]
    with counting(app) as ingest:
        assert await run_jobs(app, limit=1) == 1
    with owner_engine.connect() as conn:
        dataset = conn.execute(
            text("SELECT status, orders_rows, returns_rows FROM datasets WHERE id = :id"),
            {"id": dataset_id},
        ).one()
    assert dataset.status == "ready"
    assert dataset.orders_rows == LINES
    assert dataset.returns_rows == LINES // 20
    with counting(app) as analysis:
        assert await run_jobs(app, limit=1) == 1
    with owner_engine.connect() as conn:
        revision = conn.execute(
            text("SELECT status, summary FROM analysis_revisions WHERE dataset_id = :id"),
            {"id": dataset_id},
        ).one()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = peak_rss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    result: dict[str, Any] = {
        "case": "500000-lines",
        "order_lines": dataset.orders_rows,
        "returns": dataset.returns_rows,
        "file_bytes": {name: len(content) for name, content in files.items()},
        "upload_request": request,
        "ingestion_job": ingest,
        "ingestion_total_s": round(request["seconds"] + ingest["seconds"], 3),
        "analysis_job": analysis,
        "analysis_status": revision.status,
        "detection": revision.summary.get("detection"),
        "process_peak_rss_mb": round(rss_mb, 1),
    }
    line = json.dumps(result)
    print("\nMAXIMUM_DATASET_BENCHMARK " + line)  # noqa: T201 - benchmark evidence
    if output := os.environ.get("BENCHMARK_OUTPUT"):
        await asyncio.to_thread(append_result, output, line)
    assert revision.status == "completed"
    assert revision.summary["detection"]["flags_before_consolidation"] > 0
    assert request["seconds"] + ingest["seconds"] <= 120.0
    assert analysis["seconds"] <= 60.0
