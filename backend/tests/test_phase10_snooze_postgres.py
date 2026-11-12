"""Optional PostgreSQL concurrency tests for snoozing and rescheduling.

These tests exercise real row locking and unique constraints. They are skipped
unless ``ASTA_PHASE10_POSTGRES_TESTS=1`` so the default SQLite regression suite
remains self-contained. Run them against the configured ``asta_db``:

    $env:ASTA_PHASE10_POSTGRES_TESTS="1"
    ..\\venv\\Scripts\\python.exe -m unittest tests.test_phase10_snooze_postgres -v

Each test creates uniquely named temporary users and removes them afterwards.
No existing row is modified.
"""

import asyncio
import os
import unittest
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, select

from app.database.connection import AsyncSessionLocal, engine
from app.exceptions.notification_exceptions import NotificationConflictError
from app.exceptions.reminder_exceptions import ReminderConflictError
from app.models.notification import Notification
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.task import Task
from app.models.user import User
from app.scheduler.reminder_worker import ReminderWorker
from app.services.notification_service import (
    _schedule_dedupe_key,
    notification_service,
)
from app.services.reminder_service import reminder_service
from app.services.task_service import task_service


UTC = timezone.utc
ENABLED = os.getenv("ASTA_PHASE10_POSTGRES_TESTS") == "1"


@unittest.skipUnless(
    ENABLED,
    "Set ASTA_PHASE10_POSTGRES_TESTS=1 to run real PostgreSQL tests.",
)
class SnoozePostgresTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.suffix = uuid.uuid4().hex[:12]
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.remind_at = self.now + timedelta(hours=2)

        async with AsyncSessionLocal() as session:
            user = User(username=f"snooze_pg_{self.suffix}")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            self.user_id = user.id

            self.task = await task_service.create_task(
                session=session,
                user_id=self.user_id,
                title=f"Snooze PG {self.suffix}",
                due_date=date(2026, 12, 1),
                due_time=time(9, 0),
                timezone="UTC",
            )
            self.task_id = self.task.id

    async def asyncTearDown(self):
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(TaskReminder).where(
                    TaskReminder.user_id == self.user_id
                )
            )
            await session.execute(
                delete(Task).where(Task.user_id == self.user_id)
            )
            await session.execute(
                delete(User).where(User.id == self.user_id)
            )
            await session.commit()

        # Each IsolatedAsyncioTestCase runs on its own event loop. Dispose the
        # shared pool so asyncpg connections are not reused across loops.
        await engine.dispose()

    async def _delivered_pair(self, remind_at=None):
        async with AsyncSessionLocal() as session:
            reminder = await reminder_service.create_reminder(
                session=session,
                user_id=self.user_id,
                task_id=self.task_id,
                remind_at=remind_at or self.remind_at,
                now=self.now,
            )

            task = await task_service.get_task(
                session=session,
                user_id=self.user_id,
                task_id=self.task_id,
            )

            notification = await notification_service.deliver_reminder_notification(
                session=session,
                reminder=reminder,
                task=task,
                now=self.now,
            )

            reminder.status = ReminderStatus.DELIVERED
            reminder.delivered_at = self.now
            await session.commit()
            await session.refresh(reminder)
            await session.refresh(notification)

            return reminder.id, notification.id

    async def test_snooze_waits_for_row_lock(self):
        reminder_id, notification_id = await self._delivered_pair()

        async with AsyncSessionLocal() as holder:
            await holder.execute(
                select(TaskReminder)
                .where(TaskReminder.id == reminder_id)
                .with_for_update()
            )

            async def snooze():
                async with AsyncSessionLocal() as session:
                    return await notification_service.snooze_notification(
                        session=session,
                        user_id=self.user_id,
                        notification_id=notification_id,
                        duration_minutes=30,
                        now=self.now,
                    )

            task = asyncio.create_task(snooze())
            await asyncio.sleep(0.25)

            self.assertFalse(
                task.done(),
                "Snooze should block behind the reminder row lock.",
            )

            await holder.commit()

        result = await asyncio.wait_for(task, timeout=10)

        self.assertEqual(
            result.snoozed_until.astimezone(UTC),
            self.now + timedelta(minutes=30),
        )

    async def test_snooze_conflicts_when_worker_claims_first(self):
        reminder_id, notification_id = await self._delivered_pair()

        async with AsyncSessionLocal() as session:
            reminder = await session.get(TaskReminder, reminder_id)
            reminder.status = ReminderStatus.PROCESSING
            reminder.claim_token = "pg-claim"
            reminder.processing_started_at = self.now
            await session.commit()

        with self.assertRaises(NotificationConflictError):
            async with AsyncSessionLocal() as session:
                await notification_service.snooze_notification(
                    session=session,
                    user_id=self.user_id,
                    notification_id=notification_id,
                    duration_minutes=30,
                    now=self.now,
                )

        async with AsyncSessionLocal() as session:
            reminder = await session.get(TaskReminder, reminder_id)
            stored = await session.get(Notification, notification_id)

        self.assertEqual(reminder.status, ReminderStatus.PROCESSING)
        self.assertEqual(stored.snooze_count, 0)
        self.assertFalse(stored.is_read)

    async def test_concurrent_reschedule_to_same_instant_is_unique(self):
        async with AsyncSessionLocal() as session:
            first = await reminder_service.create_reminder(
                session=session,
                user_id=self.user_id,
                task_id=self.task_id,
                remind_at=self.now + timedelta(hours=3),
                now=self.now,
            )
            second = await reminder_service.create_reminder(
                session=session,
                user_id=self.user_id,
                task_id=self.task_id,
                remind_at=self.now + timedelta(hours=4),
                now=self.now,
            )
            first_id, second_id = first.id, second.id

        target = self.now + timedelta(hours=5)

        async def reschedule(reminder_id):
            async with AsyncSessionLocal() as session:
                try:
                    await reminder_service.reschedule_reminder(
                        session=session,
                        user_id=self.user_id,
                        reminder_id=reminder_id,
                        remind_at=target,
                        now=self.now,
                    )
                    return "ok"

                except ReminderConflictError:
                    return "conflict"

        results = await asyncio.gather(
            reschedule(first_id),
            reschedule(second_id),
        )

        self.assertEqual(sorted(results), ["conflict", "ok"])

        async with AsyncSessionLocal() as session:
            rows = list(
                (
                    await session.scalars(
                        select(TaskReminder).where(
                            TaskReminder.user_id == self.user_id,
                            TaskReminder.dedupe_key
                            == _schedule_dedupe_key(
                                self.task_id,
                                target,
                            ),
                        )
                    )
                ).all()
            )

        self.assertEqual(len(rows), 1)

    async def test_conflict_rolls_back_notification_metadata(self):
        reminder_id, notification_id = await self._delivered_pair()

        async with AsyncSessionLocal() as session:
            await reminder_service.create_reminder(
                session=session,
                user_id=self.user_id,
                task_id=self.task_id,
                remind_at=self.now + timedelta(minutes=45),
                now=self.now,
            )

        with self.assertRaises(NotificationConflictError):
            async with AsyncSessionLocal() as session:
                await notification_service.snooze_notification(
                    session=session,
                    user_id=self.user_id,
                    notification_id=notification_id,
                    duration_minutes=45,
                    now=self.now,
                )

        async with AsyncSessionLocal() as session:
            reminder = await session.get(TaskReminder, reminder_id)
            self.assertEqual(reminder.status, ReminderStatus.DELIVERED)

            stored = await session.get(Notification, notification_id)

        self.assertEqual(stored.snooze_count, 0)
        self.assertFalse(stored.is_read)
        self.assertIsNone(stored.snoozed_until)


if __name__ == "__main__":
    unittest.main()
