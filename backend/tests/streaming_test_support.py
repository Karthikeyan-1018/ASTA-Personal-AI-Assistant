"""Shared fixtures for the Phase 13 streaming and background task tests.

The helpers here keep every Phase 13 test deterministic and offline. Providers,
the LLM router, the context builder, and the memory services are replaced with
controlled doubles; no network call, real model, or embedding model is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai.query_classifier import ClassificationResult, QueryComplexity
from app.api.dependencies import get_current_user_id
from app.context.builder import unified_context_builder
from app.core.config import settings
from app.database.connection import get_db
from app.llm.llm_router import llm_router
from app.main import app
from app.services.background_task_service import background_task_service
from app.services.chat_service import chat_service
from app.task_intent.chat_handler import task_intent_chat_handler
from tests.tool_test_support import ToolEngineTestBase


def _make_file_engine(test_case):
    """Create a temporary file-backed engine with independent connections.

    Streaming requests and background tasks both run work on more than one
    session at a time. A shared in-memory ``StaticPool`` database would put
    those sessions on one connection and let their transaction boundaries
    interfere, so Phase 13 tests use a real, temporary database file.
    """

    handle, path = tempfile.mkstemp(
        prefix="asta_phase13_",
        suffix=".db",
    )
    os.close(handle)
    test_case._database_path = path

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def _remove_database_files(test_case):
    """Best-effort removal of the temporary database and its WAL files."""

    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(f"{test_case._database_path}{suffix}")

        except OSError:
            # Windows can briefly retain a handle after disposal. The file
            # lives in the temporary directory and is reaped by the operating
            # system, so cleanup is best effort.
            pass


# ----------------------------------------------------------------------
# Provider stream doubles
# ----------------------------------------------------------------------


class FakeAsyncStream:
    """A small async iterator that records whether it was closed."""

    def __init__(self, items, *, delay: float = 0.0, error=None):
        self._items = list(items)
        self._delay = delay
        self._error = error
        self.closed = False
        self.iterated = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._delay:
            await asyncio.sleep(self._delay)

        if self._items:
            self.iterated += 1
            return self._items.pop(0)

        if self._error is not None:
            error, self._error = self._error, None
            raise error

        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


class FakeSyncCloseStream:
    """An async iterator exposing a synchronous ``close`` like Groq's SDK.

    It deliberately has no ``aclose`` so tests exercise the fallback path in
    ``close_async_stream``.
    """

    def __init__(self, items, *, delay: float = 0.0, error=None):
        self._items = list(items)
        self._delay = delay
        self._error = error
        self.closed = False
        self.close_called = False
        self.iterated = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._delay:
            await asyncio.sleep(self._delay)

        if self._items:
            self.iterated += 1
            return self._items.pop(0)

        if self._error is not None:
            error, self._error = self._error, None
            raise error

        raise StopAsyncIteration

    def close(self):
        self.close_called = True
        self.closed = True


class BlockingStream:
    """An async iterator that blocks until cancelled."""

    def __init__(self):
        self.closed = False
        self.started = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        await asyncio.sleep(60)
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


def groq_chunk(text: str | None):
    delta = SimpleNamespace(content=text)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta)],
        usage=None,
    )


def groq_usage_chunk(prompt_tokens=10, completion_tokens=5, total_tokens=15):
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    return SimpleNamespace(choices=[], usage=usage)


def ollama_chunk(
    text: str | None,
    *,
    done: bool = False,
    prompt_eval_count=None,
    eval_count=None,
):
    return SimpleNamespace(
        message=SimpleNamespace(content=text),
        done=done,
        prompt_eval_count=prompt_eval_count,
        eval_count=eval_count,
    )


# ----------------------------------------------------------------------
# SSE helpers
# ----------------------------------------------------------------------


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into ``(event, data)`` tuples."""

    events: list[tuple[str, dict]] = []

    for block in text.split("\n\n"):
        if not block.strip():
            continue

        name = "message"
        data: dict = {}

        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])

        events.append((name, data))

    return events


def event_names(events: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _data in events]


def combined_text(events: list[tuple[str, dict]]) -> str:
    return "".join(
        str(data.get("text") or "")
        for name, data in events
        if name == "delta"
    )


async def call_app_with_disconnect(
    *,
    path: str,
    payload: dict,
    disconnect_after_chunks: int = 1,
    timeout: float = 10.0,
):
    """Drive the real ASGI app and disconnect after N response chunks.

    ``httpx.ASGITransport`` buffers a complete response, so it cannot exercise
    a client disconnect. Calling the ASGI app directly lets the test control
    ``receive`` and return ``http.disconnect`` while the stream is active.
    """

    body = json.dumps(payload).encode("utf-8")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "server": ("test", 80),
        "client": ("127.0.0.1", 123),
    }

    sent: list[dict] = []
    disconnect = asyncio.Event()
    request_delivered = False
    body_chunks = 0

    async def receive():
        nonlocal request_delivered

        if not request_delivered:
            request_delivered = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal body_chunks

        sent.append(message)

        if message["type"] == "http.response.body":
            body_chunks += 1

            if body_chunks >= disconnect_after_chunks:
                disconnect.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=timeout)

    return sent


# ----------------------------------------------------------------------
# Streaming API base
# ----------------------------------------------------------------------


class StreamingApiTestBase(ToolEngineTestBase):
    """Authenticated chat API with deterministic, offline streaming."""

    def _make_engine(self):
        return _make_file_engine(self)

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

        self.settings(
            STREAMING_ENABLED=True,
            AI_CACHE_ENABLED=False,
            AI_ROUTING_POLICY="groq_first",
            TOOL_CALLING_ENABLED=False,
            STREAM_TIMEOUT_SECONDS=5.0,
            STREAM_IDLE_TIMEOUT_SECONDS=2.0,
            STREAM_MAX_RESPONSE_CHARS=5000,
            STREAM_MAX_CONCURRENT=4,
            STREAM_QUEUE_TIMEOUT_SECONDS=0.5,
            AI_USAGE_TRACKING_ENABLED=False,
            AI_MEMORY_HYBRID_ENABLED=False,
            SEMANTIC_RETRIEVAL_ENABLED=False,
        )

        from app.api.chat import _reset_stream_semaphore

        _reset_stream_semaphore()
        self.addCleanup(_reset_stream_semaphore)

        classification = ClassificationResult(
            complexity=QueryComplexity.MODERATE,
            confidence=0.9,
            reasons=("test",),
        )

        doubles = (
            (chat_service, "_classify", AsyncMock(return_value=classification)),
            (chat_service, "_retrieve_memories", AsyncMock(return_value=[])),
            (
                unified_context_builder,
                "inspect",
                AsyncMock(
                    side_effect=lambda **kwargs: SimpleNamespace(
                        prompt=f"context::{kwargs['user_message']}"
                    )
                ),
            ),
            (
                task_intent_chat_handler,
                "handle",
                AsyncMock(return_value=None),
            ),
        )

        for target, name, value in doubles:
            patcher = patch.object(target, name, new=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await super().asyncTearDown()
        _remove_database_files(self)

    def _as_user(self, user_id: int) -> None:
        self._current_user = user_id

    def _patch_router_stream(
        self,
        chunks=("Hello",),
        *,
        error=None,
        delay: float = 0.0,
        provider: str = "groq",
        model: str = "test-model",
        usage=None,
        fallback_used: bool = False,
    ):
        """Patch the router's async generator with a deterministic stream."""

        async def generator(
            prompt=None,
            private=False,
            complexity=None,
            request_type=None,
            metadata=None,
            **kwargs,
        ):
            if metadata is not None:
                metadata.clear()
                metadata.update(
                    {
                        "routing_policy": "groq_first",
                        "complexity": "moderate",
                        "provider_order": [provider],
                        "request_type": request_type,
                        "provider": provider,
                        "model": model,
                        "fallback_used": fallback_used,
                        "usage": usage,
                        "duration_ms": 1.0,
                    }
                )

            for chunk in chunks:
                if delay:
                    await asyncio.sleep(delay)

                yield chunk

            if error is not None:
                raise error

        patcher = patch.object(llm_router, "stream_response", new=generator)
        patcher.start()
        self.addCleanup(patcher.stop)

        return generator

    async def _stream(self, payload: dict):
        return await self.client.post("/api/chat/stream", json=payload)

    async def _messages(self, conversation_id: int) -> list[dict]:
        response = await self.client.get(
            f"/api/conversations/{conversation_id}/messages"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messages"]

    async def _message_rows(self, conversation_id: int):
        from sqlalchemy import select

        from app.models.message import Message

        async with self.SessionLocal() as session:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
            return list(result.scalars().all())


# ----------------------------------------------------------------------
# Background task base
# ----------------------------------------------------------------------


class BackgroundTaskTestBase(ToolEngineTestBase):
    """Isolated database plus a reset in-process task runner.

    Background work runs on its own sessions concurrently with the request
    session, so this base uses a temporary file-backed database with real
    per-session connections. A shared in-memory ``StaticPool`` database would
    put the runner and the request on one connection and let their transaction
    boundaries interfere.
    """

    def _make_engine(self):
        return _make_file_engine(self)

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self._original_session_factory = background_task_service.session_factory
        background_task_service.session_factory = self.SessionLocal
        background_task_service.reset_for_tests()

        self.settings(
            BACKGROUND_TASKS_ENABLED=True,
            BACKGROUND_TASK_MAX_CONCURRENT=2,
            BACKGROUND_TASK_MAX_QUEUED_PER_USER=10,
            BACKGROUND_TASK_RETENTION_DAYS=30,
            BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS=1.0,
            BACKGROUND_TASK_MAX_EXECUTION_SECONDS=5.0,
            BACKGROUND_TASK_MAX_CONVERSATION_CHARS=20000,
        )

    async def asyncTearDown(self):
        try:
            await background_task_service.shutdown()

        except Exception:
            pass

        background_task_service.reset_for_tests()
        background_task_service.session_factory = self._original_session_factory
        await super().asyncTearDown()
        _remove_database_files(self)

    async def create_memory(self, *, user_id=None, status=None, content="A fact"):
        from app.models.memory import Memory, MemoryStatus

        async with self.SessionLocal() as session:
            memory = Memory(
                user_id=user_id or self.alice_id,
                content=content,
                category="general",
                status=status or MemoryStatus.APPROVED,
            )
            session.add(memory)
            await session.commit()
            await session.refresh(memory)
            return memory

    async def create_conversation(self, *, user_id=None, messages=("hello",)):
        from app.models.message import MessageRole
        from app.services.conversation_service import conversation_service

        async with self.SessionLocal() as session:
            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=user_id or self.alice_id,
                title="Background test",
            )

            for content in messages:
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=MessageRole.USER,
                    content=content,
                )

            return conversation


__all__ = [
    "BackgroundTaskTestBase",
    "BlockingStream",
    "FakeAsyncStream",
    "FakeSyncCloseStream",
    "StreamingApiTestBase",
    "call_app_with_disconnect",
    "combined_text",
    "event_names",
    "groq_chunk",
    "groq_usage_chunk",
    "ollama_chunk",
    "parse_sse",
]
