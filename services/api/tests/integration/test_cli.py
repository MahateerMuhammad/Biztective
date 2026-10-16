import httpx
import pytest
from sqlalchemy import Engine, func, select

from app.cli import main
from app.models import AuditLog, Business, Membership, User
from tests.conftest import DatabaseUrls
from tests.helpers import login

SEED_PASSWORD = "seeded demo password value"


@pytest.fixture
def seed_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object, db_urls: DatabaseUrls) -> None:
    monkeypatch.chdir(str(tmp_path))
    monkeypatch.setenv("DATABASE_URL_MIGRATIONS", db_urls.owner)
    monkeypatch.setenv("DEMO_OWNER_EMAIL", "Demo.Owner@Example.test")
    monkeypatch.setenv("DEMO_OWNER_PASSWORD", SEED_PASSWORD)
    monkeypatch.setenv("DEMO_BUSINESS_NAME", "Demo Retail")
    monkeypatch.setenv("DEMO_BUSINESS_TIMEZONE", "Asia/Karachi")


@pytest.mark.usefixtures("seed_env")
def test_seed_demo_is_idempotent(owner_engine: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["seed-demo"]) == 0
    assert main(["seed-demo"]) == 0
    output = capsys.readouterr()
    assert "Demo account created: demo.owner@example.test" in output.out
    assert "Demo account updated" in output.out
    assert SEED_PASSWORD not in output.out + output.err
    with owner_engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(User)) == 1
        assert conn.scalar(select(func.count()).select_from(Business)) == 1
        assert conn.scalar(select(Membership.role)) == "owner"
        assert conn.scalar(select(Business.timezone)) == "Asia/Karachi"
        assert conn.scalar(select(func.count()).where(AuditLog.action == "seed.demo_account")) == 2


@pytest.mark.usefixtures("seed_env")
async def test_seeded_account_can_log_in(client: httpx.AsyncClient) -> None:
    assert main(["seed-demo"]) == 0
    response = await login(client, "demo.owner@example.test", SEED_PASSWORD)
    assert response.status_code == 200
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["memberships"][0]["business_name"] == "Demo Retail"


@pytest.mark.usefixtures("seed_env")
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DEMO_OWNER_PASSWORD", "short"),
        ("DEMO_BUSINESS_TIMEZONE", "Mars/Olympus"),
        ("DEMO_BUSINESS_CURRENCY", "usd"),
    ],
)
def test_seed_demo_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    owner_engine: Engine,
    variable: str,
    value: str,
) -> None:
    monkeypatch.setenv(variable, value)
    assert main(["seed-demo"]) == 2
    output = capsys.readouterr()
    assert "Invalid or missing seed configuration" in output.err
    assert "short" not in output.err  # the rejected password is never printed
    with owner_engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(User)) == 0


def test_seed_demo_requires_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(str(tmp_path))
    for variable in ("DATABASE_URL_MIGRATIONS", "DEMO_OWNER_EMAIL", "DEMO_OWNER_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    assert main(["seed-demo"]) == 2
    assert "DEMO_OWNER_PASSWORD" in capsys.readouterr().err
