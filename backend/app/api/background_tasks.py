"""Authenticated API for in-process background tasks.

User identity always comes from the server-side session. A client can never
submit an owner id, and cross-owner access is reported as a missing task. The
API exposes status, timestamps, and safe error metadata only; stored handler
parameters are not returned.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.core.config import settings
from app.database.connection import get_db
from app.exceptions.background_task_exceptions import (
    BackgroundTaskLimitError,
    BackgroundTaskNotFoundError,
    BackgroundTaskValidationError,
)
from app.services.background_task_service import background_task_service


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/background-tasks",
    tags=["Background Tasks"],
    dependencies=[Depends(require_csrf)],
)


MAX_PARAMETERS_BYTES = 4096


class BackgroundTaskCreate(BaseModel):
    """Submit one allowlisted background task."""

    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(..., min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value)

        except (TypeError, ValueError) as error:
            raise ValueError("must be JSON serializable") from error

        if len(encoded.encode("utf-8")) > MAX_PARAMETERS_BYTES:
            raise ValueError("is too large")

        return value


class BackgroundTaskResponse(BaseModel):
    """Safe public representation of one task."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    status: str
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BackgroundTaskListResponse(BaseModel):
    tasks: list[BackgroundTaskResponse]
    total: int


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Background task not found.",
    )


@router.post(
    "/",
    response_model=BackgroundTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_background_task(
    payload: BackgroundTaskCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Queue one allowlisted task for the authenticated owner."""

    if not settings.BACKGROUND_TASKS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Background task processing is disabled.",
        )

    try:
        return await background_task_service.create_task(
            session=session,
            user_id=user_id,
            task_type=payload.task_type,
            payload=payload.parameters,
        )

    except BackgroundTaskLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
        ) from error

    except BackgroundTaskValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error


@router.get("/", response_model=BackgroundTaskListResponse)
async def list_background_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List the authenticated owner's tasks, newest first."""

    try:
        rows, total = await background_task_service.list_tasks(
            session=session,
            user_id=user_id,
            status=status_filter,
            limit=limit,
            offset=offset,
        )

    except BackgroundTaskValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    return {"tasks": rows, "total": total}


@router.get("/{task_id}", response_model=BackgroundTaskResponse)
async def get_background_task(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return one owned task or a 404 that does not reveal other owners."""

    try:
        return await background_task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

    except BackgroundTaskNotFoundError as error:
        raise _not_found() from error


@router.post(
    "/{task_id}/cancel",
    response_model=BackgroundTaskResponse,
)
async def cancel_background_task(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Cancel an owned task. Terminal tasks are returned unchanged."""

    try:
        return await background_task_service.cancel_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

    except BackgroundTaskNotFoundError as error:
        raise _not_found() from error


__all__ = ["router"]
