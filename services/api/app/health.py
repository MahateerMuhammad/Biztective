"""Liveness and readiness probes (NFR-07)."""

import logging
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def expected_migration_head() -> str:
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    if head is None:
        raise RuntimeError("No Alembic migration head found")
    return head


class StatusOut(BaseModel):
    status: str


@router.get("/healthz")
async def healthz() -> StatusOut:
    return StatusOut(status="ok")


async def _not_ready_reason(session: AsyncSession, expected_head: str) -> str | None:
    version = await session.scalar(text("SELECT version_num FROM alembic_version"))
    if version != expected_head:
        return "migrations_not_at_head"
    # AMD-3: RLS protects tenant data only if the runtime role cannot bypass it.
    unsafe_role = await session.scalar(
        text("""
            SELECT r.rolsuper OR r.rolbypassrls OR EXISTS (
                SELECT 1 FROM pg_tables t
                WHERE t.schemaname = 'public' AND t.tableowner = current_user
            )
            FROM pg_roles r WHERE r.rolname = current_user
        """)
    )
    if unsafe_role:
        return "unsafe_database_role"
    return None


@router.get("/readyz", responses={503: {"description": "Not ready"}})
async def readyz(request: Request) -> StatusOut:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    try:
        async with sessionmaker() as session:
            reason = await _not_ready_reason(session, request.app.state.migration_head)
    except SQLAlchemyError, OSError:
        reason = "database_unreachable"
    if reason is not None:
        logger.warning("not_ready", extra={"reason": reason})
        raise ApiError(503, "unavailable", "Service not ready.")
    return StatusOut(status="ready")
