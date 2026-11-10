"""REST endpoints for confirmation-gated task actions."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.database.connection import get_db
from app.exceptions.task_exceptions import (
    TaskNotFoundError,
    TaskValidationError,
)
from app.exceptions.task_intent_exceptions import (
    PendingActionAlreadyResolvedError,
    PendingActionExpiredError,
    PendingActionNotFoundError,
)
from app.schemas.task_action import (
    ConfirmActionResponse,
    PendingActionResponse,
)
from app.task_intent.executor import task_action_executor
from app.task_intent.pending_service import pending_action_service


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/task-actions", tags=["Task Actions"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Pending action not found.",
    )


def _expired() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="That confirmation has expired.",
    )


def _already_resolved() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="That action has already been handled.",
    )


@router.get(
    "/pending",
    response_model=list[PendingActionResponse],
)
async def list_pending_actions(
    conversation_id: int | None = Query(default=None, ge=1),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return the current user's active pending actions."""

    return await pending_action_service.list_pending(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
    )


@router.post(
    "/{action_id}/confirm",
    response_model=ConfirmActionResponse,
)
async def confirm_pending_action(
    action_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Atomically claim and execute one pending action."""

    try:
        action = await pending_action_service.claim(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

    except PendingActionNotFoundError as error:
        raise _not_found() from error

    except PendingActionExpiredError as error:
        raise _expired() from error

    except PendingActionAlreadyResolvedError as error:
        raise _already_resolved() from error

    try:
        task = await task_action_executor.execute(
            session=session,
            user_id=user_id,
            action=action,
        )

    except TaskNotFoundError as error:
        await pending_action_service.mark_failed(
            session=session,
            user_id=user_id,
            action_id=action.id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The target task no longer exists.",
        ) from error

    except (TaskValidationError, ValueError) as error:
        await pending_action_service.mark_failed(
            session=session,
            user_id=user_id,
            action_id=action.id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    except Exception as error:
        await pending_action_service.mark_failed(
            session=session,
            user_id=user_id,
            action_id=action.id,
        )
        logger.exception(
            "Failed to execute a pending task action for user %s.",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to apply the pending task action.",
        ) from error

    action = await pending_action_service.mark_result(
        session=session,
        user_id=user_id,
        action_id=action.id,
        result_task_id=task.id if task is not None else None,
    )

    return ConfirmActionResponse(action=action, task=task)


@router.post(
    "/{action_id}/reject",
    response_model=PendingActionResponse,
)
async def reject_pending_action(
    action_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Reject one pending action without executing it."""

    try:
        return await pending_action_service.reject(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

    except PendingActionNotFoundError as error:
        raise _not_found() from error

    except PendingActionExpiredError as error:
        raise _expired() from error

    except PendingActionAlreadyResolvedError as error:
        raise _already_resolved() from error
