"""Authentication dependencies for protected ASTA routes.

User identity is derived exclusively from the server-side session referenced by
the HttpOnly session cookie. A user id is never accepted from a request body,
query string, or header.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.connection import get_db
from app.services.auth_service import auth_service


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _session_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)


async def _validated_session(
    request: Request,
    session: AsyncSession,
    *,
    touch: bool,
):
    auth_session = await auth_service.get_session_by_token(
        session,
        _session_cookie(request),
    )

    if auth_session is None:
        return None

    if touch:
        await auth_service.touch_session(session, auth_session)

    request.state.auth_session = auth_session
    request.state.user_id = auth_session.user_id
    request.state.session_expires_at = auth_session.expires_at

    return auth_session


def _unauthorised() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Authentication required.",
    )


async def get_current_user_id(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> int:
    """Return the authenticated owner user id or raise ``401``."""

    auth_session = await _validated_session(request, session, touch=True)

    if auth_session is None:
        raise _unauthorised()

    return auth_session.user_id


async def get_optional_user_id(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> int | None:
    """Return the authenticated owner user id, or ``None`` if absent.

    Retained for compatibility with internal callers and existing tests. Public
    routes that must be protected use :func:`get_current_user_id`.
    """

    auth_session = await _validated_session(request, session, touch=True)

    if auth_session is None:
        return None

    return auth_session.user_id


async def require_csrf(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Reject unsafe cookie-authenticated requests without a valid CSRF token.

    The CSRF token is bound to the session row, so possessing the session
    cookie alone is not enough to perform a state-changing request.
    """

    if request.method.upper() in SAFE_METHODS:
        return

    if not _session_cookie(request):
        # No cookie means the request is not cookie-authenticated. The route's
        # authentication dependency returns 401 if protection is required.
        return

    auth_session = await auth_service.get_session_by_token(
        session,
        _session_cookie(request),
    )

    if auth_session is None:
        # Invalid/expired/revoked cookies are handled by authentication.
        return

    csrf_token = request.headers.get("X-CSRF-Token")

    if not auth_service.verify_csrf(auth_session, csrf_token):
        raise HTTPException(
            status_code=403,
            detail="CSRF validation failed.",
        )
