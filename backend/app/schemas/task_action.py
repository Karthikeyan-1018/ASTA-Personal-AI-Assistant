"""Pydantic schemas for pending task-action confirmations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.task_action import PendingActionStatus, PendingActionType
from app.schemas.task import TaskResponse


class PendingActionResponse(BaseModel):
    """A safe representation of a server-side pending action.

    The validated payload is intentionally not exposed; the summary already
    contains the proposal the user is confirming.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    action_type: PendingActionType
    status: PendingActionStatus
    target_task_id: int | None
    result_task_id: int | None
    summary: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


class ConfirmActionResponse(BaseModel):
    """The resolved action and the affected task, when one remains."""

    action: PendingActionResponse
    task: TaskResponse | None = None
