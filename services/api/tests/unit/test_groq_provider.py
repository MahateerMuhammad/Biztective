"""Groq adapter (AMD-16): request shape, response mapping and typed failures, offline."""

import json
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.investigation.service import provider_from_settings
from app.llm.provider import GroqProvider, LlmRequest, ProviderError
from tests.conftest import make_settings

REQUEST = LlmRequest(
    "synthesize_findings", "system text", '{"input": {}}', {"type": "object"}, 2048, 5.0
)
COMPLETION = {
    "model": "openai/gpt-oss-120b",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"answer": "ok"}'},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30},
}


def adapter(handler: Any) -> GroqProvider:
    return GroqProvider(
        api_key="test-key",
        model="openai/gpt-oss-120b",
        effort="low",
        transport=httpx.MockTransport(handler),
    )


async def test_adapter_sends_schema_request_without_tools() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=COMPLETION)

    response = await adapter(handler).complete(REQUEST)
    assert response.text == '{"answer": "ok"}'
    assert (response.tokens_in, response.tokens_out) == (120, 30)
    assert response.model == "openai/gpt-oss-120b"
    request = seen[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": '{"input": {}}'},
    ]
    assert body["max_completion_tokens"] == 2048
    assert body["reasoning_effort"] == "low"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "synthesize_findings",
            "schema": {"type": "object"},
            "strict": False,
        },
    }
    assert "tools" not in body  # the model gets no tools of any kind
    assert "tool_choice" not in body


@pytest.mark.parametrize(
    ("status", "headers", "kind", "retry_after"),
    [
        (429, {"retry-after": "7"}, "rate_limited", 7.0),
        (500, {}, "unavailable", None),
        (503, {}, "unavailable", None),
        (400, {}, "invalid_request", None),
        (401, {}, "auth", None),
        (403, {}, "auth", None),
    ],
)
async def test_adapter_maps_http_errors(
    status: int, headers: dict[str, str], kind: str, retry_after: float | None
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "test"}}, headers=headers)

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == kind
    assert exc.value.retry_after == retry_after


@pytest.mark.parametrize(
    ("error", "kind"),
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "unavailable")],
)
async def test_adapter_maps_transport_errors(error: Exception, kind: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == kind


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"finish_reason": "length"}, "truncated"),
        ({"message": {"role": "assistant", "content": None, "refusal": "no"}}, "refused"),
    ],
)
async def test_adapter_maps_unusable_completions(change: dict[str, Any], kind: str) -> None:
    completion = json.loads(json.dumps(COMPLETION))
    completion["choices"][0].update(change)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion)

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == kind


async def test_malformed_body_is_a_provider_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == "unavailable"


def test_groq_requires_a_key_and_is_selected_from_settings() -> None:
    url = "postgresql+psycopg://u:p@localhost/db"
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        make_settings(url, llm_provider="groq", groq_api_key=None)
    settings: Settings = make_settings(url, llm_provider="groq", groq_api_key="test-key")
    provider = provider_from_settings(settings)
    assert isinstance(provider, GroqProvider)
    assert (provider.name, provider.model) == ("groq", "openai/gpt-oss-120b")
    assert provider_from_settings(make_settings(url)) is None  # disabled by default


@pytest.mark.parametrize(
    "change",
    [
        {"choices": [{"message": []}]},
        {"choices": [{"message": {"content": ["unexpected block"]}}]},
        {"choices": [{"message": {"content": "{}"}, "finish_reason": []}]},
        {"usage": None},
        {"usage": {}},
        {"usage": {"prompt_tokens": -1, "completion_tokens": 1}},
        {"usage": {"prompt_tokens": 1, "completion_tokens": -1}},
        {"usage": {"prompt_tokens": "unknown", "completion_tokens": 1}},
        {"usage": {"prompt_tokens": True, "completion_tokens": 1}},
        {"usage": {"prompt_tokens": 1.5, "completion_tokens": 1}},
    ],
)
async def test_malformed_completion_is_a_budgeted_provider_failure(change: dict[str, Any]) -> None:
    completion = {**COMPLETION, **change}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion)

    with pytest.raises(ProviderError) as exc:
        await adapter(handler).complete(REQUEST)
    assert exc.value.kind == "unavailable"
