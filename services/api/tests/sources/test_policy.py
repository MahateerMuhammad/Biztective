"""Unit tests: SSRF network policy and identifier validation for database sources."""

import pytest

from app.core.config import Settings
from app.sources.postgres import SourceError, allowed_hosts, check_identifier, resolve_host
from tests.conftest import make_settings


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "10.1.2.3", "192.168.0.10", "169.254.169.254", "::1", "0.0.0.0", "fe80::1"],  # noqa: S104 - checked as blocked, never bound
)
def test_private_and_metadata_addresses_are_blocked(host: str) -> None:
    with pytest.raises(SourceError) as info:
        resolve_host(host, 5432, frozenset())
    assert info.value.code == "source_blocked_address"


def test_allowlisted_host_passes() -> None:
    assert resolve_host("127.0.0.1", 5432, allowed_hosts(" 127.0.0.1 , db.example ")) == "127.0.0.1"


def test_public_address_passes() -> None:
    assert resolve_host("8.8.8.8", 5432, frozenset()) == "8.8.8.8"


@pytest.mark.parametrize(
    "name",
    ["orders; DROP TABLE x", 'orders"', "Orders", "1orders", "", "a" * 64, "pg_catalog.x", "o-r"],
)
def test_injection_identifiers_are_rejected(name: str) -> None:
    with pytest.raises(SourceError) as info:
        check_identifier(name)
    assert info.value.code == "invalid_identifier"


def test_sslmode_disable_only_in_test_environment() -> None:
    assert make_settings("postgresql+psycopg://u@h/d", source_db_sslmode="disable")
    with pytest.raises(ValueError, match="test environment"):
        Settings.model_validate(
            {
                **make_settings("postgresql+psycopg://u@h/d").model_dump(),
                "environment": "local",
                "source_db_sslmode": "disable",
            }
        )
