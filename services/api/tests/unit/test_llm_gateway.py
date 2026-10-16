"""LLM gateway (budgets, retries, timeouts, validation, repair, fallback) and the Anthropic
adapter's error mapping, against a mocked HTTP transport. No network."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.llm.gateway import Accepted, CallRecord, Fallback, Gateway, Task
from app.llm.provider import (
    AnthropicProvider,
    LlmRequest,
    LlmResponse,
    ProviderError,
    ProviderErrorKind,
)
from tests.conftest import make_settings
from tests.llm_fakes import ScriptedProvider


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: Annotated[str, Field(max_length=20)]


TASK = Task("prioritize_hypotheses", 1, Out)  # reuses a real prompt file


class MemoryRecorder:
    def __init__(self, calls: int = 0, tokens: int = 0) -> None:
        self.calls: list[CallRecord] = []
        self.base = (calls, tokens)

    async def usage(self) -> tuple[int, int]:
        spent = sum(c.tokens_in + c.tokens_out for c in self.calls)
        return self.base[0] + len(self.calls), self.base[1] + spent

    async def record(self, call: CallRecord) -> uuid.UUID:
        self.calls.append(call)
        return uuid.uuid4()


def gateway(provider: ScriptedProvider | None, **settings: Any) -> tuple[Gateway, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    config = make_settings("postgresql+psycopg://u:p@127.0.0.1:1/x", **settings)
    return Gateway(provider, config, sleep=sleep), slept


def no_errors(_: Out) -> list[str]:
    return []


def sequence(*answers: dict[str, Any] | str | ProviderError) -> ScriptedProvider:
    remaining = list(answers)
    return ScriptedProvider(lambda _: remaining.pop(0))


async def test_disabled_without_provider() -> None:
    gw, _ = gateway(None)
    recorder = MemoryRecorder()
    assert await gw.generate(TASK, {}, no_errors, recorder, None) == Fallback("disabled")
    assert recorder.calls == []


async def test_accepted_on_first_attempt() -> None:
    provider = sequence({"answer": "ok"})
    gw, _ = gateway(provider)
    recorder = MemoryRecorder()
    result = await gw.generate(TASK, {"x": 1}, no_errors, recorder, None)
    assert isinstance(result, Accepted)
    assert result.value.answer == "ok"
    assert not result.repaired
    [call] = recorder.calls
    assert (call.validation, call.attempt, call.task) == ("accepted", 1, TASK.name)
    assert call.prompt_version == "prioritize_hypotheses.v1"
    assert call.input == {"x": 1}
    request = provider.requests[0]
    assert request.schema["additionalProperties"] is False
    assert "maxLength" not in json.dumps(request.schema)
    assert request.system.startswith("You help a small-business analytics system")
    assert json.loads(request.user) == {"input": {"x": 1}}


@pytest.mark.parametrize(
    "broken",
    [
        "not json at all",
        json.dumps({"answer": "ok", "extra": True}),  # schema: extra field
        json.dumps({"answer": 5}),  # schema: strict type
        json.dumps({"answer": "x" * 50}),  # constraint the provider schema cannot carry
        "",
    ],
)
async def test_malformed_output_is_repaired_once(broken: str) -> None:
    provider = sequence(broken, {"answer": "fixed"})
    gw, _ = gateway(provider)
    recorder = MemoryRecorder()
    result = await gw.generate(TASK, {}, no_errors, recorder, None)
    assert isinstance(result, Accepted)
    assert result.repaired
    assert [c.validation for c in recorder.calls] == ["invalid", "repaired"]
    repair = json.loads(provider.requests[1].user)["repair"]
    assert repair["errors"]
    assert repair["previous_output"] == broken


async def test_semantic_rejection_twice_falls_back() -> None:
    provider = sequence({"answer": "a"}, {"answer": "b"})
    gw, _ = gateway(provider)
    recorder = MemoryRecorder()

    def reject(_: Out) -> list[str]:
        return ["digit_outside_placeholder: summary contains a digit outside placeholders"]

    result = await gw.generate(TASK, {}, reject, recorder, None)
    assert result == Fallback(
        "validation", "digit_outside_placeholder: summary contains a digit outside placeholders"
    )
    assert [c.validation for c in recorder.calls] == ["invalid", "invalid"]
    assert recorder.calls[0].error_code == "digit_outside_placeholder"


async def test_timeout_is_retried_with_backoff() -> None:
    provider = sequence(ProviderError("timeout"), {"answer": "ok"})
    gw, slept = gateway(provider)
    recorder = MemoryRecorder()
    result = await gw.generate(TASK, {}, no_errors, recorder, None)
    assert isinstance(result, Accepted)
    assert [(c.validation, c.error_code) for c in recorder.calls] == [
        ("error", "timeout"),
        ("accepted", None),
    ]
    assert slept == [0.5]


async def test_rate_limit_honours_retry_after_with_a_cap() -> None:
    provider = sequence(
        ProviderError("rate_limited", retry_after=3),
        ProviderError("rate_limited", retry_after=120),
        {"answer": "ok"},
    )
    gw, slept = gateway(provider)
    result = await gw.generate(TASK, {}, no_errors, MemoryRecorder(), None)
    assert isinstance(result, Accepted)
    assert slept == [3, 10.0]


async def test_persistent_unavailability_falls_back_after_retries() -> None:
    provider = ScriptedProvider(lambda _: ProviderError("unavailable"))
    gw, slept = gateway(provider, llm_max_retries=2)
    recorder = MemoryRecorder()
    result = await gw.generate(TASK, {}, no_errors, recorder, None)
    assert result == Fallback("provider_error", "unavailable")
    assert len(provider.requests) == 3
    assert [c.error_code for c in recorder.calls] == ["unavailable"] * 3
    assert slept == [0.5, 1.0]


@pytest.mark.parametrize("kind", ["refused", "truncated", "invalid_request", "auth"])
async def test_non_retryable_errors_fall_back_immediately(kind: ProviderErrorKind) -> None:
    provider = ScriptedProvider(lambda _: ProviderError(kind))
    gw, slept = gateway(provider)
    result = await gw.generate(TASK, {}, no_errors, MemoryRecorder(), None)
    assert result == Fallback("provider_error", kind)
    assert len(provider.requests) == 1
    assert slept == []


async def test_a_hanging_provider_is_cut_off_by_the_timeout() -> None:
    class Hanging(ScriptedProvider):
        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.requests.append(request)
            await asyncio.sleep(10)
            raise AssertionError("hanging provider must be cancelled")

    provider = Hanging(lambda _: {})
    gw, _ = gateway(provider, llm_timeout_seconds=0.01, llm_max_retries=1)
    recorder = MemoryRecorder()
    result = await gw.generate(TASK, {}, no_errors, recorder, None)
    assert result == Fallback("provider_error", "timeout")
    assert [c.error_code for c in recorder.calls] == ["timeout", "timeout"]


async def test_call_budget_is_enforced_before_calling() -> None:
    provider = sequence({"answer": "ok"})
    gw, _ = gateway(provider, llm_max_calls=5)
    result = await gw.generate(TASK, {}, no_errors, MemoryRecorder(calls=5), None)
    assert isinstance(result, Fallback)
    assert result.reason == "budget"
    assert provider.requests == []


async def test_token_budget_is_enforced() -> None:
    gw, _ = gateway(sequence(), llm_max_tokens=1000)
    result = await gw.generate(TASK, {}, no_errors, MemoryRecorder(tokens=1000), None)
    assert isinstance(result, Fallback)
    assert result.reason == "budget"


async def test_budget_reached_between_retries() -> None:
    provider = ScriptedProvider(lambda _: ProviderError("unavailable"))
    gw, _ = gateway(provider, llm_max_calls=2, llm_max_retries=5)
    result = await gw.generate(TASK, {}, no_errors, MemoryRecorder(), None)
    assert isinstance(result, Fallback)
    assert result.reason == "budget"
    assert len(provider.requests) == 2


async def test_deadline() -> None:
    provider = sequence({"answer": "ok"})
    gw, _ = gateway(provider)
    past = datetime.now(UTC) - timedelta(seconds=1)
    assert await gw.generate(TASK, {}, no_errors, MemoryRecorder(), past) == Fallback("deadline")
    assert provider.requests == []


# ------------------------------------------------------------ Anthropic adapter (mocked HTTP)

MESSAGE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": '{"answer": "ok"}'}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 5},
}
REQUEST = LlmRequest("t", "system text", '{"input": {}}', {"type": "object"}, 2048, 5.0)


def adapter(handler: Any) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="test-key", model="claude-opus-5-5", effort="low")
    provider._client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        open_telemetry=False,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return provider


async def test_adapter_sends_structured_output_request() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=MESSAGE)

    response = await adapter(handler).complete(REQUEST)
    assert response.text == '{"answer": "ok"}'
    assert (response.tokens_in, response.tokens_out) == (125, 30)
    body = seen[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "system text"
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    assert body["max_tokens"] == 2048
    assert "temperature" not in body
    assert "tools" not in body  # the model gets no tools of any kind


def _error(status: int, kind: str, headers: dict[str, str] | None = None) -> Any:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            json={"type": "error", "error": {"type": kind, "message": "test"}},
            headers=headers or {},
        )

    return handler


@pytest.mark.parametrize(
    ("handler", "kind", "retry_after"),
    [
        (_error(429, "rate_limit_error", {"retry-after": "7"}), "rate_limited", 7.0),
        (_error(500, "api_error"), "unavailable", None),
        (_error(529, "overloaded_error"), "unavailable", None),
        (_error(400, "invalid_request_error"), "invalid_request", None),
        (_error(401, "authentication_error"), "auth", None),
        (_error(403, "permission_error"), "auth", None),
    ],
)
async def test_adapter_maps_http_errors(handler: Any, kind: str, retry_after: float | None) -> None:
    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == kind
    assert exc.value.retry_after == retry_after


@pytest.mark.parametrize(
    ("error", "kind"),
    [(httpx2.ConnectError("down"), "unavailable"), (httpx2.ReadTimeout("slow"), "timeout")],
)
async def test_adapter_maps_transport_errors(error: Exception, kind: str) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == kind


@pytest.mark.parametrize(
    ("stop_reason", "kind"), [("refusal", "refused"), ("max_tokens", "truncated")]
)
async def test_adapter_rejects_refusals_and_truncation(stop_reason: str, kind: str) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={**MESSAGE, "stop_reason": stop_reason})

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == kind


async def test_investigation_deadline_bounds_an_inflight_call() -> None:
    class Hanging(ScriptedProvider):
        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.requests.append(request)
            await asyncio.sleep(10)
            raise AssertionError("call must be cancelled at investigation deadline")

    provider = Hanging(lambda _: {})
    gw, slept = gateway(provider, llm_timeout_seconds=60)
    recorder = MemoryRecorder()
    deadline = datetime.now(UTC) + timedelta(milliseconds=20)
    result = await asyncio.wait_for(gw.generate(TASK, {}, no_errors, recorder, deadline), timeout=1)
    assert result == Fallback("deadline")
    assert len(provider.requests) == 1
    assert provider.requests[0].timeout_seconds <= 0.02
    assert recorder.calls[0].error_code == "timeout"
    assert slept == []


async def test_retry_delay_cannot_exceed_investigation_deadline() -> None:
    provider = ScriptedProvider(lambda _: ProviderError("rate_limited", retry_after=120))
    gw, slept = gateway(provider)
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    assert await gw.generate(TASK, {}, no_errors, MemoryRecorder(), deadline) == Fallback(
        "deadline"
    )
    assert len(provider.requests) == 1
    assert slept == []


async def test_output_allowance_is_limited_by_remaining_token_budget() -> None:
    provider = sequence({"answer": "ok"})
    gw, _ = gateway(provider, llm_max_tokens=5000)
    result = await gw.generate(TASK, {}, no_errors, MemoryRecorder(), None)
    assert isinstance(result, Accepted)
    assert 0 < provider.requests[0].max_output_tokens < 5000


async def test_unknown_failed_call_usage_is_charged_conservatively() -> None:
    provider = ScriptedProvider(lambda _: ProviderError("timeout"))
    gw, _ = gateway(provider, llm_max_tokens=5000)
    recorder = MemoryRecorder()
    result = await gw.generate(TASK, {}, no_errors, recorder, None)
    assert isinstance(result, Fallback)
    assert result.reason == "budget"
    assert len(provider.requests) == 1
    assert sum((call.tokens_in + call.tokens_out) for call in recorder.calls) == 5000


def test_adapter_explicitly_disables_provider_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    supplied: list[dict[str, Any]] = []
    original = anthropic.AsyncAnthropic

    def record_constructor(**kwargs: Any) -> anthropic.AsyncAnthropic:
        supplied.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", record_constructor)
    AnthropicProvider(api_key="test-key", model="test-model", effort="low")
    assert supplied[0]["open_telemetry"] is False
    assert supplied[0]["max_retries"] == 0
