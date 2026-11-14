"""Phase 13E tests: the authenticated background task API.

The suite drives the real FastAPI routes and the real in-process service against
an isolated, file-backed database. Handlers are controlled doubles, so no
embedding model or extractor is invoked.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_current_user_id
from app.database.connection import get_db
from app.main import app
from app.services.background_task_service import background_task_service
from app.services.memory_embedding_service import memory_embedding_service
from tests.streaming_test_support import BackgroundTaskTestBase


class BackgroundTaskApiTestBase(BackgroundTaskTestBase):
    """Authenticated API client over an isolated background task database."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self._current_user = self.alice_id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_owner():
            return self._current_user

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_owner

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await super().asyncTearDown()

    def _as_user(self, user_id: int) -> None:
        self._current_user = user_id

    def _hold_tasks(self) -> None:
        """Prevent scheduled work from running so status stays deterministic."""

        patcher = patch.object(
            background_task_service,
            "_schedule",
            new=MagicMock(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _create(
        self,
        *,
        task_type: str = "memory_embedding",
        parameters: dict | None = None,
    ):
        return await self.client.post(
            "/api/background-tasks/",
            json={
                "task_type": task_type,
                "parameters": parameters or {"memory_id": 1},
            },
        )


class BackgroundTaskCreateTests(BackgroundTaskApiTestBase):

    async def test_create_returns_a_queued_task_without_internal_fields(self):
        self._hold_tasks()

        response = await self._create()

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()

        self.assertEqual(body["task_type"], "memory_embedding")
        self.assertEqual(body["status"], "queued")
        self.assertIsNone(body["result"])
        self.assertIsNone(body["error_code"])
        self.assertIsNone(body["error_message"])
        self.assertNotIn("payload", body)
        self.assertNotIn("parameters", body)
        self.assertNotIn("user_id", body)
        self.assertEqual(
            set(body.keys()),
            {
                "id",
                "task_type",
                "status",
                "result",
                "error_code",
                "error_message",
                "created_at",
                "started_at",
                "completed_at",
            },
        )

    async def test_unsupported_task_type_is_rejected(self):
        response = await self._create(
            task_type="shell",
            parameters={"command": "whoami"},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("whoami", response.text)

    async def test_invalid_parameters_are_rejected(self):
        cases = (
            {"memory_id": 0},
            {"memory_id": 1, "command": "whoami"},
            {"memory_id": 1, "force": "yes"},
        )

        for parameters in cases:
            with self.subTest(parameters=parameters):
                response = await self._create(parameters=parameters)

                self.assertEqual(response.status_code, 400, response.text)
                self.assertNotIn("Traceback", response.text)

    async def test_oversized_parameters_are_rejected(self):
        response = await self._create(
            parameters={"memory_id": 1, "force": True, "pad": "x" * 5000},
        )

        self.assertEqual(response.status_code, 422, response.text)

    async def test_disabled_processing_returns_503(self):
        self.settings(BACKGROUND_TASKS_ENABLED=False)

        response = await self._create()

        self.assertEqual(response.status_code, 503, response.text)

    async def test_per_user_pending_limit_returns_429(self):
        self._hold_tasks()
        self.settings(BACKGROUND_TASK_MAX_QUEUED_PER_USER=1)

        first = await self._create(parameters={"memory_id": 1})
        second = await self._create(parameters={"memory_id": 2})

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 429, second.text)

    async def test_authentication_is_required(self):
        app.dependency_overrides.pop(get_current_user_id, None)

        try:
            response = await self._create()
        finally:
            async def override_owner():
                return self.alice_id

            app.dependency_overrides[get_current_user_id] = override_owner

        self.assertEqual(response.status_code, 401, response.text)


class BackgroundTaskListTests(BackgroundTaskApiTestBase):

    async def test_list_returns_only_the_authenticated_owner(self):
        self._hold_tasks()

        await self._create(parameters={"memory_id": 1})
        await self._create(parameters={"memory_id": 2})

        self._as_user(self.bob_id)
        response = await self.client.get("/api/background-tasks/")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"tasks": [], "total": 0})

        self._as_user(self.alice_id)
        response = await self.client.get("/api/background-tasks/")

        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["tasks"]), 2)
        self.assertTrue(
            all(task["task_type"] == "memory_embedding" for task in body["tasks"])
        )

    async def test_list_filters_by_status(self):
        self._hold_tasks()

        first = (await self._create(parameters={"memory_id": 1})).json()
        second = (await self._create(parameters={"memory_id": 2})).json()

        cancelled = await self.client.post(
            f"/api/background-tasks/{first['id']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)

        queued = await self.client.get(
            "/api/background-tasks/",
            params={"status": "queued"},
        )
        completed = await self.client.get(
            "/api/background-tasks/",
            params={"status": "cancelled"},
        )

        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(queued.json()["total"], 1)
        self.assertEqual(queued.json()["tasks"][0]["id"], second["id"])
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["total"], 1)
        self.assertEqual(completed.json()["tasks"][0]["id"], first["id"])

    async def test_unknown_status_filter_is_rejected(self):
        response = await self.client.get(
            "/api/background-tasks/",
            params={"status": "exploded"},
        )

        self.assertEqual(response.status_code, 400, response.text)

    async def test_list_pagination_is_bounded(self):
        self._hold_tasks()

        for index in range(3):
            await self._create(parameters={"memory_id": index + 1})

        response = await self.client.get(
            "/api/background-tasks/",
            params={"limit": 2, "offset": 1},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 3)
        self.assertEqual(len(response.json()["tasks"]), 2)

    async def test_authentication_is_required(self):
        app.dependency_overrides.pop(get_current_user_id, None)

        try:
            response = await self.client.get("/api/background-tasks/")
        finally:
            async def override_owner():
                return self.alice_id

            app.dependency_overrides[get_current_user_id] = override_owner

        self.assertEqual(response.status_code, 401, response.text)


class BackgroundTaskDetailTests(BackgroundTaskApiTestBase):

    async def test_detail_returns_an_owned_task(self):
        self._hold_tasks()
        created = (await self._create()).json()

        response = await self.client.get(
            f"/api/background-tasks/{created['id']}"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], created["id"])
        self.assertNotIn("payload", response.json())

    async def test_cross_owner_detail_is_a_404(self):
        self._hold_tasks()
        created = (await self._create()).json()

        self._as_user(self.bob_id)
        response = await self.client.get(
            f"/api/background-tasks/{created['id']}"
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Background task not found.")

    async def test_missing_task_is_a_404(self):
        response = await self.client.get("/api/background-tasks/999999")

        self.assertEqual(response.status_code, 404, response.text)

    async def test_completed_task_exposes_a_safe_result(self):
        memory = await self.create_memory()

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(return_value=True),
        ):
            created = await self._create(
                parameters={"memory_id": memory.id, "force": True}
            )

            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["id"]

            body = await self._wait_for_status(task_id, "completed")

        self.assertEqual(
            body["result"],
            {"memory_id": memory.id, "embedded": True},
        )
        self.assertIsNotNone(body["started_at"])
        self.assertIsNotNone(body["completed_at"])

    async def test_failed_task_exposes_only_a_generic_error(self):
        memory = await self.create_memory()

        async def explode(*args, **kwargs):
            raise RuntimeError("secret internal detail")

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=explode),
        ):
            created = await self._create(parameters={"memory_id": memory.id})

            task_id = created.json()["id"]
            body = await self._wait_for_status(task_id, "failed")

        self.assertEqual(body["error_code"], "task_failed")
        self.assertEqual(body["error_message"], "The background task failed.")
        self.assertNotIn("secret internal detail", body["error_message"])
        self.assertIsNone(body["result"])

    async def _wait_for_status(self, task_id: int, expected: str):
        deadline = asyncio.get_running_loop().time() + 5.0
        last = None

        while asyncio.get_running_loop().time() < deadline:
            response = await self.client.get(
                f"/api/background-tasks/{task_id}"
            )
            self.assertEqual(response.status_code, 200, response.text)
            last = response.json()

            if last["status"] == expected:
                return last

            await asyncio.sleep(0.02)

        raise AssertionError(
            f"task {task_id} did not reach {expected}; last={last}"
        )


class BackgroundTaskCancelTests(BackgroundTaskApiTestBase):

    async def test_cancel_queued_task_is_idempotent(self):
        self._hold_tasks()
        created = (await self._create()).json()

        first = await self.client.post(
            f"/api/background-tasks/{created['id']}/cancel"
        )
        second = await self.client.post(
            f"/api/background-tasks/{created['id']}/cancel"
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "cancelled")
        self.assertEqual(first.json()["error_code"], "cancelled")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["status"], "cancelled")

    async def test_cross_owner_cancel_is_a_404_and_does_not_mutate(self):
        self._hold_tasks()
        created = (await self._create()).json()

        self._as_user(self.bob_id)
        response = await self.client.post(
            f"/api/background-tasks/{created['id']}/cancel"
        )

        self.assertEqual(response.status_code, 404, response.text)

        self._as_user(self.alice_id)
        detail = await self.client.get(
            f"/api/background-tasks/{created['id']}"
        )
        self.assertEqual(detail.json()["status"], "queued")

    async def test_missing_cancel_is_a_404(self):
        response = await self.client.post(
            "/api/background-tasks/999999/cancel"
        )

        self.assertEqual(response.status_code, 404, response.text)

    async def test_authentication_is_required(self):
        app.dependency_overrides.pop(get_current_user_id, None)

        try:
            response = await self.client.post(
                "/api/background-tasks/1/cancel"
            )
        finally:
            async def override_owner():
                return self.alice_id

            app.dependency_overrides[get_current_user_id] = override_owner

        self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()
