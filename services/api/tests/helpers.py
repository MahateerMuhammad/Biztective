"""Data factories (owner role, bypassing RLS) and HTTP helpers shared by tests."""

import uuid
from dataclasses import dataclass
from datetime import date

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine, insert

from app.auth.passwords import hash_password
from app.jobs.worker import process_next_job
from app.models import Business, Dataset, Membership, User

FRONTEND_ORIGIN = "http://localhost:3000"
TEST_SESSION_SECRET = "test-session-secret-" + "x" * 32
DEFAULT_PASSWORD = "correct horse battery staple"


# ---------- data factories (run as the owner role, bypassing RLS) ----------


@dataclass(frozen=True)
class Tenant:
    business_id: uuid.UUID
    user_id: uuid.UUID
    email: str


def create_user(
    engine: Engine, email: str, *, password: str = DEFAULT_PASSWORD, is_active: bool = True
) -> uuid.UUID:
    user_id = uuid.uuid7()
    with engine.begin() as conn:
        conn.execute(
            insert(User).values(
                id=user_id,
                email=email,
                password_hash=hash_password(password),
                display_name=email.split("@")[0],
                is_active=is_active,
            )
        )
    return user_id


def create_business(engine: Engine, name: str, owner_id: uuid.UUID) -> uuid.UUID:
    business_id = uuid.uuid7()
    with engine.begin() as conn:
        conn.execute(
            insert(Business).values(
                id=business_id, name=name, currency="USD", timezone="UTC", created_by=owner_id
            )
        )
    return business_id


def add_member(engine: Engine, business_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
    with engine.begin() as conn:
        conn.execute(insert(Membership).values(business_id=business_id, user_id=user_id, role=role))


def create_tenant(engine: Engine, label: str, role: str = "owner") -> Tenant:
    email = f"{label}@example.test"
    user_id = create_user(engine, email)
    business_id = create_business(engine, f"Business {label}", user_id)
    add_member(engine, business_id, user_id, role)
    return Tenant(business_id=business_id, user_id=user_id, email=email)


def create_dataset(
    engine: Engine, business_id: uuid.UUID, uploaded_by: uuid.UUID, version: int
) -> uuid.UUID:
    dataset_id = uuid.uuid7()
    with engine.begin() as conn:
        conn.execute(
            insert(Dataset).values(
                id=dataset_id,
                business_id=business_id,
                version=version,
                status="ready",
                orders_rows=100 * version,
                source_timezone="UTC",
                business_timezone="UTC",
                currency="USD",
                date_min=date(2026, 1, 1),
                date_max=date(2026, 6, 30),
                uploaded_by=uploaded_by,
            )
        )
    return dataset_id


# ---------- HTTP helpers ----------


async def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"], "Origin": FRONTEND_ORIGIN}


async def login(
    client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> httpx.Response:
    headers = await csrf_headers(client)
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=headers
    )


async def run_jobs(app: FastAPI, limit: int = 20) -> int:
    """Run due jobs synchronously, exactly as the lifespan worker would. Returns jobs run."""
    ran = 0
    while ran < limit and await process_next_job(
        app.state.sessionmaker, app.state.job_handlers, app.state.on_job_failure
    ):
        ran += 1
    return ran


def multipart(files: dict[str, bytes], **fields: str) -> dict[str, object]:
    """httpx kwargs for a dataset upload; keys are file kinds (orders, products, ...)."""
    return {
        "files": {kind: (f"{kind}.csv", content, "text/csv") for kind, content in files.items()},
        "data": fields,
    }


async def upload(
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    files: dict[str, bytes],
    *,
    key: str | None = None,
    **fields: str,
) -> httpx.Response:
    headers = {**await csrf_headers(client), "Idempotency-Key": key or uuid.uuid4().hex}
    return await client.post(
        f"/api/v1/businesses/{business_id}/datasets",
        headers=headers,
        **multipart(files, **fields),  # type: ignore[arg-type]
    )


async def ingest_files(
    app: FastAPI,
    client: httpx.AsyncClient,
    business_id: uuid.UUID,
    files: dict[str, bytes],
    **fields: str,
) -> dict[str, object]:
    """Upload, run all jobs (ingestion and analysis), and return the dataset detail."""
    response = await upload(client, business_id, files, **fields)
    assert response.status_code == 202, response.text
    await run_jobs(app)
    detail = await client.get(f"/api/v1/businesses/{business_id}/datasets/{response.json()['id']}")
    body: dict[str, object] = detail.json()
    return body
