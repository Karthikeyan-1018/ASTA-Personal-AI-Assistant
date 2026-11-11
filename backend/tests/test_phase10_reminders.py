"""Phase 10A task reminder tests.

Covers the additive reminder table, owner-scoped CRUD, deadline-relative and
absolute scheduling, date-only anchoring, duplicate prevention, lifecycle
transitions, cascade deletion, and the REST contract. No scheduler or
notification delivery is exercised in Phase 10A.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import get_current_user_id, get_optional_user_id
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.task import TaskStatus
from app.models.user import User
from app.exceptions.reminder_exceptions import (
    ReminderConflictError,
    ReminderNotFoundError,
    ReminderValidationError,
)
from app.exceptions.task_exceptions import TaskNotFoundError
from app.services.reminder_service import reminder_service
from app.services.task_service import task_service


FIXED_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def _as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; restore UTC for comparisons."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


class ReminderServiceTests(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="reminder_alice")
            bob = User(username="reminder_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.timed_task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Timed task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

            self.date_task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Date-only task",
                due_date=date(2026, 11, 20),
                timezone="America/New_York",
            )

            self.undated_task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Undated task",
            )

            self.bob_task = await task_service.create_task(
                session=session,
                user_id=self.bob_id,
                title="Bob task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

        self._clock_patch = patch(
            "app.services.reminder_service._utcnow",
            return_value=FIXED_NOW,
        )
        self._clock_patch.start()
        self.addCleanup(self._clock_patch.stop)

        self._time_patch = patch.object(
            settings,
            "REMINDER_DEFAULT_LOCAL_TIME",
            "09:00",
        )
        self._time_patch.start()
        self.addCleanup(self._time_patch.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create(self, user_id=None, **kwargs):
        async with self.SessionLocal() as session:
            return await reminder_service.create_reminder(
                session=session,
                user_id=user_id if user_id is not None else self.alice_id,
                **kwargs,
            )

    # -- scheduling ---------------------------------------------------

    async def test_offset_from_timed_deadline(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        self.assertEqual(
            _as_utc(reminder.remind_at),
            datetime(2026, 11, 20, 16, 0, tzinfo=UTC),
        )
        self.assertEqual(reminder.offset_minutes, 60)
        self.assertEqual(reminder.status, ReminderStatus.PENDING)
        self.assertEqual(reminder.user_id, self.alice_id)
        self.assertEqual(reminder.task_id, self.timed_task.id)

    async def test_offset_zero_is_deadline(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=0,
        )

        self.assertEqual(
            _as_utc(reminder.remind_at),
            datetime(2026, 11, 20, 17, 0, tzinfo=UTC),
        )

    async def test_date_only_uses_default_local_time(self):

        reminder = await self._create(
            task_id=self.date_task.id,
            offset_minutes=1440,
        )

        # 2026-11-20 09:00 EST minus one day is 2026-11-19 14:00 UTC.
        self.assertEqual(
            _as_utc(reminder.remind_at),
            datetime(2026, 11, 19, 14, 0, tzinfo=UTC),
        )

    async def test_configured_date_only_anchor(self):

        with patch.object(
            settings,
            "REMINDER_DEFAULT_LOCAL_TIME",
            "07:30",
        ):
            reminder = await self._create(
                task_id=self.date_task.id,
                offset_minutes=0,
            )

        # 2026-11-20 07:30 EST is 12:30 UTC.
        self.assertEqual(
            _as_utc(reminder.remind_at),
            datetime(2026, 11, 20, 12, 30, tzinfo=UTC),
        )

    async def test_explicit_absolute_time_is_normalised_to_utc(self):

        reminder = await self._create(
            task_id=self.undated_task.id,
            remind_at=datetime(
                2026,
                11,
                15,
                10,
                0,
                tzinfo=timezone(timedelta(hours=2)),
            ),
        )

        self.assertEqual(
            _as_utc(reminder.remind_at),
            datetime(2026, 11, 15, 8, 0, tzinfo=UTC),
        )
        self.assertIsNone(reminder.offset_minutes)

    async def test_offset_requires_due_date(self):

        with self.assertRaises(ReminderValidationError):
            await self._create(
                task_id=self.undated_task.id,
                offset_minutes=30,
            )

    async def test_absolute_time_does_not_require_due_date(self):

        reminder = await self._create(
            task_id=self.undated_task.id,
            remind_at=FIXED_NOW + timedelta(days=2),
        )

        self.assertEqual(
            _as_utc(reminder.remind_at),
            FIXED_NOW + timedelta(days=2),
        )

    async def test_requires_exactly_one_scheduling_source(self):

        with self.assertRaises(ReminderValidationError):
            await self._create(task_id=self.timed_task.id)

        with self.assertRaises(ReminderValidationError):
            await self._create(
                task_id=self.timed_task.id,
                offset_minutes=30,
                remind_at=FIXED_NOW + timedelta(days=1),
            )

    async def test_past_reminder_is_rejected(self):

        with self.assertRaises(ReminderValidationError):
            await self._create(
                task_id=self.undated_task.id,
                remind_at=FIXED_NOW - timedelta(minutes=1),
            )

    async def test_duplicate_reminder_is_rejected(self):

        await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        with self.assertRaises(ReminderConflictError):
            await self._create(
                task_id=self.timed_task.id,
                offset_minutes=60,
            )

    async def test_multiple_distinct_reminders_are_allowed(self):

        first = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )
        second = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=120,
        )

        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.dedupe_key, second.dedupe_key)

    # -- ownership and scope -----------------------------------------

    async def test_creating_for_another_users_task_is_rejected(self):

        with self.assertRaises(TaskNotFoundError):
            await self._create(
                task_id=self.bob_task.id,
                offset_minutes=60,
            )

    async def test_completed_task_rejects_new_reminders(self):

        async with self.SessionLocal() as session:
            await task_service.complete_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.timed_task.id,
            )

        with self.assertRaises(ReminderValidationError):
            await self._create(
                task_id=self.timed_task.id,
                offset_minutes=60,
            )

    async def test_get_is_owner_scoped(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        async with self.SessionLocal() as session:
            with self.assertRaises(ReminderNotFoundError):
                await reminder_service.get_reminder(
                    session=session,
                    user_id=self.bob_id,
                    reminder_id=reminder.id,
                )

    async def test_list_for_task_is_owner_scoped(self):

        async with self.SessionLocal() as session:
            with self.assertRaises(TaskNotFoundError):
                await reminder_service.list_for_task(
                    session=session,
                    user_id=self.bob_id,
                    task_id=self.timed_task.id,
                )

    async def test_list_for_user_only_returns_own_reminders(self):

        await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )
        await self._create(
            task_id=self.bob_task.id,
            offset_minutes=60,
            user_id=self.bob_id,
        )

        async with self.SessionLocal() as session:
            alice_items = await reminder_service.list_for_user(
                session=session,
                user_id=self.alice_id,
            )
            bob_items = await reminder_service.list_for_user(
                session=session,
                user_id=self.bob_id,
            )

        self.assertEqual(len(alice_items), 1)
        self.assertEqual(len(bob_items), 1)
        self.assertEqual(alice_items[0].user_id, self.alice_id)
        self.assertEqual(bob_items[0].user_id, self.bob_id)

    async def test_list_filters_by_status_and_orders_by_time(self):

        first = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=120,
        )
        second = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        async with self.SessionLocal() as session:
            await reminder_service.cancel_reminder(
                session=session,
                user_id=self.alice_id,
                reminder_id=first.id,
            )

            pending = await reminder_service.list_for_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.timed_task.id,
                status=ReminderStatus.PENDING,
            )
            all_items = await reminder_service.list_for_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.timed_task.id,
            )

        self.assertEqual([item.id for item in pending], [second.id])
        self.assertEqual(
            [item.id for item in all_items][-1],
            second.id,
        )

    # -- lifecycle ----------------------------------------------------

    async def test_reschedule_updates_time_and_dedupe_key(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )
        original_key = reminder.dedupe_key

        async with self.SessionLocal() as session:
            updated = await reminder_service.reschedule_reminder(
                session=session,
                user_id=self.alice_id,
                reminder_id=reminder.id,
                offset_minutes=120,
            )

        self.assertEqual(
            _as_utc(updated.remind_at),
            datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        )
        self.assertEqual(updated.offset_minutes, 120)
        self.assertNotEqual(updated.dedupe_key, original_key)
        self.assertEqual(updated.attempt_count, 0)

    async def test_reschedule_to_another_reminders_time_conflicts(self):

        first = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )
        await self._create(
            task_id=self.timed_task.id,
            offset_minutes=120,
        )

        async with self.SessionLocal() as session:
            with self.assertRaises(ReminderConflictError):
                await reminder_service.reschedule_reminder(
                    session=session,
                    user_id=self.alice_id,
                    reminder_id=first.id,
                    offset_minutes=120,
                )

    async def test_cancel_is_idempotent_and_blocks_rescheduling(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        async with self.SessionLocal() as session:
            cancelled = await reminder_service.cancel_reminder(
                session=session,
                user_id=self.alice_id,
                reminder_id=reminder.id,
            )
            cancelled_again = await reminder_service.cancel_reminder(
                session=session,
                user_id=self.alice_id,
                reminder_id=reminder.id,
            )

            self.assertEqual(
                cancelled.status,
                ReminderStatus.CANCELLED,
            )
            self.assertEqual(
                cancelled_again.status,
                ReminderStatus.CANCELLED,
            )

            with self.assertRaises(ReminderConflictError):
                await reminder_service.reschedule_reminder(
                    session=session,
                    user_id=self.alice_id,
                    reminder_id=reminder.id,
                    offset_minutes=30,
                )

    async def test_delete_removes_reminder(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        async with self.SessionLocal() as session:
            await reminder_service.delete_reminder(
                session=session,
                user_id=self.alice_id,
                reminder_id=reminder.id,
            )

            with self.assertRaises(ReminderNotFoundError):
                await reminder_service.get_reminder(
                    session=session,
                    user_id=self.alice_id,
                    reminder_id=reminder.id,
                )

    async def test_deleting_task_cascades_to_reminders(self):

        reminder = await self._create(
            task_id=self.timed_task.id,
            offset_minutes=60,
        )

        async with self.SessionLocal() as session:
            await task_service.delete_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.timed_task.id,
            )

            remaining = await session.scalar(
                select(func.count())
                .select_from(TaskReminder)
                .where(TaskReminder.id == reminder.id)
            )

        self.assertEqual(remaining, 0)


class ReminderApiTests(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="api_reminder_alice")
            bob = User(username="api_reminder_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.alice_task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="API reminder task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

            self.bob_task = await task_service.create_task(
                session=session,
                user_id=self.bob_id,
                title="Bob API task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

        self._clock_patch = patch(
            "app.services.reminder_service._utcnow",
            return_value=FIXED_NOW,
        )
        self._clock_patch.start()
        self.addCleanup(self._clock_patch.stop)

        self._time_patch = patch.object(
            settings,
            "REMINDER_DEFAULT_LOCAL_TIME",
            "09:00",
        )
        self._time_patch.start()
        self.addCleanup(self._time_patch.stop)

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

    def _use_bob(self):
        app.dependency_overrides[get_current_user_id] = self.override_bob
        app.dependency_overrides[get_optional_user_id] = self.override_bob

    def _use_alice(self):
        app.dependency_overrides[get_current_user_id] = self.override_alice
        app.dependency_overrides[get_optional_user_id] = self.override_alice

    async def _create_via_api(self, **overrides):
        payload = {"offset_minutes": 60}
        payload.update(overrides)

        return await self.client.post(
            f"/api/tasks/{self.alice_task.id}/reminders",
            json=payload,
        )

    async def test_create_and_list_reminder(self):

        response = await self._create_via_api()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["task_id"], self.alice_task.id)
        self.assertEqual(body["user_id"], self.alice_id)
        self.assertEqual(body["offset_minutes"], 60)
        self.assertEqual(body["status"], "pending")
        self.assertEqual(
            _as_utc(datetime.fromisoformat(body["remind_at"])),
            datetime(2026, 11, 20, 16, 0, tzinfo=UTC),
        )

        listed = await self.client.get(
            f"/api/tasks/{self.alice_task.id}/reminders"
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["id"], body["id"])

    async def test_create_validates_scheduling_source(self):

        response = await self.client.post(
            f"/api/tasks/{self.alice_task.id}/reminders",
            json={},
        )

        self.assertEqual(response.status_code, 422)

        response = await self.client.post(
            f"/api/tasks/{self.alice_task.id}/reminders",
            json={
                "offset_minutes": 60,
                "remind_at": "2026-11-20T16:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 422)

    async def test_create_for_missing_task_returns_404(self):

        response = await self.client.post(
            "/api/tasks/999999/reminders",
            json={"offset_minutes": 60},
        )

        self.assertEqual(response.status_code, 404)

    async def test_duplicate_returns_409(self):

        first = await self._create_via_api()
        second = await self._create_via_api()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)

    async def test_offset_without_due_date_returns_400(self):

        async with self.SessionLocal() as session:
            undated = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Undated API task",
            )

        response = await self.client.post(
            f"/api/tasks/{undated.id}/reminders",
            json={"offset_minutes": 30},
        )

        self.assertEqual(response.status_code, 400)

    async def test_absolute_reminder_and_user_list(self):

        response = await self._create_via_api(
            offset_minutes=None,
            remind_at="2026-11-15T10:00:00+02:00",
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["offset_minutes"])

        listed = await self.client.get("/api/reminders")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

    async def test_reschedule_cancel_and_delete(self):

        created = await self._create_via_api()
        reminder_id = created.json()["id"]

        rescheduled = await self.client.patch(
            f"/api/reminders/{reminder_id}",
            json={"offset_minutes": 120},
        )

        self.assertEqual(rescheduled.status_code, 200)
        self.assertEqual(rescheduled.json()["offset_minutes"], 120)
        self.assertEqual(
            _as_utc(datetime.fromisoformat(rescheduled.json()["remind_at"])),
            datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        )

        cancelled = await self.client.post(
            f"/api/reminders/{reminder_id}/cancel"
        )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

        deleted = await self.client.delete(
            f"/api/reminders/{reminder_id}"
        )

        self.assertEqual(deleted.status_code, 204)

        listed = await self.client.get(
            f"/api/tasks/{self.alice_task.id}/reminders"
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])

    async def test_missing_reminder_returns_404(self):

        response = await self.client.patch(
            "/api/reminders/999999",
            json={"offset_minutes": 30},
        )

        self.assertEqual(response.status_code, 404)

    async def test_other_owner_cannot_read_or_change_reminders(self):

        created = await self._create_via_api()
        reminder_id = created.json()["id"]

        self._use_bob()

        task_list = await self.client.get(
            f"/api/tasks/{self.alice_task.id}/reminders"
        )
        self.assertEqual(task_list.status_code, 404)

        user_list = await self.client.get("/api/reminders")
        self.assertEqual(user_list.status_code, 200)
        self.assertEqual(user_list.json(), [])

        patched = await self.client.patch(
            f"/api/reminders/{reminder_id}",
            json={"offset_minutes": 30},
        )
        self.assertEqual(patched.status_code, 404)

        cancelled = await self.client.post(
            f"/api/reminders/{reminder_id}/cancel"
        )
        self.assertEqual(cancelled.status_code, 404)

        deleted = await self.client.delete(
            f"/api/reminders/{reminder_id}"
        )
        self.assertEqual(deleted.status_code, 404)

        self._use_alice()

        still_there = await self.client.get(
            f"/api/tasks/{self.alice_task.id}/reminders"
        )
        self.assertEqual(len(still_there.json()), 1)
        self.assertEqual(
            still_there.json()[0]["status"],
            ReminderStatus.PENDING.value,
        )

    async def test_other_owner_cannot_create_for_foreign_task(self):

        self._use_bob()

        response = await self.client.post(
            f"/api/tasks/{self.alice_task.id}/reminders",
            json={"offset_minutes": 60},
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
