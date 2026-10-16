"""Database engine, per-request sessions and the row-level-security context."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Writes must be committed explicitly by the code that makes them."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def set_rls_context(
    session: AsyncSession, *, user_id: UUID, business_id: UUID | None = None
) -> None:
    """Set transaction-local identifiers read by the RLS policies (cleared at commit/rollback)."""
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(user_id)}
    )
    await session.execute(
        text("SELECT set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id) if business_id else ""},
    )
