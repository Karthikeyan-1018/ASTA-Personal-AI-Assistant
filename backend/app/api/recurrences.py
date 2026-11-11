"""REST endpoints for owner-scoped recurring tasks."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.exceptions.recurrence_exceptions import (
    RecurrenceConflictError,
    RecurrenceNotFoundError,
    RecurrenceValidationError,
)
from app.exceptions.task_exceptions import TaskNotFoundError
from app.schemas.recurrence import (
    RecurrenceCreate,
    RecurrenceOccurrenceResponse,
    RecurrenceResponse,
    RecurrenceUpdate,
)
from app.services.recurrence_service import recurrence_service


logger = logging.getLogger(__name__)


router = APIRouter(
    tags=["Recurrences"],
    dependencies=[Depends(require_csrf)],
)


def _task_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Task not found.",
    )


def _recurrence_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Recurrence not found.",
    )


def _validation_error(error: Exception) -> HTTPException:
    logger.warning("Invalid recurrence request: %s", error)

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    )


def _conflict(error: Exception) -> HTTPException:
    logger.warning("Recurrence conflict: %s", error)

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(error),
    )


@router.post(
    "/api/tasks/{task_id}/recurrence",
    response_model=RecurrenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_recurrence(
    task_id: int,
    payload: RecurrenceCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Create a recurrence rule for an owned, open task."""

    try:
        return await recurrence_service.create_recurrence(
            session=session,
            user_id=user_id,
            task_id=task_id,
            frequency=payload.frequency,
            interval=payload.interval,
            weekdays=payload.weekdays,
            start_date=payload.start_date,
            end_date=payload.end_date,
            timezone_name=payload.timezone,
        )

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except RecurrenceValidationError as error:
        raise _validation_error(error) from error

    except RecurrenceConflictError as error:
        raise _conflict(error) from error

    except Exception as error:
        logger.exception("Unexpected error while creating a recurrence.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create recurrence.",
        ) from error


@router.get(
    "/api/tasks/{task_id}/recurrence",
    response_model=RecurrenceResponse,
)
async def get_task_recurrence(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return the recurrence rule for one owned task."""

    try:
        rule = await recurrence_service.get_for_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        if rule is None:
            raise _recurrence_not_found()

        return rule

    except HTTPException:
        raise

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while reading a recurrence.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve recurrence.",
        ) from error


@router.get(
    "/api/recurrences",
    response_model=list[RecurrenceResponse],
)
async def list_recurrences(
    task_id: int | None = Query(default=None, ge=1),
    include_inactive: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List the owner's recurrence rules."""

    try:
        return await recurrence_service.list_recurrences(
            session=session,
            user_id=user_id,
            task_id=task_id,
            include_inactive=include_inactive,
            limit=limit,
            offset=offset,
        )

    except TaskNotFoundError as error:
        raise _task_not_found() from error

    except RecurrenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing recurrences.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve recurrences.",
        ) from error


@router.get(
    "/api/recurrences/{recurrence_id}",
    response_model=RecurrenceResponse,
)
async def get_recurrence(
    recurrence_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return one owned recurrence rule."""

    try:
        return await recurrence_service.get_recurrence(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
        )

    except RecurrenceNotFoundError as error:
        raise _recurrence_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while reading a recurrence.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve recurrence.",
        ) from error


@router.patch(
    "/api/recurrences/{recurrence_id}",
    response_model=RecurrenceResponse,
)
async def update_recurrence(
    recurrence_id: int,
    payload: RecurrenceUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Update an owned recurrence rule."""

    changes = payload.model_dump(exclude_unset=True)

    try:
        return await recurrence_service.update_recurrence(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
            changes=changes,
        )

    except RecurrenceNotFoundError as error:
        raise _recurrence_not_found() from error

    except RecurrenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while updating a recurrence.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update recurrence.",
        ) from error


@router.delete(
    "/api/recurrences/{recurrence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_recurrence(
    recurrence_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Stop future generation while preserving the rule and history."""

    try:
        await recurrence_service.deactivate_recurrence(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
        )

    except RecurrenceNotFoundError as error:
        raise _recurrence_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while deactivating a recurrence.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to deactivate recurrence.",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/recurrences/{recurrence_id}/occurrences",
    response_model=list[RecurrenceOccurrenceResponse],
)
async def list_occurrences(
    recurrence_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List generated occurrence links for one owned recurrence."""

    try:
        return await recurrence_service.list_occurrences(
            session=session,
            user_id=user_id,
            recurrence_id=recurrence_id,
            limit=limit,
            offset=offset,
        )

    except RecurrenceNotFoundError as error:
        raise _recurrence_not_found() from error

    except RecurrenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing occurrences.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve occurrences.",
        ) from error
