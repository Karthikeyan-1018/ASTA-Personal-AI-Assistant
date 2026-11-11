"""REST endpoints for owner-scoped notifications and quiet-hour preferences.

Identity always comes from the validated server-side session. Every query
filters by owner and no client-supplied user id is accepted.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.exceptions.notification_exceptions import (
    NotificationNotFoundError,
    NotificationValidationError,
)
from app.models.notification_preference import NotificationPreference
from app.schemas.notification import (
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationReadAllResponse,
    NotificationResponse,
)
from app.services.notification_preference_service import (
    notification_preference_service,
)
from app.services.notification_service import notification_service


logger = logging.getLogger(__name__)


router = APIRouter(
    tags=["Notifications"],
    dependencies=[Depends(require_csrf)],
)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Notification not found.",
    )


def _validation_error(error: Exception) -> HTTPException:
    logger.warning("Invalid notification request: %s", error)

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    )


def _preference_response(
    pref: NotificationPreference,
) -> NotificationPreferenceResponse:
    active = notification_preference_service.is_quiet_hours_active(pref)
    ends_at = (
        notification_preference_service.quiet_hours_end(pref)
        if active
        else None
    )

    return NotificationPreferenceResponse(
        id=pref.id,
        user_id=pref.user_id,
        in_app_enabled=pref.in_app_enabled,
        quiet_hours_enabled=pref.quiet_hours_enabled,
        quiet_hours_start=pref.quiet_hours_start,
        quiet_hours_end=pref.quiet_hours_end,
        timezone=notification_preference_service.effective_timezone(pref),
        quiet_hours_active=active,
        quiet_hours_ends_at=ends_at,
        created_at=pref.created_at,
        updated_at=pref.updated_at,
    )


# Static paths are declared before the dynamic notification-id routes.
@router.get(
    "/api/notifications/preferences",
    response_model=NotificationPreferenceResponse,
)
async def get_notification_preferences(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return the effective notification preferences for the owner."""

    try:
        pref = await notification_preference_service.get_preferences(
            session=session,
            user_id=user_id,
        )
        return _preference_response(pref)

    except NotificationValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while reading notification preferences."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve notification preferences.",
        ) from error


@router.patch(
    "/api/notifications/preferences",
    response_model=NotificationPreferenceResponse,
)
async def update_notification_preferences(
    payload: NotificationPreferenceUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Update notification preferences and quiet hours."""

    changes = payload.model_dump(exclude_unset=True)

    try:
        pref = await notification_preference_service.update_preferences(
            session=session,
            user_id=user_id,
            changes=changes,
        )
        return _preference_response(pref)

    except NotificationValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while updating notification preferences."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update notification preferences.",
        ) from error


@router.post(
    "/api/notifications/read-all",
    response_model=NotificationReadAllResponse,
)
async def mark_all_notifications_read(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Mark every unread notification for the owner as read."""

    try:
        updated = await notification_service.mark_all_read(
            session=session,
            user_id=user_id,
        )
        return NotificationReadAllResponse(updated=updated)

    except Exception as error:
        logger.exception("Unexpected error while marking notifications read.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update notifications.",
        ) from error


@router.get(
    "/api/notifications/unread",
    response_model=list[NotificationResponse],
)
async def list_unread_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List the owner's unread notifications, newest first."""

    try:
        return await notification_service.list_notifications(
            session=session,
            user_id=user_id,
            unread_only=True,
            limit=limit,
            offset=offset,
        )

    except NotificationValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing unread notifications.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve notifications.",
        ) from error


@router.get(
    "/api/notifications",
    response_model=list[NotificationResponse],
)
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List the owner's notification history, newest first."""

    try:
        return await notification_service.list_notifications(
            session=session,
            user_id=user_id,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )

    except NotificationValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing notifications.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve notifications.",
        ) from error


@router.get(
    "/api/notifications/{notification_id}",
    response_model=NotificationResponse,
)
async def get_notification(
    notification_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return one owned notification."""

    try:
        return await notification_service.get_notification(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

    except NotificationNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while reading a notification.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve notification.",
        ) from error


@router.patch(
    "/api/notifications/{notification_id}/read",
    response_model=NotificationResponse,
)
async def mark_notification_read(
    notification_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Mark one owned notification as read. Repeating the call is safe."""

    try:
        return await notification_service.mark_read(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

    except NotificationNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while marking a notification read.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update notification.",
        ) from error


@router.delete(
    "/api/notifications/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification(
    notification_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Delete one owned notification."""

    try:
        await notification_service.delete_notification(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

    except NotificationNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while deleting a notification.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete notification.",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)
