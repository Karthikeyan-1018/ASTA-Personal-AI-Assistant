"""Timezone-aware deadline interpretation and classification.

Deadlines are stored as a user-intended wall-clock date/time plus an optional
IANA timezone. Comparisons are always performed in UTC. Date-only deadlines are
treated as the end of the local due date, so they never become overdue before
the due date has passed. A missing timezone is interpreted as UTC rather than
guessing the user's local timezone.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.exceptions.task_exceptions import TaskValidationError
from app.models.task import TaskStatus


UTC = timezone.utc
MAX_TIMEZONE_LENGTH = 64

OPEN_STATUSES = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


class DeadlineState(str, enum.Enum):
    """How a deadline relates to the caller's current instant."""

    OVERDUE = "overdue"
    TODAY = "today"
    TOMORROW = "tomorrow"
    UPCOMING = "upcoming"
    LATER = "later"


RELEVANT_STATES = (
    DeadlineState.OVERDUE,
    DeadlineState.TODAY,
    DeadlineState.TOMORROW,
    DeadlineState.UPCOMING,
)

STATE_RANK = {
    DeadlineState.OVERDUE: 0,
    DeadlineState.TODAY: 1,
    DeadlineState.TOMORROW: 2,
    DeadlineState.UPCOMING: 3,
    DeadlineState.LATER: 4,
}


def utcnow() -> datetime:
    """Return the current timezone-aware UTC instant."""

    return datetime.now(UTC)


def canonical_timezone(name: str | None) -> str | None:
    """Validate and canonicalise an IANA timezone name.

    ``None`` and blank strings mean "no explicit timezone". UTC is handled
    without requiring the optional tz database package.
    """

    if name is None:
        return None

    cleaned = str(name).strip()

    if not cleaned:
        return None

    if len(cleaned) > MAX_TIMEZONE_LENGTH:
        raise TaskValidationError(
            "Timezone cannot exceed "
            f"{MAX_TIMEZONE_LENGTH} characters."
        )

    if cleaned.upper() in {"UTC", "ETC/UTC", "GMT"}:
        return "UTC"

    try:
        return ZoneInfo(cleaned).key

    except (ZoneInfoNotFoundError, ValueError) as error:
        raise TaskValidationError(
            "Timezone must be a recognised IANA timezone name."
        ) from error


def resolve_timezone(name: str | None) -> tzinfo:
    """Return a tzinfo for a stored timezone name.

    UTC always works. Other names require the ``tzdata`` package (or system
    timezone data) to be available.
    """

    canonical = canonical_timezone(name)

    if canonical is None or canonical == "UTC":
        return UTC

    try:
        return ZoneInfo(canonical)

    except (ZoneInfoNotFoundError, ValueError) as error:
        raise TaskValidationError(
            "Timezone must be a recognised IANA timezone name."
        ) from error


def deadline_instant(task: Any) -> datetime | None:
    """Return a task's deadline as a UTC instant, or ``None``.

    A date-only deadline resolves to the final microsecond of that date in the
    task's timezone. A timed deadline uses the stored wall-clock time.
    """

    due_date = getattr(task, "due_date", None)

    if due_date is None:
        return None

    zone = resolve_timezone(getattr(task, "timezone", None))
    due_time = getattr(task, "due_time", None)

    local_wall_clock = datetime.combine(
        due_date,
        due_time if due_time is not None else time.max
    ).replace(tzinfo=zone)

    return local_wall_clock.astimezone(UTC)


def local_date(task: Any, now: datetime) -> date:
    """Return the current date in the task's own timezone."""

    zone = resolve_timezone(getattr(task, "timezone", None))

    return now.astimezone(zone).date()


def classify_deadline(
    task: Any,
    now: datetime,
    horizon_days: int
) -> DeadlineState | None:
    """Classify a task deadline relative to ``now``.

    Returns ``None`` when the task has no deadline. Overdue takes precedence
    over "today" so a timed deadline that has already passed is not reported as
    merely due today.
    """

    instant = deadline_instant(task)

    if instant is None:
        return None

    current = now.astimezone(UTC)

    if instant < current:
        return DeadlineState.OVERDUE

    today = local_date(task, current)

    if getattr(task, "due_date", None) == today:
        return DeadlineState.TODAY

    if getattr(task, "due_date", None) == today + timedelta(days=1):
        return DeadlineState.TOMORROW

    if instant <= current + timedelta(days=max(1, horizon_days)):
        return DeadlineState.UPCOMING

    return DeadlineState.LATER


def is_open_status(status: TaskStatus | str) -> bool:
    """Return whether a status is still actionable."""

    value = getattr(status, "value", status)

    return value in {
        TaskStatus.PENDING.value,
        TaskStatus.IN_PROGRESS.value,
    }
