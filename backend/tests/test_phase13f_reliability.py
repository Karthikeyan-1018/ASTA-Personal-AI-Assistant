"""Phase 13F tests: streaming and background task reliability.

The suite focuses on failure behaviour rather than the happy path: bounded
concurrency, client disconnects, provider failures, resource cleanup, secret-free
logging, cancellation, recovery, and graceful shutdown. Providers, handlers, and
models are controlled doubles; nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.exceptions.llm_exceptions import LLMProviderError, LLMTimeoutError
from app.llm.llm_router import llm_router
from app.models.background_task import BackgroundTask, BackgroundTaskStatus
from app.services.background_task_service import background_task_service
from app.services.memory_embedding_service import memory_embedding_service
from tests.streaming_test_support import (
    BackgroundTaskTestBase,
    FakeAsyncStream,
    StreamingApiTestBase,
    call_app_with_disconnect,
    combined_text,
    event_names,
    parse_sse,
)


class StreamingReliabilityTests(StreamingApiTestBase):
    """Failure, concurrency, and cleanup behaviour of the SSE endpoint."""

    def _patch_stream_object(self, stream):
        def factory(**kwargs):
            return stream

        patcher = patch.object(llm_router, "stream_response", new=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return stream

    async def test_concurrency_limit_returns_429_and_then_recovers(self):
        self.settings(
            STREAM_MAX_CONCURRENT=1,
            STREAM_QUEUE_TIMEOUT_SECONDS=0.05,
        )
        self._patch_router_stream(["slow"], delay=0.5)

        first = asyncio.create_task(self._stream({"message": "first"}))
        await asyncio.sleep(0.15)

        second = await self._stream({"message": "second"})

        self.assertEqual(second.status_code, 429, second.text)
        self.assertEqual(second.headers.get("retry-after"), "5")

        first_response = await first
        self.assertEqual(first_response.status_code, 200, first_response.text)

        # The slot must be released after the first stream finishes.
        self._patch_router_stream(["fast"])
        third = await self._stream({"message": "third"})

        self.assertEqual(third.status_code, 200, third.text)
        self.assertEqual(combined_text(parse_sse(third.text)), "fast")

    async def test_client_disconnect_releases_the_stream_slot(self):
        self.settings(
            STREAM_MAX_CONCURRENT=1,
            STREAM_QUEUE_TIMEOUT_SECONDS=0.1,
        )
        stream = self._patch_stream_object(
            FakeAsyncStream(["a"] * 100, delay=0.02)
        )

        await call_app_with_disconnect(
            path="/api/chat/stream",
            payload={"message": "cancel me"},
            disconnect_after_chunks=1,
            timeout=10.0,
        )

        await asyncio.sleep(0.05)

        self.assertTrue(stream.closed)

        self._patch_router_stream(["after disconnect"])
        response = await self._stream({"message": "after"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            combined_text(parse_sse(response.text)),
            "after disconnect",
        )

    async def test_provider_stream_is_closed_after_success(self):
        stream = self._patch_stream_object(FakeAsyncStream(["done"]))

        response = await self._stream({"message": "Hi"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(stream.closed)
        self.assertEqual(combined_text(parse_sse(response.text)), "done")

    async def test_provider_stream_is_closed_after_failure(self):
        stream = self._patch_stream_object(
            FakeAsyncStream(
                ["partial"],
                error=LLMProviderError("secret-provider-detail"),
            )
        )

        response = await self._stream({"message": "Hi"})
        events = parse_sse(response.text)

        self.assertTrue(stream.closed)
        self.assertEqual(event_names(events), ["start", "delta", "error"])
        self.assertEqual(events[-1][1]["code"], "provider_error")
        self.assertNotIn("secret-provider-detail", response.text)

        rows = await self._message_rows(events[0][1]["conversation_id"])
        self.assertEqual([row.role.value for row in rows], ["user"])

    async def test_timeout_after_partial_output_is_safe_and_not_persisted(self):
        stream = self._patch_stream_object(
            FakeAsyncStream(
                ["partial"],
                error=LLMTimeoutError("secret-internal-timeout"),
            )
        )

        response = await self._stream({"message": "Hi"})
        events = parse_sse(response.text)

        self.assertTrue(stream.closed)
        self.assertEqual(events[-1][1]["code"], "timeout")
        self.assertNotIn("secret-internal-timeout", response.text)

        rows = await self._message_rows(events[0][1]["conversation_id"])
        self.assertEqual([row.role.value for row in rows], ["user"])

    async def test_unexpected_error_is_masked_and_never_logged(self):
        stream = self._patch_stream_object(
            FakeAsyncStream([], error=RuntimeError("password=hunter2-secret"))
        )

        with self.assertLogs("app.services.chat_service", level="ERROR") as logs:
            response = await self._stream({"message": "Hi"})

        joined = "\n".join(logs.output)
        events = parse_sse(response.text)

        self.assertTrue(stream.closed)
        self.assertEqual(events[-1][1]["code"], "internal_error")
        self.assertNotIn("hunter2-secret", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn("hunter2-secret", joined)
        self.assertIn("RuntimeError", joined)

        rows = await self._message_rows(events[0][1]["conversation_id"])
        self.assertEqual([row.role.value for row in rows], ["user"])

    async def test_disabled_streaming_does_not_call_the_provider(self):
        self.settings(STREAMING_ENABLED=False)
        provider = AsyncMock()
        patcher = patch.object(
            llm_router,
            "stream_response",
            new=provider,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        response = await self._stream({"message": "Hi"})

        self.assertEqual(response.status_code, 503, response.text)
        provider.assert_not_called()

    async def test_two_complete_streams_persist_exactly_two_assistants(self):
        self._patch_router_stream(["one"])
        first = await self._stream({"message": "first"})
        conversation_id = parse_sse(first.text)[0][1]["conversation_id"]

        self._patch_router_stream(["two"])
        await self._stream(
            {"message": "second", "conversation_id": conversation_id}
        )

        rows = await self._message_rows(conversation_id)
        assistants = [row for row in rows if row.role.value == "assistant"]

        self.assertEqual(len(assistants), 2)
        self.assertEqual([row.content for row in assistants], ["one", "two"])


class BackgroundTaskReliabilityTests(BackgroundTaskTestBase):
    """Failure, logging, cancellation, recovery, and shutdown behaviour."""

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
            f"task {task_id} did not reach {statuses}; "
            f"last={last.status if last else None}"
        )

    async def test_failed_task_logs_are_secret_free(self):
        memory = await self.create_memory()

        async def explode(*args, **kwargs):
            raise RuntimeError("SECRET-TOKEN-123")

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=explode),
        ):
            with self.assertLogs(
                "app.services.background_task_service",
                level="ERROR",
            ) as logs:
                async with self.SessionLocal() as session:
                    task = await background_task_service.create_task(
                        session=session,
                        user_id=self.alice_id,
                        task_type="memory_embedding",
                        payload={"memory_id": memory.id},
                    )

                await background_task_service.wait_for_tasks(timeout=5.0)

        joined = "\n".join(logs.output)
        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.FAILED.value},
        )

        self.assertNotIn("SECRET-TOKEN-123", joined)
        self.assertNotIn("SECRET-TOKEN-123", stored.error_message)
        self.assertIn("RuntimeError", joined)
        self.assertEqual(stored.error_message, "The background task failed.")

    async def test_slot_is_released_after_a_handler_failure(self):
        self.settings(BACKGROUND_TASK_MAX_CONCURRENT=1)

        first_memory = await self.create_memory(content="first")
        second_memory = await self.create_memory(content="second")
        calls = {"count": 0}

        async def handler(*args, **kwargs):
            calls["count"] += 1

            if calls["count"] == 1:
                raise RuntimeError("first attempt fails")

            return True

        with patch.object(
            memory_embedding_service,
            "ensure_embedding",
            new=AsyncMock(side_effect=handler),
        ):
            async with self.SessionLocal() as session:
                first = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": first_memory.id},
                )

            await self._wait_status(
                first.id,
                {BackgroundTaskStatus.FAILED.value},
            )

            async with self.SessionLocal() as session:
                second = await background_task_service.create_task(
                    session=session,
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    payload={"memory_id": second_memory.id},
                )

            await background_task_service.wait_for_tasks(timeout=5.0)

        stored = await self._wait_status(
            second.id,
            {BackgroundTaskStatus.COMPLETED.value},
        )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(stored.result["memory_id"], second_memory.id)

    async def test_cancelled_running_task_keeps_no_result(self):
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

        stored = await self._wait_status(
            task.id,
            {BackgroundTaskStatus.CANCELLED.value},
        )

        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(stored.error_code, "cancelled")
        self.assertIsNone(stored.result)

    async def test_shutdown_cancels_rows_and_clears_tracking(self):
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

        self.assertIn(stored.error_code, {"cancelled", "shutdown"})
        self.assertEqual(background_task_service._tasks, {})

    async def test_startup_recovery_is_idempotent(self):
        now = datetime.now(timezone.utc)

        async with self.SessionLocal() as session:
            session.add_all(
                [
                    BackgroundTask(
                        user_id=self.alice_id,
                        task_type="memory_embedding",
                        status="running",
                        payload={"memory_id": 1},
                        started_at=now,
                    ),
                    BackgroundTask(
                        user_id=self.alice_id,
                        task_type="memory_embedding",
                        status="queued",
                        payload={"memory_id": 2},
                    ),
                ]
            )
            await session.commit()

            first = await background_task_service.recover_interrupted(session)
            second = await background_task_service.recover_interrupted(session)

        self.assertEqual(first, {"failed": 1, "cancelled": 1})
        self.assertEqual(second, {"failed": 0, "cancelled": 0})

    async def test_retention_purge_is_idempotent(self):
        now = datetime.now(timezone.utc)

        async with self.SessionLocal() as session:
            session.add(
                BackgroundTask(
                    user_id=self.alice_id,
                    task_type="memory_embedding",
                    status="completed",
                    payload={"memory_id": 1},
                    result={"memory_id": 1, "embedded": True},
                    completed_at=now,
                )
            )
            await session.commit()

            first = await background_task_service.purge_expired(
                session,
                now=now,
            )
            second = await background_task_service.purge_expired(
                session,
                now=now,
            )

        self.assertEqual(first, 0)
        self.assertEqual(second, 0)

        async with self.SessionLocal() as session:
            rows = (
                await session.execute(select(BackgroundTask))
            ).scalars().all()

        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
