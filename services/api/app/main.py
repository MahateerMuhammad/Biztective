"""Application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import health
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
from app.tenancy.router import router as tenancy_router

MAX_JSON_BODY_BYTES = 1024 * 1024  # SEC-07


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
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
    app.state.sessionmaker = create_sessionmaker(engine)
    app.state.migration_head = health.expected_migration_head()

    api = APIRouter(prefix="/api/v1")
    api.include_router(health.router)
    api.include_router(auth_router)
    api.include_router(tenancy_router)
    api.include_router(datasets_router)
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
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_JSON_BODY_BYTES)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    return app
