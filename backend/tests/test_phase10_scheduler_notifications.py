"""Phase 10B/10C scheduler and notification tests.

Covers atomic claims, duplicate prevention, retry/backoff, stale-lease
recovery, terminal failures, scheduler start/stop, idempotent notification
delivery, task-deletion retention, read state, pagination, and owner isolation.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.dependencies import get_current_user_id, get_optional_user_id
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.notification import (
    Notification,
    NotificationDeliveryStatus,
)
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.user import User
from app.scheduler.notification_delivery import (
    deliver_in_app_notification,
)
from app.scheduler.recurrence_worker import RecurrenceWorker
from app.scheduler.reminder_worker import (
    ClaimedReminder,
    DeliveryOutcome,
    DeliveryOutcomeStatus,
    ReminderWorker,
    retry_backoff_seconds,
)
from app.scheduler.runner import BackgroundScheduler
from app.services.notification_service import (
    notification_service,
    reminder_dedupe_key,
)
from app.services.reminder_service import reminder_service
from app.services.task_service import task_service


FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


class _FakeWorker:
    def __init__(self):
        self.calls = 0
        self.batch_size = 1

    async def run_once(self, now=None):
        self.calls += 1
        return {}


class SchedulerAndNotificationTestBase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async with self.SessionLocal() as session:
            alice = User(username="sched_alice")
            bob = User(username="sched_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Scheduler task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

            self.bob_task = await task_service.create_task(
                session=session,
                user_id=self.bob_id,
                title="Bob scheduler task",
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

    async def _due_reminder(self, user_id=None, task_id=None):
        async with self.SessionLocal() as session:
            reminder = await reminder_service.create_reminder(
                session=session,
                user_id=user_id or self.alice_id,
                task_id=task_id or self.task.id,
                offset_minutes=60,
            )
            reminder.remind_at = FIXED_NOW - timedelta(minutes=1)
            await session.commit()
            await session.refresh(reminder)
            return reminder

    async def _get_reminder(self, reminder_id):
        async with self.SessionLocal() as session:
            return await session.get(TaskReminder, reminder_id)

    async def _notification_count(self, user_id=None):
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.user_id
                        == (user_id or self.alice_id)
                    )
                )
                or 0
            )


class ReminderWorkerTests(SchedulerAndNotificationTestBase):

    async def test_due_reminder_is_claimed_and_delivered(self):
        reminder = await self._due_reminder()
        delivered = []

        async def handler(session, claimed, task):
            delivered.append(claimed.id)
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["claimed"], 1)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(delivered, [reminder.id])

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.DELIVERED)
        self.assertIsNotNone(stored.delivered_at)
        self.assertIsNone(stored.claim_token)

    async def test_reminder_is_not_claimed_before_it_is_due(self):
        reminder = await self._due_reminder()

        async def handler(session, claimed, task):
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW - timedelta(hours=2))

        self.assertEqual(stats["claimed"], 0)
        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.PENDING)

    async def test_two_workers_do_not_claim_the_same_reminder(self):
        await self._due_reminder()

        async def handler(session, claimed, task):
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        first = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        second = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )

        first_claim = await first.claim_batch(FIXED_NOW)
        second_claim = await second.claim_batch(FIXED_NOW)

        self.assertEqual(len(first_claim), 1)
        self.assertEqual(second_claim, [])

    async def test_second_run_is_idempotent(self):
        reminder = await self._due_reminder()
        calls = []

        async def handler(session, claimed, task):
            calls.append(claimed.id)
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        await worker.run_once(FIXED_NOW)
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["claimed"], 0)
        self.assertEqual(calls, [reminder.id])

    async def test_failure_is_retried_with_backoff(self):
        reminder = await self._due_reminder()
        attempts = []

        async def handler(session, claimed, task):
            attempts.append(claimed.id)
            raise RuntimeError("database delivery failure")

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
            max_attempts=3,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["failed"], 1)
        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.PENDING)
        self.assertEqual(stored.attempt_count, 1)
        self.assertIsNotNone(stored.next_attempt_at)
        self.assertNotIn("database delivery failure", stored.last_error)

        # Before the backoff elapses the reminder is not claimed again.
        early = await worker.run_once(FIXED_NOW + timedelta(seconds=1))
        self.assertEqual(early["claimed"], 0)

        later = await worker.run_once(
            FIXED_NOW + timedelta(seconds=retry_backoff_seconds(1) + 1)
        )
        self.assertEqual(later["claimed"], 1)
        self.assertEqual(later["failed"], 1)

    async def test_max_attempts_marks_reminder_failed(self):
        reminder = await self._due_reminder()

        async def handler(session, claimed, task):
            raise RuntimeError("always fails")

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
            max_attempts=2,
        )
        await worker.run_once(FIXED_NOW)
        await worker.run_once(
            FIXED_NOW + timedelta(seconds=retry_backoff_seconds(1) + 1)
        )

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.FAILED)
        self.assertEqual(stored.attempt_count, 2)
        self.assertIsNone(stored.next_attempt_at)

    async def test_stale_processing_claim_is_recovered(self):
        reminder = await self._due_reminder()

        async with self.SessionLocal() as session:
            stored = await session.get(TaskReminder, reminder.id)
            stored.status = ReminderStatus.PROCESSING
            stored.claim_token = "stale-token"
            stored.processing_started_at = FIXED_NOW - timedelta(minutes=10)
            await session.commit()

        delivered = []

        async def handler(session, claimed, task):
            delivered.append(claimed.id)
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
            lease_seconds=60,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["recovered"], 1)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(delivered, [reminder.id])

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.DELIVERED)

    async def test_deferred_outcome_returns_reminder_to_pending(self):
        reminder = await self._due_reminder()
        next_time = FIXED_NOW + timedelta(hours=1)

        async def handler(session, claimed, task):
            return DeliveryOutcome(
                DeliveryOutcomeStatus.DEFERRED,
                next_attempt_at=next_time,
            )

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["deferred"], 1)
        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.PENDING)
        self.assertEqual(_as_utc(stored.next_attempt_at), next_time)

    async def test_suppressed_outcome_ends_reminder(self):
        reminder = await self._due_reminder()

        async def handler(session, claimed, task):
            return DeliveryOutcome(
                DeliveryOutcomeStatus.SUPPRESSED,
                reason="notifications disabled",
            )

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["suppressed"], 1)
        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.SUPPRESSED)
        self.assertEqual(stored.last_error, "notifications disabled")

    async def test_completed_task_suppresses_stale_reminder(self):
        reminder = await self._due_reminder()

        async with self.SessionLocal() as session:
            await task_service.complete_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.task.id,
            )

        calls = []

        async def handler(session, claimed, task):
            calls.append(claimed.id)
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["suppressed"], 1)
        self.assertEqual(calls, [])
        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.SUPPRESSED)

    async def test_deleted_task_suppresses_stale_reminder(self):
        reminder = await self._due_reminder()

        async with self.SessionLocal() as session:
            await task_service.delete_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.task.id,
            )

        async def handler(session, claimed, task):
            return DeliveryOutcome(DeliveryOutcomeStatus.DELIVERED)

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=handler,
        )
        stats = await worker.run_once(FIXED_NOW)

        # The reminder row itself is cascade-deleted with the task, so the
        # worker has no work rather than delivering a stale notification.
        self.assertEqual(stats["claimed"], 0)

    async def test_backoff_is_bounded(self):
        with patch.object(
            type(__import__("app.core.config", fromlist=["settings"]).settings),
            "REMINDER_RETRY_BACKOFF_MAX_SECONDS",
            100,
            create=True,
        ):
            self.assertEqual(retry_backoff_seconds(50), 100)


class NotificationDeliveryTests(SchedulerAndNotificationTestBase):

    async def test_delivery_persists_one_notification_and_marks_reminder(self):
        reminder = await self._due_reminder()

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
        )
        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=FIXED_NOW,
        ):
            stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["delivered"], 1)

        async with self.SessionLocal() as session:
            notifications = list(
                (
                    await session.scalars(
                        select(Notification).where(
                            Notification.user_id == self.alice_id
                        )
                    )
                ).all()
            )

        self.assertEqual(len(notifications), 1)
        item = notifications[0]
        self.assertEqual(item.delivery_status, NotificationDeliveryStatus.DELIVERED)
        self.assertEqual(item.dedupe_key, reminder_dedupe_key(reminder.id))
        self.assertFalse(item.is_read)
        self.assertIn(self.task.title, item.title)
        self.assertEqual(item.task_id, self.task.id)
        self.assertEqual(item.reminder_id, reminder.id)

    async def test_repeated_delivery_is_idempotent(self):
        reminder = await self._due_reminder()

        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=FIXED_NOW,
        ):
            async with self.SessionLocal() as session:
                stored = await session.get(TaskReminder, reminder.id)
                first = await notification_service.deliver_reminder_notification(
                    session=session,
                    reminder=stored,
                    task=self.task,
                    now=FIXED_NOW,
                )
                await session.commit()

                second = await notification_service.deliver_reminder_notification(
                    session=session,
                    reminder=stored,
                    task=self.task,
                    now=FIXED_NOW,
                )
                await session.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(await self._notification_count(), 1)

    async def test_notification_survives_task_deletion(self):
        reminder = await self._due_reminder()

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
        )
        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=FIXED_NOW,
        ):
            await worker.run_once(FIXED_NOW)

        async with self.SessionLocal() as session:
            await task_service.delete_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.task.id,
            )

            notification = await session.scalar(
                select(Notification).where(
                    Notification.user_id == self.alice_id
                )
            )

        self.assertIsNotNone(notification)
        self.assertIsNone(notification.task_id)
        self.assertIsNone(notification.reminder_id)
        self.assertIn("Scheduler task", notification.body)
        self.assertEqual(
            notification.delivery_status,
            NotificationDeliveryStatus.DELIVERED,
        )

    async def test_read_state_and_pagination(self):
        async with self.SessionLocal() as session:
            for index in range(3):
                session.add(
                    Notification(
                        user_id=self.alice_id,
                        notification_type="system",
                        delivery_status="delivered",
                        title=f"Notice {index}",
                        body="Body",
                        dedupe_key=f"system:{index}",
                        is_read=False,
                        delivered_at=FIXED_NOW,
                    )
                )
            await session.commit()

        async with self.SessionLocal() as session:
            page = await notification_service.list_notifications(
                session=session,
                user_id=self.alice_id,
                limit=2,
            )
            self.assertEqual(len(page), 2)

            unread = await notification_service.count_unread(
                session=session,
                user_id=self.alice_id,
            )
            self.assertEqual(unread, 3)

            target = page[0]
            marked = await notification_service.mark_read(
                session=session,
                user_id=self.alice_id,
                notification_id=target.id,
                now=FIXED_NOW,
            )
            self.assertTrue(marked.is_read)
            self.assertIsNotNone(marked.read_at)

            updated = await notification_service.mark_all_read(
                session=session,
                user_id=self.alice_id,
                now=FIXED_NOW,
            )
            self.assertEqual(updated, 2)

            remaining = await notification_service.count_unread(
                session=session,
                user_id=self.alice_id,
            )
            self.assertEqual(remaining, 0)

    async def test_owner_isolation(self):
        await self._due_reminder()

        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
        )
        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=FIXED_NOW,
        ):
            await worker.run_once(FIXED_NOW)

        async with self.SessionLocal() as session:
            bob_items = await notification_service.list_notifications(
                session=session,
                user_id=self.bob_id,
            )

        self.assertEqual(bob_items, [])

    async def test_retention_purges_only_old_read_notifications(self):
        async with self.SessionLocal() as session:
            old = Notification(
                user_id=self.alice_id,
                notification_type="system",
                delivery_status="delivered",
                title="Old read",
                body="Body",
                dedupe_key="old-read",
                is_read=True,
                created_at=FIXED_NOW - timedelta(days=400),
                delivered_at=FIXED_NOW - timedelta(days=400),
            )
            old_unread = Notification(
                user_id=self.alice_id,
                notification_type="system",
                delivery_status="delivered",
                title="Old unread",
                body="Body",
                dedupe_key="old-unread",
                is_read=False,
                created_at=FIXED_NOW - timedelta(days=400),
                delivered_at=FIXED_NOW - timedelta(days=400),
            )
            recent = Notification(
                user_id=self.alice_id,
                notification_type="system",
                delivery_status="delivered",
                title="Recent",
                body="Body",
                dedupe_key="recent",
                is_read=True,
                created_at=FIXED_NOW - timedelta(days=1),
                delivered_at=FIXED_NOW,
            )
            session.add_all([old, old_unread, recent])
            await session.commit()

        async with self.SessionLocal() as session:
            purged = await notification_service.purge_expired(
                session=session,
                now=FIXED_NOW,
                retention_days=365,
            )

            remaining = list(
                (
                    await session.scalars(
                        select(Notification.title).where(
                            Notification.user_id == self.alice_id
                        )
                    )
                ).all()
            )

        self.assertEqual(purged, 1)
        self.assertEqual(
            sorted(remaining),
            ["Old unread", "Recent"],
        )


class SchedulerLifecycleTests(SchedulerAndNotificationTestBase):

    async def test_scheduler_starts_and_stops_cleanly(self):
        reminder_worker = _FakeWorker()
        recurrence_worker = _FakeWorker()

        scheduler = BackgroundScheduler(
            reminder_worker=reminder_worker,
            recurrence_worker=recurrence_worker,
            poll_seconds=60,
            session_factory=self.SessionLocal,
        )

        await scheduler.start()
        self.assertTrue(scheduler.running)

        # Give the loop one opportunity to run.
        import asyncio

        await asyncio.sleep(0.05)
        await scheduler.stop()

        self.assertFalse(scheduler.running)
        self.assertGreaterEqual(reminder_worker.calls, 1)

    async def test_scheduler_run_once_returns_component_results(self):
        scheduler = BackgroundScheduler(
            reminder_worker=_FakeWorker(),
            recurrence_worker=_FakeWorker(),
            session_factory=self.SessionLocal,
        )

        results = await scheduler.run_once(FIXED_NOW)

        self.assertIn("reminders", results)
        self.assertIn("recurrences", results)


class NotificationApiTests(SchedulerAndNotificationTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()

        async with self.SessionLocal() as session:
            self.notification = Notification(
                user_id=self.alice_id,
                task_id=self.task.id,
                notification_type="system",
                delivery_status="delivered",
                title="API notice",
                body="API body",
                dedupe_key="api:notice",
                is_read=False,
                delivered_at=FIXED_NOW,
            )
            session.add(self.notification)
            await session.commit()
            await session.refresh(self.notification)
            self.notification_id = self.notification.id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_alice():
            return self.alice_id

        async def override_bob():
            return self.bob_id

        self.override_alice = override_alice
        self.override_bob = override_bob

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_alice
        app.dependency_overrides[get_optional_user_id] = override_alice

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await super().asyncTearDown()

    async def test_list_unread_and_detail(self):
        listed = await self.client.get("/api/notifications")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        unread = await self.client.get("/api/notifications/unread")
        self.assertEqual(unread.status_code, 200)
        self.assertEqual(len(unread.json()), 1)

        detail = await self.client.get(
            f"/api/notifications/{self.notification_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["title"], "API notice")

    async def test_mark_read_and_read_all(self):
        marked = await self.client.patch(
            f"/api/notifications/{self.notification_id}/read"
        )
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.json()["is_read"])
        self.assertIsNotNone(marked.json()["read_at"])

        async with self.SessionLocal() as session:
            session.add(
                Notification(
                    user_id=self.alice_id,
                    notification_type="system",
                    delivery_status="delivered",
                    title="Second notice",
                    body="Body",
                    dedupe_key="api:second",
                    is_read=False,
                    delivered_at=FIXED_NOW,
                )
            )
            await session.commit()

        all_read = await self.client.post("/api/notifications/read-all")
        self.assertEqual(all_read.status_code, 200)
        self.assertEqual(all_read.json()["updated"], 1)

    async def test_delete_notification(self):
        deleted = await self.client.delete(
            f"/api/notifications/{self.notification_id}"
        )
        self.assertEqual(deleted.status_code, 204)

        missing = await self.client.get(
            f"/api/notifications/{self.notification_id}"
        )
        self.assertEqual(missing.status_code, 404)

    async def test_other_owner_cannot_access_notifications(self):
        app.dependency_overrides[get_current_user_id] = self.override_bob
        app.dependency_overrides[get_optional_user_id] = self.override_bob

        listed = await self.client.get("/api/notifications")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])

        detail = await self.client.get(
            f"/api/notifications/{self.notification_id}"
        )
        self.assertEqual(detail.status_code, 404)

        patched = await self.client.patch(
            f"/api/notifications/{self.notification_id}/read"
        )
        self.assertEqual(patched.status_code, 404)

        deleted = await self.client.delete(
            f"/api/notifications/{self.notification_id}"
        )
        self.assertEqual(deleted.status_code, 404)


if __name__ == "__main__":
    unittest.main()
