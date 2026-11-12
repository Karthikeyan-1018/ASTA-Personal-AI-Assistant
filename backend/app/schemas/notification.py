"""Pydantic schemas for in-app notifications and their preferences."""

from __future__ import annotations

from datetime import datetime, time

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.notification import (
    NotificationDeliveryStatus,
    NotificationType,
)


class NotificationResponse(BaseModel):
    """A stored, owner-scoped notification."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    task_id: int | None
    reminder_id: int | None
    notification_type: NotificationType
    delivery_status: NotificationDeliveryStatus
    title: str
    body: str
    task_title: str | None
    reminder_at: datetime | None
    is_read: bool
    read_at: datetime | None
    snoozed_at: datetime | None
    snoozed_until: datetime | None
    snooze_count: int
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationSnoozeRequest(BaseModel):
    """Snooze a delivered notification by duration or absolute instant.

    Exactly one of ``duration_minutes`` and ``snooze_until`` must be supplied.
    Common durations are 10, 30, and 60 minutes, and 1440 minutes for "until
    tomorrow". ``snooze_until`` must include a timezone offset and is normalised
    to UTC by the service.
    """

    model_config = ConfigDict(extra="forbid")

    duration_minutes: int | None = Field(
        default=None,
        ge=1,
        le=525600,
    )

    snooze_until: datetime | None = None

    @field_validator("snooze_until")
    @classmethod
    def validate_snooze_until(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "snooze_until must include a timezone offset."
            )

        return value

    @model_validator(mode="after")
    def validate_one_source(self):
        has_duration = self.duration_minutes is not None
        has_instant = self.snooze_until is not None

        if has_duration and has_instant:
            raise ValueError(
                "Provide either duration_minutes or snooze_until, not both."
            )

        if not has_duration and not has_instant:
            raise ValueError(
                "Provide duration_minutes or snooze_until."
            )

        return self


class NotificationReadAllResponse(BaseModel):
    """Result of marking every unread notification as read."""

    updated: int


class NotificationPreferenceUpdate(BaseModel):
    """Partial update of the owner's notification preferences."""

    model_config = ConfigDict(extra="forbid")

    in_app_enabled: bool | None = None
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def validate_time(cls, value: time | None) -> time | None:
        if value is None:
            return None

        if value.tzinfo is not None:
            raise ValueError(
                "Quiet-hour times must be wall-clock values without a "
                "timezone. Use the timezone field instead."
            )

        return value

    @model_validator(mode="after")
    def validate_window(self):
        if (
            self.quiet_hours_enabled is True
            and self.quiet_hours_start is not None
            and self.quiet_hours_end is not None
            and self.quiet_hours_start == self.quiet_hours_end
        ):
            raise ValueError(
                "quiet_hours_start and quiet_hours_end must differ."
            )

        return self


class NotificationPreferenceResponse(BaseModel):
    """The effective notification preferences for the owner."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None
    user_id: int
    in_app_enabled: bool
    quiet_hours_enabled: bool
    quiet_hours_start: time
    quiet_hours_end: time
    timezone: str
    quiet_hours_active: bool
    quiet_hours_ends_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
