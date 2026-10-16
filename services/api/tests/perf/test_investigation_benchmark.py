"""Measure the real uploaded-data workflow with PostgreSQL checkpoints and evidence cache."""

import json
import time

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from tests.helpers import create_tenant, login
from tests.investigation.support import (
    files_of,
    finish,
    ingest,
    investigate,
    options,
    respond,
    signature,
)
from tests.llm_fakes import CooperativeModel, ScriptedProvider

pytestmark = pytest.mark.perf


async def test_investigation_benchmark(
    app: FastAPI, client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    """NFR-08: start to the first clarification, then answer to the next stable point, then the
    same revision again with cached evidence and a scripted model."""
    tenant = create_tenant(owner_engine, "investigation_benchmark")
    await login(client, tenant.email)
    files, _ = files_of(options(7, "discount_spike"))  # needs business context: it pauses
    dataset_id = await ingest(app, client, tenant.business_id, files)
    started = time.perf_counter()
    first = await investigate(app, client, tenant.business_id, dataset_id)
    first_question_seconds = time.perf_counter() - started
    assert first["status"] == "awaiting_human"
    started = time.perf_counter()
    answered = await respond(app, client, tenant.business_id, first, option="unsure")
    answer_seconds = time.perf_counter() - started
    assert answered["version"] == 2
    finished = await finish(app, client, tenant.business_id, answered)
    assert finished["status"] == "completed"
    provider = ScriptedProvider(CooperativeModel())
    started = time.perf_counter()
    cached = await investigate(app, client, tenant.business_id, dataset_id, provider)
    cached_seconds = time.perf_counter() - started
    assert cached["status"] == "awaiting_human"
    assert cached["id"] != first["id"]
    assert signature(first) == signature(cached)  # version 1 of both: identical analysis
    result = {
        "first_question_seconds": round(first_question_seconds, 3),
        "answer_to_next_question_seconds": round(answer_seconds, 3),
        "cached_mock_llm_first_question_seconds": round(cached_seconds, 3),
        "questions_until_completed": finished["version"] - 1,
        "incidents": len(first["incidents"]),
        "evidence_items": sum(len(i["evidence"]) for i in first["incidents"]),
        "events_at_first_question": first["last_seq"],
        "mock_llm_calls": len(provider.requests),
    }
    print("\nINVESTIGATION_BENCHMARK " + json.dumps(result))  # noqa: T201
    assert first_question_seconds <= 60  # NFR-08: start to first clarification.
