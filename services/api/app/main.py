"""Application factory."""

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import health
from app.analytics.router import router as analytics_router
from app.analytics.service import analyze_dataset, mark_analysis_failed
from app.audit.router import router as audit_router
from app.auth.csrf import CsrfMiddleware
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_sessionmaker
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.datasets.router import router as datasets_router
from app.ingestion.contract import MAX_UPLOAD_BYTES
from app.ingestion.pipeline import ingest_dataset, mark_dataset_failed
from app.investigation.router import router as investigation_router
from app.investigation.service import InvestigationRuntime, provider_from_settings
from app.jobs.queue import JobType, LeasedJob
from app.jobs.worker import Handler, run_worker
from app.recovery.router import router as recovery_router
from app.recovery.service import RecoveryRuntime
from app.tenancy.router import router as tenancy_router

MAX_JSON_BODY_BYTES = 1024 * 1024  # SEC-07
UPLOAD_ROUTE = re.compile(r"/api/v1/businesses/[^/]+/datasets")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    provider = provider_from_settings(settings)
    investigations = InvestigationRuntime(settings, provider)
    recovery = RecoveryRuntime(settings, provider)
    job_handlers: dict[JobType, Handler] = {
        "ingest_dataset": ingest_dataset,
        "analyze_dataset": analyze_dataset,
        "run_investigation": investigations.run,
        "run_recovery_plan": recovery.run,
    }

    async def on_final_failure(job: LeasedJob, code: str) -> None:
        if job.type == "ingest_dataset":
            await mark_dataset_failed(sessionmaker, job, code)
        elif job.type == "analyze_dataset":
            await mark_analysis_failed(sessionmaker, job, code)
        elif job.type == "run_recovery_plan":
            await recovery.mark_failed(sessionmaker, job, code)
        else:
            await investigations.mark_failed(sessionmaker, job, code)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        worker: asyncio.Task[None] | None = None
        if settings.worker_enabled:
            worker = asyncio.create_task(
                run_worker(
                    sessionmaker,
                    job_handlers,
                    on_final_failure,
                    poll_seconds=settings.worker_poll_seconds,
                )
            )
        yield
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await engine.dispose()

    expose_docs = not settings.is_production
    app = FastAPI(
        title="ROOT API",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/api/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if expose_docs else None,
    )
    app.state.settings = settings
    app.state.sessionmaker = sessionmaker
    app.state.on_job_failure = on_final_failure
    app.state.job_handlers = job_handlers
    app.state.investigations = investigations
    app.state.recovery = recovery
    app.state.migration_head = health.expected_migration_head()

    api = APIRouter(prefix="/api/v1")
    api.include_router(health.router)
    api.include_router(auth_router)
    api.include_router(tenancy_router)
    api.include_router(datasets_router)
    api.include_router(analytics_router)
    api.include_router(investigation_router)
    api.include_router(recovery_router)
    api.include_router(audit_router)
    app.include_router(api)
    register_error_handlers(app)

    # Added innermost first: the last middleware added is the outermost.
    app.add_middleware(CsrfMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=MAX_JSON_BODY_BYTES,
        overrides=(("POST", UPLOAD_ROUTE, MAX_UPLOAD_BYTES),),
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    return app
