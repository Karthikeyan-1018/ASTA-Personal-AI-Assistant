"""Pydantic schemas for task management and deadline validation."""

from datetime import date, datetime, time

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.task import TaskCategory, TaskPriority, TaskStatus
from app.services.task_deadlines import canonical_timezone


MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000
MAX_TIMEZONE_LENGTH = 64


def _strip_not_blank(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError("must not be blank")

    return cleaned


def _validate_timezone(value: str | None) -> str | None:
    if value is None:
        return None

    try:
        return canonical_timezone(value)

    except Exception as error:
        raise ValueError(str(error)) from error


def _validate_description(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()

    return cleaned or None


class TaskCreate(BaseModel):
    """Payload for creating a task."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TITLE_LENGTH
    )

    description: str | None = Field(
        default=None,
        max_length=MAX_DESCRIPTION_LENGTH
    )

    category: TaskCategory = TaskCategory.OTHER
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING

    due_date: date | None = None
    due_time: time | None = None

    timezone: str | None = Field(
        default=None,
        max_length=MAX_TIMEZONE_LENGTH
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return _strip_not_blank(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _validate_description(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def validate_deadline_combination(self):
        if self.due_time is not None and self.due_date is None:
            raise ValueError("due_time requires due_date.")

        if self.timezone is not None and self.due_date is None:
            raise ValueError("timezone requires due_date.")

        return self


class TaskUpdate(BaseModel):
    """Payload for partially updating a task.

    Deadline combinations are validated against the stored task by the service,
    because an update may only supply one side of the combination.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TITLE_LENGTH
    )

    description: str | None = Field(
        default=None,
        max_length=MAX_DESCRIPTION_LENGTH
    )

    category: TaskCategory | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None

    due_date: date | None = None
    due_time: time | None = None

    timezone: str | None = Field(
        default=None,
        max_length=MAX_TIMEZONE_LENGTH
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return value

        return _strip_not_blank(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _validate_description(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value)


class TaskPriorityUpdate(BaseModel):
    """Payload for changing only a task's priority."""

    model_config = ConfigDict(extra="forbid")

    priority: TaskPriority


class TaskStatusUpdate(BaseModel):
    """Payload for changing only a task's status."""

    model_config = ConfigDict(extra="forbid")

    status: TaskStatus


class TaskResponse(BaseModel):
    """A stored task and its deadline information."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    description: str | None
    category: TaskCategory
    priority: TaskPriority
    status: TaskStatus
    due_date: date | None
    due_time: time | None
    timezone: str | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TaskListResponse(BaseModel):
    """A page of tasks plus pagination metadata."""

    tasks: list[TaskResponse]
    total: int
    limit: int
    offset: int
