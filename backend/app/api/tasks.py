"""REST endpoints for structured task and deadline management."""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.database.connection import get_db
from app.exceptions.task_exceptions import (
    TaskNotFoundError,
    TaskValidationError,
)
from app.schemas.task import (
    TaskCreate,
    TaskListResponse,
    TaskPriorityUpdate,
    TaskResponse,
    TaskStatusUpdate,
    TaskUpdate,
)
from app.services.task_service import task_service


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Task not found.")


def _validation_error(error: Exception) -> HTTPException:
    logger.warning("Invalid task request: %s", error)

    return HTTPException(status_code=400, detail=str(error))


@router.post(
    "/",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_task(
    payload: TaskCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Create a task owned by the current user."""

    try:
        return await task_service.create_task(
            session=session,
            user_id=user_id,
            title=payload.title,
            description=payload.description,
            category=payload.category,
            priority=payload.priority,
            status=payload.status,
            due_date=payload.due_date,
            due_time=payload.due_time,
            timezone=payload.timezone,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while creating a task.")
        raise HTTPException(
            status_code=500,
            detail="Unable to create task."
        ) from error


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    category: str | None = None,
    priority: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    due_from: date | None = None,
    due_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = "created_at",
    sort_order: str = "desc",
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """List the current user's tasks with filters and pagination."""

    try:
        tasks, total = await task_service.list_tasks(
            session=session,
            user_id=user_id,
            category=category,
            priority=priority,
            status=status_filter,
            due_from=due_from,
            due_to=due_to,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        return TaskListResponse(
            tasks=tasks,
            total=total,
            limit=limit,
            offset=offset,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing tasks.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve tasks."
        ) from error


# Static routes must be declared before the dynamic ``/{task_id}`` route so
# paths such as ``/upcoming`` are never interpreted as a task id.


@router.get("/upcoming", response_model=list[TaskResponse])
async def get_upcoming_tasks(
    days: int | None = Query(default=None, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return open tasks due today, tomorrow, or within the next N days."""

    try:
        return await task_service.get_upcoming(
            session=session,
            user_id=user_id,
            days=days,
            limit=limit,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing upcoming tasks.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve upcoming tasks."
        ) from error


@router.get("/overdue", response_model=list[TaskResponse])
async def get_overdue_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return open tasks whose deadline has passed."""

    try:
        return await task_service.get_overdue(
            session=session,
            user_id=user_id,
            limit=limit,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing overdue tasks.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve overdue tasks."
        ) from error


@router.get("/today", response_model=list[TaskResponse])
async def get_tasks_due_today(
    limit: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return open tasks due today in their own timezone."""

    try:
        return await task_service.get_due_today(
            session=session,
            user_id=user_id,
            limit=limit,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing tasks due today.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve tasks due today."
        ) from error


@router.get("/tomorrow", response_model=list[TaskResponse])
async def get_tasks_due_tomorrow(
    limit: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return open tasks due tomorrow in their own timezone."""

    try:
        return await task_service.get_due_tomorrow(
            session=session,
            user_id=user_id,
            limit=limit,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while listing tasks due tomorrow."
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve tasks due tomorrow."
        ) from error


@router.get("/completed", response_model=TaskListResponse)
async def get_completed_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return completed tasks, most recently updated first."""

    try:
        tasks, total = await task_service.get_completed(
            session=session,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )

        return TaskListResponse(
            tasks=tasks,
            total=total,
            limit=limit,
            offset=offset,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing completed tasks.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve completed tasks."
        ) from error


@router.get("/pending", response_model=list[TaskResponse])
async def get_pending_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return pending tasks."""

    try:
        return await task_service.get_pending(
            session=session,
            user_id=user_id,
            limit=limit,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing pending tasks.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve pending tasks."
        ) from error


@router.get("/in-progress", response_model=list[TaskResponse])
async def get_in_progress_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return in-progress tasks."""

    try:
        return await task_service.get_in_progress(
            session=session,
            user_id=user_id,
            limit=limit,
        )

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while listing in-progress tasks."
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve in-progress tasks."
        ) from error


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Retrieve one owned task."""

    try:
        return await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while retrieving a task.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve task."
        ) from error


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Partially update one owned task."""

    changes = payload.model_dump(exclude_unset=True)

    try:
        return await task_service.update_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
            changes=changes,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while updating a task.")
        raise HTTPException(
            status_code=500,
            detail="Unable to update task."
        ) from error


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_task(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Delete one owned task."""

    try:
        await task_service.delete_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while deleting a task.")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete task."
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Mark an owned task as completed and record ``completed_at``."""

    try:
        return await task_service.complete_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while completing a task.")
        raise HTTPException(
            status_code=500,
            detail="Unable to complete task."
        ) from error


@router.post("/{task_id}/reopen", response_model=TaskResponse)
async def reopen_task(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Reopen an owned task and clear its completion metadata."""

    try:
        return await task_service.reopen_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while reopening a task.")
        raise HTTPException(
            status_code=500,
            detail="Unable to reopen task."
        ) from error


@router.post("/{task_id}/priority", response_model=TaskResponse)
async def change_task_priority(
    task_id: int,
    payload: TaskPriorityUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Change only an owned task's priority."""

    try:
        return await task_service.change_priority(
            session=session,
            user_id=user_id,
            task_id=task_id,
            priority=payload.priority,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while changing task priority.")
        raise HTTPException(
            status_code=500,
            detail="Unable to change task priority."
        ) from error


@router.post("/{task_id}/status", response_model=TaskResponse)
async def change_task_status(
    task_id: int,
    payload: TaskStatusUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Change only an owned task's status."""

    try:
        return await task_service.change_status(
            session=session,
            user_id=user_id,
            task_id=task_id,
            status=payload.status,
        )

    except TaskNotFoundError as error:
        raise _not_found() from error

    except TaskValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while changing task status.")
        raise HTTPException(
            status_code=500,
            detail="Unable to change task status."
        ) from error
