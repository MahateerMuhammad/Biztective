"""Measure the real uploaded-data workflow with PostgreSQL checkpoints and evidence cache."""

import json
import time

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from tests.helpers import create_tenant, login
from tests.investigation.support import files_of, ingest, investigate, options, signature
from tests.llm_fakes import CooperativeModel, ScriptedProvider

pytestmark = pytest.mark.perf


async def test_investigation_benchmark(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "investigation_benchmark")
    await login(client, tenant.email)
    files, _ = files_of(options(7, "return_spike"))
    dataset_id = await ingest(app, client, tenant.business_id, files)
    started = time.perf_counter()
    first = await investigate(app, client, tenant.business_id, dataset_id)
    deterministic_seconds = time.perf_counter() - started
    provider = ScriptedProvider(CooperativeModel())
    started = time.perf_counter()
    cached = await investigate(app, client, tenant.business_id, dataset_id, provider)
    cached_seconds = time.perf_counter() - started
    assert first["status"] == "completed"
    assert cached["status"] == "completed"
    assert signature(first) == signature(cached)
    result = {
        "deterministic_seconds": round(deterministic_seconds, 3),
        "cached_mock_llm_seconds": round(cached_seconds, 3),
        "incidents": len(first["incidents"]),
        "evidence_items": sum(len(i["evidence"]) for i in first["incidents"]),
        "events": first["last_seq"],
        "mock_llm_calls": len(provider.requests),
    }
    print("\nINVESTIGATION_BENCHMARK " + json.dumps(result))  # noqa: T201
    assert deterministic_seconds <= 60  # NFR-08 investigation latency budget.
