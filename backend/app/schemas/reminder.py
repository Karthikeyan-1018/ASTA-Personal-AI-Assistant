"""Pydantic schemas for task-scoped reminders."""

from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.reminder import ReminderStatus


MAX_REMINDER_OFFSET_MINUTES = 525600  # 365 days


def _require_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "remind_at must include a timezone offset."
        )

    return value


class ReminderCreate(BaseModel):
    """Create a reminder from a deadline offset or an absolute instant.

    Exactly one of ``offset_minutes`` and ``remind_at`` must be supplied.
    ``offset_minutes=0`` means at the task deadline. For a date-only task the
    anchor is the configured local reminder time on the due date, not the end
    of the day.
    """

    model_config = ConfigDict(extra="forbid")

    offset_minutes: int | None = Field(
        default=None,
        ge=0,
        le=MAX_REMINDER_OFFSET_MINUTES,
    )

    remind_at: datetime | None = None

    @field_validator("remind_at")
    @classmethod
    def validate_remind_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_one_source(self):
        has_offset = self.offset_minutes is not None
        has_instant = self.remind_at is not None

        if has_offset and has_instant:
            raise ValueError(
                "Provide either offset_minutes or remind_at, not both."
            )

        if not has_offset and not has_instant:
            raise ValueError(
                "Provide offset_minutes or remind_at."
            )

        return self


class ReminderUpdate(BaseModel):
    """Reschedule a pending reminder.

    Cancelling and deleting use their dedicated endpoints. A cancelled or
    delivered reminder cannot be silently rescheduled.
    """

    model_config = ConfigDict(extra="forbid")

    offset_minutes: int | None = Field(
        default=None,
        ge=0,
        le=MAX_REMINDER_OFFSET_MINUTES,
    )

    remind_at: datetime | None = None

    @field_validator("remind_at")
    @classmethod
    def validate_remind_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_one_source(self):
        has_offset = self.offset_minutes is not None
        has_instant = self.remind_at is not None

        if has_offset and has_instant:
            raise ValueError(
                "Provide either offset_minutes or remind_at, not both."
            )

        if not has_offset and not has_instant:
            raise ValueError(
                "Provide offset_minutes or remind_at."
            )

        return self


class ReminderResponse(BaseModel):
    """A stored task reminder."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    task_id: int
    remind_at: datetime
    offset_minutes: int | None
    status: ReminderStatus
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None
