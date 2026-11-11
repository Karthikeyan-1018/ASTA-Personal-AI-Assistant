"""Phase 10D recurring-task tests.

Covers daily/weekly/monthly cadences, custom weekdays, intervals, start and end
dates, timezone-aware anchors, duplicate prevention, finite generation, history
preservation, restart safety, concurrent claims, stale-lease recovery, worker
failures, and the owner-scoped REST contract.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.dependencies import get_current_user_id, get_optional_user_id
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.recurrence_exceptions import (
    RecurrenceConflictError,
    RecurrenceNotFoundError,
    RecurrenceValidationError,
)
from app.main import app
from app.models.recurrence import (
    TaskRecurrence,
    TaskRecurrenceOccurrence,
)
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.scheduler.recurrence_worker import RecurrenceWorker
from app.services.recurrence_service import recurrence_service
from app.services.task_service import task_service


FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def _as_date(value):
    return value.date() if isinstance(value, datetime) else value


class RecurrenceTestBase(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="rec_alice")
            bob = User(username="rec_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

            self.template = await task_service.create_task(
                session=session,
                user_id=self.alice_id,
                title="Daily standup",
                description="Team sync",
                category="project",
                priority="high",
                due_date=date(2026, 11, 12),
                due_time=time(9, 0),
                timezone="America/New_York",
            )

            self.bob_template = await task_service.create_task(
                session=session,
                user_id=self.bob_id,
                title="Bob routine",
                due_date=date(2026, 11, 12),
                due_time=time(9, 0),
                timezone="UTC",
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create_rule(self, **overrides):
        params = {
            "session": None,
            "user_id": self.alice_id,
            "task_id": self.template.id,
            "frequency": "daily",
            "interval": 1,
            "now": FIXED_NOW,
        }
        params.update(overrides)

        async with self.SessionLocal() as session:
            params["session"] = session
            return await recurrence_service.create_recurrence(**params)

    async def _new_task(self, **kwargs):
        async with self.SessionLocal() as session:
            return await task_service.create_task(
                session=session,
                user_id=kwargs.pop("user_id", self.alice_id),
                **kwargs,
            )

    async def _get_rule(self, rule_id):
        async with self.SessionLocal() as session:
            return await session.get(TaskRecurrence, rule_id)

    async def _occurrences(self, rule_id):
        async with self.SessionLocal() as session:
            rows = list(
                (
                    await session.scalars(
                        select(TaskRecurrenceOccurrence)
                        .where(
                            TaskRecurrenceOccurrence.recurrence_id == rule_id
                        )
                        .order_by(
                            TaskRecurrenceOccurrence.occurrence_date.asc()
                        )
                    )
                ).all()
            )
            return [row.occurrence_date for row in rows]

    async def _occurrence_tasks(self, rule_id):
        async with self.SessionLocal() as session:
            links = list(
                (
                    await session.scalars(
                        select(TaskRecurrenceOccurrence).where(
                            TaskRecurrenceOccurrence.recurrence_id == rule_id
                        )
                    )
                ).all()
            )

            tasks = []

            for link in links:
                task = await session.get(Task, link.task_id)

                if task is not None:
                    tasks.append(task)

            return tasks

    async def _generate(self, rule_id, now, horizon_days=30, cap=100):
        async with self.SessionLocal() as session:
            rule = await session.get(TaskRecurrence, rule_id)
            generated = await recurrence_service.generate_due(
                session=session,
                rule=rule,
                now=now,
                horizon_days=horizon_days,
                max_generations=cap,
            )
            return generated

    async def _task_count(self, user_id=None):
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.user_id == (user_id or self.alice_id))
                )
                or 0
            )


class RecurrenceGenerationTests(RecurrenceTestBase):

    async def test_daily_interval_generates_bounded_occurrences(self):
        rule = await self._create_rule(interval=2)

        self.assertEqual(rule.frequency.value, "daily")
        self.assertEqual(rule.start_date, date(2026, 11, 12))
        self.assertEqual(rule.last_generated_date, date(2026, 11, 12))
        self.assertEqual(await self._occurrences(rule.id), [date(2026, 11, 12)])

        generated = await self._generate(
            rule.id,
            now=FIXED_NOW,
            horizon_days=10,
        )

        self.assertEqual(len(generated), 4)
        self.assertEqual(
            [task.due_date for task in generated],
            [
                date(2026, 11, 14),
                date(2026, 11, 16),
                date(2026, 11, 18),
                date(2026, 11, 20),
            ],
        )

    async def test_generated_tasks_copy_template_fields(self):
        rule = await self._create_rule()
        generated = await self._generate(rule.id, now=FIXED_NOW, horizon_days=3)

        self.assertTrue(generated)
        baseline = self.template

        for task in generated:
            self.assertEqual(task.title, baseline.title)
            self.assertEqual(task.description, baseline.description)
            self.assertEqual(task.category, baseline.category)
            self.assertEqual(task.priority, baseline.priority)
            self.assertEqual(task.due_time, baseline.due_time)
            self.assertEqual(task.timezone, "America/New_York")
            self.assertEqual(task.status, TaskStatus.PENDING)
            self.assertFalse(task.is_completed)

    async def test_second_generation_is_idempotent(self):
        rule = await self._create_rule()
        first = await self._generate(rule.id, now=FIXED_NOW, horizon_days=5)
        before = await self._task_count()

        second = await self._generate(rule.id, now=FIXED_NOW, horizon_days=5)

        self.assertTrue(first)
        self.assertEqual(second, [])
        self.assertEqual(await self._task_count(), before)

    async def test_reset_cursor_does_not_duplicate_existing_dates(self):
        rule = await self._create_rule()
        await self._generate(rule.id, now=FIXED_NOW, horizon_days=10)
        before_dates = await self._occurrences(rule.id)
        before_tasks = await self._task_count()

        async with self.SessionLocal() as session:
            stored = await session.get(TaskRecurrence, rule.id)
            stored.last_generated_date = stored.start_date
            await session.commit()

        generated = await self._generate(rule.id, now=FIXED_NOW, horizon_days=10)

        self.assertEqual(generated, [])
        self.assertEqual(await self._occurrences(rule.id), before_dates)
        self.assertEqual(await self._task_count(), before_tasks)

    async def test_weekly_custom_weekdays(self):
        # 2026-11-09 is a Monday.
        task = await self._new_task(
            user_id=self.alice_id,
            title="Weekly review",
            due_date=date(2026, 11, 9),
            due_time=time(17, 0),
            timezone="UTC",
        )
        rule = await self._create_rule(
            task_id=task.id,
            frequency="weekly",
            weekdays=[0, 2, 4],
            interval=1,
        )

        self.assertEqual(await self._occurrences(rule.id), [date(2026, 11, 9)])

        generated = await self._generate(
            rule.id,
            now=FIXED_NOW,
            horizon_days=14,
        )

        expected = [
            date(2026, 11, 11),
            date(2026, 11, 13),
            date(2026, 11, 16),
            date(2026, 11, 18),
            date(2026, 11, 20),
            date(2026, 11, 23),
        ]

        self.assertEqual([task.due_date for task in generated], expected)

        for task in generated:
            self.assertIn(task.due_date.weekday(), {0, 2, 4})

    async def test_weekly_interval_skips_alternate_weeks(self):
        task = await self._new_task(
            user_id=self.alice_id,
            title="Biweekly review",
            due_date=date(2026, 11, 9),
            timezone="UTC",
        )
        rule = await self._create_rule(
            task_id=task.id,
            frequency="weekly",
            weekdays=[0],
            interval=2,
        )

        generated = await self._generate(
            rule.id,
            now=FIXED_NOW,
            horizon_days=28,
        )

        self.assertEqual(
            [task.due_date for task in generated],
            [date(2026, 11, 23), date(2026, 12, 7)],
        )

    async def test_monthly_preserves_day_of_month(self):
        task = await self._new_task(
            user_id=self.alice_id,
            title="Monthly report",
            due_date=date(2026, 1, 15),
            due_time=time(12, 0),
            timezone="UTC",
        )
        rule = await self._create_rule(
            task_id=task.id,
            frequency="monthly",
            interval=1,
        )

        generated = await self._generate(
            rule.id,
            now=datetime(2026, 1, 31, 12, 0, tzinfo=UTC),
            horizon_days=60,
        )

        self.assertEqual(
            [task.due_date for task in generated],
            [date(2026, 2, 15), date(2026, 3, 15)],
        )

    async def test_monthly_interval(self):
        task = await self._new_task(
            user_id=self.alice_id,
            title="Quarterly report",
            due_date=date(2026, 1, 15),
            timezone="UTC",
        )
        rule = await self._create_rule(
            task_id=task.id,
            frequency="monthly",
            interval=2,
        )

        generated = await self._generate(
            rule.id,
            now=datetime(2026, 1, 31, 12, 0, tzinfo=UTC),
            horizon_days=120,
        )

        self.assertEqual(
            [task.due_date for task in generated],
            [date(2026, 3, 15), date(2026, 5, 15)],
        )

    async def test_end_date_makes_generation_finite(self):
        rule = await self._create_rule(
            end_date=date(2026, 11, 16),
        )

        await self._generate(rule.id, now=FIXED_NOW, horizon_days=30)
        stored = await self._get_rule(rule.id)

        self.assertFalse(stored.is_active)
        self.assertIsNone(stored.next_generation_at)
        self.assertEqual(
            await self._occurrences(rule.id),
            [
                date(2026, 11, 12),
                date(2026, 11, 13),
                date(2026, 11, 14),
                date(2026, 11, 15),
                date(2026, 11, 16),
            ],
        )

    async def test_per_run_cap_limits_generation(self):
        rule = await self._create_rule()
        generated = await self._generate(
            rule.id,
            now=FIXED_NOW,
            horizon_days=30,
            cap=3,
        )

        self.assertEqual(len(generated), 3)

        # The rule remains active so the next run continues from the cursor.
        stored = await self._get_rule(rule.id)
        self.assertTrue(stored.is_active)
        self.assertEqual(stored.last_generated_date, date(2026, 11, 15))

    async def test_completed_and_cancelled_occurrences_do_not_stop_future_runs(self):
        rule = await self._create_rule()
        first = await self._generate(rule.id, now=FIXED_NOW, horizon_days=5)

        self.assertTrue(first)

        async with self.SessionLocal() as session:
            await task_service.complete_task(
                session=session,
                user_id=self.alice_id,
                task_id=first[0].id,
            )
            await task_service.change_status(
                session=session,
                user_id=self.alice_id,
                task_id=first[1].id,
                status=TaskStatus.CANCELLED,
            )

        second = await self._generate(
            rule.id,
            now=FIXED_NOW + timedelta(days=1),
            horizon_days=10,
        )

        self.assertTrue(second)
        self.assertTrue(all(task.due_date > first[-1].due_date for task in second))

        async with self.SessionLocal() as session:
            completed = await session.get(Task, first[0].id)
            cancelled = await session.get(Task, first[1].id)

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)

    async def test_deactivated_rule_generates_nothing(self):
        rule = await self._create_rule()

        async with self.SessionLocal() as session:
            await recurrence_service.deactivate_recurrence(
                session=session,
                user_id=self.alice_id,
                recurrence_id=rule.id,
            )

        generated = await self._generate(rule.id, now=FIXED_NOW, horizon_days=30)

        self.assertEqual(generated, [])
        self.assertEqual(await self._occurrences(rule.id), [date(2026, 11, 12)])

    async def test_missing_template_deactivates_rule(self):
        rule = await self._create_rule()

        # Delete the template directly. SQLite does not enforce foreign keys
        # here, so the rule survives as an orphan and exercises the worker's
        # missing-template recovery branch.
        async with self.SessionLocal() as session:
            template = await session.get(Task, self.template.id)
            await session.delete(template)
            await session.commit()

        generated = await self._generate(rule.id, now=FIXED_NOW, horizon_days=30)

        self.assertEqual(generated, [])

        stored = await self._get_rule(rule.id)
        self.assertIsNotNone(stored)
        self.assertFalse(stored.is_active)
        self.assertIsNone(stored.next_generation_at)
        self.assertIn("no longer exists", stored.last_error)

    async def test_start_date_after_due_date_does_not_duplicate_template(self):
        rule = await self._create_rule(start_date=date(2026, 11, 20))

        self.assertEqual(rule.start_date, date(2026, 11, 20))
        self.assertEqual(rule.last_generated_date, date(2026, 11, 12))
        self.assertEqual(await self._occurrences(rule.id), [])

        generated = await self._generate(
            rule.id,
            now=FIXED_NOW,
            horizon_days=20,
        )

        self.assertEqual(generated[0].due_date, date(2026, 11, 20))

    async def test_validation_rejects_past_start_and_bad_weekdays(self):
        with self.assertRaises(RecurrenceValidationError):
            await self._create_rule(start_date=date(2026, 11, 1))

        with self.assertRaises(RecurrenceValidationError):
            await self._create_rule(
                frequency="weekly",
                weekdays=[9],
            )

        with self.assertRaises(RecurrenceValidationError):
            await self._create_rule(
                frequency="daily",
                weekdays=[1],
            )

        with self.assertRaises(RecurrenceValidationError):
            await self._create_rule(
                end_date=date(2026, 11, 11),
            )

    async def test_duplicate_rule_is_rejected(self):
        await self._create_rule()

        with self.assertRaises(RecurrenceConflictError):
            await self._create_rule()

    async def test_task_without_due_date_is_rejected(self):
        task = await self._new_task(
            user_id=self.alice_id,
            title="No deadline",
        )

        with self.assertRaises(RecurrenceValidationError):
            await self._create_rule(task_id=task.id)

    async def test_completed_template_is_rejected(self):
        async with self.SessionLocal() as session:
            await task_service.complete_task(
                session=session,
                user_id=self.alice_id,
                task_id=self.template.id,
            )

        with self.assertRaises(RecurrenceValidationError):
            await self._create_rule()

    async def test_occurrences_are_owner_scoped(self):
        rule = await self._create_rule()
        await self._generate(rule.id, now=FIXED_NOW, horizon_days=5)

        async with self.SessionLocal() as session:
            with self.assertRaises(RecurrenceNotFoundError):
                await recurrence_service.get_recurrence(
                    session=session,
                    user_id=self.bob_id,
                    recurrence_id=rule.id,
                )


class RecurrenceWorkerTests(RecurrenceTestBase):

    def _worker(self, **kwargs):
        params = {
            "session_factory": self.SessionLocal,
            "batch_size": 10,
            "lease_seconds": 60,
            "horizon_days": 10,
            "max_generations": 3,
        }
        params.update(kwargs)
        return RecurrenceWorker(**params)

    async def test_worker_generates_and_advances_rule(self):
        rule = await self._create_rule()
        worker = self._worker()

        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["claimed"], 1)
        self.assertEqual(stats["generated"], 3)

        stored = await self._get_rule(rule.id)
        self.assertIsNone(stored.claim_token)
        self.assertIsNone(stored.processing_started_at)
        self.assertIsNone(stored.last_error)
        self.assertEqual(stored.last_generated_date, date(2026, 11, 15))

    async def test_worker_resumes_after_restart(self):
        rule = await self._create_rule()
        first = self._worker()

        await first.run_once(FIXED_NOW)

        restarted = self._worker()
        stats = await restarted.run_once(
            FIXED_NOW + timedelta(seconds=3601)
        )

        self.assertEqual(stats["claimed"], 1)
        self.assertEqual(stats["generated"], 3)

        dates = await self._occurrences(rule.id)
        self.assertEqual(len(dates), len(set(dates)))

    async def test_concurrent_claims_are_exclusive(self):
        await self._create_rule()
        first = self._worker()
        second = self._worker()

        first_claim = await first.claim_batch(FIXED_NOW)
        second_claim = await second.claim_batch(FIXED_NOW)

        self.assertEqual(len(first_claim), 1)
        self.assertEqual(second_claim, [])

    async def test_stale_processing_claim_is_recovered(self):
        rule = await self._create_rule()

        async with self.SessionLocal() as session:
            stored = await session.get(TaskRecurrence, rule.id)
            stored.claim_token = "stale-token"
            stored.processing_started_at = FIXED_NOW - timedelta(minutes=10)
            await session.commit()

        worker = self._worker()
        stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["recovered"], 1)
        self.assertEqual(stats["claimed"], 1)
        self.assertEqual(stats["generated"], 3)

        stored = await self._get_rule(rule.id)
        self.assertIsNone(stored.claim_token)
        self.assertIsNone(stored.processing_started_at)

    async def test_worker_failure_is_recorded_and_retried_later(self):
        rule = await self._create_rule()
        worker = self._worker()

        with patch.object(
            recurrence_service,
            "generate_due",
            new=AsyncMock(side_effect=RuntimeError("generation exploded")),
        ):
            stats = await worker.run_once(FIXED_NOW)

        self.assertEqual(stats["failed"], 1)

        stored = await self._get_rule(rule.id)
        self.assertIsNone(stored.claim_token)
        self.assertIsNotNone(stored.next_generation_at)
        self.assertIn("RuntimeError", stored.last_error)
        self.assertNotIn("generation exploded", stored.last_error)

    async def test_deactivated_rule_is_not_claimed(self):
        rule = await self._create_rule()

        async with self.SessionLocal() as session:
            await recurrence_service.deactivate_recurrence(
                session=session,
                user_id=self.alice_id,
                recurrence_id=rule.id,
            )

        stats = await self._worker().run_once(FIXED_NOW)

        self.assertEqual(stats["claimed"], 0)
        self.assertEqual(stats["generated"], 0)


class RecurrenceApiTests(RecurrenceTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.no_due_task = await self._new_task(
            user_id=self.alice_id,
            title="No deadline",
        )

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

    async def _post_rule(self, payload):
        return await self.client.post(
            f"/api/tasks/{self.template.id}/recurrence",
            json=payload,
        )

    async def test_create_get_and_list(self):
        created = await self._post_rule(
            {
                "frequency": "daily",
                "interval": 2,
                "start_date": "2026-11-12",
            }
        )

        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual(body["frequency"], "daily")
        self.assertEqual(body["interval"], 2)
        self.assertEqual(body["weekdays"], [])
        self.assertTrue(body["is_active"])

        fetched = await self.client.get(
            f"/api/tasks/{self.template.id}/recurrence"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["id"], body["id"])

        listed = await self.client.get("/api/recurrences")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        detail = await self.client.get(
            f"/api/recurrences/{body['id']}"
        )
        self.assertEqual(detail.status_code, 200)

    async def test_weekly_rule_serialises_weekdays(self):
        created = await self._post_rule(
            {
                "frequency": "weekly",
                "interval": 1,
                "weekdays": [0, 2, 4],
            }
        )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["weekdays"], [0, 2, 4])

    async def test_duplicate_rule_returns_409(self):
        first = await self._post_rule({"frequency": "daily"})
        self.assertEqual(first.status_code, 201, first.text)

        duplicate = await self._post_rule({"frequency": "daily"})
        self.assertEqual(duplicate.status_code, 409)

    async def test_invalid_payload_returns_400(self):
        invalid = await self._post_rule(
            {
                "frequency": "daily",
                "weekdays": [0, 1],
            }
        )
        self.assertEqual(invalid.status_code, 422)

        bad_window = await self._post_rule(
            {
                "frequency": "daily",
                "start_date": "2026-11-20",
                "end_date": "2026-11-15",
            }
        )
        self.assertEqual(bad_window.status_code, 422)

    async def test_task_without_due_date_returns_400(self):
        response = await self.client.post(
            f"/api/tasks/{self.no_due_task.id}/recurrence",
            json={"frequency": "daily"},
        )
        self.assertEqual(response.status_code, 400)

    async def test_update_and_deactivate_preserve_history(self):
        created = await self._post_rule({"frequency": "daily"})
        recurrence_id = created.json()["id"]

        async with self.SessionLocal() as session:
            rule = await session.get(TaskRecurrence, recurrence_id)
            await recurrence_service.generate_due(
                session=session,
                rule=rule,
                now=FIXED_NOW,
                horizon_days=5,
                max_generations=10,
            )

        before = await self._occurrences(recurrence_id)
        self.assertTrue(before)

        patched = await self.client.patch(
            f"/api/recurrences/{recurrence_id}",
            json={"interval": 3, "end_date": "2026-12-31"},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["interval"], 3)
        self.assertEqual(patched.json()["end_date"], "2026-12-31")

        deleted = await self.client.delete(f"/api/recurrences/{recurrence_id}")
        self.assertEqual(deleted.status_code, 204)

        fetched = await self.client.get(f"/api/recurrences/{recurrence_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertFalse(fetched.json()["is_active"])

        occurrences = await self.client.get(
            f"/api/recurrences/{recurrence_id}/occurrences"
        )
        self.assertEqual(occurrences.status_code, 200)
        self.assertEqual(len(occurrences.json()), len(before))

        async with self.SessionLocal() as session:
            remaining = await session.get(TaskRecurrence, recurrence_id)

        self.assertIsNotNone(remaining)

    async def test_missing_rule_and_task_return_404(self):
        missing_rule = await self.client.get("/api/recurrences/999999")
        self.assertEqual(missing_rule.status_code, 404)

        missing_task = await self.client.get(
            "/api/tasks/999999/recurrence"
        )
        self.assertEqual(missing_task.status_code, 404)

        missing_create = await self.client.post(
            "/api/tasks/999999/recurrence",
            json={"frequency": "daily"},
        )
        self.assertEqual(missing_create.status_code, 404)

    async def test_other_owner_cannot_access_rules(self):
        created = await self._post_rule({"frequency": "daily"})
        recurrence_id = created.json()["id"]

        app.dependency_overrides[get_current_user_id] = self.override_bob
        app.dependency_overrides[get_optional_user_id] = self.override_bob

        listed = await self.client.get("/api/recurrences")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])

        detail = await self.client.get(f"/api/recurrences/{recurrence_id}")
        self.assertEqual(detail.status_code, 404)

        patched = await self.client.patch(
            f"/api/recurrences/{recurrence_id}",
            json={"interval": 2},
        )
        self.assertEqual(patched.status_code, 404)

        deleted = await self.client.delete(
            f"/api/recurrences/{recurrence_id}"
        )
        self.assertEqual(deleted.status_code, 404)

        occurrences = await self.client.get(
            f"/api/recurrences/{recurrence_id}/occurrences"
        )
        self.assertEqual(occurrences.status_code, 404)

        foreign_task = await self.client.post(
            f"/api/tasks/{self.template.id}/recurrence",
            json={"frequency": "daily"},
        )
        self.assertEqual(foreign_task.status_code, 404)

        bob_rule = await self.client.post(
            f"/api/tasks/{self.bob_template.id}/recurrence",
            json={"frequency": "daily"},
        )
        self.assertEqual(bob_rule.status_code, 201, bob_rule.text)


if __name__ == "__main__":
    unittest.main()
