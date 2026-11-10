"""Single-owner authentication endpoints.

The raw session token is only sent to the client as an HttpOnly cookie. The CSRF
token is readable by the frontend and must be echoed in ``X-CSRF-Token`` for
state-changing requests.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.core.config import settings
from app.database.connection import get_db
from app.exceptions.auth_exceptions import (
    InvalidOwnerPasswordError,
    InvalidOwnerUsernameError,
    OwnerAlreadyExistsError,
)
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    OwnerResponse,
    OwnerSetupRequest,
)
from app.services.auth_service import auth_service
from app.services.login_rate_limit_service import (
    login_rate_limit_service,
    normalize_account_key,
    resolve_client_ip,
    setup_account_key,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

LOGIN_RATE_LIMITED_DETAIL = (
    "Too many failed authentication attempts. Try again later."
)


def _rate_limited(retry_after_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=LOGIN_RATE_LIMITED_DETAIL,
        headers={
            "Retry-After": str(max(1, int(retry_after_seconds))),
        },
    )


def _owner_response(owner, user) -> OwnerResponse:
    return OwnerResponse(
        id=user.id,
        username=user.username,
        created_at=user.created_at,
    )


def _cookie_common(path: str) -> dict:
    return {
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.auth_cookie_samesite,
        "path": path,
    }


def _set_auth_cookies(
    response: Response,
    raw_token: str,
    csrf_token: str,
    max_age: int,
) -> None:
    response.set_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        **_cookie_common(settings.AUTH_COOKIE_PATH),
    )

    # The CSRF token must be readable by the frontend so it can send the
    # X-CSRF-Token header. It is not a session credential on its own.
    response.set_cookie(
        key=settings.AUTH_CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        **_cookie_common(settings.AUTH_CSRF_COOKIE_PATH),
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=settings.AUTH_SESSION_COOKIE_NAME,
        httponly=True,
        **_cookie_common(settings.AUTH_COOKIE_PATH),
    )
    response.delete_cookie(
        key=settings.AUTH_CSRF_COOKIE_NAME,
        httponly=False,
        **_cookie_common(settings.AUTH_CSRF_COOKIE_PATH),
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post(
    "/setup",
    response_model=OwnerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def setup_owner(
    payload: OwnerSetupRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Create the single owner account using a bootstrap setup token."""

    configured_token = settings.AUTH_OWNER_SETUP_TOKEN

    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Owner setup is disabled.",
        )

    client_ip = resolve_client_ip(request)
    account_key = setup_account_key(payload.username)

    decision = await login_rate_limit_service.check_allowed(
        session,
        ip_address=client_ip,
        account_key=account_key,
    )

    if not decision.allowed:
        raise _rate_limited(decision.retry_after_seconds)

    provided_token = request.headers.get("X-Setup-Token", "")

    if not hmac.compare_digest(provided_token, configured_token):
        failure = await login_rate_limit_service.record_failure(
            session,
            ip_address=client_ip,
            account_key=account_key,
        )

        logger.warning("Owner setup rejected: invalid setup token.")

        if failure.locked:
            raise _rate_limited(failure.retry_after_seconds)

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid setup token.",
        )

    try:
        owner, user = await auth_service.create_owner(
            session=session,
            username=payload.username,
            password=payload.password,
        )

    except OwnerAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except (InvalidOwnerUsernameError, InvalidOwnerPasswordError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception("Unexpected failure while creating the owner.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create the owner account.",
        ) from error

    await login_rate_limit_service.record_success(
        session,
        ip_address=client_ip,
        account_key=account_key,
    )

    return _owner_response(owner, user)


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
):
    """Authenticate the owner and start a new server-side session."""

    client_ip = resolve_client_ip(request)
    account_key = normalize_account_key(payload.username)

    decision = await login_rate_limit_service.check_allowed(
        session,
        ip_address=client_ip,
        account_key=account_key,
    )

    if not decision.allowed:
        logger.warning("Owner login blocked by rate limiting.")
        raise _rate_limited(decision.retry_after_seconds)

    owner = await auth_service.authenticate(
        session=session,
        username=payload.username,
        password=payload.password,
    )

    if owner is None:
        failure = await login_rate_limit_service.record_failure(
            session,
            ip_address=client_ip,
            account_key=account_key,
        )

        logger.warning("Owner login failed.")

        if failure.locked:
            raise _rate_limited(failure.retry_after_seconds)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    user = await auth_service.get_owner_user(session, owner)

    if user is None:
        logger.error("Owner account references a missing user.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Owner account is misconfigured.",
        )

    raw_token, csrf_token, auth_session = await auth_service.create_session(
        session=session,
        owner=owner,
    )

    await login_rate_limit_service.record_success(
        session,
        ip_address=client_ip,
        account_key=account_key,
    )

    lifetime = auth_service.session_lifetime()
    _set_auth_cookies(
        response,
        raw_token=raw_token,
        csrf_token=csrf_token,
        max_age=int(lifetime.total_seconds()),
    )
    _no_store(response)

    logger.info("Owner login succeeded.")

    return LoginResponse(
        owner=_owner_response(owner, user),
        expires_at=auth_session.expires_at,
        csrf_token=csrf_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Revoke the current session and clear the authentication cookies."""

    raw_token = request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)

    try:
        await auth_service.revoke_session(session, raw_token)

    except Exception as error:
        logger.exception("Unexpected failure while logging out.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to log out securely. Please try again.",
        ) from error

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_auth_cookies(response)
    _no_store(response)

    logger.info("Owner logged out.")
    return response


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return the authenticated owner and session expiry."""

    owner = await auth_service.get_owner(session)

    if owner is None or owner.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    user = await auth_service.get_owner_user(session, owner)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    expires_at: datetime | None = getattr(
        request.state,
        "session_expires_at",
        None,
    )

    if expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    response = MeResponse(
        owner=_owner_response(owner, user),
        session_expires_at=expires_at,
    )

    return response
