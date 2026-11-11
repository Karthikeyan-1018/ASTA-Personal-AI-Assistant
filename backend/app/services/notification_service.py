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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.notification_exceptions import (
    NotificationNotFoundError,
    NotificationValidationError,
)
from app.models.notification import (
    Notification,
    NotificationDeliveryStatus,
    NotificationType,
)
from app.models.reminder import TaskReminder
from app.models.task import Task
from app.services.task_deadlines import utcnow


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
        notification = await self.get_notification(
            session=session,
            user_id=user_id,
            notification_id=notification_id,
        )

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
            if existing.delivery_status != NotificationDeliveryStatus.DELIVERED:
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
                )
            )
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception("Failed to purge expired notifications.")
            raise

        return int(result.rowcount or 0)


notification_service = NotificationService()
