"""Opt-in real-provider gate; CI runs the scripted-provider suite instead.

Run ROOT_LIVE_LLM_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/investigation/test_live_model.py
(or ROOT_LIVE_LLM_PROVIDER=groq with GROQ_API_KEY, AMD-16) with the normal disposable PostgreSQL
test settings. No credentials are stored in artifacts.
"""

import os

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.core.config import Settings
from app.llm.provider import AnthropicProvider, GroqProvider, LlmProvider
from tests.helpers import create_tenant, login
from tests.investigation.support import files_of, finish, ingest, investigate, options, signature


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
    name = os.environ.get("ROOT_LIVE_LLM_PROVIDER", "anthropic")
    variable = {"anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY"}[name]
    key = os.environ.get(variable)
    assert key, f"The live-model opt-in requires {variable}."
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    files, _ = files_of(options(7, "return_spike"))
    dataset_id = await ingest(app, client, tenant.business_id, files)
    baseline = await finish(
        app,
        client,
        tenant.business_id,
        await investigate(app, client, tenant.business_id, dataset_id),
    )
    provider: LlmProvider
    if name == "groq":
        model = os.environ.get("GROQ_MODEL", settings.groq_model)
        provider = GroqProvider(api_key=key, model=model, effort=settings.llm_effort)
    else:
        model = os.environ.get("LLM_MODEL", settings.llm_model)
        provider = AnthropicProvider(api_key=key, model=model, effort=settings.llm_effort)
    body = await investigate(app, client, tenant.business_id, dataset_id, provider)
    app.state.investigations.provider = provider  # the same model words the final findings
    try:
        body = await finish(app, client, tenant.business_id, body)
    finally:
        app.state.investigations.provider = None
    assert body["status"] == "completed"
    assert signature(body) == signature(baseline)
    with owner_engine.connect() as conn:
        accepted = conn.scalar(
            text(
                "SELECT count(*) FROM llm_calls WHERE investigation_id = :i "
                "AND provider = :p AND validation IN ('accepted', 'repaired')"
            ),
            {"i": body["id"], "p": name},
        )
    assert accepted is not None
    assert accepted > 0, "Live provider must produce a validated response."
