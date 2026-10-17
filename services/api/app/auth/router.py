from typing import cast

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.audit.writer import record_audit
from app.auth import service
from app.auth.csrf import csrf_token_for, new_csrf_nonce
from app.auth.dependencies import AppSettings, CurrentUser, client_host
from app.auth.schemas import CsrfResponse, LoginRequest, MembershipOut, MeResponse, UserOut
from app.core.config import Settings
from app.core.db import DbSession, set_rls_context
from app.core.errors import ApiError
from app.models import Business, Membership
from app.tenancy.permissions import Role

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = "Invalid email or password."


def _set_cookie(
    response: Response, settings: Settings, name: str, value: str, max_age: int
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=settings.is_production,
        httponly=True,
        samesite="lax" if name == settings.session_cookie_name else "strict",
    )


@router.get("/csrf")
async def get_csrf_token(
    request: Request, response: Response, settings: AppSettings
) -> CsrfResponse:
    nonce = request.cookies.get(settings.csrf_cookie_name)
    if not nonce:
        nonce = new_csrf_nonce()
        _set_cookie(response, settings, settings.csrf_cookie_name, nonce, max_age=60 * 60 * 24 * 7)
    token = csrf_token_for(
        settings.session_secret.get_secret_value().encode(),
        nonce,
        request.cookies.get(settings.session_cookie_name),
    )
    return CsrfResponse(csrf_token=token)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> UserOut:
    secret = settings.session_secret.get_secret_value().encode()
    email = service.normalise_email(body.email)
    host = client_host(request)

    await service.lock_login_attempt(db, secret=secret, email=email, client_host=host)
    retry_after = await service.throttle_retry_after(
        db, secret=secret, email=email, client_host=host
    )
    if retry_after is not None:
        await record_audit(db, action="auth.login_throttled", actor_user_id=None, client_host=host)
        await db.commit()
        raise ApiError(
            429,
            "rate_limited",
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = await service.authenticate(db, email=email, password=body.password)
    await service.record_attempt(
        db, secret=secret, email=email, client_host=host, succeeded=user is not None
    )
    if user is None:
        await record_audit(db, action="auth.login_failed", actor_user_id=None, client_host=host)
        await db.commit()
        raise ApiError(401, "invalid_credentials", _INVALID_CREDENTIALS)

    previous = request.cookies.get(settings.session_cookie_name)
    if previous:
        await service.revoke_session(db, token=previous)
    token = await service.create_session(db, user_id=user.id)
    await record_audit(
        db,
        action="auth.login_succeeded",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        client_host=host,
    )
    await db.commit()
    _set_cookie(
        response,
        settings,
        settings.session_cookie_name,
        token,
        max_age=int(service.SESSION_ABSOLUTE_TIMEOUT.total_seconds()),
    )
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: DbSession, settings: AppSettings
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        user = await service.resolve_session(db, token=token)
        await service.revoke_session(db, token=token)
        if user is not None:
            await record_audit(
                db,
                action="auth.logout",
                actor_user_id=user.id,
                target_type="user",
                target_id=str(user.id),
                client_host=client_host(request),
            )
        await db.commit()
    response.delete_cookie(
        settings.session_cookie_name, path="/", secure=settings.is_production, httponly=True
    )


@router.get("/me")
async def me(user: CurrentUser, db: DbSession) -> MeResponse:
    await set_rls_context(db, user_id=user.id)
    rows = await db.execute(
        select(Membership.business_id, Business.name, Membership.role)
        .join(Business, Business.id == Membership.business_id)
        .where(Membership.user_id == user.id, Business.deleted_at.is_(None))
        .order_by(Business.name, Business.id)
    )
    return MeResponse(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name),
        memberships=[
            MembershipOut(business_id=bid, business_name=name, role=cast(Role, role))
            for bid, name, role in rows.all()
        ],
    )
