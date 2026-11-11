"""Phase 10E reliability and recovery tests.

Covers abandoned claims, restart recovery, per-reminder failure isolation,
rollback of partially persisted notifications, bounded attempts, notification
delivery-marker recovery, retention, scheduler error isolation, and graceful
shutdown.
"""

import asyncio
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database.base import Base
from app.models.notification import (
    Notification,
    NotificationDeliveryStatus,
)
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.user import User
from app.scheduler.notification_delivery import (
    deliver_in_app_notification,
)
from app.scheduler.reminder_worker import (
    DeliveryOutcome,
    DeliveryOutcomeStatus,
    ReminderWorker,
)
from app.scheduler.runner import BackgroundScheduler
from app.services.notification_service import notification_service
from app.services.reminder_service import reminder_service
from app.services.task_service import task_service


FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
UTC = timezone.utc


class _FakeWorker:
    def __init__(self, failures: int = 0):
        self.calls = 0
        self.failures = failures
        self.batch_size = 1

    async def run_once(self, now=None):
        self.calls += 1

        if self.calls <= self.failures:
            raise RuntimeError("worker cycle failed")

        return {}


class ReliabilityTestBase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async with self.SessionLocal() as session:
            alice = User(username="rel_alice")
            session.add(alice)
            await session.commit()
            await session.refresh(alice)
            self.alice_id = alice.id

            self.task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Reliability task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

        self._clock = patch(
            "app.services.reminder_service._utcnow",
            return_value=FIXED_NOW,
        )
        self._clock.start()
        self.addCleanup(self._clock.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _due_reminder(self, offset_seconds=0, task_id=None):
        async with self.SessionLocal() as session:
            reminder = await reminder_service.create_reminder(
                session=session,
                user_id=self.alice_id,
                task_id=task_id or self.task.id,
                offset_minutes=60,
            )
            reminder.remind_at = (
                FIXED_NOW - timedelta(minutes=1) + timedelta(seconds=offset_seconds)
            )
            await session.commit()
            await session.refresh(reminder)
            return reminder

    async def _new_task(self, title):
        async with self.SessionLocal() as session:
            return await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title=title,
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

    async def _get_reminder(self, reminder_id):
        async with self.SessionLocal() as session:
            return await session.get(TaskReminder, reminder_id)

    async def _notification_count(self):
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(Notification)
                )
                or 0
            )


class RecoveryTests(ReliabilityTestBase):

    async def test_abandoned_claim_is_recovered_after_restart(self):
        reminder = await self._due_reminder()

        crashed = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
            lease_seconds=60,
        )
        claimed = await crashed.claim_batch(FIXED_NOW)
        self.assertEqual(len(claimed), 1)

        # Simulate the process disappearing after the claim: no handler runs.
        restarted = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
            lease_seconds=60,
        )

        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=FIXED_NOW + timedelta(seconds=61),
        ):
            stats = await restarted.run_once(
                FIXED_NOW + timedelta(seconds=61)
            )

        self.assertEqual(stats["recovered"], 1)
        self.assertEqual(stats["delivered"], 1)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.DELIVERED)
        self.assertIsNone(stored.claim_token)

    async def test_repeated_lease_expiry_is_bounded_by_max_attempts(self):
        reminder = await self._due_reminder()

        async with self.SessionLocal() as session:
            stored = await session.get(TaskReminder, reminder.id)
            stored.status = ReminderStatus.PROCESSING
            stored.claim_token = "abandoned"
            stored.processing_started_at = FIXED_NOW - timedelta(minutes=10)
            stored.attempt_count = 1
            await session.commit()

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
            lease_seconds=60,
            max_attempts=2,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["recovered"], 1)
        self.assertEqual(stats["claimed"], 0)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.FAILED)
        self.assertEqual(stored.attempt_count, 2)
        self.assertIsNone(stored.next_attempt_at)
        self.assertIsNone(stored.claim_token)

    async def test_one_failure_does_not_block_other_reminders(self):
        other_task = await self._new_task("Second reliability task")
        first = await self._due_reminder(offset_seconds=-30)
        second = await self._due_reminder(offset_seconds=0, task_id=other_task.id)

        async def handler(session, claimed, task):
            if claimed.id == first.id:
                raise RuntimeError("only the first reminder fails")

            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
            batch_size=10,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["claimed"], 2)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["delivered"], 1)

        failed = await self._get_reminder(first.id)
        delivered = await self._get_reminder(second.id)

        self.assertEqual(failed.status, ReminderStatus.PENDING)
        self.assertEqual(failed.attempt_count, 1)
        self.assertEqual(delivered.status, ReminderStatus.DELIVERED)

    async def test_batch_size_is_enforced(self):
        for index in range(4):
            task = await self._new_task(f"Batch task {index}")
            await self._due_reminder(offset_seconds=index, task_id=task.id)

        async def handler(session, claimed, task):
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
            batch_size=2,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["claimed"], 2)
        self.assertEqual(stats["delivered"], 2)

    async def test_partial_notification_is_rolled_back_on_handler_failure(self):
        reminder = await self._due_reminder()

        async def handler(session, claimed, task):
            await notification_service.deliver_reminder_notification(
                session=session,
                reminder=claimed,
                task=task,
                now=FIXED_NOW,
            )
            # Persist-then-crash must not leave a delivered notification
            # behind while the reminder is still retryable.
            raise RuntimeError("crash after persist")

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(await self._notification_count(), 0)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.PENDING)
        self.assertIsNone(stored.delivered_at)

    async def test_no_notification_until_handler_succeeds(self):
        reminder = await self._due_reminder()

        async def handler(session, claimed, task):
            return DeliveryOutcome(
                DeliveryOutcomeStatus.DEFERRED,
                next_attempt_at=FIXED_NOW + timedelta(hours=1),
            )

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        await worker.run_once(FIXED_NOW)

        self.assertEqual(await self._notification_count(), 0)

        stored = await self._get_reminder(reminder.id)
        self.assertIsNone(stored.delivered_at)
        self.assertEqual(stored.status, ReminderStatus.PENDING)


class NotificationRecoveryTests(ReliabilityTestBase):

    async def test_pending_and_failed_markers_are_recovered(self):
        async with self.SessionLocal() as session:
            pending = Notification(
                user_id=self.alice_id,
                notification_type="system",
                delivery_status=NotificationDeliveryStatus.PENDING,
                title="Pending",
                body="Body",
                dedupe_key="recover:pending",
                last_error="previous failure",
            )
            failed = Notification(
                user_id=self.alice_id,
                notification_type="system",
                delivery_status=NotificationDeliveryStatus.FAILED,
                title="Failed",
                body="Body",
                dedupe_key="recover:failed",
                last_error="previous failure",
            )
            delivered = Notification(
                user_id=self.alice_id,
                notification_type="system",
                delivery_status=NotificationDeliveryStatus.DELIVERED,
                title="Delivered",
                body="Body",
                dedupe_key="recover:delivered",
                delivered_at=FIXED_NOW - timedelta(days=1),
            )
            session.add_all([pending, failed, delivered])
            await session.commit()

        async with self.SessionLocal() as session:
            recovered = (
                await notification_service.recover_pending_deliveries(
                    session=session,
                    now=FIXED_NOW,
                )
            )
            again = await notification_service.recover_pending_deliveries(
                session=session,
                now=FIXED_NOW,
            )

            rows = list(
                (
                    await session.scalars(
                        select(Notification).order_by(Notification.id)
                    )
                ).all()
            )

        self.assertEqual(recovered, 2)
        self.assertEqual(again, 0)

        statuses = {row.dedupe_key: row for row in rows}

        for key in ("recover:pending", "recover:failed"):
            item = statuses[key]
            self.assertEqual(
                item.delivery_status,
                NotificationDeliveryStatus.DELIVERED,
            )
            self.assertIsNotNone(item.delivered_at)
            self.assertIsNone(item.last_error)

        unchanged = statuses["recover:delivered"]
        self.assertEqual(
            unchanged.delivered_at.replace(tzinfo=UTC),
            FIXED_NOW - timedelta(days=1),
        )

    async def test_duplicate_dedupe_key_is_rejected(self):
        async with self.SessionLocal() as session:
            session.add(
                Notification(
                    user_id=self.alice_id,
                    notification_type="system",
                    delivery_status="delivered",
                    title="One",
                    body="Body",
                    dedupe_key="unique-key",
                )
            )
            await session.commit()

        async with self.SessionLocal() as session:
            session.add(
                Notification(
                    user_id=self.alice_id,
                    notification_type="system",
                    delivery_status="delivered",
                    title="Two",
                    body="Body",
                    dedupe_key="unique-key",
                )
            )

            with self.assertRaises(Exception):
                await session.commit()

            await session.rollback()

        self.assertEqual(await self._notification_count(), 1)

    async def test_zero_retention_disables_purge(self):
        async with self.SessionLocal() as session:
            session.add(
                Notification(
                    user_id=self.alice_id,
                    notification_type="system",
                    delivery_status="delivered",
                    title="Old",
                    body="Body",
                    dedupe_key="old",
                    is_read=True,
                    created_at=FIXED_NOW - timedelta(days=1000),
                )
            )
            await session.commit()

        async with self.SessionLocal() as session:
            purged = await notification_service.purge_expired(
                session=session,
                now=FIXED_NOW,
                retention_days=0,
            )

        self.assertEqual(purged, 0)
        self.assertEqual(await self._notification_count(), 1)


class SchedulerIsolationTests(ReliabilityTestBase):

    async def test_scheduler_loop_survives_worker_failure(self):
        reminder_worker = _FakeWorker(failures=1)
        recurrence_worker = _FakeWorker()

        scheduler = BackgroundScheduler(
            reminder_worker=reminder_worker,
            recurrence_worker=recurrence_worker,
            poll_seconds=1,
            session_factory=self.SessionLocal,
        )

        await scheduler.start()

        try:
            for _ in range(40):
                if reminder_worker.calls >= 2:
                    break
                await asyncio.sleep(0.05)
        finally:
            await scheduler.stop()

        self.assertGreaterEqual(reminder_worker.calls, 2)
        self.assertFalse(scheduler.running)

    async def test_scheduler_run_once_recovers_notifications_and_retention(self):
        async with self.SessionLocal() as session:
            session.add_all(
                [
                    Notification(
                        user_id=self.alice_id,
                        notification_type="system",
                        delivery_status="pending",
                        title="Recover me",
                        body="Body",
                        dedupe_key="runner:recover",
                    ),
                    Notification(
                        user_id=self.alice_id,
                        notification_type="system",
                        delivery_status="delivered",
                        title="Purge me",
                        body="Body",
                        dedupe_key="runner:purge",
                        is_read=True,
                        created_at=FIXED_NOW - timedelta(days=400),
                        delivered_at=FIXED_NOW - timedelta(days=400),
                    ),
                ]
            )
            await session.commit()

        scheduler = BackgroundScheduler(
            reminder_worker=_FakeWorker(),
            recurrence_worker=_FakeWorker(),
            session_factory=self.SessionLocal,
        )

        await scheduler.run_once(FIXED_NOW)

        async with self.SessionLocal() as session:
            recovered = await session.scalar(
                select(Notification).where(
                    Notification.dedupe_key == "runner:recover"
                )
            )
            purged = await session.scalar(
                select(Notification).where(
                    Notification.dedupe_key == "runner:purge"
                )
            )

        self.assertEqual(
            recovered.delivery_status,
            NotificationDeliveryStatus.DELIVERED,
        )
        self.assertIsNotNone(recovered.delivered_at)
        self.assertIsNone(purged)

    async def test_graceful_stop_waits_for_in_flight_cycle(self):
        completed = asyncio.Event()

        class _SlowWorker:
            batch_size = 1

            async def run_once(self, now=None):
                await asyncio.sleep(0.15)
                completed.set()
                return {}

        scheduler = BackgroundScheduler(
            reminder_worker=_SlowWorker(),
            recurrence_worker=_FakeWorker(),
            poll_seconds=60,
            shutdown_timeout_seconds=5,
            session_factory=self.SessionLocal,
        )

        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

        self.assertTrue(completed.is_set())
        self.assertFalse(scheduler.running)

    async def test_disabled_scheduler_does_not_start(self):
        # The FastAPI lifespan only starts the scheduler when enabled. This
        # asserts the runner itself is inert until explicitly started.
        scheduler = BackgroundScheduler(
            reminder_worker=_FakeWorker(),
            recurrence_worker=_FakeWorker(),
            session_factory=self.SessionLocal,
        )

        self.assertFalse(scheduler.running)
        await scheduler.stop()
        self.assertFalse(scheduler.running)

    async def test_failed_reminder_is_delivered_after_backoff(self):
        reminder = await self._due_reminder()
        attempts = []

        async def handler(session, claimed, task):
            attempts.append(claimed.id)

            if len(attempts) == 1:
                raise RuntimeError("transient")

            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
            max_attempts=3,
        )

        await worker.run_once(FIXED_NOW)

        for _ in range(20):
            stats = await worker.run_once(FIXED_NOW + timedelta(hours=1))

            if stats["delivered"] == 1:
                break

            await asyncio.sleep(0)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.DELIVERED)
        self.assertIsNone(stored.last_error)


if __name__ == "__main__":
    unittest.main()
