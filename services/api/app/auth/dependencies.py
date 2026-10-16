from typing import Annotated

from fastapi import Depends, Request

from app.auth.service import AuthenticatedUser, resolve_session
from app.core.config import Settings
from app.core.db import DbSession
from app.core.errors import ApiError


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def client_host(request: Request) -> str:
    # Uvicorn rewrites request.client from X-Forwarded-For only for --forwarded-allow-ips peers.
    return request.client.host if request.client else "unknown"


async def get_current_user(
    request: Request, db: DbSession, settings: AppSettings
) -> AuthenticatedUser:
    token = request.cookies.get(settings.session_cookie_name)
    user = await resolve_session(db, token=token) if token else None
    if user is None:
        raise ApiError(401, "unauthenticated", "Authentication required.")
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
