"""LLM and tool failure modes: malformed output, unsupported claims, timeouts, rate limits,
provider outage, budget exhaustion, runaway tool loops, depth limits and tool failures. In
every case the investigation finishes with the same analytical facts as deterministic mode."""

import asyncio
import dataclasses
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.investigation import tools
from app.investigation.tools import ToolContext
from app.llm.provider import LlmRequest, ProviderError
from tests.helpers import Tenant, create_tenant, csrf_headers, login, run_jobs
from tests.investigation.support import (
    events,
    files_of,
    ingest,
    investigate,
    options,
    planted,
    signature,
)
from tests.llm_fakes import CooperativeModel, ScriptedProvider, envelope


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


@pytest.fixture
async def spike(app: FastAPI, client: httpx.AsyncClient, owner: Tenant) -> tuple[str, Any]:
    files, manifest = files_of(options(7, "return_spike"))
    return await ingest(app, client, owner.business_id, files), manifest


@pytest.fixture
def slept(app: FastAPI) -> Iterator[list[float]]:
    calls: list[float] = []

    async def sleep(seconds: float) -> None:
        calls.append(seconds)

    runtime = app.state.investigations
    runtime.sleep = sleep
    yield calls
    runtime.sleep = asyncio.sleep


@pytest.fixture
def limits(app: FastAPI) -> Iterator[Any]:
    """Temporarily override investigation settings: `limits(llm_max_calls=3)`."""
    runtime = app.state.investigations
    original = runtime.settings

    def apply(**values: Any) -> None:
        runtime.settings = original.model_copy(update=values)

    yield apply
    runtime.settings = original


async def deterministic_signature(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, dataset_id: str
) -> list[tuple[Any, ...]]:
    return signature(await investigate(app, client, owner.business_id, dataset_id))


def llm_rows(engine: Engine, investigation_id: str) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT task, attempt, validation, error_code FROM llm_calls "
                    "WHERE investigation_id = :i ORDER BY created_at, id"
                ),
                {"i": investigation_id},
            ).all()
        )


MALFORMED: dict[str, Callable[[LlmRequest], str | dict[str, Any]]] = {
    "not_json": lambda _: "Sure! Here is my analysis: the refund rate went up.",
    "wrong_schema": lambda _: {"ranking": "first", "calls": None, "summary": 3},
    "digits_and_unknown_evidence": lambda request: _with_digits(request),
}


def _with_digits(request: LlmRequest) -> dict[str, Any]:
    out = CooperativeModel()(request)
    unknown = "{{ev:00000000-0000-0000-0000-000000000000.impact}}"
    blob = (
        json.dumps(out)
        .replace("Overall the investigation found", f"Refunds rose 42% per {unknown}")
        .replace("Worth checking first given", "Up 3x given")
    )
    data: dict[str, Any] = json.loads(blob)
    return data


@pytest.mark.parametrize("kind", sorted(MALFORMED))
async def test_malformed_llm_output_is_repaired_or_replaced(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    kind: str,
) -> None:
    dataset_id, _ = spike
    expected = await deterministic_signature(app, client, owner, dataset_id)
    provider = ScriptedProvider(MALFORMED[kind])
    body = await investigate(app, client, owner.business_id, dataset_id, provider)

    assert body["status"] == "completed"
    assert signature(body) == expected
    rows = llm_rows(owner_engine, body["id"])
    invalid = [r for r in rows if r.validation == "invalid"]
    assert invalid
    timeline = await events(client, owner.business_id, body["id"])
    fallbacks = [e["payload"] for e in timeline if e["type"] == "llm.fallback_used"]
    assert fallbacks
    assert {f["reason"] for f in fallbacks} == {"validation"}
    if kind != "digits_and_unknown_evidence":
        assert body["findings"]["source"] == "deterministic"
        assert {h["source"] for i in body["incidents"] for h in i["hypotheses"]} == {"catalog"}
    else:
        errors = {r.error_code for r in invalid}
        assert errors & {"digit_outside_placeholder", "unknown_evidence"}
    # Each failing task was attempted exactly twice (the call and one repair), then replaced.
    assert len(invalid) == 2 * len(fallbacks)


async def test_unsupported_hypotheses_are_never_stated_as_causes(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
) -> None:
    dataset_id, _ = spike

    def overclaiming(request: LlmRequest) -> dict[str, Any]:
        out = CooperativeModel()(request)
        if request.task == "synthesize_findings":
            # Present every hypothesis, including refuted ones, as a root cause.
            context = envelope(request)["context"]
            out["root_causes"] = [
                {
                    "hypothesis_id": h["id"],
                    "statement": "This explains the change.",
                    "evidence_refs": [incident["evidence"][0]["id"]],
                }
                for incident in context["incidents"]
                for h in incident["hypotheses"]
            ][:5]
            out["alternatives_considered"] = []
        return out

    body = await investigate(
        app, client, owner.business_id, dataset_id, ScriptedProvider(overclaiming)
    )
    assert body["status"] == "completed"
    assert body["findings"]["source"] == "deterministic"
    rows = llm_rows(owner_engine, body["id"])
    synth = [r for r in rows if r.task == "synthesize_findings"]
    assert [r.validation for r in synth] == ["invalid", "invalid"]
    assert {r.error_code for r in synth} == {"unsupported_root_cause"}
    supported = {
        h["id"] for i in body["incidents"] for h in i["hypotheses"] if h["status"] == "supported"
    }
    stated = {c["hypothesis_id"] for c in body["findings"]["rendered"]["root_causes"]}
    assert stated
    assert stated <= supported


async def test_timeouts_and_rate_limits_are_retried(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    slept: list[float],
) -> None:
    dataset_id, _ = spike
    cooperative = CooperativeModel()
    failures = [ProviderError("timeout"), ProviderError("rate_limited", retry_after=2)]

    def flaky(request: LlmRequest) -> dict[str, Any] | ProviderError:
        return failures.pop(0) if failures else cooperative(request)

    body = await investigate(app, client, owner.business_id, dataset_id, ScriptedProvider(flaky))
    assert body["status"] == "completed"
    assert body["llm_mode"] == "llm"
    assert body["findings"]["source"] == "llm"
    rows = llm_rows(owner_engine, body["id"])
    assert [(r.validation, r.error_code) for r in rows[:3]] == [
        ("error", "timeout"),
        ("error", "rate_limited"),
        ("accepted", None),
    ]
    assert [r.attempt for r in rows[:3]] == [1, 2, 3]
    assert slept == [0.5, 2]


async def test_provider_outage_completes_in_deterministic_mode(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    slept: list[float],
) -> None:
    dataset_id, _ = spike
    expected = await deterministic_signature(app, client, owner, dataset_id)
    provider = ScriptedProvider(lambda _: ProviderError("unavailable"))
    body = await investigate(app, client, owner.business_id, dataset_id, provider)

    assert body["status"] == "completed"
    assert body["llm_mode"] == "deterministic"
    assert body["llm_disabled_reason"] == "provider_error"
    assert body["findings"]["source"] == "deterministic"
    assert signature(body) == expected
    assert len(provider.requests) == 3  # one task, two retries, then the circuit opened
    assert slept == [0.5, 1.0]
    timeline = await events(client, owner.business_id, body["id"])
    assert [e["type"] for e in timeline].count("llm.disabled") == 1


async def test_budget_exhaustion_switches_to_deterministic_mode(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    owner_engine: Engine,
    spike: tuple[str, Any],
    limits: Any,
) -> None:
    dataset_id, _ = spike
    limits(llm_max_calls=3)
    provider = ScriptedProvider(CooperativeModel())
    body = await investigate(app, client, owner.business_id, dataset_id, provider)
    assert body["status"] == "completed"
    assert body["llm_mode"] == "deterministic"
    assert body["llm_disabled_reason"] == "budget"
    assert len(llm_rows(owner_engine, body["id"])) == 3
    assert len(provider.requests) == 3
    timeline = await events(client, owner.business_id, body["id"])
    reasons = {e["payload"]["reason"] for e in timeline if e["type"] == "llm.fallback_used"}
    assert reasons == {"budget"}


async def test_token_budget(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    limits: Any,
) -> None:
    dataset_id, _ = spike
    limits(llm_max_tokens=1000)  # insufficient for the reserved input/output of even one call
    provider = ScriptedProvider(CooperativeModel())
    body = await investigate(app, client, owner.business_id, dataset_id, provider)
    assert body["status"] == "completed"
    assert body["llm_disabled_reason"] == "budget"
    assert len(provider.requests) == 0


def _flooding(request: LlmRequest) -> dict[str, Any]:
    """Always asks for five new, distinct tool calls and never says it is done."""
    if request.task != "select_tools":
        return CooperativeModel(extra_tools=False)(request)
    context = envelope(request)["context"]
    hypothesis = context["hypotheses"][0]["template"]
    combos = [
        (metric, by)
        for metric in ("net_revenue", "refund_rate", "discount_rate", "aov")
        for by in ("none", "category", "product", "region", "channel", "customer_segment")
    ]
    start = (context["round"] - 1) * 5
    return {
        "calls": [
            {
                "tool": "metric_breakdown",
                "arguments": {"metric": metric, "by": by},
                "hypothesis": hypothesis,
                "purpose": "More evidence.",
            }
            for metric, by in combos[start : start + 5]
        ],
        "done": False,
    }


async def test_excessive_tool_call_loops_are_bounded(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    limits: Any,
) -> None:
    dataset_id, _ = spike
    limits(investigation_llm_tools_per_incident=8, investigation_tool_rounds=2)
    provider = ScriptedProvider(_flooding)
    body = await investigate(app, client, owner.business_id, dataset_id, provider)
    assert body["status"] == "completed"
    investigated = [i for i in body["incidents"] if i["investigated"]]
    for incident in investigated:
        llm_evidence = [e for e in incident["evidence"] if e["requested_by"] == "llm"]
        assert len(llm_evidence) <= 8
    assert provider.tasks().count("select_tools") == 2 * len(investigated)  # never a third round
    timeline = await events(client, owner.business_id, body["id"])
    limited = [e for e in timeline if e["type"] == "tools.limit_reached"]
    assert len(limited) == len(investigated)
    assert all(e["payload"]["dropped"] == 2 for e in limited)


async def test_workflow_depth_limit_fails_safely_and_can_be_retried(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    limits: Any,
) -> None:
    dataset_id, _ = spike
    limits(investigation_recursion_limit=10)
    body = await investigate(app, client, owner.business_id, dataset_id)
    assert body["status"] == "failed"
    assert body["error_code"] == "workflow_limit"
    limits(investigation_recursion_limit=120)
    response = await client.post(
        f"/api/v1/businesses/{owner.business_id}/investigations/{body['id']}/retry",
        headers=await csrf_headers(client),
    )
    assert response.status_code == 202
    await run_jobs(app)
    timeline = await events(client, owner.business_id, body["id"])
    types = [e["type"] for e in timeline]
    assert "investigation.failed" in types
    assert "run.resumed" in types  # continued from the checkpoint, not from the start
    assert types.count("run.started") == 1
    assert types[-1] == "investigation.completed"


async def test_tool_failure_is_contained_and_reported(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, manifest = spike

    async def broken(ctx: ToolContext, params: Any) -> dict[str, Any]:
        raise RuntimeError("simulated tool defect")

    spec = tools.TOOLS["return_investigation"]
    monkeypatch.setitem(tools.TOOLS, "return_investigation", dataclasses.replace(spec, run=broken))
    body = await investigate(app, client, owner.business_id, dataset_id)

    assert body["status"] == "completed"
    incident = planted(body, manifest, "refund_rate")
    statuses = {h["template"]: h["status"] for h in incident["hypotheses"]}
    assert statuses["H_RETURNS_QUALITY"] == "inconclusive"  # unknown, never refuted
    assert statuses["H_FULFILLMENT"] == "inconclusive"
    assert {"template": "H_RETURNS_QUALITY", "criterion": "C1", "reason": "evidence_missing"} in (
        incident["data_gaps"]
    )
    timeline = await events(client, owner.business_id, body["id"])
    failed = [e["payload"] for e in timeline if e["type"] == "tool.failed"]
    assert failed
    assert {f["tool"] for f in failed} == {"return_investigation"}
    assert {f["error"] for f in failed} == {"RuntimeError"}
    caveats = [
        "".join(s.get("text", "") for s in c) for c in body["findings"]["rendered"]["caveats"]
    ]
    assert any("evidence was unavailable" in c for c in caveats)


async def test_impossible_tool_result_is_rejected_by_the_critic(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner: Tenant,
    spike: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, manifest = spike
    spec = tools.TOOLS["customer_exposure"]
    original = spec.run
    assert original is not None

    async def inflated(ctx: ToolContext, params: Any) -> dict[str, Any]:
        result = await original(ctx, params)
        result["facts"]["top_k.share"] = {"value": "1.7", "unit": "ratio"}  # > 100%
        return result

    monkeypatch.setitem(tools.TOOLS, "customer_exposure", dataclasses.replace(spec, run=inflated))
    body = await investigate(app, client, owner.business_id, dataset_id)
    assert body["status"] == "completed"
    timeline = await events(client, owner.business_id, body["id"])
    rejected = [e["payload"] for e in timeline if e["type"] == "tool.rejected"]
    assert rejected
    assert all("share_out_of_range: top_k.share" in r["problems"] for r in rejected)
    kinds = {e["kind"] for i in body["incidents"] for e in i["evidence"]}
    assert "customer_exposure" not in kinds  # never stored, never cited
    incident = planted(body, manifest, "refund_rate")
    concentration = next(h for h in incident["hypotheses"] if h["template"] == "H_CONCENTRATION")
    assert concentration["status"] == "inconclusive"
