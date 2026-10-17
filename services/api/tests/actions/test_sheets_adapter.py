"""Sheets adapter contract (T-35): identifiers, key files, error classes and retries."""

import base64
import json

import httpx
import pytest

from app.actions.sheets import (
    SheetsClient,
    SheetsError,
    TokenCache,
    load_service_account,
    sanitize,
    spreadsheet_id,
)
from tests.actions.fake_google import SID, FakeGoogle, make_account


def test_spreadsheet_ids_come_only_from_google_sheets_links() -> None:
    assert spreadsheet_id(SID) == SID
    assert spreadsheet_id(f"https://docs.google.com/spreadsheets/d/{SID}/edit") == SID
    for bad in (
        f"http://docs.google.com/spreadsheets/d/{SID}",
        f"https://docs.google.com.evil.example/spreadsheets/d/{SID}",
        f"https://user@docs.google.com/spreadsheets/d/{SID}",
        "short",
        SID + "/../x",
    ):
        assert spreadsheet_id(bad) is None, bad


@pytest.mark.parametrize(
    "value",
    ["=1+1", "+1", "-1", "@A1", "\tx", "\rx"],
)
def test_formula_starts_are_escaped(value: str) -> None:
    assert sanitize(value) == "'" + value


def test_ordinary_text_is_unchanged() -> None:
    assert sanitize("Quality review with the supplier") == "Quality review with the supplier"
    assert sanitize("a=b") == "a=b"


def test_key_files_are_validated() -> None:
    def encoded(info: object) -> str:
        return base64.b64encode(json.dumps(info).encode()).decode()

    for bad in (
        "not base64!",
        encoded([]),
        encoded({"type": "authorized_user"}),
        encoded({"type": "service_account", "client_email": "a@example.com"}),
        encoded(
            {
                "type": "service_account",
                "client_email": "a@p.iam.gserviceaccount.com",
                "private_key": "x",
            }
        ),
    ):
        with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
            load_service_account(bad)


async def _fake() -> FakeGoogle:
    account, _, public = make_account()
    fake = FakeGoogle(public_key=public, email=account.email)
    fake.sheet()
    return fake


async def test_reads_retry_until_the_limit_then_raise() -> None:
    fake = await _fake()
    sleeps: list[float] = []
    account, _, public = make_account()
    fake.public_key = public
    client = SheetsClient(
        account=account,
        http=httpx.AsyncClient(transport=fake.transport()),
        tokens=TokenCache(),
        max_retries=2,
        backoff=0.5,
        sleep=lambda s: _record(sleeps, s),
    )
    fake.email = account.email
    fake.fail("metadata", "500", "500", "500")
    with pytest.raises(SheetsError) as exc:
        await client.titles(SID)
    assert (exc.value.code, exc.value.retryable, exc.value.ambiguous) == (
        "provider_unavailable",
        True,
        False,
    )
    assert sleeps == [0.5, 1.0]
    assert [c["status"] for c in client.calls if c["op"] == "metadata"] == [500, 500, 500]


async def test_write_errors_that_may_have_applied_are_not_retried_blindly() -> None:
    fake = await _fake()
    account, _, public = make_account()
    fake.public_key, fake.email = public, account.email
    sleeps: list[float] = []
    client = SheetsClient(
        account=account,
        http=httpx.AsyncClient(transport=fake.transport()),
        tokens=TokenCache(),
        max_retries=3,
        backoff=0.5,
        sleep=lambda s: _record(sleeps, s),
    )
    fake.spreadsheets[SID]["T"] = [["k"]]
    for fault, code in (
        ("timeout_after", "timeout"),
        ("500", "provider_unavailable"),
        ("reset_before", "network_error"),
    ):
        fake.fail("append", fault)
        with pytest.raises(SheetsError) as exc:
            await client.append(SID, "'T'!A:A", [["x"]])
        assert (exc.value.code, exc.value.ambiguous) == (code, True)
    assert fake.ops("append") == 3
    assert sleeps == []
    # Certainly-not-applied write failures are retried in place.
    fake.fail("append", "429", "connect")
    await client.append(SID, "'T'!A:A", [["y"]])
    assert fake.ops("append") == 6
    assert sleeps == [7.0, 1.0]  # Retry-After, then the exponential schedule


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        ("403", "integration_permission_denied"),
        ("404", "destination_not_found"),
        ("400", "destination_invalid"),
    ],
)
async def test_permanent_errors_are_not_retried(fault: str, code: str) -> None:
    fake = await _fake()
    account, _, public = make_account()
    fake.public_key, fake.email = public, account.email
    client = SheetsClient(
        account=account,
        http=httpx.AsyncClient(transport=fake.transport()),
        tokens=TokenCache(),
        max_retries=3,
        backoff=0.5,
        sleep=lambda s: _record([], s),
    )
    fake.fail("metadata", fault)
    with pytest.raises(SheetsError) as exc:
        await client.titles(SID)
    assert (exc.value.code, exc.value.retryable) == (code, False)
    assert fake.ops("metadata") == 1


async def test_a_second_unauthorized_response_means_bad_credentials() -> None:
    fake = await _fake()
    account, _, public = make_account()
    fake.public_key, fake.email = public, account.email
    client = SheetsClient(
        account=account,
        http=httpx.AsyncClient(transport=fake.transport()),
        tokens=TokenCache(),
        max_retries=3,
        backoff=0.5,
        sleep=lambda s: _record([], s),
    )
    fake.fail("metadata", "401", "401")
    with pytest.raises(SheetsError) as exc:
        await client.titles(SID)
    assert exc.value.code == "integration_credentials_invalid"
    assert fake.ops("token") == 2


async def _record(sleeps: list[float], seconds: float) -> None:
    sleeps.append(seconds)
