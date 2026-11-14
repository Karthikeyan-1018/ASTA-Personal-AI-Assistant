"""Phase 13D tests: the in-process background task runner.

The runner uses only asyncio and the existing database. Handlers are controlled
doubles; no real embedding model, extractor, or shell command is invoked.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from app.exceptions.background_task_exceptions import (
    BackgroundTaskLimitError,
    BackgroundTaskNotFoundError,
    BackgroundTaskValidationError,
)
from app.models.background_task import BackgroundTask, BackgroundTaskStatus
from app.services.background_task_service import background_task_service
from app.services.memory_embedding_service import memory_embedding_service
from tests.streaming_test_support import BackgroundTaskTestBase


class BackgroundTaskServiceTests(BackgroundTaskTestBase):

    async def _get(self, task_id: int):
        async with self.SessionLocal() as session:
            return await session.get(BackgroundTask, task_id)

    async def _wait_status(self, task_id, statuses, timeout=5.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last = None

        while loop.time() < deadline:
            last = await self._get(task_id)

            if last is not None and last.status in statuses:
                return last

            await asyncio.sleep(0.02)

        raise AssertionError(
            f"task {task_id} did not reach {statuses}; last={last.status if last else None}"
        )

    async def _create_queued(self, task_type="memory_embedding", payload=None):
        with patch.object(
            background_task_service,
            "_schedule",
            new=MagicMock(),
        ):
            async with self.SessionLocal() as session:
                return await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type=task_type,
                    payload=payload or {"memory_id": 1},
                )

    async def test_create_persists_a_queued_task(self):
        task = await self._create_queued()

        self.assertEqual(task.status, BackgroundTaskStatus.QUEUED.value)
        self.assertEqual(
            task.payload,
            {"memory_id": 1, "force": False},
        )
        self.assertEqual(task.user_id, self.alice_id)

        stored = await self._get(task.id)
        self.assertEqual(stored.status, "queued")

    async def test_unsupported_task_types_are_rejected(self):
        with self.assertRaises(BackgroundTaskValidationError):
            async with self.SessionLocal() as session:
                await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="shell",
                    payload={"command": "whoami"},
                )

    async def test_invalid_payloads_are_rejected(self):
        cases = (
            ("memory_embedding", {"command": "whoami"}),
            ("memory_embedding", {"memory_id": 0}),
            ("memory_embedding", {"memory_id": 1, "force": "yes"}),
            ("memory_extraction", {"conversation_id": -1}),
            ("memory_extraction", {"conversation_id": 1, "private": "no"}),
            ("memory_extraction", {"conversation_id": 1, "extra": True}),
        )

        for task_type, payload in cases:
            with self.subTest(task_type=task_type, payload=payload):
                with self.assertRaises(BackgroundTaskValidationError):
                    async with self.SessionLocal() as session:
                        await background_task_service.create_task(
                            session=session,
                            user_id=self.alice_id,
                            task_type=task_type,
                            payload=payload,
                        )

    async def test_disabled_processing_rejects_new_tasks(self):
        self.settings(BACKGROUND_TASKS_ENABLED=False)

        with self.assertRaises(BackgroundTaskValidationError):
            async with self.SessionLocal() as session:
                await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": 1},
                )

    async def test_per_user_pending_limit_is_enforced(self):
        self.settings(BACKGROUND_TASK_MAX_QUEUED_PER_USER=1)
        await self._create_queued()

        with self.assertRaises(BackgroundTaskLimitError):
            async with self.SessionLocal() as session:
                await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": 2},
                )

        # Another owner has an independent allowance.
        async with self.SessionLocal() as session:
            bob_task = await background_task_service.create_task(
                session=session,
                user_id=self.bob_id,
                task_type="memory_embedding",
                payload={"memory_id": 3},
            )

        self.assertEqual(bob_task.user_id, self.bob_id)

    async def test_owner_isolation_for_get_list_and_cancel(self):
        task = await self._create_queued()

        async with self.SessionLocal() as session:
            with self.assertRaises(BackgroundTaskNotFoundError):
                await background_task_service.get_task(
                    session=session,
                    user_id=self.bob_id,
                    task_id=task.id,
                )

            rows, total = await background_task_service.list_tasks(
                session=session,
                user_id=self.bob_id,
            )
            self.assertEqual((rows, total), ([], 0))

            with self.assertRaises(BackgroundTaskNotFoundError):
                await background_task_service.cancel_task(
                    session=session,
                    user_id=self.bob_id,
                    task_id=task.id,
                )

        stored = await self._get(task.id)
        self.assertEqual(stored.status, "queued")

    async def test_memory_embedding_success_marks_the_task_completed(self):
        memory = await self.create_memory()

        ensure = AsyncMock(return_value=True)

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=ensure,
        ):
            async with self.SessionLocal() as session:
                task = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": memory.id, "force": True},
                )

            await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.COMPLETED.value},
        )

        self.assertEqual(
            stored.result,
            {"memory_id": memory.id, "embedded": True},
        )
        self.assertIsNotNone(stored.started_at)
        self.assertIsNotNone(stored.completed_at)

        ensure.assert_awaited_once()

    async def test_handler_failure_is_generic_and_secret_free(self):
        memory = await self.create_memory()

        async def explode(*args, **kwargs):
            raise RuntimeError("secret internal detail")

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=explode),
        ):
            async with self.SessionLocal() as session:
                task = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": memory.id},
                )

            await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.FAILED.value},
        )

        self.assertEqual(stored.error_code, "task_failed")
        self.assertEqual(stored.error_message, "The background task failed.")
        self.assertNotIn("secret internal detail", stored.error_message)

    async def test_missing_memory_is_reported_as_not_found(self):
        async with self.SessionLocal() as session:
            task = await background_task_service.create_task(
                session=session,
                user_id=self.alice_id,
                task_type="memory_embedding",
                payload={"memory_id": 999999},
            )

        await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.FAILED.value},
        )

        self.assertEqual(stored.error_code, "not_found")
        self.assertIn("not found", stored.error_message.lower())

    async def test_unapproved_memory_is_rejected(self):
        from app.models.memory import MemoryStatus

        memory = await self.create_memory(status=MemoryStatus.PENDING)

        async with self.SessionLocal() as session:
            task = await background_task_service.create_task(
                session=session,
                user_id=self.alice_id,
                task_type="memory_embedding",
                payload={"memory_id": memory.id},
            )

        await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.FAILED.value},
        )

        self.assertEqual(stored.error_code, "validation_error")

    async def test_execution_timeout_marks_the_task_failed(self):
        memory = await self.create_memory()
        self.settings(BACKGROUND_TASK_MAX_EXECUTION_SECONDS=0.05)

        async def slow(*args, **kwargs):
            await asyncio.sleep(5)
            return True

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=slow),
        ):
            async with self.SessionLocal() as session:
                task = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": memory.id},
                )

            await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.FAILED.value},
        )

        self.assertEqual(stored.error_code, "timeout")

    async def test_memory_extraction_success_uses_the_extractor(self):
        conversation = await self.create_conversation(
            messages=("I like tea", "I live in Pune"),
        )
        extract = AsyncMock(return_value=[object(), object()])

        with patch(
            "app.services.background_task_service.memory_extractor.extract",
            new=extract,
        ):
            async with self.SessionLocal() as session:
                task = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_extraction",
                    payload={
                        "conversation_id": conversation.id,
                        "private": True,
                    },
                )

            await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.COMPLETED.value},
        )

        self.assertEqual(
            stored.result,
            {"conversation_id": conversation.id, "candidates": 2},
        )
        self.assertTrue(extract.await_args.kwargs["private"])

    async def test_memory_extraction_rejects_a_foreign_conversation(self):
        conversation = await self.create_conversation(
            user_id=self.bob_id,
            messages=("Bob's private note",),
        )

        async with self.SessionLocal() as session:
            task = await background_task_service.create_task(
                session=session,
                user_id=self.alice_id,
                task_type="memory_extraction",
                payload={"conversation_id": conversation.id},
            )

        await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.FAILED.value},
        )

        self.assertEqual(stored.error_code, "not_found")

    async def test_cancel_queued_task_is_idempotent(self):
        task = await self._create_queued()

        async with self.SessionLocal() as session:
            cancelled = await background_task_service.cancel_task(
                session=session,
                user_id=self.alice_id,
                task_id=task.id,
            )

        self.assertEqual(cancelled.status, "cancelled")
        self.assertTrue(cancelled.cancel_requested)

        async with self.SessionLocal() as session:
            again = await background_task_service.cancel_task(
                session=session,
                user_id=self.alice_id,
                task_id=task.id,
            )

        self.assertEqual(again.status, "cancelled")

    async def test_cancel_running_task_stops_the_handler(self):
        memory = await self.create_memory()
        started = asyncio.Event()

        async def blocking(*args, **kwargs):
            started.set()
            await asyncio.sleep(30)
            return True

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=blocking),
        ):
            async with self.SessionLocal() as session:
                task = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": memory.id},
                )

            await asyncio.wait_for(started.wait(), timeout=5.0)
            await self._wait_status(
                task.id,
                {BackgroundTaskStatus.RUNNING.value},
            )

            async with self.SessionLocal() as session:
                cancelled = await background_task_service.cancel_task(
                    session=session,
                    user_id=self.alice_id,
                    task_id=task.id,
                )

            await background_task_service.wait_for_tasks(timeout=5.0)

        self.assertEqual(cancelled.status, "cancelled")

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.CANCELLED.value},
        )
        self.assertTrue(stored.cancel_requested)

    async def test_concurrency_is_bounded(self):
        self.settings(BACKGROUND_TASK_MAX_CONCURRENT=1)

        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def measured(*args, **kwargs):
            nonlocal active, peak

            async with lock:
                active += 1
                peak = max(peak, active)

            try:
                await asyncio.sleep(0.05)
                return True
            finally:
                async with lock:
                    active -= 1

        memories = [await self.create_memory(content=f"fact {i}") for i in range(3)]

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=measured),
        ):
            async with self.SessionLocal() as session:
                tasks = [
                    await background_task_service.create_task(
                        session=session,
                        user_id=self.alice_id,
                        task_type="memory_embedding",
                        payload={"memory_id": memory.id},
                    )
                    for memory in memories
                ]

            await background_task_service.wait_for_tasks(timeout=10.0)

        for task in tasks:
            await self._wait_status(
                task.id,
                {BackgroundTaskStatus.COMPLETED.value},
            )

        self.assertEqual(peak, 1)

    async def test_startup_recovery_resolves_interrupted_rows(self):
        now = datetime.now(timezone.utc)

        async with self.SessionLocal() as session:
            running = BackgroundTask(
                user_id=self.alice_id,
                task_type="memory_embedding",
                status="running",
                payload={"memory_id": 1},
                started_at=now,
            )
            queued = BackgroundTask(
                user_id=self.alice_id,
                task_type="memory_embedding",
                status="queued",
                payload={"memory_id": 2},
            )
            completed = BackgroundTask(
                user_id=self.alice_id,
                task_type="memory_embedding",
                status="completed",
                payload={"memory_id": 3},
                completed_at=now,
            )
            session.add_all([running, queued, completed])
            await session.commit()
            ids = (running.id, queued.id, completed.id)

            recovered = await background_task_service.recover_interrupted(
                session
            )

        self.assertEqual(recovered, {"failed": 1, "cancelled": 1})

        running_row = await self._get(ids[0])
        queued_row = await self._get(ids[1])
        completed_row = await self._get(ids[2])

        self.assertEqual(running_row.status, "failed")
        self.assertEqual(running_row.error_code, "interrupted")
        self.assertEqual(queued_row.status, "cancelled")
        self.assertEqual(queued_row.error_code, "restart")
        self.assertEqual(completed_row.status, "completed")

    async def test_purge_removes_only_expired_terminal_rows(self):
        old = datetime.now(timezone.utc) - timedelta(days=40)

        async with self.SessionLocal() as session:
            expired = BackgroundTask(
                user_id=self.alice_id,
                task_type="memory_embedding",
                status="completed",
                payload={"memory_id": 1},
                completed_at=old,
                created_at=old,
                updated_at=old,
            )
            recent = BackgroundTask(
                user_id=self.alice_id,
                task_type="memory_embedding",
                status="completed",
                payload={"memory_id": 2},
                completed_at=datetime.now(timezone.utc),
            )
            session.add_all([expired, recent])
            await session.commit()
            expired_id, recent_id = expired.id, recent.id

            purged = await background_task_service.purge_expired(session)

        self.assertEqual(purged, 1)
        self.assertIsNone(await self._get(expired_id))
        self.assertIsNotNone(await self._get(recent_id))

    async def test_shutdown_cancels_incomplete_tasks(self):
        memory = await self.create_memory()
        started = asyncio.Event()

        async def blocking(*args, **kwargs):
            started.set()
            await asyncio.sleep(30)
            return True

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=blocking),
        ):
            async with self.SessionLocal() as session:
                task = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": memory.id},
                )

            await asyncio.wait_for(started.wait(), timeout=5.0)
            await background_task_service.shutdown()

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.CANCELLED.value},
        )

        self.assertIn(
            stored.error_code,
            {"cancelled", "shutdown"},
        )
        self.assertEqual(background_task_service._tasks, {})

    async def test_list_filters_by_status_and_orders_newest_first(self):
        first = await self._create_queued(payload={"memory_id": 1})
        second = await self._create_queued(payload={"memory_id": 2})

        async with self.SessionLocal() as session:
            await background_task_service.cancel_task(
                session=session,
                user_id=self.alice_id,
                task_id=first.id,
            )

            queued, queued_total = await background_task_service.list_tasks(
                session=session,
                user_id=self.alice_id,
                status="queued",
            )
            cancelled, cancelled_total = await background_task_service.list_tasks(
                session=session,
                user_id=self.alice_id,
                status="cancelled",
            )

        self.assertEqual([row.id for row in queued], [second.id])
        self.assertEqual(queued_total, 1)
        self.assertEqual([row.id for row in cancelled], [first.id])
        self.assertEqual(cancelled_total, 1)

        async with self.SessionLocal() as session:
            with self.assertRaises(BackgroundTaskValidationError):
                await background_task_service.list_tasks(
                    session=session,
                    user_id=self.alice_id,
                    status="unknown",
                )


if __name__ == "__main__":
    unittest.main()
