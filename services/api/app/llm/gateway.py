"""LLM gateway (ARCHITECTURE §8, AMD-12): budgets, retries, validation, repair, fallback.

Every attempt is recorded (`llm_calls`) through the caller's recorder before anything else
happens, so budgets survive restarts. The gateway never raises for provider or validation
problems: it returns either an accepted, fully validated value or a `Fallback` with a reason,
and the caller then runs its deterministic path.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal, Protocol

# Any is restricted to heterogeneous JSON payloads at validated schema/provider boundaries.
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.analytics.evidence import canonical_json, sha256_of
from app.core.config import Settings
from app.llm.provider import LlmProvider, LlmRequest, LlmResponse, ProviderError
from app.llm.schema import provider_schema

PROMPTS = Path(__file__).parent / "prompts"
MAX_BACKOFF_SECONDS = 10.0
BASE_BACKOFF_SECONDS = 0.5
MAX_ERRORS_REPORTED = 20
TOKEN_FRAMING_ALLOWANCE = 1024

FallbackReason = Literal["disabled", "budget", "deadline", "provider_error", "validation"]
Validation = Literal["accepted", "repaired", "invalid", "error"]


@dataclass(frozen=True)
class Task[T: BaseModel]:
    name: str
    version: int
    output_model: type[T]

    @property
    def prompt_version(self) -> str:
        return f"{self.name}.v{self.version}"

    @property
    def system(self) -> str:
        return _prompt(self.prompt_version)


@cache
def _prompt(prompt_version: str) -> str:
    return (PROMPTS / f"{prompt_version}.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Accepted[T: BaseModel]:
    value: T
    llm_call_id: UUID
    repaired: bool


@dataclass(frozen=True)
class Fallback:
    reason: FallbackReason
    detail: str | None = None


@dataclass(frozen=True)
class CallRecord:
    task: str
    attempt: int
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    stop_reason: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    validation: Validation
    error_code: str | None


class CallRecorder(Protocol):
    async def usage(self) -> tuple[int, int]:
        """(calls, tokens) already spent by this investigation."""
        ...

    async def record(self, call: CallRecord) -> UUID: ...


class Gateway:
    def __init__(
        self,
        provider: LlmProvider | None,
        settings: Settings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self._sleep = sleep

    async def generate[T: BaseModel](
        self,
        task: Task[T],
        envelope: dict[str, Any],
        validate: Callable[[T], list[str]],
        recorder: CallRecorder,
        deadline: datetime | None,
    ) -> Accepted[T] | Fallback:
        if self.provider is None:
            return Fallback("disabled")
        input_hash = sha256_of(envelope)
        user = canonical_json({"input": envelope})
        attempt = 0
        for repair_round in range(2):  # the original request, then at most one repair
            outcome = await self._call_with_retries(
                task, envelope, input_hash, user, recorder, deadline, attempt
            )
            if isinstance(outcome, Fallback):
                return outcome
            response, attempt, latency_ms = outcome
            value, errors, output = _validate(task, response, validate)
            call_id = await recorder.record(
                self._record(
                    task,
                    attempt,
                    envelope,
                    input_hash,
                    response,
                    latency_ms,
                    output,
                    validation=("repaired" if repair_round else "accepted")
                    if not errors
                    else "invalid",
                    error_code=None if not errors else errors[0].split(":", 1)[0],
                )
            )
            if value is not None and not errors:
                return Accepted(value, call_id, repaired=repair_round == 1)
            user = canonical_json(
                {
                    "input": envelope,
                    "repair": {
                        "previous_output": response.text or "",
                        "errors": errors[:MAX_ERRORS_REPORTED],
                    },
                }
            )
        return Fallback("validation", errors[0] if errors else None)

    def _require_provider(self) -> LlmProvider:
        if self.provider is None:
            raise RuntimeError("no LLM provider configured")
        return self.provider

    async def _call_with_retries[T: BaseModel](
        self,
        task: Task[T],
        envelope: dict[str, Any],
        input_hash: str,
        user: str,
        recorder: CallRecorder,
        deadline: datetime | None,
        attempt: int,
    ) -> tuple[LlmResponse, int, int] | Fallback:
        """(response, attempt number, latency ms), or a fallback after retries/budget."""
        provider = self._require_provider()
        s = self.settings
        for retry in range(s.llm_max_retries + 1):
            if deadline is not None and datetime.now(UTC) >= deadline:
                return Fallback("deadline")
            calls, tokens = await recorder.usage()
            if calls >= s.llm_max_calls or tokens >= s.llm_max_tokens:
                return Fallback("budget", f"{calls} calls, {tokens} tokens")
            remaining = (
                (deadline - datetime.now(UTC)).total_seconds() if deadline is not None else None
            )
            if remaining is not None and remaining <= 0:
                return Fallback("deadline")
            timeout = (
                min(s.llm_timeout_seconds, remaining)
                if remaining is not None
                else s.llm_timeout_seconds
            )
            # UTF-8 bytes bound text tokenisation conservatively. Include the output
            # schema and provider framing; never spend the entire remainder on output.
            input_reservation = (
                len(task.system.encode("utf-8"))
                + len(user.encode("utf-8"))
                + len(canonical_json(_schema(task.output_model)).encode("utf-8"))
                + TOKEN_FRAMING_ALLOWANCE
            )
            output_reservation = min(
                s.llm_max_output_tokens, s.llm_max_tokens - tokens - input_reservation
            )
            if output_reservation < 1:
                return Fallback("budget", "insufficient tokens for request and output")
            attempt += 1
            request = LlmRequest(
                task=task.name,
                system=task.system,
                user=user,
                schema=_schema(task.output_model),
                max_output_tokens=output_reservation,
                timeout_seconds=timeout,
            )
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(provider.complete(request), timeout=timeout)
            except (ProviderError, TimeoutError) as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError("timeout")
                await recorder.record(
                    CallRecord(
                        task=task.name,
                        attempt=attempt,
                        provider=provider.name,
                        model=provider.model,
                        prompt_version=task.prompt_version,
                        input_hash=input_hash,
                        input=envelope,
                        output=None,
                        stop_reason=None,
                        # Failure usage is unknown: charge the entire reservation so
                        # timeouts and truncation cannot evade the persistent budget.
                        tokens_in=input_reservation,
                        tokens_out=output_reservation,
                        latency_ms=_elapsed_ms(started),
                        validation="error",
                        error_code=error.kind,
                    )
                )
                if deadline is not None and datetime.now(UTC) >= deadline:
                    return Fallback("deadline")
                if not error.retryable or retry == s.llm_max_retries:
                    return Fallback("provider_error", error.kind)
                delay = error.retry_after or BASE_BACKOFF_SECONDS * 2**retry
                delay = min(MAX_BACKOFF_SECONDS, max(0.0, delay))
                if deadline is not None:
                    remaining = (deadline - datetime.now(UTC)).total_seconds()
                    if remaining <= delay:
                        return Fallback("deadline")
                await self._sleep(delay)
                continue
            return response, attempt, _elapsed_ms(started)
        return Fallback("provider_error")  # pragma: no cover - the loop always returns

    def _record[T: BaseModel](
        self,
        task: Task[T],
        attempt: int,
        envelope: dict[str, Any],
        input_hash: str,
        response: LlmResponse,
        latency_ms: int,
        output: dict[str, Any] | None,
        *,
        validation: Validation,
        error_code: str | None,
    ) -> CallRecord:
        return CallRecord(
            task=task.name,
            attempt=attempt,
            provider=self._require_provider().name,
            model=response.model,
            prompt_version=task.prompt_version,
            input_hash=input_hash,
            input=envelope,
            output=output,
            stop_reason=response.stop_reason,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=latency_ms,
            validation=validation,
            error_code=error_code,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


@cache
def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return provider_schema(model)


def _validate[T: BaseModel](
    task: Task[T], response: LlmResponse, validate: Callable[[T], list[str]]
) -> tuple[T | None, list[str], dict[str, Any] | None]:
    """Gateway pipeline steps 1-3: JSON, strict schema, then the caller's semantic checks."""
    if not response.text:
        return None, ["empty_output: the response contained no JSON text"], None
    try:
        raw = json.loads(response.text)
    except json.JSONDecodeError:
        return (
            None,
            ["invalid_json: the response is not valid JSON"],
            {"unparsed": response.text[:2000]},
        )
    output = raw if isinstance(raw, dict) else {"unparsed": response.text[:2000]}
    try:  # JSON-mode strict validation (UUIDs and enums arrive as JSON strings)
        value = task.output_model.model_validate_json(response.text)
    except ValidationError as exc:
        errors = [
            f"schema: {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors(include_url=False, include_input=False)
        ]
        return None, errors, {"response": output, "errors": errors[:MAX_ERRORS_REPORTED]}
    errors = validate(value)
    if errors:
        return value, errors, {"response": output, "errors": errors[:MAX_ERRORS_REPORTED]}
    return value, [], output
