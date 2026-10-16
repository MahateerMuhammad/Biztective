import httpx
import pytest
from sqlalchemy import Engine

from tests.helpers import add_member, create_dataset, create_tenant, create_user, login


async def test_get_business(client: httpx.AsyncClient, owner_engine: Engine) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    response = await client.get(f"/api/v1/businesses/{tenant.business_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(tenant.business_id)
    assert body["name"] == "Business alice"
    assert body["currency"] == "USD"
    assert body["role"] == "owner"


async def test_dataset_list_paginates_by_version(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    for version in (1, 2, 3):
        create_dataset(owner_engine, tenant.business_id, tenant.user_id, version)
    await login(client, tenant.email)
    base = f"/api/v1/businesses/{tenant.business_id}/datasets"

    first = (await client.get(base, params={"limit": 2})).json()
    assert [d["version"] for d in first["items"]] == [3, 2]
    assert first["next_cursor"]
    second = (await client.get(base, params={"limit": 2, "cursor": first["next_cursor"]})).json()
    assert [d["version"] for d in second["items"]] == [1]
    assert second["next_cursor"] is None


async def test_dataset_detail(client: httpx.AsyncClient, owner_engine: Engine) -> None:
    tenant = create_tenant(owner_engine, "alice")
    dataset_id = create_dataset(owner_engine, tenant.business_id, tenant.user_id, 1)
    await login(client, tenant.email)
    response = await client.get(f"/api/v1/businesses/{tenant.business_id}/datasets/{dataset_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["orders_rows"] == 100
    assert body["date_min"] == "2026-01-01"


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": "abc"}, {"cursor": "%%%"}, {"cursor": "LTE"}],
)
async def test_invalid_pagination_is_422(
    client: httpx.AsyncClient, owner_engine: Engine, params: dict[str, str | int]
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    response = await client.get(f"/api/v1/businesses/{tenant.business_id}/datasets", params=params)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_malformed_identifiers_are_422(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    await login(client, tenant.email)
    assert (await client.get("/api/v1/businesses/not-a-uuid")).status_code == 422
    response = await client.get(f"/api/v1/businesses/{tenant.business_id}/datasets/1;DROP")
    assert response.status_code == 422


async def test_audit_log_lists_business_events_newest_first(
    client: httpx.AsyncClient, owner_engine: Engine
) -> None:
    tenant = create_tenant(owner_engine, "alice")
    viewer_id = create_user(owner_engine, "viewer@example.test")
    add_member(owner_engine, tenant.business_id, viewer_id, "viewer")
    # A denied viewer request produces a business-scoped audit entry.
    await login(client, "viewer@example.test")
    await client.get(f"/api/v1/businesses/{tenant.business_id}/audit-log")
    await client.get(f"/api/v1/businesses/{tenant.business_id}/audit-log")

    await login(client, tenant.email)
    page = (
        await client.get(f"/api/v1/businesses/{tenant.business_id}/audit-log", params={"limit": 1})
    ).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["action"] == "authz.denied"
    assert page["items"][0]["metadata"]["reason"] == "missing_permission"
    rest = (
        await client.get(
            f"/api/v1/businesses/{tenant.business_id}/audit-log",
            params={"cursor": page["next_cursor"]},
        )
    ).json()
    assert len(rest["items"]) == 1
    assert rest["items"][0]["id"] < page["items"][0]["id"]
    assert rest["next_cursor"] is None
