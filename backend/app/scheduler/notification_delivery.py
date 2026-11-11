"""In-app delivery handler used by the reminder worker.

The handler applies notification preferences before persisting a notification:

* in-app notifications disabled  -> the reminder is suppressed explicitly
* quiet hours active             -> the reminder is deferred until the window ends
* otherwise                      -> a notification row is persisted

A reminder is only marked delivered after the notification row has been
persisted successfully.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder import TaskReminder
from app.models.task import Task
from app.scheduler.reminder_worker import (
    DeliveryOutcome,
    DeliveryOutcomeStatus,
)
from app.services.notification_preference_service import (
    notification_preference_service,
)
from app.services.notification_service import notification_service
from app.services.task_deadlines import utcnow


logger = logging.getLogger(__name__)

UTC = timezone.utc


def _utcnow() -> datetime:
    """Clock hook used by delivery; tests can patch this function."""

    return utcnow()


async def deliver_in_app_notification(
    session: AsyncSession,
    reminder: TaskReminder,
    task: Task,
) -> DeliveryOutcome:
    """Deliver one reminder as an in-app notification, or defer it."""

    pref = await notification_preference_service.get_preferences(
        session=session,
        user_id=reminder.user_id,
    )

    if not pref.in_app_enabled:
        return DeliveryOutcome(
            status=DeliveryOutcomeStatus.SUPPRESSED,
            reason="in-app notifications disabled",
        )

    current = _utcnow().astimezone(UTC)
    quiet_end = notification_preference_service.quiet_hours_end(
        pref,
        current,
    )

    if quiet_end is not None:
        return DeliveryOutcome(
            status=DeliveryOutcomeStatus.DEFERRED,
            next_attempt_at=quiet_end,
            reason="quiet hours",
        )

    await notification_service.deliver_reminder_notification(
        session=session,
        reminder=reminder,
        task=task,
        now=current,
    )

    return DeliveryOutcome(status=DeliveryOutcomeStatus.DELIVERED)


__all__ = ["deliver_in_app_notification"]
