"""Owner-scoped reminder persistence and scheduling calculations.

Phase 10A provides CRUD only. No background worker or notification delivery is
performed here. Reminder instants are resolved from the task's wall-clock
deadline and IANA timezone using the existing deadline helpers, then stored as
absolute UTC values for later scheduler use.

Date-only tasks use ``REMINDER_DEFAULT_LOCAL_TIME`` (09:00 by default) on the
due date as their anchor. This avoids the unintuitive end-of-day anchor used
for overdue classification.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.reminder_exceptions import (
    ReminderConflictError,
    ReminderNotFoundError,
    ReminderValidationError,
)
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.task import Task
from app.services.task_deadlines import (
    OPEN_STATUSES,
    resolve_timezone,
    utcnow,
)
from app.services.task_service import task_service


logger = logging.getLogger(__name__)


MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_OFFSET_MINUTES = 525600  # 365 days
DEFAULT_LOCAL_TIME = time(9, 0)


def _utcnow() -> datetime:
    """Clock hook used by the service; tests can patch this function."""

    return utcnow()


def _default_local_time() -> time:
    """Parse the configured date-only reminder anchor time."""

    raw = (settings.REMINDER_DEFAULT_LOCAL_TIME or "09:00").strip()

    try:
        parsed = time.fromisoformat(raw)

    except ValueError:
        logger.warning(
            "Invalid REMINDER_DEFAULT_LOCAL_TIME %r; using 09:00.",
            raw,
        )
        return DEFAULT_LOCAL_TIME

    # A timezone on this setting would make the anchor ambiguous. Task
    # timezones remain authoritative, so discard any supplied offset.
    return parsed.replace(tzinfo=None)


def _normalise_instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderValidationError(
            "remind_at must include a timezone offset."
        )

    return value.astimezone(timezone.utc)


def _anchor_instant(task: Task) -> datetime:
    """Return the task's reminder anchor as a UTC instant."""

    if task.due_date is None:
        raise ReminderValidationError(
            "A deadline offset requires a task with a due date."
        )

    zone = resolve_timezone(task.timezone)

    wall_clock = datetime.combine(
        task.due_date,
        task.due_time if task.due_time is not None else _default_local_time(),
    ).replace(tzinfo=zone)

    return wall_clock.astimezone(timezone.utc)


def _compute_remind_at(
    task: Task,
    offset_minutes: int | None,
    remind_at: datetime | None,
    now: datetime,
) -> tuple[datetime, int | None]:

    if offset_minutes is not None and remind_at is not None:
        raise ReminderValidationError(
            "Provide either offset_minutes or remind_at, not both."
        )

    if offset_minutes is None and remind_at is None:
        raise ReminderValidationError(
            "Provide offset_minutes or remind_at."
        )

    if offset_minutes is not None:
        try:
            offset = int(offset_minutes)

        except (TypeError, ValueError) as error:
            raise ReminderValidationError(
                "offset_minutes must be an integer."
            ) from error

        if offset < 0 or offset > MAX_OFFSET_MINUTES:
            raise ReminderValidationError(
                "offset_minutes must be between 0 and "
                f"{MAX_OFFSET_MINUTES}."
            )

        resolved = _anchor_instant(task) - timedelta(minutes=offset)

    else:
        offset = None
        resolved = _normalise_instant(remind_at)

    if resolved <= now:
        raise ReminderValidationError(
            "Reminder time must be in the future."
        )

    return resolved, offset


def _dedupe_key(task_id: int, remind_at: datetime) -> str:
    """Return a deterministic, content-free duplicate-prevention key."""

    return (
        f"task_reminder:{task_id}:"
        f"{int(remind_at.astimezone(timezone.utc).timestamp())}"
    )


def _supports_for_update(session: AsyncSession) -> bool:
    bind = session.get_bind()

    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


def _normalise_status(value: Any) -> ReminderStatus:
    if isinstance(value, ReminderStatus):
        return value

    try:
        return ReminderStatus(str(value).strip().lower())

    except (ValueError, TypeError) as error:
        allowed = ", ".join(item.value for item in ReminderStatus)
        raise ReminderValidationError(
            f"Unsupported reminder status. Allowed: {allowed}."
        ) from error


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ReminderValidationError(
            f"limit must be between 1 and {MAX_PAGE_SIZE}."
        )

    if offset < 0:
        raise ReminderValidationError("offset cannot be negative.")


def _ensure_open_task(task: Task) -> None:
    if task.status not in OPEN_STATUSES:
        raise ReminderValidationError(
            "Reminders can only be scheduled for pending or in-progress "
            "tasks."
        )


class ReminderService:
    """Asynchronous CRUD for task-scoped reminders."""

    async def create_reminder(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        offset_minutes: int | None = None,
        remind_at: datetime | None = None,
        now: datetime | None = None,
    ) -> TaskReminder:
        """Create an owner-scoped reminder for an open task."""

        task = await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        _ensure_open_task(task)

        current = now or _utcnow()

        resolved, normalised_offset = _compute_remind_at(
            task=task,
            offset_minutes=offset_minutes,
            remind_at=remind_at,
            now=current,
        )

        dedupe_key = _dedupe_key(task.id, resolved)

        existing = await session.scalar(
            select(TaskReminder).where(
                TaskReminder.user_id == user_id,
                TaskReminder.dedupe_key == dedupe_key,
            )
        )

        if existing is not None:
            raise ReminderConflictError(
                "A reminder already exists for this task at that time."
            )

        reminder = TaskReminder(
            user_id=user_id,
            task_id=task.id,
            remind_at=resolved,
            offset_minutes=normalised_offset,
            dedupe_key=dedupe_key,
            status=ReminderStatus.PENDING,
            attempt_count=0,
        )

        try:
            session.add(reminder)
            await session.commit()
            await session.refresh(reminder)

        except IntegrityError as error:
            await session.rollback()
            raise ReminderConflictError(
                "A reminder already exists for this task at that time."
            ) from error

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create a reminder for task %s and user %s.",
                task_id,
                user_id,
            )
            raise

        return reminder

    async def get_reminder(
        self,
        session: AsyncSession,
        user_id: int,
        reminder_id: int,
    ) -> TaskReminder:
        """Return an owned reminder or raise ``ReminderNotFoundError``."""

        result = await session.execute(
            select(TaskReminder).where(
                TaskReminder.id == reminder_id,
                TaskReminder.user_id == user_id,
            )
        )

        reminder = result.scalar_one_or_none()

        if reminder is None:
            raise ReminderNotFoundError("Reminder not found.")

        return reminder

    async def _get_reminder_for_update(
        self,
        session: AsyncSession,
        user_id: int,
        reminder_id: int,
    ) -> TaskReminder:
        """Return an owned reminder while locking it on PostgreSQL.

        Mutations use this variant so a concurrent scheduler claim and a
        reschedule or cancellation cannot interleave. SQLite serialises writers
        and is supported for tests.
        """

        stmt = select(TaskReminder).where(
            TaskReminder.id == reminder_id,
            TaskReminder.user_id == user_id,
        )

        if _supports_for_update(session):
            stmt = stmt.with_for_update()

        reminder = (await session.execute(stmt)).scalar_one_or_none()

        if reminder is None:
            raise ReminderNotFoundError("Reminder not found.")

        return reminder

    async def list_for_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        status: ReminderStatus | str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[TaskReminder]:
        """List reminders for one owned task."""

        # Ownership is validated even when the task has no reminders.
        await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        _validate_page(limit, offset)

        conditions = [
            TaskReminder.user_id == user_id,
            TaskReminder.task_id == task_id,
        ]

        if status is not None:
            conditions.append(
                TaskReminder.status == _normalise_status(status)
            )

        result = await session.execute(
            select(TaskReminder)
            .where(*conditions)
            .order_by(TaskReminder.remind_at.asc(), TaskReminder.id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    async def list_for_user(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int | None = None,
        status: ReminderStatus | str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[TaskReminder]:
        """List the current user's reminders across tasks."""

        _validate_page(limit, offset)

        if task_id is not None:
            # A non-owned task must not be distinguishable from a missing one.
            await task_service.get_task(
                session=session,
                user_id=user_id,
                task_id=task_id,
            )

        conditions = [TaskReminder.user_id == user_id]

        if task_id is not None:
            conditions.append(TaskReminder.task_id == task_id)

        if status is not None:
            conditions.append(
                TaskReminder.status == _normalise_status(status)
            )

        result = await session.execute(
            select(TaskReminder)
            .where(*conditions)
            .order_by(TaskReminder.remind_at.asc(), TaskReminder.id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    async def reschedule_reminder(
        self,
        session: AsyncSession,
        user_id: int,
        reminder_id: int,
        offset_minutes: int | None = None,
        remind_at: datetime | None = None,
        now: datetime | None = None,
    ) -> TaskReminder:
        """Reschedule a pending reminder using a new offset or instant."""

        reminder = await self._get_reminder_for_update(
            session=session,
            user_id=user_id,
            reminder_id=reminder_id,
        )

        if reminder.status != ReminderStatus.PENDING:
            raise ReminderConflictError(
                "Only pending reminders can be rescheduled."
            )

        task = await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=reminder.task_id,
        )

        _ensure_open_task(task)

        current = now or _utcnow()

        resolved, normalised_offset = _compute_remind_at(
            task=task,
            offset_minutes=offset_minutes,
            remind_at=remind_at,
            now=current,
        )

        dedupe_key = _dedupe_key(task.id, resolved)

        existing = await session.scalar(
            select(TaskReminder).where(
                TaskReminder.user_id == user_id,
                TaskReminder.dedupe_key == dedupe_key,
                TaskReminder.id != reminder.id,
            )
        )

        if existing is not None:
            raise ReminderConflictError(
                "Another reminder already exists for this task at that time."
            )

        reminder.remind_at = resolved
        reminder.offset_minutes = normalised_offset
        reminder.dedupe_key = dedupe_key
        reminder.attempt_count = 0
        reminder.next_attempt_at = None
        reminder.claim_token = None
        reminder.processing_started_at = None
        reminder.last_error = None
        reminder.delivered_at = None

        try:
            await session.commit()
            await session.refresh(reminder)

        except IntegrityError as error:
            await session.rollback()
            raise ReminderConflictError(
                "Another reminder already exists for this task at that time."
            ) from error

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to reschedule reminder %s for user %s.",
                reminder_id,
                user_id,
            )
            raise

        return reminder

    async def cancel_reminder(
        self,
        session: AsyncSession,
        user_id: int,
        reminder_id: int,
    ) -> TaskReminder:
        """Cancel a pending reminder. Cancelling twice is idempotent."""

        reminder = await self._get_reminder_for_update(
            session=session,
            user_id=user_id,
            reminder_id=reminder_id,
        )

        if reminder.status == ReminderStatus.CANCELLED:
            return reminder

        if reminder.status != ReminderStatus.PENDING:
            raise ReminderConflictError(
                "Only pending reminders can be cancelled."
            )

        reminder.status = ReminderStatus.CANCELLED

        try:
            await session.commit()
            await session.refresh(reminder)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to cancel reminder %s for user %s.",
                reminder_id,
                user_id,
            )
            raise

        return reminder

    async def delete_reminder(
        self,
        session: AsyncSession,
        user_id: int,
        reminder_id: int,
    ) -> None:
        """Delete an owned reminder permanently."""

        reminder = await self.get_reminder(
            session=session,
            user_id=user_id,
            reminder_id=reminder_id,
        )

        try:
            await session.delete(reminder)
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to delete reminder %s for user %s.",
                reminder_id,
                user_id,
            )
            raise


reminder_service = ReminderService()
