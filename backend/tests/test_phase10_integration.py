"""Phase 10G end-to-end integration and regression tests.

Exercises the complete reminder delivery path, notification history retention,
recurrence generation and confirmation-gated completion, preference-driven
suppression, ownership boundaries, authentication, and the pre-existing task
workflow through the public REST API and the real scheduler workers.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.dependencies import get_current_user_id, get_optional_user_id
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.conversation import Conversation
from app.models.notification import Notification
from app.models.recurrence import TaskRecurrenceOccurrence
from app.models.reminder import TaskReminder
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.scheduler.notification_delivery import (
    deliver_in_app_notification,
)
from app.scheduler.recurrence_worker import RecurrenceWorker
from app.scheduler.reminder_worker import ReminderWorker
from app.task_intent.pending_service import pending_action_service


FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
DELIVERY_NOW = datetime(2026, 11, 20, 16, 1, tzinfo=timezone.utc)
UTC = timezone.utc


class Phase10IntegrationTests(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="int_alice")
            bob = User(username="int_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.conversation = Conversation(
                user_id=self.alice_id,
                title="Integration",
            )
            session.add(self.conversation)
            await session.commit()
            await session.refresh(self.conversation)
            self.conversation_id = self.conversation.id

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

    async def _create_task(self, **overrides):
        payload = {
            "title": "Integration task",
            "description": "End-to-end",
            "category": "project",
            "priority": "high",
            "due_date": "2026-11-20",
            "due_time": "17:00:00",
            "timezone": "UTC",
        }
        payload.update(overrides)

        response = await self.client.post("/api/tasks/", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def _notifications(self):
        response = await self.client.get("/api/notifications")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

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

    async def _run_recurrences(self, now, *, horizon=3, cap=10):
        worker = RecurrenceWorker(
            session_factory=self.SessionLocal,
            horizon_days=horizon,
            max_generations=cap,
        )
        return await worker.run_once(now)

    async def _create_recurrence(self, task_id, payload):
        with patch(
            "app.services.recurrence_service._utcnow",
            return_value=FIXED_NOW,
        ):
            return await self.client.post(
                f"/api/tasks/{task_id}/recurrence",
                json=payload,
            )

    async def test_task_reminder_notification_read_and_delete_flow(self):
        task = await self._create_task()

        reminder_response = await self.client.post(
            f"/api/tasks/{task['id']}/reminders",
            json={"offset_minutes": 60},
        )
        self.assertEqual(reminder_response.status_code, 201, reminder_response.text)
        reminder = reminder_response.json()
        self.assertEqual(reminder["status"], "pending")

        stats = await self._run_reminders(DELIVERY_NOW)
        self.assertEqual(stats["delivered"], 1)

        notifications = await self._notifications()
        self.assertEqual(len(notifications), 1)

        item = notifications[0]
        self.assertEqual(item["notification_type"], "reminder")
        self.assertEqual(item["delivery_status"], "delivered")
        self.assertEqual(item["task_id"], task["id"])
        self.assertEqual(item["reminder_id"], reminder["id"])
        self.assertEqual(item["task_title"], task["title"])
        self.assertFalse(item["is_read"])

        unread = await self.client.get("/api/notifications/unread")
        self.assertEqual(unread.status_code, 200)
        self.assertEqual(len(unread.json()), 1)

        marked = await self.client.patch(
            f"/api/notifications/{item['id']}/read"
        )
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.json()["is_read"])

        empty = await self.client.get("/api/notifications/unread")
        self.assertEqual(empty.json(), [])

        deleted = await self.client.delete(f"/api/tasks/{task['id']}")
        self.assertEqual(deleted.status_code, 204)

        retained = await self.client.get(
            f"/api/notifications/{item['id']}"
        )
        self.assertEqual(retained.status_code, 200)

        body = retained.json()
        self.assertIsNone(body["task_id"])
        self.assertIsNone(body["reminder_id"])
        self.assertEqual(body["task_title"], task["title"])
        self.assertTrue(body["is_read"])

        async with self.SessionLocal() as session:
            reminder_rows = list(
                (
                    await session.scalars(
                        select(TaskReminder).where(
                            TaskReminder.id == reminder["id"]
                        )
                    )
                ).all()
            )
            notification = await session.get(Notification, item["id"])

        self.assertEqual(reminder_rows, [])
        self.assertIsNotNone(notification)

    async def test_second_worker_run_does_not_duplicate_notifications(self):
        task = await self._create_task()

        await self.client.post(
            f"/api/tasks/{task['id']}/reminders",
            json={"offset_minutes": 60},
        )

        await self._run_reminders(DELIVERY_NOW)
        stats = await self._run_reminders(DELIVERY_NOW)

        self.assertEqual(stats["claimed"], 0)

        notifications = await self._notifications()
        self.assertEqual(len(notifications), 1)

    async def test_in_app_disabled_suppresses_end_to_end(self):
        task = await self._create_task()

        await self.client.post(
            f"/api/tasks/{task['id']}/reminders",
            json={"offset_minutes": 60},
        )

        pref = await self.client.patch(
            "/api/notifications/preferences",
            json={"in_app_enabled": False},
        )
        self.assertEqual(pref.status_code, 200)

        stats = await self._run_reminders(DELIVERY_NOW)

        self.assertEqual(stats["suppressed"], 1)
        self.assertEqual(await self._notifications(), [])

        reminders = await self.client.get(
            f"/api/tasks/{task['id']}/reminders"
        )
        self.assertEqual(reminders.status_code, 200)
        self.assertEqual(reminders.json()[0]["status"], "suppressed")

    async def test_quiet_hours_defers_then_delivers_end_to_end(self):
        task = await self._create_task(
            due_date="2026-11-21",
            due_time="07:30:00",
        )

        await self.client.post(
            f"/api/tasks/{task['id']}/reminders",
            json={"offset_minutes": 60},
        )

        pref = await self.client.patch(
            "/api/notifications/preferences",
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:00:00",
                "quiet_hours_end": "07:00:00",
                "timezone": "UTC",
            },
        )
        self.assertEqual(pref.status_code, 200)

        # The reminder is due at 06:30 while the quiet window runs until 07:00.
        quiet_now = datetime(2026, 11, 21, 6, 45, tzinfo=UTC)
        stats = await self._run_reminders(quiet_now)
        self.assertEqual(stats["deferred"], 1)
        self.assertEqual(await self._notifications(), [])

        after = datetime(2026, 11, 21, 7, 1, tzinfo=UTC)
        stats = await self._run_reminders(after)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(len(await self._notifications()), 1)

    async def test_recurrence_generation_and_confirmation_completion(self):
        task = await self._create_task(
            due_date="2026-11-12",
            due_time="09:00:00",
        )

        created = await self._create_recurrence(
            task["id"],
            {
                "frequency": "daily",
                "interval": 1,
                "start_date": "2026-11-12",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        recurrence_id = created.json()["id"]

        first = await self._run_recurrences(FIXED_NOW, horizon=3)
        self.assertEqual(first["generated"], 1)

        occurrences = await self.client.get(
            f"/api/recurrences/{recurrence_id}/occurrences"
        )
        self.assertEqual(occurrences.status_code, 200)
        self.assertEqual(
            [row["occurrence_date"] for row in occurrences.json()],
            ["2026-11-12", "2026-11-13"],
        )

        second = await self._run_recurrences(
            FIXED_NOW + timedelta(seconds=3601),
            horizon=10,
            cap=5,
        )
        self.assertEqual(second["generated"], 5)

        occurrence_tasks = await self.client.get(
            "/api/tasks/",
            params={"status": "pending", "limit": 100},
        )
        self.assertEqual(occurrence_tasks.status_code, 200)

        generated = [
            item
            for item in occurrence_tasks.json()["tasks"]
            if item["due_date"] == "2026-11-14"
        ]
        self.assertEqual(len(generated), 1)
        target = generated[0]

        async with self.SessionLocal() as session:
            action = await pending_action_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=self.conversation_id,
                action_type="complete",
                payload={},
                summary=f"Complete {target['title']}",
                target_task_id=target["id"],
            )
            action_id = action.id

        confirmed = await self.client.post(
            f"/api/task-actions/{action_id}/confirm"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["task"]["status"], "completed")

        before = len(
            (
                await self.client.get(
                    f"/api/recurrences/{recurrence_id}/occurrences"
                )
            ).json()
        )

        third = await self._run_recurrences(
            FIXED_NOW + timedelta(days=5),
            horizon=10,
            cap=5,
        )
        self.assertEqual(third["generated"], 5)

        after = len(
            (
                await self.client.get(
                    f"/api/recurrences/{recurrence_id}/occurrences"
                )
            ).json()
        )
        self.assertEqual(after, before + 5)

        async with self.SessionLocal() as session:
            stored = await session.get(Task, target["id"])

        self.assertEqual(stored.status, TaskStatus.COMPLETED)

    async def test_deactivated_recurrence_preserves_generated_tasks(self):
        task = await self._create_task(
            due_date="2026-11-12",
            due_time="09:00:00",
        )

        created = await self._create_recurrence(
            task["id"],
            {"frequency": "daily"},
        )
        recurrence_id = created.json()["id"]

        await self._run_recurrences(FIXED_NOW, horizon=3)

        async with self.SessionLocal() as session:
            occurrence_count = len(
                (
                    await session.scalars(
                        select(TaskRecurrenceOccurrence).where(
                            TaskRecurrenceOccurrence.recurrence_id
                            == recurrence_id
                        )
                    )
                ).all()
            )

        deleted = await self.client.delete(
            f"/api/recurrences/{recurrence_id}"
        )
        self.assertEqual(deleted.status_code, 204)

        stats = await self._run_recurrences(
            FIXED_NOW + timedelta(seconds=7200),
            horizon=10,
        )
        self.assertEqual(stats["claimed"], 0)

        async with self.SessionLocal() as session:
            remaining = len(
                (
                    await session.scalars(
                        select(TaskRecurrenceOccurrence).where(
                            TaskRecurrenceOccurrence.recurrence_id
                            == recurrence_id
                        )
                    )
                ).all()
            )

        self.assertEqual(remaining, occurrence_count)

        rule = await self.client.get(f"/api/recurrences/{recurrence_id}")
        self.assertEqual(rule.status_code, 200)
        self.assertFalse(rule.json()["is_active"])

    async def test_cross_feature_ownership_isolation(self):
        task = await self._create_task()

        reminder = await self.client.post(
            f"/api/tasks/{task['id']}/reminders",
            json={"offset_minutes": 60},
        )
        reminder_id = reminder.json()["id"]

        recurrence = await self.client.post(
            f"/api/tasks/{task['id']}/recurrence",
            json={"frequency": "daily"},
        )
        recurrence_id = recurrence.json()["id"]

        await self._run_reminders(DELIVERY_NOW)
        notifications = await self._notifications()
        notification_id = notifications[0]["id"]

        self._as_bob()

        self.assertEqual((await self._notifications()), [])
        self.assertEqual(
            (
                await self.client.get(
                    f"/api/notifications/{notification_id}"
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.patch(
                    f"/api/notifications/{notification_id}/read"
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.get(
                    f"/api/tasks/{task['id']}/reminders"
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.delete(
                    f"/api/reminders/{reminder_id}"
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.get(f"/api/recurrences/{recurrence_id}")
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.get(
                    f"/api/recurrences/{recurrence_id}/occurrences"
                )
            ).status_code,
            404,
        )

    async def test_protected_routes_require_authentication(self):
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_optional_user_id, None)

        try:
            response = await self.client.get("/api/notifications")
            self.assertEqual(response.status_code, 401)

            prefs = await self.client.get(
                "/api/notifications/preferences"
            )
            self.assertEqual(prefs.status_code, 401)

            recurring = await self.client.get("/api/recurrences")
            self.assertEqual(recurring.status_code, 401)

        finally:
            self._as_alice()

    async def test_existing_task_lifecycle_remains_intact(self):
        task = await self._create_task(title="Regression task")

        fetched = await self.client.get(f"/api/tasks/{task['id']}")
        self.assertEqual(fetched.status_code, 200)

        updated = await self.client.patch(
            f"/api/tasks/{task['id']}",
            json={"title": "Renamed regression task"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["title"], "Renamed regression task")

        completed = await self.client.post(
            f"/api/tasks/{task['id']}/complete"
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "completed")

        pending = await self.client.get(
            "/api/tasks/",
            params={"status": "pending"},
        )
        self.assertEqual(pending.status_code, 200)
        self.assertNotIn(
            task["id"],
            [item["id"] for item in pending.json()["tasks"]],
        )

        reopened = await self.client.post(
            f"/api/tasks/{task['id']}/reopen"
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()["status"], "pending")

        deleted = await self.client.delete(f"/api/tasks/{task['id']}")
        self.assertEqual(deleted.status_code, 204)

        missing = await self.client.get(f"/api/tasks/{task['id']}")
        self.assertEqual(missing.status_code, 404)

    async def test_rejected_confirmation_does_not_change_task(self):
        task = await self._create_task(title="Confirmation task")

        async with self.SessionLocal() as session:
            action = await pending_action_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=self.conversation_id,
                action_type="complete",
                payload={},
                summary="Complete confirmation task",
                target_task_id=task["id"],
            )
            action_id = action.id

        rejected = await self.client.post(
            f"/api/task-actions/{action_id}/reject"
        )
        self.assertEqual(rejected.status_code, 200)

        async with self.SessionLocal() as session:
            stored = await session.get(Task, task["id"])

        self.assertEqual(stored.status, TaskStatus.PENDING)

        second = await self.client.post(
            f"/api/task-actions/{action_id}/confirm"
        )
        self.assertIn(second.status_code, {404, 409})


if __name__ == "__main__":
    unittest.main()
