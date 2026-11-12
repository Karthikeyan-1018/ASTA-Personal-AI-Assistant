"""Phase 10H notification snoozing and reminder rescheduling tests.

Covers duration presets, custom instants, timezone normalisation, repeated
snoozing and limits, quiet-hour interaction, dismissal and deleted-task
behaviour, reminder lifecycle rules, duplicate prevention, ownership isolation,
scheduler re-delivery, restart persistence, and existing contracts.
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
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.notification import Notification
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.user import User
from app.scheduler.notification_delivery import (
    deliver_in_app_notification,
)
from app.scheduler.reminder_worker import ReminderWorker
from app.services.notification_service import notification_service
from app.services.task_service import task_service


UTC = timezone.utc
DELIVERY_NOW = datetime(2026, 11, 20, 16, 1, tzinfo=UTC)
SNOOZE_NOW = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
REMIND_AT = "2026-11-20T16:00:00Z"


def _as_utc(value):
    if value is None:
        return None

    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


class SnoozeTestBase(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="snooze_alice")
            bob = User(username="snooze_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Machine Learning assignment",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )
            self.task_id = self.task.id

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
        await self.engine.dispose()

    def _as_alice(self):
        app.dependency_overrides[get_current_user_id] = self.override_alice
        app.dependency_overrides[get_optional_user_id] = self.override_alice

    def _as_bob(self):
        app.dependency_overrides[get_current_user_id] = self.override_bob
        app.dependency_overrides[get_optional_user_id] = self.override_bob

    async def _run_reminders(self, now):
        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
        )

        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=now,
        ):
            return await worker.run_once(now)

    async def _create_reminder(self, remind_at=REMIND_AT):
        response = await self.client.post(
            f"/api/tasks/{self.task_id}/reminders",
            json={"remind_at": remind_at},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def _deliver_notification(self, task_id=None, remind_at=REMIND_AT):
        task_id = task_id or self.task_id
        response = await self.client.post(
            f"/api/tasks/{task_id}/reminders",
            json={"remind_at": remind_at},
        )
        self.assertEqual(response.status_code, 201, response.text)
        reminder = response.json()
        stats = await self._run_reminders(DELIVERY_NOW)
        self.assertEqual(stats["delivered"], 1, stats)

        listing = await self.client.get("/api/notifications")
        self.assertEqual(listing.status_code, 200, listing.text)
        notifications = listing.json()
        self.assertEqual(len(notifications), 1)
        return notifications[0], reminder

    async def _snooze(self, notification_id, now=None, **payload):
        with patch(
            "app.services.notification_service._utcnow",
            return_value=now or SNOOZE_NOW,
        ):
            return await self.client.post(
                f"/api/notifications/{notification_id}/snooze",
                json=payload,
            )

    async def _reschedule(self, reminder_id, now=None, **payload):
        with patch(
            "app.services.reminder_service._utcnow",
            return_value=now or SNOOZE_NOW,
        ):
            return await self.client.patch(
                f"/api/reminders/{reminder_id}",
                json=payload,
            )

    async def _stored_reminder(self, reminder_id):
        async with self.SessionLocal() as session:
            return await session.get(TaskReminder, reminder_id)

    async def _stored_notification(self, notification_id):
        async with self.SessionLocal() as session:
            return await session.get(Notification, notification_id)

    async def _notification_count(self):
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(Notification)
                )
                or 0
            )

    async def _set_preferences(self, **changes):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json=changes,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class SnoozeDurationTests(SnoozeTestBase):

    async def _assert_duration(self, minutes):
        notification, reminder = await self._deliver_notification()

        response = await self._snooze(
            notification["id"],
            duration_minutes=minutes,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        expected = SNOOZE_NOW + timedelta(minutes=minutes)
        self.assertEqual(_as_utc(body["snoozed_until"]), expected)
        self.assertEqual(_as_utc(body["reminder_at"]), expected)
        self.assertEqual(body["snooze_count"], 1)
        self.assertTrue(body["is_read"])
        self.assertIsNotNone(body["snoozed_at"])

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(stored.status, ReminderStatus.PENDING)
        self.assertEqual(_as_utc(stored.remind_at), expected)
        self.assertIsNone(stored.delivered_at)
        self.assertEqual(stored.attempt_count, 0)

        # The original notification is not re-created by snoozing.
        self.assertEqual(await self._notification_count(), 1)

    async def test_snooze_for_ten_minutes(self):
        await self._assert_duration(10)

    async def test_snooze_for_thirty_minutes(self):
        await self._assert_duration(30)

    async def test_snooze_for_one_hour(self):
        await self._assert_duration(60)

    async def test_snooze_until_tomorrow(self):
        await self._assert_duration(1440)

    async def test_custom_snooze_time_normalises_timezone(self):
        notification, reminder = await self._deliver_notification()

        response = await self._snooze(
            notification["id"],
            snooze_until="2026-11-21T09:00:00+05:30",
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        expected = datetime(2026, 11, 21, 3, 30, tzinfo=UTC)
        self.assertEqual(_as_utc(body["snoozed_until"]), expected)
        self.assertEqual(_as_utc(body["reminder_at"]), expected)

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(_as_utc(stored.remind_at), expected)

    async def test_past_and_naive_snooze_times_are_rejected(self):
        notification, _ = await self._deliver_notification()

        past = await self._snooze(
            notification["id"],
            snooze_until="2026-11-20T10:00:00Z",
        )
        self.assertEqual(past.status_code, 400, past.text)

        naive = await self._snooze(
            notification["id"],
            snooze_until="2026-11-21T09:00:00",
        )
        self.assertEqual(naive.status_code, 422, naive.text)

    async def test_snooze_requires_exactly_one_source(self):
        notification, _ = await self._deliver_notification()

        neither = await self._snooze(notification["id"])
        self.assertEqual(neither.status_code, 422, neither.text)

        both = await self._snooze(
            notification["id"],
            duration_minutes=30,
            snooze_until="2026-11-21T09:00:00Z",
        )
        self.assertEqual(both.status_code, 422, both.text)

    async def test_snooze_limit_is_enforced(self):
        notification, _ = await self._deliver_notification()

        with patch.object(settings, "NOTIFICATION_SNOOZE_MAX_COUNT", 2):
            first = await self._snooze(
                notification["id"],
                duration_minutes=10,
            )
            self.assertEqual(first.status_code, 200, first.text)

            second = await self._snooze(
                notification["id"],
                duration_minutes=20,
            )
            self.assertEqual(second.status_code, 200, second.text)

            third = await self._snooze(
                notification["id"],
                duration_minutes=30,
            )
            self.assertEqual(third.status_code, 409, third.text)

        stored = await self._stored_notification(notification["id"])
        self.assertEqual(stored.snooze_count, 2)

    async def test_repeated_snooze_extends_schedule(self):
        notification, reminder = await self._deliver_notification()

        first = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(first.status_code, 200, first.text)

        second = await self._snooze(
            notification["id"],
            duration_minutes=30,
        )
        self.assertEqual(second.status_code, 200, second.text)

        body = second.json()
        self.assertEqual(body["snooze_count"], 2)
        self.assertEqual(
            _as_utc(body["snoozed_until"]),
            SNOOZE_NOW + timedelta(minutes=30),
        )

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(
            _as_utc(stored.remind_at),
            SNOOZE_NOW + timedelta(minutes=30),
        )


class SnoozeLifecycleTests(SnoozeTestBase):

    async def test_dismissed_notification_cannot_be_snoozed(self):
        notification, _ = await self._deliver_notification()

        deleted = await self.client.delete(
            f"/api/notifications/{notification['id']}"
        )
        self.assertEqual(deleted.status_code, 204)

        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 404, response.text)

    async def test_dismissing_a_snoozed_notification_cancels_redelivery(self):
        notification, reminder = await self._deliver_notification()

        snoozed = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(snoozed.status_code, 200, snoozed.text)

        deleted = await self.client.delete(
            f"/api/notifications/{notification['id']}"
        )
        self.assertEqual(deleted.status_code, 204)

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(stored.status, ReminderStatus.CANCELLED)

        stats = await self._run_reminders(
            SNOOZE_NOW + timedelta(minutes=20)
        )
        self.assertEqual(stats["claimed"], 0)
        self.assertEqual(await self._notification_count(), 0)

    async def test_notification_with_deleted_task_cannot_be_snoozed(self):
        notification, _ = await self._deliver_notification()

        deleted = await self.client.delete(f"/api/tasks/{self.task_id}")
        self.assertEqual(deleted.status_code, 204)

        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("task", response.json()["detail"].lower())

    async def test_processing_reminder_cannot_be_snoozed(self):
        notification, reminder = await self._deliver_notification()

        async with self.SessionLocal() as session:
            stored = await session.get(TaskReminder, reminder["id"])
            stored.status = ReminderStatus.PROCESSING
            stored.claim_token = "claim-in-flight"
            stored.processing_started_at = SNOOZE_NOW
            await session.commit()

        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 409, response.text)

    async def test_in_app_disabled_rejects_snooze(self):
        notification, _ = await self._deliver_notification()
        await self._set_preferences(in_app_enabled=False)

        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("disabled", response.json()["detail"].lower())

    async def test_already_read_notification_can_be_snoozed(self):
        notification, _ = await self._deliver_notification()

        marked = await self.client.patch(
            f"/api/notifications/{notification['id']}/read"
        )
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.json()["is_read"])

        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["is_read"])

    async def test_other_owner_cannot_snooze_notification(self):
        notification, _ = await self._deliver_notification()

        self._as_bob()
        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 404, response.text)

        self._as_alice()
        stored = await self._stored_notification(notification["id"])
        self.assertFalse(stored.is_read)

    async def test_quiet_hours_move_snooze_to_window_end(self):
        notification, reminder = await self._deliver_notification()

        await self._set_preferences(
            quiet_hours_enabled=True,
            quiet_hours_start="22:00:00",
            quiet_hours_end="07:00:00",
            timezone="UTC",
        )

        quiet_now = datetime(2026, 11, 20, 21, 30, tzinfo=UTC)
        response = await self._snooze(
            notification["id"],
            now=quiet_now,
            duration_minutes=120,
        )
        self.assertEqual(response.status_code, 200, response.text)

        expected = datetime(2026, 11, 21, 7, 0, tzinfo=UTC)
        self.assertEqual(_as_utc(response.json()["snoozed_until"]), expected)

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(_as_utc(stored.remind_at), expected)

        before = await self._run_reminders(
            expected - timedelta(seconds=30)
        )
        self.assertEqual(before["claimed"], 0)

        after = await self._run_reminders(
            expected + timedelta(seconds=30)
        )
        self.assertEqual(after["delivered"], 1)

    async def test_snoozed_reminder_survives_process_restart(self):
        notification, reminder = await self._deliver_notification()

        response = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self._notification_count(), 1)

        # A new worker and session factory simulate a process restart. The
        # schedule is entirely database-backed.
        restarted_factory = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )
        worker = ReminderWorker(
            session_factory=restarted_factory,
            delivery_handler=deliver_in_app_notification,
        )

        early = await worker.run_once(
            SNOOZE_NOW + timedelta(minutes=5)
        )
        self.assertEqual(early["claimed"], 0)

        due = await worker.run_once(
            SNOOZE_NOW + timedelta(minutes=11)
        )
        self.assertEqual(due["delivered"], 1)

        self.assertEqual(await self._notification_count(), 1)

        replayed = await self.client.get(
            f"/api/notifications/{notification['id']}"
        )
        self.assertEqual(replayed.status_code, 200, replayed.text)
        body = replayed.json()
        self.assertFalse(body["is_read"])
        self.assertIsNone(body["snoozed_until"])
        self.assertEqual(body["snooze_count"], 1)

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(stored.status, ReminderStatus.DELIVERED)

    async def test_retention_does_not_purge_a_pending_snooze(self):
        notification, _ = await self._deliver_notification()

        snoozed = await self._snooze(
            notification["id"],
            duration_minutes=10,
        )
        self.assertEqual(snoozed.status_code, 200, snoozed.text)

        async with self.SessionLocal() as session:
            purged = await notification_service.purge_expired(
                session=session,
                now=SNOOZE_NOW + timedelta(days=400),
                retention_days=1,
            )

        self.assertEqual(purged, 0)
        self.assertEqual(await self._notification_count(), 1)

    async def test_redelivery_does_not_duplicate_notification(self):
        notification, _ = await self._deliver_notification()

        await self._snooze(notification["id"], duration_minutes=10)

        first = await self._run_reminders(
            SNOOZE_NOW + timedelta(minutes=11)
        )
        self.assertEqual(first["delivered"], 1)

        second = await self._run_reminders(
            SNOOZE_NOW + timedelta(minutes=12)
        )
        self.assertEqual(second["claimed"], 0)

        listing = await self.client.get("/api/notifications")
        self.assertEqual(len(listing.json()), 1)
        self.assertEqual(listing.json()[0]["id"], notification["id"])


class ReminderRescheduleTests(SnoozeTestBase):

    async def test_reschedule_pending_reminder(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-21T10:30:00+02:00",
        )
        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        expected = datetime(2026, 11, 21, 8, 30, tzinfo=UTC)
        self.assertEqual(_as_utc(body["remind_at"]), expected)
        self.assertEqual(body["status"], "pending")

        stored = await self._stored_reminder(reminder["id"])
        self.assertEqual(_as_utc(stored.remind_at), expected)
        self.assertIn(
            str(int(expected.timestamp())),
            stored.dedupe_key,
        )

    async def test_reschedule_rejects_duplicate_instant(self):
        first = await self._create_reminder("2026-11-21T09:00:00Z")
        await self._create_reminder("2026-11-21T10:00:00Z")

        response = await self._reschedule(
            first["id"],
            remind_at="2026-11-21T10:00:00Z",
        )
        self.assertEqual(response.status_code, 409, response.text)

    async def test_reschedule_rejects_delivered_reminder(self):
        notification, reminder = await self._deliver_notification()

        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-21T10:00:00Z",
        )
        self.assertEqual(response.status_code, 409, response.text)

    async def test_reschedule_rejects_cancelled_reminder(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        cancelled = await self.client.post(
            f"/api/reminders/{reminder['id']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)

        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-21T10:00:00Z",
        )
        self.assertEqual(response.status_code, 409, response.text)

    async def test_reschedule_rejects_processing_reminder(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        async with self.SessionLocal() as session:
            stored = await session.get(TaskReminder, reminder["id"])
            stored.status = ReminderStatus.PROCESSING
            stored.claim_token = "claim-in-flight"
            stored.processing_started_at = SNOOZE_NOW
            await session.commit()

        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-21T10:00:00Z",
        )
        self.assertEqual(response.status_code, 409, response.text)

    async def test_reschedule_rejects_past_instant(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-19T09:00:00Z",
        )
        self.assertEqual(response.status_code, 400, response.text)

    async def test_other_owner_cannot_reschedule(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        self._as_bob()
        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-21T10:00:00Z",
        )
        self.assertEqual(response.status_code, 404, response.text)

    async def test_reschedule_does_not_create_a_notification(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        response = await self._reschedule(
            reminder["id"],
            remind_at="2026-11-21T10:00:00Z",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self._notification_count(), 0)


class ExistingContractRegressionTests(SnoozeTestBase):

    async def test_notification_response_includes_snooze_metadata(self):
        notification, _ = await self._deliver_notification()

        self.assertEqual(notification["snooze_count"], 0)
        self.assertIsNone(notification["snoozed_at"])
        self.assertIsNone(notification["snoozed_until"])

    async def test_reminder_lifecycle_still_works(self):
        reminder = await self._create_reminder("2026-11-21T09:00:00Z")

        cancelled = await self.client.post(
            f"/api/reminders/{reminder['id']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)

        again = await self.client.post(
            f"/api/reminders/{reminder['id']}/cancel"
        )
        self.assertEqual(again.status_code, 200, again.text)

        deleted = await self.client.delete(
            f"/api/reminders/{reminder['id']}"
        )
        self.assertEqual(deleted.status_code, 204)

        missing = await self.client.delete(
            f"/api/reminders/{reminder['id']}"
        )
        self.assertEqual(missing.status_code, 404)

    async def test_existing_read_and_delete_contract(self):
        notification, _ = await self._deliver_notification()

        marked = await self.client.patch(
            f"/api/notifications/{notification['id']}/read"
        )
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertTrue(marked.json()["is_read"])

        read_all = await self.client.post("/api/notifications/read-all")
        self.assertEqual(read_all.status_code, 200, read_all.text)
        self.assertEqual(read_all.json()["updated"], 0)


if __name__ == "__main__":
    unittest.main()
