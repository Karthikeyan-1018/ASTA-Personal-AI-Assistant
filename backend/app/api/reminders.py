"""REST endpoints for owner-scoped task reminders.

Phase 10A exposes CRUD only. Scheduling, notification delivery, and recurring
tasks are implemented in later Phase 10 subphases. Every route derives identity
from the validated server-side session and every lookup filters by owner.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.exceptions.reminder_exceptions import (
    ReminderConflictError,
    ReminderNotFoundError,
    ReminderValidationError,
)
from app.exceptions.task_exceptions import TaskNotFoundError
from app.models.reminder import ReminderStatus
from app.schemas.reminder import (
    ReminderCreate,
    ReminderResponse,
    ReminderUpdate,
)
from app.services.reminder_service import reminder_service


logger = logging.getLogger(__name__)


router = APIRouter(
    tags=["Reminders"],
    dependencies=[Depends(require_csrf)],
)


def _task_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Task not found.",
    )


def _reminder_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Reminder not found.",
    )


def _validation_error(error: Exception) -> HTTPException:
    logger.warning("Invalid reminder request: %s", error)

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    )


def _conflict(error: Exception) -> HTTPException:
    logger.warning("Reminder conflict: %s", error)

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(error),
    )


@router.post(
    "/api/tasks/{task_id}/reminders",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_reminder(
    task_id: int,
    payload: ReminderCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Create a reminder for an owned, open task."""

    try:
        return await reminder_service.create_reminder(
            session=session,
            user_id=user_id,
            task_id=task_id,
            offset_minutes=payload.offset_minutes,
            remind_at=payload.remind_at,
        )

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except ReminderValidationError as error:
        raise _validation_error(error) from error

    except ReminderConflictError as error:
        raise _conflict(error) from error

    except Exception as error:
        logger.exception("Unexpected error while creating a reminder.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create reminder.",
        ) from error


@router.get(
    "/api/tasks/{task_id}/reminders",
    response_model=list[ReminderResponse],
)
async def list_task_reminders(
    task_id: int,
    status_filter: ReminderStatus | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List reminders for one owned task."""

    try:
        return await reminder_service.list_for_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
            status=status_filter,
            limit=limit,
            offset=offset,
        )

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except ReminderValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing task reminders.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve reminders.",
        ) from error


@router.get(
    "/api/reminders",
    response_model=list[ReminderResponse],
)
async def list_user_reminders(
    task_id: int | None = Query(default=None, ge=1),
    status_filter: ReminderStatus | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List the current user's reminders across tasks."""

    try:
        return await reminder_service.list_for_user(
            session=session,
            user_id=user_id,
            task_id=task_id,
            status=status_filter,
            limit=limit,
            offset=offset,
        )

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except ReminderValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing reminders.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve reminders.",
        ) from error


@router.patch(
    "/api/reminders/{reminder_id}",
    response_model=ReminderResponse,
)
async def reschedule_reminder(
    reminder_id: int,
    payload: ReminderUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Reschedule one owned pending reminder."""

    try:
        return await reminder_service.reschedule_reminder(
            session=session,
            user_id=user_id,
            reminder_id=reminder_id,
            offset_minutes=payload.offset_minutes,
            remind_at=payload.remind_at,
        )

    except ReminderNotFoundError as error:
        raise _reminder_not_found() from error

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except ReminderValidationError as error:
        raise _validation_error(error) from error

    except ReminderConflictError as error:
        raise _conflict(error) from error

    except Exception as error:
        logger.exception("Unexpected error while rescheduling a reminder.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to reschedule reminder.",
        ) from error


@router.post(
    "/api/reminders/{reminder_id}/cancel",
    response_model=ReminderResponse,
)
async def cancel_reminder(
    reminder_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Cancel one owned pending reminder."""

    try:
        return await reminder_service.cancel_reminder(
            session=session,
            user_id=user_id,
            reminder_id=reminder_id,
        )

    except ReminderNotFoundError as error:
        raise _reminder_not_found() from error

    except ReminderConflictError as error:
        raise _conflict(error) from error

    except Exception as error:
        logger.exception("Unexpected error while cancelling a reminder.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to cancel reminder.",
        ) from error


@router.delete(
    "/api/reminders/{reminder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_reminder(
    reminder_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Delete one owned reminder."""

    try:
        await reminder_service.delete_reminder(
            session=session,
            user_id=user_id,
            reminder_id=reminder_id,
        )

    except ReminderNotFoundError as error:
        raise _reminder_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while deleting a reminder.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete reminder.",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)
