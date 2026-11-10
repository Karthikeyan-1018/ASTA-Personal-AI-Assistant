"""Strict Pydantic schemas for task intent, extraction, and pending actions."""

from __future__ import annotations

from datetime import date, time

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.task import TaskCategory, TaskPriority, TaskStatus
from app.services.task_deadlines import canonical_timezone
from app.task_intent.enums import TaskIntent


MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000
MAX_TIMEZONE_LENGTH = 64


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"must not exceed {MAX_TITLE_LENGTH} characters"
        )

    return cleaned


def _clean_description(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    if len(cleaned) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(
            f"must not exceed {MAX_DESCRIPTION_LENGTH} characters"
        )

    return cleaned


def _clean_timezone(value: str | None) -> str | None:
    if value is None:
        return None

    if len(value) > MAX_TIMEZONE_LENGTH:
        raise ValueError(
            f"must not exceed {MAX_TIMEZONE_LENGTH} characters"
        )

    return canonical_timezone(value)


class TaskFilters(BaseModel):
    """Optional filters extracted from a task query."""

    model_config = ConfigDict(extra="forbid")

    category: TaskCategory | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None


class TaskDraft(BaseModel):
    """Validated fields for a proposed new task."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    category: TaskCategory = TaskCategory.OTHER
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: date | None = None
    due_time: time | None = None
    timezone: str | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        return _clean_title(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _clean_description(value)

    @field_validator("timezone")
    @classmethod
    def clean_timezone(cls, value: str | None) -> str | None:
        return _clean_timezone(value)

    @model_validator(mode="after")
    def validate_deadline_combination(self):
        if self.due_time is not None and self.due_date is None:
            raise ValueError("due_time requires due_date.")

        if self.timezone is not None and self.due_date is None:
            raise ValueError("timezone requires due_date.")

        if self.due_time is not None and self.timezone is None:
            raise ValueError("A timed deadline requires a timezone.")

        return self


class TaskChanges(BaseModel):
    """Validated fields for a proposed task update."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    category: TaskCategory | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    due_time: time | None = None
    timezone: str | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        return _clean_title(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _clean_description(value)

    @field_validator("timezone")
    @classmethod
    def clean_timezone(cls, value: str | None) -> str | None:
        return _clean_timezone(value)

    @model_validator(mode="after")
    def validate_deadline_combination(self):
        if self.due_time is not None and self.due_date is None:
            raise ValueError("due_time requires due_date.")

        if self.timezone is not None and self.due_date is None:
            raise ValueError("timezone requires due_date.")

        if self.due_time is not None and self.timezone is None:
            raise ValueError("A timed deadline requires a timezone.")

        return self


class TaskIntentResult(BaseModel):
    """Deterministic interpretation of one user message.

    The result is a *proposal*, never an execution instruction. ``draft`` and
    ``changes`` are validated against the same enums and deadline helpers used
    by the existing task API.
    """

    model_config = ConfigDict(extra="forbid")

    intent: TaskIntent = TaskIntent.NO_TASK_ACTION
    draft: TaskDraft | None = None
    changes: TaskChanges | None = None
    target_hint: str | None = Field(
        default=None,
        max_length=MAX_TITLE_LENGTH,
    )
    filters: TaskFilters | None = None
    clarification: str | None = None
    invalid_reason: str | None = None


class LLMTaskIntent(BaseModel):
    """Schema accepted from the LLM.

    Deliberately excludes task IDs, user IDs, and raw database fields. Unknown
    keys are rejected, so a model cannot smuggle an execution target or owner.
    """

    model_config = ConfigDict(extra="forbid")

    intent: TaskIntent
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(
        default=None,
        max_length=MAX_DESCRIPTION_LENGTH,
    )
    category: TaskCategory | None = None
    priority: TaskPriority | None = None
    due_expression: str | None = Field(default=None, max_length=128)
    time_expression: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(
        default=None,
        max_length=MAX_TIMEZONE_LENGTH,
    )
    target_hint: str | None = Field(
        default=None,
        max_length=MAX_TITLE_LENGTH,
    )
    category_filter: TaskCategory | None = None
    priority_filter: TaskPriority | None = None
    status_filter: TaskStatus | None = None
