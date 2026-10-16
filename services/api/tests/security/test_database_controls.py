"""T-18 RLS defence in depth and T-38 audit immutability, exercised directly as `root_app`."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.conftest import DatabaseUrls
from tests.helpers import Tenant, create_dataset, create_tenant

TENANT_TABLES = (
    "businesses",
    "memberships",
    "datasets",
    "audit_log",
    "dataset_files",
    "products",
    "customers",
    "order_lines",
    "returns",
    "validation_issues",
    "idempotency_keys",
)


@pytest.fixture
def app_engine(db_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(db_urls.app)
    yield engine
    engine.dispose()


@pytest.fixture
def tenants(owner_engine: Engine) -> tuple[Tenant, Tenant]:
    a = create_tenant(owner_engine, "a")
    b = create_tenant(owner_engine, "b")
    for tenant in (a, b):
        create_dataset(owner_engine, tenant.business_id, tenant.user_id, 1)
        with owner_engine.begin() as conn:
            conn.execute(
                text("INSERT INTO audit_log (id, business_id, action) VALUES (:id, :b, 'x')"),
                {"id": uuid.uuid7(), "b": tenant.business_id},
            )
    return a, b


def _scope(conn: Connection, tenant: Tenant | None) -> None:
    user_id = str(tenant.user_id) if tenant else ""
    business_id = str(tenant.business_id) if tenant else ""
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})
    conn.execute(text("SELECT set_config('app.current_business_id', :b, true)"), {"b": business_id})


def test_no_context_sees_no_tenant_rows(app_engine: Engine, tenants: tuple[Tenant, Tenant]) -> None:
    with app_engine.connect() as conn:
        for table in TENANT_TABLES:
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


def test_context_after_reset_still_sees_nothing(
    app_engine: Engine, tenants: tuple[Tenant, Tenant]
) -> None:
    """After a scoped transaction ends the setting reads as '' and must not error or leak."""
    with app_engine.connect() as conn:
        with conn.begin():
            _scope(conn, tenants[0])
        for table in TENANT_TABLES:
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


def test_scoped_context_sees_only_own_rows(
    app_engine: Engine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    with app_engine.connect() as conn, conn.begin():
        _scope(conn, a)
        for table in ("datasets", "audit_log"):
            ids = set(conn.scalars(text(f"SELECT business_id FROM {table}")))
            assert ids == {a.business_id}, table
        assert set(conn.scalars(text("SELECT id FROM businesses"))) == {a.business_id}
        assert set(conn.scalars(text("SELECT user_id FROM memberships"))) == {a.user_id}
        assert b.business_id not in set(conn.scalars(text("SELECT business_id FROM memberships")))


def test_audit_insert_for_foreign_business_is_rejected(
    app_engine: Engine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    forged = text("INSERT INTO audit_log (id, business_id, action) VALUES (:id, :b, 'forged')")
    with app_engine.connect() as conn, conn.begin():
        _scope(conn, a)
        with pytest.raises(ProgrammingError):
            conn.execute(forged, {"id": uuid.uuid7(), "b": b.business_id})


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'tampered'",
        "DELETE FROM audit_log",
        "INSERT INTO businesses (id, name, currency, timezone, created_by) "
        "VALUES (gen_random_uuid(), 'x', 'USD', 'UTC', gen_random_uuid())",
        "UPDATE users SET email = 'x@example.test'",
        "UPDATE datasets SET checksum_sha256 = NULL",
        "UPDATE datasets SET business_id = gen_random_uuid()",
        "DELETE FROM datasets",
        "UPDATE jobs SET type = 'x'",
        "DELETE FROM jobs",
        "INSERT INTO memberships (business_id, user_id, role) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'owner')",
        "DELETE FROM sessions",
        "CREATE TABLE intruder (id int)",
    ],
)
def test_app_role_lacks_privileges(
    app_engine: Engine, tenants: tuple[Tenant, Tenant], statement: str
) -> None:
    with app_engine.connect() as conn, conn.begin():
        _scope(conn, tenants[0])
        with pytest.raises(ProgrammingError) as exc:
            conn.execute(text(statement))
    assert "permission denied" in str(exc.value)


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"]
)
def test_audit_log_is_append_only_even_for_owner(
    owner_engine: Engine, tenants: tuple[Tenant, Tenant], statement: str
) -> None:
    with owner_engine.connect() as conn, conn.begin(), pytest.raises(DBAPIError) as exc:
        conn.execute(text(statement))
    assert "audit_log is append-only" in str(exc.value)


def test_app_role_attributes(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    assert tuple(row) == (False, False, False, False)
