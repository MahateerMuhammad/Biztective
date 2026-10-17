from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from app.actions.executor import ActionRuntime
from app.actions.sheets import ServiceAccount, TokenCache
from tests.actions.fake_google import FakeGoogle, make_account


@pytest.fixture(autouse=True)
def _clean(clean_db: None) -> None:
    """Every test in this directory starts from empty tables."""


@pytest.fixture(scope="session")
def service_account() -> tuple[ServiceAccount, str, bytes]:
    return make_account()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def google(
    app: FastAPI, service_account: tuple[ServiceAccount, str, bytes], sleeps: list[float]
) -> Iterator[FakeGoogle]:
    """The app's executor talks to a fresh fake Google; backoff sleeps are recorded, not
    waited for."""
    account, _, public = service_account
    fake = FakeGoogle(public_key=public, email=account.email)
    runtime: ActionRuntime = app.state.actions

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    saved = (runtime.account, runtime.transport, runtime.sleep, runtime.tokens)
    runtime.account, runtime.transport, runtime.sleep = account, fake.transport(), sleep
    runtime.tokens = TokenCache()
    yield fake
    runtime.account, runtime.transport, runtime.sleep, runtime.tokens = saved
