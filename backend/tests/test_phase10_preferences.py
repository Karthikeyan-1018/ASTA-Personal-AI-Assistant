"""Phase 10F notification-preference and quiet-hour tests.

Covers default preferences, persistence, validation, midnight-crossing windows,
timezone-aware windows, daylight-saving behaviour, quiet-hour deferral (never
discard), disabled in-app notifications, and the owner-scoped REST contract.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.dependencies import get_current_user_id, get_optional_user_id
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.notification_exceptions import (
    NotificationValidationError,
)
from app.main import app
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.user import User
from app.scheduler.notification_delivery import (
    deliver_in_app_notification,
)
from app.scheduler.reminder_worker import ReminderWorker
from app.services.notification_preference_service import (
    notification_preference_service,
)
from app.services.reminder_service import reminder_service
from app.services.task_service import task_service


FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
QUIET_NOW = datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)
UTC = timezone.utc


def _as_utc(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


class PreferenceTestBase(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="pref_alice")
            bob = User(username="pref_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.task = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Quiet task",
                due_date=date(2026, 11, 20),
                due_time=time(17, 0),
                timezone="UTC",
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _set_preferences(self, user_id=None, **changes):
        async with self.SessionLocal() as session:
            return await notification_preference_service.update_preferences(
                session=session,
                user_id=user_id or self.alice_id,
                changes=changes,
            )

    async def _get_preferences(self, user_id=None):
        async with self.SessionLocal() as session:
            return await notification_preference_service.get_preferences(
                session=session,
                user_id=user_id or self.alice_id,
            )

    async def _preference_count(self):
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(NotificationPreference)
                )
                or 0
            )

    async def _due_reminder(self):
        with patch(
            "app.services.reminder_service._utcnow",
            return_value=FIXED_NOW,
        ):
            async with self.SessionLocal() as session:
                reminder = await reminder_service.create_reminder(
                    session=session,
                    user_id=self.alice_id,
                    task_id=self.task.id,
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
                        Notification.user_id == (user_id or self.alice_id)
                    )
                )
                or 0
            )


class PreferenceServiceTests(PreferenceTestBase):

    async def test_missing_row_uses_defaults_without_persisting(self):
        pref = await self._get_preferences()

        self.assertIsNone(pref.id)
        self.assertEqual(pref.user_id, self.alice_id)
        self.assertTrue(pref.in_app_enabled)
        self.assertFalse(pref.quiet_hours_enabled)
        self.assertEqual(pref.quiet_hours_start, time(22, 0))
        self.assertEqual(pref.quiet_hours_end, time(7, 0))
        self.assertEqual(pref.timezone, "UTC")
        self.assertEqual(await self._preference_count(), 0)

    async def test_first_update_persists_and_reuses_row(self):
        pref = await self._set_preferences(
            quiet_hours_enabled=True,
            quiet_hours_start=time(21, 30),
            quiet_hours_end=time(6, 30),
            timezone="America/New_York",
        )

        self.assertIsNotNone(pref.id)
        self.assertEqual(await self._preference_count(), 1)

        reread = await self._get_preferences()
        self.assertEqual(reread.id, pref.id)
        self.assertTrue(reread.quiet_hours_enabled)
        self.assertEqual(reread.quiet_hours_start, time(21, 30))
        self.assertEqual(reread.quiet_hours_end, time(6, 30))
        self.assertEqual(reread.timezone, "America/New_York")

    async def test_partial_update_preserves_other_fields(self):
        await self._set_preferences(
            in_app_enabled=False,
            quiet_hours_start=time(20, 0),
        )

        updated = await self._set_preferences(quiet_hours_end=time(8, 0))

        self.assertFalse(updated.in_app_enabled)
        self.assertEqual(updated.quiet_hours_start, time(20, 0))
        self.assertEqual(updated.quiet_hours_end, time(8, 0))

    async def test_timezone_is_canonicalised(self):
        pref = await self._set_preferences(timezone="  utc  ")

        self.assertEqual(pref.timezone, "UTC")

    async def test_invalid_timezone_is_rejected(self):
        with self.assertRaises(NotificationValidationError):
            await self._set_preferences(timezone="Mars/Olympus_Mons")

        self.assertEqual(await self._preference_count(), 0)

    async def test_unknown_field_is_rejected(self):
        with self.assertRaises(NotificationValidationError):
            await self._set_preferences(unknown_field=True)

    async def test_equal_window_when_enabled_is_rejected(self):
        with self.assertRaises(NotificationValidationError):
            await self._set_preferences(
                quiet_hours_enabled=True,
                quiet_hours_start=time(10, 0),
                quiet_hours_end=time(10, 0),
            )

        # Validation happens before persistence.
        self.assertEqual(await self._preference_count(), 0)

    async def test_owner_preferences_are_isolated(self):
        await self._set_preferences(
            quiet_hours_enabled=True,
            quiet_hours_start=time(21, 0),
            quiet_hours_end=time(6, 0),
        )

        bob_pref = await self._get_preferences(self.bob_id)

        self.assertIsNone(bob_pref.id)
        self.assertFalse(bob_pref.quiet_hours_enabled)


class QuietHoursTests(PreferenceTestBase):

    async def _window(self, start, end, tz="UTC", enabled=True):
        return await self._set_preferences(
            quiet_hours_enabled=enabled,
            quiet_hours_start=start,
            quiet_hours_end=end,
            timezone=tz,
        )

    async def test_disabled_window_is_never_active(self):
        pref = await self._window(time(0, 0), time(23, 59), enabled=False)

        self.assertFalse(
            notification_preference_service.is_quiet_hours_active(
                pref, datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
            )
        )
        self.assertIsNone(
            notification_preference_service.quiet_hours_end(
                pref, datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
            )
        )

    async def test_same_day_window(self):
        pref = await self._window(time(9, 0), time(17, 0))

        active = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        before = datetime(2026, 11, 10, 8, 59, tzinfo=UTC)
        after = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)

        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(pref, active)
        )
        self.assertFalse(
            notification_preference_service.is_quiet_hours_active(pref, before)
        )
        # The end boundary is exclusive.
        self.assertFalse(
            notification_preference_service.is_quiet_hours_active(pref, after)
        )
        self.assertEqual(
            _as_utc(
                notification_preference_service.quiet_hours_end(pref, active)
            ),
            datetime(2026, 11, 10, 17, 0, tzinfo=UTC),
        )

    async def test_midnight_crossing_window_after_start(self):
        pref = await self._window(time(22, 0), time(7, 0))

        late = datetime(2026, 11, 10, 23, 30, tzinfo=UTC)

        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(pref, late)
        )
        self.assertEqual(
            _as_utc(
                notification_preference_service.quiet_hours_end(pref, late)
            ),
            datetime(2026, 11, 11, 7, 0, tzinfo=UTC),
        )

    async def test_midnight_crossing_window_before_end(self):
        pref = await self._window(time(22, 0), time(7, 0))

        early = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)

        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(pref, early)
        )
        self.assertEqual(
            _as_utc(
                notification_preference_service.quiet_hours_end(pref, early)
            ),
            datetime(2026, 11, 11, 7, 0, tzinfo=UTC),
        )

    async def test_window_boundaries(self):
        pref = await self._window(time(22, 0), time(7, 0))

        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(
                pref, datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
            )
        )
        self.assertFalse(
            notification_preference_service.is_quiet_hours_active(
                pref, datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
            )
        )
        self.assertFalse(
            notification_preference_service.is_quiet_hours_active(
                pref, datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
            )
        )

    async def test_window_is_evaluated_in_owner_timezone(self):
        pref = await self._window(
            time(22, 0),
            time(7, 0),
            tz="America/New_York",
        )

        # 2026-11-11 04:00 UTC is 2026-11-10 23:00 EST.
        now = datetime(2026, 11, 11, 4, 0, tzinfo=UTC)

        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(pref, now)
        )
        self.assertEqual(
            _as_utc(
                notification_preference_service.quiet_hours_end(pref, now)
            ),
            datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        )

    async def test_window_respects_daylight_saving(self):
        pref = await self._window(
            time(22, 0),
            time(7, 0),
            tz="America/New_York",
        )

        # Summer: 2026-06-15 03:00 UTC is 2026-06-14 23:00 EDT (UTC-4).
        summer = datetime(2026, 6, 15, 3, 0, tzinfo=UTC)
        # Winter: 2026-12-15 03:00 UTC is 2026-12-14 22:00 EST (UTC-5).
        winter = datetime(2026, 12, 15, 3, 0, tzinfo=UTC)

        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(
                pref, summer
            )
        )
        self.assertTrue(
            notification_preference_service.is_quiet_hours_active(
                pref, winter
            )
        )
        self.assertEqual(
            _as_utc(
                notification_preference_service.quiet_hours_end(pref, summer)
            ),
            datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
        )
        self.assertEqual(
            _as_utc(
                notification_preference_service.quiet_hours_end(pref, winter)
            ),
            datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
        )

    async def test_quiet_end_is_strictly_future(self):
        pref = await self._window(time(22, 0), time(7, 0))

        now = datetime(2026, 11, 11, 6, 59, 59, tzinfo=UTC)
        end = notification_preference_service.quiet_hours_end(pref, now)

        self.assertGreater(_as_utc(end), now)


class QuietHoursDeliveryTests(PreferenceTestBase):

    async def _deliver(self, now):
        worker = ReminderWorker(
            session_factory=self.SessionLocal,
            delivery_handler=deliver_in_app_notification,
        )

        with patch(
            "app.scheduler.notification_delivery._utcnow",
            return_value=now,
        ):
            return await worker.run_once(now)

    async def test_quiet_hours_defers_instead_of_discarding(self):
        reminder = await self._due_reminder()
        await self._set_preferences(
            quiet_hours_enabled=True,
            quiet_hours_start=time(22, 0),
            quiet_hours_end=time(7, 0),
            timezone="UTC",
        )

        stats = await self._deliver(QUIET_NOW)

        self.assertEqual(stats["deferred"], 1)
        self.assertEqual(await self._notification_count(), 0)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.PENDING)
        self.assertEqual(
            _as_utc(stored.next_attempt_at),
            datetime(2026, 11, 11, 7, 0, tzinfo=UTC),
        )

    async def test_deferred_reminder_is_delivered_after_window(self):
        reminder = await self._due_reminder()
        await self._set_preferences(
            quiet_hours_enabled=True,
            quiet_hours_start=time(22, 0),
            quiet_hours_end=time(7, 0),
            timezone="UTC",
        )

        await self._deliver(QUIET_NOW)

        after = datetime(2026, 11, 11, 7, 1, tzinfo=UTC)
        stats = await self._deliver(after)

        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(await self._notification_count(), 1)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.DELIVERED)

    async def test_notification_time_is_after_quiet_hours(self):
        await self._due_reminder()
        await self._set_preferences(
            quiet_hours_enabled=True,
            quiet_hours_start=time(22, 0),
            quiet_hours_end=time(7, 0),
            timezone="UTC",
        )

        await self._deliver(QUIET_NOW)
        after = datetime(2026, 11, 11, 7, 1, tzinfo=UTC)
        await self._deliver(after)

        async with self.SessionLocal() as session:
            item = await session.scalar(
                select(Notification).where(
                    Notification.user_id == self.alice_id
                )
            )

        self.assertIsNotNone(item)
        self.assertGreaterEqual(_as_utc(item.delivered_at), after)

    async def test_disabled_in_app_notifications_suppress_delivery(self):
        reminder = await self._due_reminder()
        await self._set_preferences(in_app_enabled=False)

        stats = await self._deliver(FIXED_NOW)

        self.assertEqual(stats["suppressed"], 1)
        self.assertEqual(await self._notification_count(), 0)

        stored = await self._get_reminder(reminder.id)
        self.assertEqual(stored.status, ReminderStatus.SUPPRESSED)
        self.assertEqual(
            stored.last_error,
            "in-app notifications disabled",
        )


class PreferenceApiTests(PreferenceTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()

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

    async def test_get_defaults_does_not_create_row(self):
        response = await self.client.get("/api/notifications/preferences")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["id"])
        self.assertTrue(body["in_app_enabled"])
        self.assertFalse(body["quiet_hours_enabled"])
        self.assertEqual(body["quiet_hours_start"], "22:00:00")
        self.assertEqual(body["quiet_hours_end"], "07:00:00")
        self.assertEqual(body["timezone"], "UTC")
        self.assertFalse(body["quiet_hours_active"])
        self.assertIsNone(body["quiet_hours_ends_at"])
        self.assertEqual(await self._preference_count(), 0)

    async def test_patch_persists_preferences(self):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "21:30:00",
                "quiet_hours_end": "06:30:00",
                "timezone": "America/New_York",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNotNone(body["id"])
        self.assertTrue(body["quiet_hours_enabled"])
        self.assertEqual(body["quiet_hours_start"], "21:30:00")
        self.assertEqual(body["quiet_hours_end"], "06:30:00")
        self.assertEqual(body["timezone"], "America/New_York")
        self.assertEqual(await self._preference_count(), 1)

        reread = await self.client.get("/api/notifications/preferences")
        self.assertEqual(reread.status_code, 200)
        self.assertEqual(reread.json()["id"], body["id"])

    async def test_patch_reports_active_quiet_window(self):
        await self.client.patch(
            "/api/notifications/preferences",
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:00:00",
                "quiet_hours_end": "07:00:00",
                "timezone": "UTC",
            },
        )

        with patch(
            "app.services.notification_preference_service._utcnow",
            return_value=QUIET_NOW,
        ):
            response = await self.client.get(
                "/api/notifications/preferences"
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["quiet_hours_active"])
        self.assertEqual(
            _as_utc(datetime.fromisoformat(body["quiet_hours_ends_at"])),
            datetime(2026, 11, 11, 7, 0, tzinfo=UTC),
        )

    async def test_in_app_toggle(self):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json={"in_app_enabled": False},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["in_app_enabled"])

    async def test_invalid_timezone_returns_400(self):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json={"timezone": "Not/AZone"},
        )

        self.assertEqual(response.status_code, 400)

    async def test_timezone_aware_quiet_time_returns_422(self):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json={"quiet_hours_start": "22:00:00+00:00"},
        )

        self.assertEqual(response.status_code, 422)

    async def test_unknown_field_returns_422(self):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json={"snooze": True},
        )

        self.assertEqual(response.status_code, 422)

    async def test_equal_window_returns_422(self):
        response = await self.client.patch(
            "/api/notifications/preferences",
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "10:00:00",
                "quiet_hours_end": "10:00:00",
            },
        )

        self.assertEqual(response.status_code, 422)

    async def test_preferences_are_owner_scoped(self):
        await self.client.patch(
            "/api/notifications/preferences",
            json={
                "in_app_enabled": False,
                "quiet_hours_enabled": True,
            },
        )

        app.dependency_overrides[get_current_user_id] = self.override_bob
        app.dependency_overrides[get_optional_user_id] = self.override_bob

        bob = await self.client.get("/api/notifications/preferences")

        self.assertEqual(bob.status_code, 200)
        body = bob.json()
        self.assertIsNone(body["id"])
        self.assertTrue(body["in_app_enabled"])
        self.assertFalse(body["quiet_hours_enabled"])
        self.assertEqual(await self._preference_count(), 1)


if __name__ == "__main__":
    unittest.main()
