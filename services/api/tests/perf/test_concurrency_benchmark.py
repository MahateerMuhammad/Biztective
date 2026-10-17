"""Bounded authenticated API load on the real PostgreSQL tenant context."""

import asyncio
import json
import resource
import time
from contextlib import AsyncExitStack

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from tests.helpers import create_dataset, create_tenant, login

pytestmark = pytest.mark.perf


async def test_authenticated_concurrent_reads(app: FastAPI, owner_engine: Engine) -> None:
    clients: list[httpx.AsyncClient] = []
    paths: list[list[str]] = []
    semaphore = asyncio.Semaphore(20)

    async def read(index: int) -> float:
        async with semaphore:
            started = time.perf_counter()
            user = index % len(clients)
            response = await clients[user].get(paths[user][index % 3])
            assert response.status_code == 200, response.text
            return time.perf_counter() - started

    async with AsyncExitStack() as stack:
        for index in range(20):
            tenant = create_tenant(owner_engine, f"concurrent_reads_{index}")
            create_dataset(owner_engine, tenant.business_id, tenant.user_id, 1)
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=app, client=(f"203.0.113.{index + 1}", 50000)
                    ),
                    base_url="http://testserver",
                )
            )
            assert (await login(client, tenant.email)).status_code == 200
            clients.append(client)
            paths.append(
                [
                    "/api/v1/auth/me",
                    f"/api/v1/businesses/{tenant.business_id}",
                    f"/api/v1/businesses/{tenant.business_id}/datasets",
                ]
            )
        started = time.perf_counter()
        durations = sorted(await asyncio.gather(*(read(i) for i in range(60))))
        elapsed = time.perf_counter() - started
    p95 = durations[int(len(durations) * 0.95)]
    print(  # noqa: T201
        "\nCONCURRENCY_BENCHMARK "
        + json.dumps(
            {
                "requests": len(durations),
                "concurrency": 20,
                "distinct_users": 20,
                "seconds": round(elapsed, 3),
                "requests_per_second": round(len(durations) / elapsed, 1),
                "p95_seconds": round(p95, 3),
                "process_peak_rss_platform_units": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
            }
        )
    )
    assert p95 <= 0.3, "NFR-02: authenticated read p95 exceeds 300ms at concurrency 20"
