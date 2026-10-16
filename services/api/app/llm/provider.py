"""LLM provider boundary (AMD-12): one request shape, one response shape, typed failures.

The Anthropic adapter is the only code that talks to a model. Tests replace it with a scripted
fake that implements the same protocol; nothing else in the application knows which is used.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

# Any is restricted to heterogeneous JSON payloads at validated schema/provider boundaries.
import anthropic

ProviderErrorKind = Literal[
    "timeout", "rate_limited", "unavailable", "refused", "truncated", "invalid_request", "auth"
]
RETRYABLE: frozenset[ProviderErrorKind] = frozenset({"timeout", "rate_limited", "unavailable"})


@dataclass(frozen=True)
class LlmRequest:
    task: str
    system: str
    user: str  # canonical JSON of the input envelope (plus repair notes, if any)
    schema: dict[str, Any]  # provider-safe JSON schema of the expected output
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class LlmResponse:
    text: str | None
    model: str
    stop_reason: str | None
    tokens_in: int
    tokens_out: int


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, retry_after: float | None = None) -> None:
        super().__init__(kind)
        self.kind: ProviderErrorKind = kind
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


class LlmProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: LlmRequest) -> LlmResponse: ...


def _retry_after(headers: Any) -> float | None:
    try:
        value = headers.get("retry-after")
        return float(value) if value is not None else None
    except TypeError, ValueError, AttributeError:
        return None


class AnthropicProvider:
    """Claude through the official SDK with JSON-schema structured output.

    SDK retries are disabled: the gateway owns retries so every attempt is budgeted and
    recorded. Thinking is always on for Opus 5.5; `effort` bounds its depth."""

    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, effort: Literal["low", "medium", "high"]):
        self.model = model
        self._effort: Literal["low", "medium", "high"] = effort
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, max_retries=0, open_telemetry=False
        )

    async def complete(self, request: LlmRequest) -> LlmResponse:
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=request.max_output_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": request.schema},
                },
                timeout=request.timeout_seconds,
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderError("timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("unavailable") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("rate_limited", _retry_after(exc.response.headers)) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderError("auth") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderError("unavailable", _retry_after(exc.response.headers)) from exc
            raise ProviderError("invalid_request") from exc

        if message.stop_reason == "refusal":
            raise ProviderError("refused")
        if message.stop_reason == "max_tokens":
            raise ProviderError("truncated")
        text = next((block.text for block in message.content if block.type == "text"), None)
        usage = message.usage
        tokens_in = (
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
        )
        return LlmResponse(
            text=text,
            model=message.model,
            stop_reason=message.stop_reason,
            tokens_in=tokens_in,
            tokens_out=usage.output_tokens,
        )
