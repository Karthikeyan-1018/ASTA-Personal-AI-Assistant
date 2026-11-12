"""Owner-scoped notification persistence, read state, and in-app delivery.

In-app delivery is defined as the successful persistence of the notification
row. The reminder worker only marks a reminder delivered after this service has
returned successfully, and the notification's unique ``(user_id, dedupe_key)``
constraint makes retries idempotent.

Notifications snapshot their own title and body and use ``ON DELETE SET NULL``
for task and reminder references, so history survives task deletion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.notification_exceptions import (
    NotificationConflictError,
    NotificationNotFoundError,
    NotificationValidationError,
)
from app.models.notification import (
    Notification,
    NotificationDeliveryStatus,
    NotificationType,
)
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.task import Task
from app.services.notification_preference_service import (
    notification_preference_service,
)
from app.services.task_deadlines import OPEN_STATUSES, utcnow


logger = logging.getLogger(__name__)

UTC = timezone.utc
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_TITLE_LENGTH = 255
MAX_BODY_LENGTH = 2000


def _utcnow() -> datetime:
    """Clock hook used by the service; tests can patch this function."""

    return utcnow()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def reminder_dedupe_key(reminder_id: int) -> str:
    """Return the idempotency key for one reminder notification."""

    return f"reminder:{reminder_id}"


def _schedule_dedupe_key(task_id: int, remind_at: datetime) -> str:
    """Match the reminder service's schedule key for duplicate prevention."""

    return (
        f"task_reminder:{task_id}:"
        f"{int(remind_at.astimezone(UTC).timestamp())}"
    )


def _supports_for_update(session: AsyncSession) -> bool:
    bind = session.get_bind()

    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


def _normalise_user_instant(value: datetime) -> datetime:
    """Normalise a client-supplied instant, rejecting naive datetimes."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise NotificationValidationError(
            "snooze_until must include a timezone offset."
        )

    return value.astimezone(UTC)


def _max_snooze_minutes() -> int:
    return max(1, settings.NOTIFICATION_MAX_SNOOZE_MINUTES)


def _max_snooze_count() -> int:
    """Return the per-notification snooze limit; 0 or less is unlimited."""

    return settings.NOTIFICATION_SNOOZE_MAX_COUNT


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise NotificationValidationError(
            f"limit must be between 1 and {MAX_PAGE_SIZE}."
        )

    if offset < 0:
        raise NotificationValidationError("offset cannot be negative.")


def _reminder_message(task: Task) -> tuple[str, str]:
    """Build a minimal, owner-only reminder message.

    Task descriptions, conversation text, memories, and credentials are never
    copied into a notification.
    """

    title = f"Task reminder: {task.title}"

    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 3] + "..."

    if task.due_date is None:
        body = f"Your task '{task.title}' has a reminder."
    else:
        due = task.due_date.isoformat()

        if task.due_time is not None:
            due = (
                f"{due} {task.due_time.strftime('%H:%M')} "
                f"({task.timezone or 'UTC'})"
            )

        body = f"Your task '{task.title}' is due {due}."

    if len(body) > MAX_BODY_LENGTH:
        body = body[: MAX_BODY_LENGTH - 3] + "..."

    return title, body


class NotificationService:
    """Asynchronous CRUD, read state, and idempotent delivery."""

    async def get_notification(
        self,
        session: AsyncSession,
        user_id: int,
        notification_id: int,
    ) -> Notification:
        result = await session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )

        notification = result.scalar_one_or_none()

        if notification is None:
            raise NotificationNotFoundError("Notification not found.")

        return notification

    async def list_notifications(
        self,
        session: AsyncSession,
        user_id: int,
        unread_only: bool = False,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[Notification]:
        _validate_page(limit, offset)

        conditions = [Notification.user_id == user_id]

        if unread_only:
            conditions.append(Notification.is_read.is_(False))

        result = await session.execute(
            select(Notification)
            .where(*conditions)
            .order_by(
                Notification.created_at.desc(),
                Notification.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    async def count_unread(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
            )
            or 0
        )

    async def mark_read(
        self,
        session: AsyncSession,
        user_id: int,
        notification_id: int,
        now: datetime | None = None,
    ) -> Notification:
        notification = await self.get_notification(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

        if notification.is_read:
            return notification

        notification.is_read = True
        notification.read_at = (now or _utcnow()).astimezone(UTC)

        try:
            await session.commit()
            await session.refresh(notification)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to mark notification %s as read for user %s.",
                notification_id,
                user_id,
            )
            raise

        return notification

    async def _get_notification_for_update(
        self,
        session: AsyncSession,
        user_id: int,
        notification_id: int,
    ) -> Notification:
        """Return an owned notification while locking it on PostgreSQL."""

        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )

        if _supports_for_update(session):
            stmt = stmt.with_for_update()

        notification = (await session.execute(stmt)).scalar_one_or_none()

        if notification is None:
            raise NotificationNotFoundError("Notification not found.")

        return notification

    async def snooze_notification(
        self,
        session: AsyncSession,
        user_id: int,
        notification_id: int,
        duration_minutes: int | None = None,
        snooze_until: datetime | None = None,
        now: datetime | None = None,
    ) -> Notification:
        """Snooze a delivered notification by re-arming its reminder.

        Snoozing is deliberately not a read-status change. The associated
        reminder returns to ``pending`` with a new schedule and dedupe key, and
        the existing notification row is reused on re-delivery. A second
        notification is never created.
        """

        current = (now or _utcnow()).astimezone(UTC)
        maximum = _max_snooze_minutes()

        if (duration_minutes is None) == (snooze_until is None):
            raise NotificationValidationError(
                "Provide either duration_minutes or snooze_until, not both."
            )

        if duration_minutes is not None:
            try:
                minutes = int(duration_minutes)

            except (TypeError, ValueError) as error:
                raise NotificationValidationError(
                    "duration_minutes must be an integer."
                ) from error

            if minutes < 1 or minutes > maximum:
                raise NotificationValidationError(
                    f"duration_minutes must be between 1 and {maximum}."
                )

            target = current + timedelta(minutes=minutes)

        else:
            target = _normalise_user_instant(snooze_until)

        if target <= current:
            raise NotificationValidationError(
                "Snooze time must be in the future."
            )

        if target > current + timedelta(minutes=maximum):
            raise NotificationValidationError(
                "Snooze time is too far in the future."
            )

        notification = await self._get_notification_for_update(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

        if notification.delivery_status != NotificationDeliveryStatus.DELIVERED:
            raise NotificationConflictError(
                "Only delivered notifications can be snoozed."
            )

        if notification.task_id is None:
            raise NotificationConflictError(
                "The associated task no longer exists."
            )

        if notification.reminder_id is None:
            raise NotificationConflictError(
                "The associated reminder no longer exists."
            )

        limit = _max_snooze_count()

        if limit > 0 and (notification.snooze_count or 0) >= limit:
            raise NotificationConflictError(
                "Snooze limit reached for this notification."
            )

        pref = await notification_preference_service.get_preferences(
            session=session,
            user_id=user_id,
        )

        if not pref.in_app_enabled:
            raise NotificationConflictError(
                "In-app notifications are disabled."
            )

        stmt = select(TaskReminder).where(
            TaskReminder.id == notification.reminder_id,
            TaskReminder.user_id == user_id,
        )

        if _supports_for_update(session):
            stmt = stmt.with_for_update()

        reminder = (await session.execute(stmt)).scalar_one_or_none()

        if reminder is None:
            raise NotificationConflictError(
                "The associated reminder no longer exists."
            )

        if reminder.status == ReminderStatus.PROCESSING:
            raise NotificationConflictError(
                "The reminder is currently being delivered."
            )

        if reminder.status not in (
            ReminderStatus.PENDING,
            ReminderStatus.DELIVERED,
        ):
            raise NotificationConflictError(
                "Only pending or delivered reminders can be snoozed."
            )

        task = await session.scalar(
            select(Task).where(
                Task.id == reminder.task_id,
                Task.user_id == user_id,
            )
        )

        if task is None:
            raise NotificationConflictError(
                "The associated task no longer exists."
            )

        if task.status not in OPEN_STATUSES:
            raise NotificationConflictError(
                "The associated task is no longer open."
            )

        # A snooze target inside quiet hours is moved to the first instant after
        # the window. This matches the delivery handler's deferral rule while
        # keeping the stored schedule honest.
        quiet_end = notification_preference_service.quiet_hours_end(
            pref,
            target,
        )

        effective_target = quiet_end or target
        dedupe_key = _schedule_dedupe_key(task.id, effective_target)

        duplicate = await session.scalar(
            select(TaskReminder).where(
                TaskReminder.user_id == user_id,
                TaskReminder.dedupe_key == dedupe_key,
                TaskReminder.id != reminder.id,
            )
        )

        if duplicate is not None:
            raise NotificationConflictError(
                "Another reminder already exists for this task at that time."
            )

        reminder.status = ReminderStatus.PENDING
        reminder.remind_at = effective_target
        reminder.offset_minutes = None
        reminder.dedupe_key = dedupe_key
        reminder.attempt_count = 0
        reminder.next_attempt_at = effective_target
        reminder.claim_token = None
        reminder.processing_started_at = None
        reminder.last_error = None
        reminder.delivered_at = None

        notification.is_read = True
        notification.read_at = current
        notification.snoozed_at = current
        notification.snoozed_until = effective_target
        notification.snooze_count = (notification.snooze_count or 0) + 1
        notification.reminder_at = effective_target
        notification.last_error = None

        try:
            await session.commit()
            await session.refresh(notification)

        except IntegrityError as error:
            await session.rollback()
            raise NotificationConflictError(
                "Another reminder already exists for this task at that time."
            ) from error

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to snooze notification %s for user %s.",
                notification_id,
                user_id,
            )
            raise

        return notification

    async def mark_all_read(
        self,
        session: AsyncSession,
        user_id: int,
        now: datetime | None = None,
    ) -> int:
        current = (now or _utcnow()).astimezone(UTC)

        try:
            result = await session.execute(
                update(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
                .values(is_read=True, read_at=current)
            )
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to mark all notifications as read for user %s.",
                user_id,
            )
            raise

        return int(result.rowcount or 0)

    async def delete_notification(
        self,
        session: AsyncSession,
        user_id: int,
        notification_id: int,
    ) -> None:
        notification = await self._get_notification_for_update(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

        # Dismissing a notification that is waiting for snooze re-delivery also
        # cancels the re-armed reminder, so a dismissed notification cannot
        # reappear later.
        if (
            notification.snoozed_until is not None
            and notification.reminder_id is not None
        ):
            stmt = select(TaskReminder).where(
                TaskReminder.id == notification.reminder_id,
                TaskReminder.user_id == user_id,
            )

            if _supports_for_update(session):
                stmt = stmt.with_for_update()

            reminder = (await session.execute(stmt)).scalar_one_or_none()

            if reminder is not None and reminder.status == ReminderStatus.PENDING:
                reminder.status = ReminderStatus.CANCELLED
                reminder.next_attempt_at = None

        try:
            await session.delete(notification)
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to delete notification %s for user %s.",
                notification_id,
                user_id,
            )
            raise

    async def deliver_reminder_notification(
        self,
        session: AsyncSession,
        reminder: TaskReminder,
        task: Task,
        now: datetime | None = None,
    ) -> Notification:
        """Persist one notification for a reminder idempotently.

        The caller owns the transaction. This method flushes so uniqueness
        violations surface before the worker commits the reminder lifecycle.
        """

        current = (now or _utcnow()).astimezone(UTC)
        dedupe_key = reminder_dedupe_key(reminder.id)

        existing = await session.scalar(
            select(Notification).where(
                Notification.user_id == reminder.user_id,
                Notification.dedupe_key == dedupe_key,
            )
        )

        if existing is not None:
            if existing.snoozed_until is not None:
                # Re-delivery after a snooze reuses the same notification row.
                # It becomes unread again and the pending snooze marker clears,
                # while the original snooze audit fields are preserved.
                existing.delivery_status = (
                    NotificationDeliveryStatus.DELIVERED
                )
                existing.delivered_at = current
                existing.reminder_at = reminder.remind_at
                existing.is_read = False
                existing.read_at = None
                existing.snoozed_until = None
                existing.last_error = None
                existing.attempt_count = (existing.attempt_count or 0) + 1
                await session.flush()

            elif (
                existing.delivery_status
                != NotificationDeliveryStatus.DELIVERED
            ):
                existing.delivery_status = (
                    NotificationDeliveryStatus.DELIVERED
                )
                existing.delivered_at = current
                existing.last_error = None
                await session.flush()

            return existing

        title, body = _reminder_message(task)

        notification = Notification(
            user_id=reminder.user_id,
            task_id=task.id,
            reminder_id=reminder.id,
            notification_type=NotificationType.REMINDER,
            delivery_status=NotificationDeliveryStatus.DELIVERED,
            title=title,
            body=body,
            task_title=task.title,
            reminder_at=reminder.remind_at,
            is_read=False,
            dedupe_key=dedupe_key,
            attempt_count=1,
            delivered_at=current,
        )

        session.add(notification)
        await session.flush()

        return notification

    async def recover_pending_deliveries(
        self,
        session: AsyncSession,
        now: datetime | None = None,
    ) -> int:
        """Finalise notifications persisted without a delivery marker.

        For the in-app channel, persistence is delivery. A row that exists but
        is still marked pending/failed therefore only needs its delivery
        marker restored. The operation is idempotent.
        """

        current = (now or _utcnow()).astimezone(UTC)

        result = await session.execute(
            update(Notification)
            .where(
                Notification.delivery_status.in_(
                    (
                        NotificationDeliveryStatus.PENDING,
                        NotificationDeliveryStatus.FAILED,
                    )
                )
            )
            .values(
                delivery_status=NotificationDeliveryStatus.DELIVERED,
                delivered_at=current,
                last_error=None,
            )
        )

        await session.commit()

        return int(result.rowcount or 0)

    async def purge_expired(
        self,
        session: AsyncSession,
        now: datetime | None = None,
        retention_days: int | None = None,
    ) -> int:
        """Delete old read notifications according to the retention setting."""

        days = (
            settings.NOTIFICATION_RETENTION_DAYS
            if retention_days is None
            else retention_days
        )

        if days <= 0:
            return 0

        current = (now or _utcnow()).astimezone(UTC)
        cutoff = current - timedelta(days=days)

        try:
            result = await session.execute(
                delete(Notification).where(
                    Notification.is_read.is_(True),
                    Notification.created_at < cutoff,
                    # Never purge a notification that is waiting for snooze
                    # re-delivery; that would lose the user's pending action.
                    Notification.snoozed_until.is_(None),
                )
            )
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception("Failed to purge expired notifications.")
            raise

        return int(result.rowcount or 0)


notification_service = NotificationService()
