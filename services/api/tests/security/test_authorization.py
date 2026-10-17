"""T-16 authorization matrix and T-17 cross-tenant isolation over every registered route."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine, func, select, text

from app.auth.dependencies import get_current_user
from app.models import AuditLog
from app.tenancy.dependencies import RequirePermission
from app.tenancy.permissions import ROLE_PERMISSIONS, Permission, Role
from tests.helpers import (
    add_member,
    create_dataset,
    create_tenant,
    create_user,
    csrf_headers,
    login,
)

PUBLIC_ROUTES = {
    ("GET", "/api/v1/healthz"),
    ("GET", "/api/v1/readyz"),
    ("GET", "/api/v1/auth/csrf"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
}
ROLES: tuple[Role, ...] = ("owner", "analyst", "viewer")


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for child in dependant.dependencies:
        yield from _walk(child)


def _required_permission(route: RouteInfo) -> Permission | None:
    for dep in _walk(route.dependant):
        if isinstance(dep.call, RequirePermission):
            return dep.call.permission
    return None


def _requires_user(route: RouteInfo) -> bool:
    return any(dep.call is get_current_user for dep in _walk(route.dependant))


@dataclass(frozen=True)
class RouteInfo:
    path: str
    dependant: Dependant


def _api_routes(app: FastAPI) -> list[tuple[str, RouteInfo]]:
    """Every (method, route) with its effective path, including routes of included routers."""
    found: list[tuple[str, RouteInfo]] = []
    for context in iter_route_contexts(app.routes):
        original = context.original_route
        if isinstance(original, APIRoute) and context.path and context.path.startswith("/api/v1"):
            info = RouteInfo(path=context.path, dependant=original.dependant)
            found.extend((method, info) for method in sorted(original.methods or ()))
    assert found, "no API routes discovered"
    return found


def test_every_route_is_public_by_allowlist_or_authenticated(app: FastAPI) -> None:
    for method, route in _api_routes(app):
        if (method, route.path) in PUBLIC_ROUTES:
            continue
        assert _requires_user(route), f"{method} {route.path} lacks authentication"


def test_every_business_route_declares_a_permission(app: FastAPI) -> None:
    business_routes = [
        (m, r) for m, r in _api_routes(app) if r.path.startswith("/api/v1/businesses/{business_id}")
    ]
    assert business_routes, "expected business-scoped routes"
    for method, route in business_routes:
        assert _required_permission(route) is not None, f"{method} {route.path} has no permission"


@dataclass(frozen=True)
class World:
    business_id: uuid.UUID
    dataset_id: uuid.UUID
    other_business_id: uuid.UUID
    other_dataset_id: uuid.UUID


@pytest.fixture
def world(owner_engine: Engine) -> World:
    own = create_tenant(owner_engine, "owner")
    other = create_tenant(owner_engine, "other")
    for role in ("analyst", "viewer"):
        user_id = create_user(owner_engine, f"{role}@example.test")
        add_member(owner_engine, own.business_id, user_id, role)
    return World(
        business_id=own.business_id,
        dataset_id=create_dataset(owner_engine, own.business_id, own.user_id, 1),
        other_business_id=other.business_id,
        other_dataset_id=create_dataset(owner_engine, other.business_id, other.user_id, 1),
    )


def _url(path: str, business_id: uuid.UUID, dataset_id: uuid.UUID) -> str:
    return (
        path.replace("{business_id}", str(business_id))
        .replace("{dataset_id}", str(dataset_id))
        .replace("{kind}", "orders")
        .replace("{incident_id}", str(uuid.UUID(int=1)))
        .replace("{investigation_id}", str(uuid.UUID(int=1)))
        .replace("{evidence_id}", str(uuid.UUID(int=1)))
        .replace("{clarification_id}", str(uuid.UUID(int=1)))
        .replace("{plan_id}", str(uuid.UUID(int=1)))
        .replace("{action_id}", str(uuid.UUID(int=1)))
        .replace("{integration_id}", str(uuid.UUID(int=1)))
        .replace("{version}", "1")
    )


def _business_routes(app: FastAPI) -> list[tuple[str, str, Permission]]:
    routes: list[tuple[str, str, Permission]] = []
    for method, route in _api_routes(app):
        permission = _required_permission(route)
        if permission is not None:
            routes.append((method, route.path, permission))
    return routes


async def _call(client: httpx.AsyncClient, method: str, url: str) -> httpx.Response:
    """Minimal request: unsafe methods carry CSRF protection and an (invalid) empty body."""
    if method == "GET":
        return await client.get(url)
    headers = {**await csrf_headers(client), "Idempotency-Key": uuid.uuid4().hex}
    return await client.request(method, url, headers=headers, json={})


@pytest.mark.parametrize("role", ROLES)
async def test_role_permission_matrix(
    app: FastAPI, client: httpx.AsyncClient, world: World, role: Role
) -> None:
    email = "owner@example.test" if role == "owner" else f"{role}@example.test"
    assert (await login(client, email)).status_code == 200
    for method, path, permission in _business_routes(app):
        response = await _call(client, method, _url(path, world.business_id, world.dataset_id))
        label = f"{role} {method} {path}"
        if permission in ROLE_PERMISSIONS[role]:
            # Authorised: may still fail validation, find nothing (random incident id) or wait
            # for work in progress (409), but never fail authorisation or with a 5xx.
            assert response.status_code not in {401, 403}, label
            assert response.status_code < 500, label
            if method == "GET":
                # 404: a random resource id, or no analysis ever requested for the dataset.
                random_ids = (
                    "{incident_id}",
                    "{investigation_id}",
                    "{evidence_id}",
                    "{plan_id}",
                    "{action_id}",
                )
                not_found_ok = any(p in path for p in random_ids) or path.endswith("/analysis")
                allowed = {200, 404, 409} if not_found_ok else {200, 409}
                assert response.status_code in allowed, label
        else:
            assert response.status_code == 403, label
            assert response.json()["code"] == "forbidden"


async def test_unauthenticated_business_routes_are_401(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    for method, path, _ in _business_routes(app):
        response = await _call(client, method, _url(path, world.business_id, world.dataset_id))
        assert response.status_code == 401, f"{method} {path}"


async def test_cross_tenant_access_is_404_without_data(
    app: FastAPI, client: httpx.AsyncClient, world: World, owner_engine: Engine
) -> None:
    await login(client, "owner@example.test")
    with owner_engine.connect() as conn:
        before = conn.execute(
            text("SELECT currency, timezone FROM businesses WHERE id = :id"),
            {"id": world.other_business_id},
        ).one()
    for method, path, _ in _business_routes(app):
        response = await _call(
            client, method, _url(path, world.other_business_id, world.other_dataset_id)
        )
        assert response.status_code == 404, f"{method} {path}"
        body = response.json()
        assert body["code"] == "not_found"
        # Only the generic problem fields; `instance` merely echoes the requested path.
        assert set(body) == {"type", "title", "status", "detail", "instance", "code", "request_id"}
        assert "Business other" not in response.text
    with owner_engine.connect() as conn:
        denied = conn.scalar(
            select(func.count()).where(
                AuditLog.action == "authz.denied",
                AuditLog.metadata_["reason"].astext == "not_member",
            )
        )
        after = conn.execute(
            text("SELECT currency, timezone FROM businesses WHERE id = :id"),
            {"id": world.other_business_id},
        ).one()
        foreign_datasets = conn.scalar(
            text("SELECT count(*) FROM datasets WHERE business_id = :id"),
            {"id": world.other_business_id},
        )
    assert denied == len(_business_routes(app))
    assert tuple(before) == tuple(after)
    assert foreign_datasets == 1  # no upload landed in the other business


async def test_foreign_dataset_through_own_business_is_404(
    client: httpx.AsyncClient, world: World
) -> None:
    await login(client, "owner@example.test")
    response = await client.get(
        f"/api/v1/businesses/{world.business_id}/datasets/{world.other_dataset_id}"
    )
    assert response.status_code == 404


async def test_lists_only_contain_own_tenant_rows(client: httpx.AsyncClient, world: World) -> None:
    await login(client, "other@example.test")  # generates audit rows for "other" only via denial
    await client.get(f"/api/v1/businesses/{world.business_id}")
    await login(client, "owner@example.test")
    datasets = (await client.get(f"/api/v1/businesses/{world.business_id}/datasets")).json()
    assert [d["id"] for d in datasets["items"]] == [str(world.dataset_id)]
    audit = (await client.get(f"/api/v1/businesses/{world.business_id}/audit-log")).json()
    # Denials of non-members are recorded without a business, so they are not visible here.
    assert audit["items"] == []
    me = (await client.get("/api/v1/auth/me")).json()
    assert [m["business_id"] for m in me["memberships"]] == [str(world.business_id)]
