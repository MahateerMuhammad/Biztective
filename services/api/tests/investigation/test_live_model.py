"""Opt-in real-provider gate; CI runs the scripted-provider suite instead.

Run ROOT_LIVE_LLM_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/investigation/test_live_model.py
with the normal disposable PostgreSQL test settings. No credentials are stored in artifacts.
"""

import os

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.llm.provider import AnthropicProvider
from tests.helpers import create_tenant, login
from tests.investigation.support import files_of, ingest, investigate, options, signature


@pytest.mark.skipif(
    os.environ.get("ROOT_LIVE_LLM_TESTS") != "1",
    reason="Real-provider integration requires explicit ROOT_LIVE_LLM_TESTS=1 opt-in.",
)
async def test_live_model_investigates_uploaded_data_with_identical_analytical_facts(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner_engine: Engine,
    settings: Settings,
) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    assert key, "The live-model opt-in requires ANTHROPIC_API_KEY."
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    files, _ = files_of(options(7, "return_spike"))
    dataset_id = await ingest(app, client, tenant.business_id, files)
    baseline = await investigate(app, client, tenant.business_id, dataset_id)
    provider = AnthropicProvider(
        api_key=key,
        model=os.environ.get("LLM_MODEL", settings.llm_model),
        effort=settings.llm_effort,
    )
    body = await investigate(app, client, tenant.business_id, dataset_id, provider)
    assert body["status"] == "completed"
    assert signature(body) == signature(baseline)
    with owner_engine.connect() as conn:
        accepted = conn.scalar(
            text(
                "SELECT count(*) FROM llm_calls WHERE investigation_id = :i "
                "AND provider = 'anthropic' AND validation IN ('accepted', 'repaired')"
            ),
            {"i": body["id"]},
        )
    assert accepted is not None
    assert accepted > 0, "Live provider must produce a validated response."
