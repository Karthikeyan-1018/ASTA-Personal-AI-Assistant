"""Pydantic schemas for recurring tasks."""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.recurrence import RecurrenceFrequency


MAX_INTERVAL = 365


def _normalise_weekdays(value):
    if value is None:
        return None

    if isinstance(value, str):
        raw = [item for item in value.split(",") if item.strip()]
        value = raw

    try:
        days = sorted({int(item) for item in value})
    except (TypeError, ValueError) as error:
        raise ValueError(
            "weekdays must contain integers from 0 (Monday) to 6 (Sunday)."
        ) from error

    if any(day < 0 or day > 6 for day in days):
        raise ValueError(
            "weekdays must contain integers from 0 (Monday) to 6 (Sunday)."
        )

    return days


class RecurrenceCreate(BaseModel):
    """Create a recurrence rule for an owned task."""

    model_config = ConfigDict(extra="forbid")

    frequency: RecurrenceFrequency
    interval: int = Field(default=1, ge=1, le=MAX_INTERVAL)
    weekdays: list[int] | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("weekdays", mode="before")
    @classmethod
    def validate_weekdays(cls, value):
        return _normalise_weekdays(value)

    @model_validator(mode="after")
    def validate_rule(self):
        if self.frequency != RecurrenceFrequency.WEEKLY and self.weekdays:
            raise ValueError(
                "weekdays can only be supplied for weekly recurrence."
            )

        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date cannot be before start_date.")

        return self


class RecurrenceUpdate(BaseModel):
    """Partially update a recurrence rule."""

    model_config = ConfigDict(extra="forbid")

    frequency: RecurrenceFrequency | None = None
    interval: int | None = Field(default=None, ge=1, le=MAX_INTERVAL)
    weekdays: list[int] | None = None
    end_date: date | None = None
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None

    @field_validator("weekdays", mode="before")
    @classmethod
    def validate_weekdays(cls, value):
        return _normalise_weekdays(value)


class RecurrenceResponse(BaseModel):
    """A stored recurrence rule."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    task_id: int
    frequency: RecurrenceFrequency
    interval: int
    weekdays: list[int]
    start_date: date
    end_date: date | None
    timezone: str | None
    anchor_time: time | None
    is_active: bool
    last_generated_date: date | None
    next_generation_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("weekdays", mode="before")
    @classmethod
    def parse_weekdays(cls, value):
        if value is None or value == "":
            return []

        return _normalise_weekdays(value)


class RecurrenceOccurrenceResponse(BaseModel):
    """A generated occurrence linked to a recurrence rule."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    recurrence_id: int | None
    task_id: int
    occurrence_date: date
    created_at: datetime
