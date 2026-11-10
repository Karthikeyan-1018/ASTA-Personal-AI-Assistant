"""Phase 7B task CRUD, deadline, isolation, and context tests."""

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import (
    get_current_user_id,
    get_optional_user_id,
)
from app.context.builder import UnifiedContextBuilder
from app.context.entries import ContextRequest, ContextSource
from app.context.providers import (
    DeadlineContextProvider,
    TaskContextProvider,
)
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.memory.memory_retriever import RetrievedMemory
from app.models.memory import Memory, MemoryStatus
from app.models.preference import (
    PreferenceSource,
    PreferenceStatus,
    UserPreference,
)
from app.models.task import (
    Task,
    TaskCategory,
    TaskPriority,
    TaskStatus,
)
from app.models.user import User
from app.services.task_service import task_service


FIXED_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


class TaskDBTestCase(unittest.IsolatedAsyncioTestCase):
    """Shared isolated database, users, and deterministic clock."""

    async def asyncSetUp(self):

        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False
        )

        async with self.SessionLocal() as session:
            alice = User(username="task_alice")
            bob = User(username="task_bob")

            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

        self.service_now_patcher = patch(
            "app.services.task_service._utcnow",
            return_value=FIXED_NOW
        )
        self.service_now = self.service_now_patcher.start()
        self.addCleanup(self.service_now_patcher.stop)

        self.selector_now_patcher = patch(
            "app.context.task_selector._utcnow",
            return_value=FIXED_NOW
        )
        self.selector_now = self.selector_now_patcher.start()
        self.addCleanup(self.selector_now_patcher.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _set_now(self, value: datetime):
        self.service_now.return_value = value
        self.selector_now.return_value = value

    async def _direct_task(self, user_id: int, **kwargs) -> Task:
        async with self.SessionLocal() as session:
            return await task_service.create_task(
                session=session,
                user_id=user_id,
                **kwargs
            )

    async def _count_tasks(self, user_id: int) -> int:
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.user_id == user_id)
                ) or 0
            )


class TaskAPITests(TaskDBTestCase):
    """Exercise /api/tasks CRUD, filters, lifecycle, and validation."""

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
            base_url="http://test"
        )

    async def asyncTearDown(self):

        await self.client.aclose()
        app.dependency_overrides.clear()
        await super().asyncTearDown()

    def _as_alice(self):
        app.dependency_overrides[get_current_user_id] = self.override_alice

    def _as_bob(self):
        app.dependency_overrides[get_current_user_id] = self.override_bob

    async def _create_task(self, **kwargs) -> dict:
        payload = {
            "title": kwargs.pop("title", "Sample task"),
            "category": kwargs.pop("category", "other"),
            "priority": kwargs.pop("priority", "medium"),
            "status": kwargs.pop("status", "pending"),
        }

        payload.update(kwargs)

        due_date = payload.get("due_date")
        due_time = payload.get("due_time")

        if isinstance(due_date, date):
            payload["due_date"] = due_date.isoformat()

        if isinstance(due_time, time):
            payload["due_time"] = due_time.isoformat()

        response = await self.client.post("/api/tasks/", json=payload)
        self.assertEqual(response.status_code, 201, response.text)

        return response.json()

    # ------------------------------------------------------------------
    # CRUD and filtering
    # ------------------------------------------------------------------

    async def test_create_get_and_list_task(self):

        created = await self._create_task(
            title="Submit college assignment",
            description="Finish the database assignment.",
            category="college",
            priority="high",
            due_date=date(2026, 11, 10),
            due_time=time(17, 30),
            timezone="America/New_York"
        )

        self.assertEqual(created["title"], "Submit college assignment")
        self.assertEqual(created["category"], "college")
        self.assertEqual(created["priority"], "high")
        self.assertEqual(created["status"], "pending")
        self.assertFalse(created["is_completed"])
        self.assertEqual(created["timezone"], "America/New_York")
        self.assertIsNone(created["completed_at"])

        task_id = created["id"]

        response = await self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], task_id)

        response = await self.client.get("/api/tasks/")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["tasks"][0]["id"], task_id)

        response = await self.client.get(
            "/api/tasks/",
            params={"category": "college", "priority": "high"}
        )
        self.assertEqual(response.json()["total"], 1)

        response = await self.client.get(
            "/api/tasks/",
            params={"category": "career"}
        )
        self.assertEqual(response.json()["total"], 0)

        response = await self.client.get(
            "/api/tasks/",
            params={
                "due_from": "2026-11-10",
                "due_to": "2026-11-10"
            }
        )
        self.assertEqual(response.json()["total"], 1)

    async def test_pagination_and_sorting(self):

        for title in ("Alpha task", "Bravo task", "Charlie task"):
            await self._create_task(title=title)

        response = await self.client.get(
            "/api/tasks/",
            params={"limit": 2, "offset": 0, "sort_by": "title",
                    "sort_order": "asc"}
        )

        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["tasks"]), 2)
        self.assertEqual(body["tasks"][0]["title"], "Alpha task")

        response = await self.client.get(
            "/api/tasks/",
            params={"limit": 2, "offset": 2, "sort_by": "title",
                    "sort_order": "asc"}
        )

        self.assertEqual(len(response.json()["tasks"]), 1)

    async def test_update_task_and_clear_deadline(self):

        created = await self._create_task(
            title="Original title",
            due_date=date(2026, 11, 10),
            due_time=time(9, 0),
            timezone="UTC"
        )

        task_id = created["id"]

        response = await self.client.patch(
            f"/api/tasks/{task_id}",
            json={
                "title": "Updated title",
                "description": "Updated description",
                "category": "career",
                "priority": "urgent",
                "status": "in_progress"
            }
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["title"], "Updated title")
        self.assertEqual(body["category"], "career")
        self.assertEqual(body["priority"], "urgent")
        self.assertEqual(body["status"], "in_progress")

        response = await self.client.patch(
            f"/api/tasks/{task_id}",
            json={"due_date": None}
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body["due_date"])
        self.assertIsNone(body["due_time"])
        self.assertIsNone(body["timezone"])

    async def test_delete_task(self):

        created = await self._create_task(title="Delete me")
        task_id = created["id"]

        response = await self.client.delete(f"/api/tasks/{task_id}")
        self.assertEqual(response.status_code, 204)

        response = await self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(response.status_code, 404)

        response = await self.client.get("/api/tasks/")
        self.assertEqual(response.json()["total"], 0)

    async def test_complete_and_reopen_task(self):

        created = await self._create_task(title="Complete me")
        task_id = created["id"]

        first = await self.client.post(f"/api/tasks/{task_id}/complete")
        self.assertEqual(first.status_code, 200, first.text)

        completed = first.json()
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["is_completed"])
        self.assertIsNotNone(completed["completed_at"])

        second = await self.client.post(f"/api/tasks/{task_id}/complete")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            second.json()["completed_at"],
            completed["completed_at"]
        )

        response = await self.client.get("/api/tasks/completed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

        reopened = await self.client.post(f"/api/tasks/{task_id}/reopen")
        self.assertEqual(reopened.status_code, 200, reopened.text)

        body = reopened.json()
        self.assertEqual(body["status"], "pending")
        self.assertFalse(body["is_completed"])
        self.assertIsNone(body["completed_at"])

    async def test_change_priority_and_status(self):

        created = await self._create_task(title="Lifecycle task")
        task_id = created["id"]

        response = await self.client.post(
            f"/api/tasks/{task_id}/priority",
            json={"priority": "high"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["priority"], "high")

        response = await self.client.post(
            f"/api/tasks/{task_id}/status",
            json={"status": "completed"}
        )
        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertTrue(body["is_completed"])
        self.assertIsNotNone(body["completed_at"])

        response = await self.client.post(
            f"/api/tasks/{task_id}/status",
            json={"status": "cancelled"}
        )

        body = response.json()
        self.assertEqual(body["status"], "cancelled")
        self.assertFalse(body["is_completed"])
        self.assertIsNone(body["completed_at"])

    async def test_category_priority_status_filters(self):

        await self._create_task(
            title="College task", category="college", priority="high"
        )
        await self._create_task(
            title="Career task", category="career", priority="low"
        )
        await self._create_task(
            title="Done task", category="career", priority="medium",
            status="completed"
        )

        for params, expected in (
            ({"category": "college"}, 1),
            ({"priority": "high"}, 1),
            ({"status": "completed"}, 1),
            ({"category": "career", "status": "pending"}, 1),
        ):
            response = await self.client.get("/api/tasks/", params=params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], expected)

        response = await self.client.get("/api/tasks/pending")
        self.assertEqual(len(response.json()), 2)

        response = await self.client.get("/api/tasks/in-progress")
        self.assertEqual(len(response.json()), 0)

    # ------------------------------------------------------------------
    # Ownership and invalid ids
    # ------------------------------------------------------------------

    async def test_ownership_isolation(self):

        bob_task = await self._direct_task(
            self.bob_id,
            title="Bob private task"
        )

        for method, path in (
            ("get", f"/api/tasks/{bob_task.id}"),
            ("patch", f"/api/tasks/{bob_task.id}"),
            ("delete", f"/api/tasks/{bob_task.id}"),
            ("post", f"/api/tasks/{bob_task.id}/complete"),
            ("post", f"/api/tasks/{bob_task.id}/reopen"),
        ):
            if method == "get":
                response = await self.client.get(path)
            elif method == "patch":
                response = await self.client.patch(
                    path, json={"title": "Hijacked"}
                )
            elif method == "delete":
                response = await self.client.delete(path)
            else:
                response = await self.client.post(path)

            self.assertEqual(
                response.status_code,
                404,
                f"{method} {path} -> {response.status_code}"
            )

        response = await self.client.get("/api/tasks/")
        self.assertEqual(response.json()["total"], 0)

        self._as_bob()

        response = await self.client.get(f"/api/tasks/{bob_task.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Bob private task")

    async def test_invalid_task_id_returns_404(self):

        for method, path in (
            ("get", "/api/tasks/999999"),
            ("patch", "/api/tasks/999999"),
            ("delete", "/api/tasks/999999"),
            ("post", "/api/tasks/999999/complete"),
            ("post", "/api/tasks/999999/reopen"),
        ):
            if method == "get":
                response = await self.client.get(path)
            elif method == "patch":
                response = await self.client.patch(
                    path, json={"title": "Missing"}
                )
            elif method == "delete":
                response = await self.client.delete(path)
            else:
                response = await self.client.post(path)

            self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def test_validation_rejects_invalid_payloads(self):

        invalid_payloads = (
            {"title": "   "},
            {"title": "Bad priority", "priority": "extreme"},
            {"title": "Bad status", "status": "blocked"},
            {"title": "Bad category", "category": "space"},
            {
                "title": "Bad timezone",
                "due_date": "2026-11-10",
                "timezone": "Mars/Olympus_Mons"
            },
            {"title": "Time without date", "due_time": "10:00"},
            {
                "title": "Timezone without date",
                "timezone": "America/New_York"
            },
            {"title": "Bad date", "due_date": "2026-13-40"},
            {
                "title": "Bad time",
                "due_date": "2026-11-10",
                "due_time": "25:00"
            },
            {"title": "Too long", "description": "x" * 5001},
            {"title": "Server field", "user_id": 99},
            {"title": "Server field", "is_completed": True},
            {"title": "Server field", "completed_at": "2026-11-09T12:00:00Z"},
        )

        for payload in invalid_payloads:
            response = await self.client.post("/api/tasks/", json=payload)
            self.assertEqual(
                response.status_code,
                422,
                f"{payload} -> {response.status_code} {response.text}"
            )

        response = await self.client.post(
            "/api/tasks/",
            json={"title": "x" * 256}
        )
        self.assertEqual(response.status_code, 422)

    async def test_invalid_update_is_rejected(self):

        created = await self._create_task(title="Update validation")

        response = await self.client.patch(
            f"/api/tasks/{created['id']}",
            json={"priority": "extreme"}
        )
        self.assertEqual(response.status_code, 422)

        response = await self.client.patch(
            f"/api/tasks/{created['id']}",
            json={}
        )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Deadline behavior
    # ------------------------------------------------------------------

    async def test_date_only_deadline_not_overdue_on_due_date(self):

        created = await self._create_task(
            title="Date-only today",
            due_date=FIXED_NOW.date()
        )

        response = await self.client.get("/api/tasks/overdue")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

        response = await self.client.get("/api/tasks/today")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [created["id"]]
        )

    async def test_date_only_deadline_overdue_after_due_date(self):

        created = await self._create_task(
            title="Date-only yesterday",
            due_date=FIXED_NOW.date() - timedelta(days=1)
        )

        self._set_now(FIXED_NOW + timedelta(days=1))

        response = await self.client.get("/api/tasks/overdue")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [created["id"]]
        )

    async def test_timezone_aware_timed_deadline(self):

        created = await self._create_task(
            title="New York morning deadline",
            due_date=date(2026, 11, 9),
            due_time=time(9, 0),
            timezone="America/New_York"
        )

        # 09:00 New York is 14:00 UTC; at 12:00 UTC it is not overdue.
        response = await self.client.get("/api/tasks/overdue")
        self.assertEqual(response.json(), [])

        response = await self.client.get("/api/tasks/today")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [created["id"]]
        )

        # At 15:00 UTC the same deadline has passed.
        self._set_now(datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc))

        response = await self.client.get("/api/tasks/overdue")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [created["id"]]
        )

    async def test_timed_deadline_without_timezone_uses_utc(self):

        created = await self._create_task(
            title="Naive timed deadline",
            due_date=date(2026, 11, 9),
            due_time=time(11, 0)
        )

        response = await self.client.get("/api/tasks/overdue")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [created["id"]]
        )

    async def test_upcoming_tomorrow_and_within_days(self):

        tomorrow = await self._create_task(
            title="Due tomorrow",
            due_date=FIXED_NOW.date() + timedelta(days=1)
        )
        three_days = await self._create_task(
            title="Due in three days",
            due_date=FIXED_NOW.date() + timedelta(days=3)
        )
        far = await self._create_task(
            title="Due in ten days",
            due_date=FIXED_NOW.date() + timedelta(days=10)
        )

        response = await self.client.get(
            "/api/tasks/upcoming", params={"days": 7}
        )
        ids = {item["id"] for item in response.json()}
        self.assertIn(tomorrow["id"], ids)
        self.assertIn(three_days["id"], ids)
        self.assertNotIn(far["id"], ids)

        response = await self.client.get(
            "/api/tasks/upcoming", params={"days": 1}
        )
        ids = {item["id"] for item in response.json()}
        self.assertIn(tomorrow["id"], ids)
        self.assertNotIn(three_days["id"], ids)

        response = await self.client.get("/api/tasks/tomorrow")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [tomorrow["id"]]
        )

    async def test_completed_and_cancelled_excluded_from_overdue(self):

        yesterday = FIXED_NOW.date() - timedelta(days=1)

        completed = await self._create_task(
            title="Completed overdue",
            due_date=yesterday
        )
        cancelled = await self._create_task(
            title="Cancelled overdue",
            due_date=yesterday
        )
        open_task = await self._create_task(
            title="Open overdue",
            due_date=yesterday
        )

        await self.client.post(f"/api/tasks/{completed['id']}/complete")
        await self.client.post(
            f"/api/tasks/{cancelled['id']}/status",
            json={"status": "cancelled"}
        )

        response = await self.client.get("/api/tasks/overdue")
        self.assertEqual(
            [item["id"] for item in response.json()],
            [open_task["id"]]
        )


class TaskContextTests(TaskDBTestCase):
    """Exercise task/deadline context selection and isolation."""

    async def _request(
        self,
        session,
        query: str = "",
        private: bool = False
    ) -> ContextRequest:
        return ContextRequest(
            session=session,
            user_id=self.alice_id,
            query=query,
            private=private
        )

    async def test_relevant_task_and_deadline_sections_are_separate(self):

        yesterday = FIXED_NOW.date() - timedelta(days=1)

        overdue = await self._direct_task(
            self.alice_id,
            title="Overdue report",
            due_date=yesterday
        )
        today = await self._direct_task(
            self.alice_id,
            title="Today lab",
            due_date=FIXED_NOW.date()
        )
        high = await self._direct_task(
            self.alice_id,
            title="High priority essay",
            priority=TaskPriority.HIGH
        )
        low = await self._direct_task(
            self.alice_id,
            title="Someday hobby",
            priority=TaskPriority.LOW
        )
        completed = await self._direct_task(
            self.alice_id,
            title="Done report",
            due_date=yesterday,
            status=TaskStatus.COMPLETED
        )

        async with self.SessionLocal() as session:
            request = await self._request(session)
            task_entries = await TaskContextProvider().retrieve(request)
            deadline_entries = await DeadlineContextProvider().retrieve(
                request
            )

        task_text = " ".join(entry.content for entry in task_entries)
        deadline_text = " ".join(
            entry.content for entry in deadline_entries
        )

        self.assertIn("High priority essay", task_text)
        self.assertNotIn("Someday hobby", task_text)
        self.assertNotIn("Overdue report", task_text)
        self.assertNotIn("Today lab", task_text)

        self.assertIn("Overdue report", deadline_text)
        self.assertIn("Today lab", deadline_text)
        self.assertNotIn("Done report", deadline_text)

        task_ids = {entry.metadata["task_id"] for entry in task_entries}
        deadline_ids = {
            entry.metadata["task_id"] for entry in deadline_entries
        }

        self.assertTrue(task_ids.isdisjoint(deadline_ids))
        self.assertNotIn(completed.id, deadline_ids)
        self.assertNotIn(low.id, task_ids)
        self.assertIn(high.id, task_ids)
        self.assertIn(overdue.id, deadline_ids)
        self.assertIn(today.id, deadline_ids)

        self.assertTrue(
            all(entry.source == ContextSource.TASK for entry in task_entries)
        )
        self.assertTrue(
            all(
                entry.source == ContextSource.DEADLINE
                for entry in deadline_entries
            )
        )

    async def test_explicitly_mentioned_task_is_relevant(self):

        task = await self._direct_task(
            self.alice_id,
            title="Finish thesis draft",
            priority=TaskPriority.LOW
        )

        async with self.SessionLocal() as session:
            entries = await TaskContextProvider().retrieve(
                await self._request(session, query="finish thesis")
            )

        self.assertEqual(
            [entry.metadata["task_id"] for entry in entries],
            [task.id]
        )

    async def test_irrelevant_low_priority_task_is_excluded(self):

        await self._direct_task(
            self.alice_id,
            title="Someday hobby",
            priority=TaskPriority.LOW
        )

        async with self.SessionLocal() as session:
            entries = await TaskContextProvider().retrieve(
                await self._request(session, query="database assignment")
            )

        self.assertEqual(entries, [])

    async def test_private_request_excludes_task_and_deadline_context(self):

        await self._direct_task(
            self.alice_id,
            title="Private high priority",
            priority=TaskPriority.HIGH
        )
        await self._direct_task(
            self.alice_id,
            title="Private overdue",
            due_date=FIXED_NOW.date() - timedelta(days=1)
        )

        async with self.SessionLocal() as session:
            request = await self._request(session, private=True)

            self.assertEqual(
                await TaskContextProvider().retrieve(request), []
            )
            self.assertEqual(
                await DeadlineContextProvider().retrieve(request), []
            )

    async def test_provider_failure_is_isolated(self):

        with patch.object(
            task_service,
            "get_open_tasks",
            new=AsyncMock(side_effect=RuntimeError("database unavailable"))
        ):
            async with self.SessionLocal() as session:
                prompt = await UnifiedContextBuilder(
                    providers=[
                        TaskContextProvider(),
                        DeadlineContextProvider(),
                    ]
                ).build(
                    session=session,
                    user_id=self.alice_id,
                    user_message="hello without tasks"
                )

        self.assertEqual(prompt.strip(), "hello without tasks")

    async def test_context_character_budget_is_enforced(self):

        for index in range(8):
            await self._direct_task(
                self.alice_id,
                title=("Long priority task " + str(index) + " ") + ("x" * 180),
                priority=TaskPriority.HIGH
            )

        with patch.object(settings, "CONTEXT_MAX_CHARS", 1400):
            async with self.SessionLocal() as session:
                prompt = await UnifiedContextBuilder().build(
                    session=session,
                    user_id=self.alice_id,
                    user_message="budget message"
                )

        self.assertLessEqual(len(prompt), 1400)
        self.assertIn("budget message", prompt)

    async def test_task_context_coexists_with_preferences_and_memories(self):

        async with self.SessionLocal() as session:
            session.add_all([
                UserPreference(
                    user_id=self.alice_id,
                    key="communication_style",
                    value="Prefer concise explanations.",
                    category="communication",
                    status=PreferenceStatus.ACTIVE,
                    source=PreferenceSource.EXPLICIT,
                ),
                Memory(
                    user_id=self.alice_id,
                    content="Alice studies computer science.",
                    category="education",
                    status=MemoryStatus.APPROVED,
                    embedding=None,
                ),
            ])
            await session.commit()

        await self._direct_task(
            self.alice_id,
            title="Prepare concise database notes",
            priority=TaskPriority.HIGH
        )

        async with self.SessionLocal() as session:
            prompt = await UnifiedContextBuilder().build(
                session=session,
                user_id=self.alice_id,
                user_message="concise database notes",
                memories=[
                    RetrievedMemory(
                        content="Alice studies computer science.",
                        category="education",
                        relevance_score=0.9,
                    )
                ],
            )

        self.assertIn("USER PREFERENCES", prompt)
        self.assertIn("RETRIEVED MEMORIES", prompt)
        self.assertIn("TASK CONTEXT", prompt)

    async def test_task_context_is_rendered_as_an_untrusted_block(self):

        await self._direct_task(
            self.alice_id,
            title="High priority essay",
            priority=TaskPriority.HIGH
        )

        async with self.SessionLocal() as session:
            prompt = await UnifiedContextBuilder().build(
                session=session,
                user_id=self.alice_id,
                user_message="What should I do?"
            )

        self.assertIn("TASK CONTEXT", prompt)
        self.assertIn("<task_context>", prompt)
        self.assertIn("High priority essay", prompt)


if __name__ == "__main__":
    unittest.main()
