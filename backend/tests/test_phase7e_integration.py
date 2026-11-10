"""Phase 7E end-to-end integration and reliability tests.

These tests focus on gaps not covered by the earlier phase suites:

* blank / non-string provider answers must fail safely and never be persisted;
* provider and model metadata must both be stored on assistant messages;
* optional feature (pending action) persistence failures must not break chat;
* confirmation state must remain conversation-scoped;
* database failures must return a controlled error;
* prompts and secrets must not appear in application logs;
* persisted history must survive a process/engine restart;
* a failed commit must roll back the session.

External providers are always mocked. Database integration verification against
real PostgreSQL lives in ``backend/scripts/verify_phase7e_integration.py``.
"""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import (
    get_current_user_id,
    get_optional_user_id,
)
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMUnavailableError,
)
from app.llm.groq_provider import groq_provider
from app.llm.llm_router import llm_router
from app.llm.ollama_provider import ollama_provider
from app.main import app
from app.memory.memory_retriever import memory_retriever
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.task import Task
from app.models.task_action import TaskPendingAction
from app.models.user import User
from app.services.conversation_service import conversation_service
from app.task_intent.pending_service import pending_action_service


class ProviderEmptyResponseTests(unittest.IsolatedAsyncioTestCase):
    """Blank provider output is a failure, not a successful empty answer."""

    def _providers(
        self,
        *,
        groq_health=True,
        groq_response="groq answer",
        groq_error=None,
        ollama_health=True,
        ollama_response="ollama answer",
        ollama_error=None,
    ):
        stack = ExitStack()

        groq_generate = AsyncMock(
            side_effect=groq_error,
            return_value=None if groq_error else groq_response,
        )
        ollama_generate = AsyncMock(
            side_effect=ollama_error,
            return_value=None if ollama_error else ollama_response,
        )

        stack.enter_context(
            patch.object(
                groq_provider,
                "health_check",
                new=AsyncMock(return_value=groq_health),
            )
        )
        stack.enter_context(
            patch.object(groq_provider, "generate_response", new=groq_generate)
        )
        stack.enter_context(
            patch.object(
                ollama_provider,
                "health_check",
                new=AsyncMock(return_value=ollama_health),
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider,
                "generate_response",
                new=ollama_generate,
            )
        )

        return stack, groq_generate, ollama_generate

    async def test_groq_none_falls_back_to_ollama(self):

        stack, groq_generate, ollama_generate = self._providers(
            groq_response=None,
            ollama_response="local answer",
        )

        with stack:
            result = await llm_router.generate_response("hello")

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["model"], settings.OLLAMA_MODEL)
        self.assertEqual(result["response"], "local answer")
        groq_generate.assert_awaited_once()
        ollama_generate.assert_awaited_once()

    async def test_groq_whitespace_falls_back_to_ollama(self):

        stack, _groq_generate, _ollama_generate = self._providers(
            groq_response="   \n\t",
            ollama_response="local answer",
        )

        with stack:
            result = await llm_router.generate_response("hello")

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["response"], "local answer")

    async def test_non_string_groq_answer_falls_back_to_ollama(self):

        stack, _groq_generate, _ollama_generate = self._providers(
            groq_response=SimpleNamespace(content="not a string"),
            ollama_response="local answer",
        )

        with stack:
            result = await llm_router.generate_response("hello")

        self.assertEqual(result["provider"], "ollama")

    async def test_blank_groq_without_ollama_returns_unavailable(self):

        stack, _groq_generate, ollama_generate = self._providers(
            groq_response="",
            ollama_health=False,
        )

        with stack:
            with self.assertRaises(LLMUnavailableError):
                await llm_router.generate_response("hello")

        ollama_generate.assert_not_awaited()

    async def test_private_blank_ollama_never_calls_groq(self):

        stack, groq_generate, ollama_generate = self._providers(
            groq_health=True,
            ollama_health=True,
            ollama_response="   ",
        )

        with stack:
            with self.assertRaises(LLMUnavailableError):
                await llm_router.generate_response("secret", private=True)

        ollama_generate.assert_awaited_once()
        groq_generate.assert_not_awaited()

    async def test_blank_ollama_fallback_returns_unavailable(self):

        stack, _groq_generate, ollama_generate = self._providers(
            groq_error=LLMProviderError("groq failed"),
            ollama_response=None,
        )

        with stack:
            with self.assertRaises(LLMUnavailableError):
                await llm_router.generate_response("hello")

        ollama_generate.assert_awaited_once()


class ChatReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """API-level reliability, metadata, isolation, and log-safety checks."""

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
            alice = User(username="phase7e_alice")
            bob = User(username="phase7e_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)
            self.alice_id = alice.id
            self.bob_id = bob.id

        self.memory_patch = patch.object(
            memory_retriever,
            "retrieve",
            new=AsyncMock(return_value=[]),
        )
        self.memory_patch.start()
        self.addCleanup(self.memory_patch.stop)

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_alice():
            return self.alice_id

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

    def _providers(
        self,
        *,
        groq_health=True,
        groq_response="Groq answer",
        groq_error=None,
        ollama_health=True,
        ollama_response="Ollama answer",
        ollama_error=None,
    ):
        stack = ExitStack()

        stack.enter_context(
            patch.object(
                groq_provider,
                "health_check",
                new=AsyncMock(return_value=groq_health),
            )
        )
        stack.enter_context(
            patch.object(
                groq_provider,
                "generate_response",
                new=AsyncMock(
                    side_effect=groq_error,
                    return_value=None if groq_error else groq_response,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider,
                "health_check",
                new=AsyncMock(return_value=ollama_health),
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider,
                "generate_response",
                new=AsyncMock(
                    side_effect=ollama_error,
                    return_value=None if ollama_error else ollama_response,
                ),
            )
        )

        return stack

    async def _messages(self, conversation_id):
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.asc())
            )
            return list(result.scalars().all())

    async def _conversations(self):
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == self.alice_id)
                .order_by(Conversation.id.asc())
            )
            return list(result.scalars().all())

    async def _count(self, model):
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(model)
                ) or 0
            )

    async def test_blank_provider_answers_return_503_without_assistant(self):

        stack = self._providers(groq_response="", ollama_response="")

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "Please answer"},
            )

        self.assertEqual(response.status_code, 503, response.text)

        conversations = await self._conversations()
        self.assertEqual(len(conversations), 1)

        messages = await self._messages(conversations[0].id)
        self.assertEqual([message.role for message in messages],
                         [MessageRole.USER])
        self.assertEqual(messages[0].content, "Please answer")

    async def test_blank_groq_falls_back_and_persists_provider_and_model(self):

        stack = self._providers(
            groq_response="   ",
            ollama_response="Local answer",
        )

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "Hello"},
            )

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        self.assertEqual(body["provider"], "ollama")
        self.assertEqual(body["model"], settings.OLLAMA_MODEL)
        self.assertEqual(body["response"], "Local answer")

        messages = await self._messages(body["conversation_id"])
        self.assertEqual([message.role for message in messages],
                         [MessageRole.USER, MessageRole.ASSISTANT])
        self.assertIsNone(messages[0].provider)
        self.assertIsNone(messages[0].model)
        self.assertEqual(messages[1].provider, "ollama")
        self.assertEqual(messages[1].model, settings.OLLAMA_MODEL)

    async def test_task_engine_reply_persists_deterministic_model(self):

        stack = self._providers()

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "Add a task to call mom tomorrow."},
            )

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        self.assertEqual(body["provider"], "task_engine")
        self.assertEqual(body["model"], "deterministic")

        messages = await self._messages(body["conversation_id"])
        self.assertEqual(messages[-1].provider, "task_engine")
        self.assertEqual(messages[-1].model, "deterministic")

    async def test_pending_action_persistence_failure_does_not_break_chat(self):

        stack = self._providers(groq_response="Normal answer.")

        with stack, patch.object(
            pending_action_service,
            "create_pending",
            new=AsyncMock(side_effect=RuntimeError("pending store down")),
        ):
            with self.assertLogs(
                "app.services.chat_service", level="ERROR"
            ):
                response = await self.client.post(
                    "/api/chat/",
                    json={"message": "Add a task to call mom tomorrow."},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provider"], "groq")
        self.assertEqual(response.json()["response"], "Normal answer.")
        self.assertEqual(await self._count(Task), 0)
        self.assertEqual(await self._count(TaskPendingAction), 0)

    async def test_pending_action_is_conversation_scoped(self):

        stack = self._providers(groq_response="Normal answer.")

        with stack:
            first = await self.client.post(
                "/api/chat/",
                json={"message": "Add a task to call mom tomorrow."},
            )

            second = await self.client.post(
                "/api/conversations/",
                json={"title": "Other conversation"},
            )

            other_id = second.json()["id"]

            reply = await self.client.post(
                "/api/chat/",
                json={"message": "yes", "conversation_id": other_id},
            )

            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual(reply.json()["response"], "Normal answer.")
            self.assertEqual(await self._count(Task), 0)

            async with self.SessionLocal() as session:
                actions = list(
                    (
                        await session.execute(select(TaskPendingAction))
                    ).scalars().all()
                )

            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].conversation_id,
                             first.json()["conversation_id"])

            confirmed = await self.client.post(
                "/api/chat/",
                json={
                    "message": "yes",
                    "conversation_id": first.json()["conversation_id"],
                },
            )

        self.assertIn("Done.", confirmed.json()["response"])
        self.assertEqual(await self._count(Task), 1)

    async def test_database_failure_returns_controlled_error(self):

        stack = self._providers()

        failure = OperationalError(
            "INSERT INTO conversations",
            {},
            Exception("connection refused"),
        )

        with stack, patch.object(
            conversation_service,
            "create_conversation",
            new=AsyncMock(side_effect=failure),
        ):
            with self.assertLogs("app.api.chat", level="ERROR"):
                response = await self.client.post(
                    "/api/chat/",
                    json={"message": "Hello"},
                )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json()["detail"],
            "An unexpected error occurred while processing your request.",
        )

    async def test_failed_provider_does_not_log_the_prompt(self):

        secret = "my password is hunter2-secret-value"

        stack = self._providers(
            groq_error=LLMProviderError("Groq failed."),
            ollama_error=LLMProviderError("Ollama failed."),
        )

        with stack:
            with self.assertLogs("app", level="DEBUG") as captured:
                response = await self.client.post(
                    "/api/chat/",
                    json={"message": secret},
                )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn(secret, "\n".join(captured.output))
        self.assertNotIn("hunter2", "\n".join(captured.output))


class ConversationRestartTests(unittest.IsolatedAsyncioTestCase):
    """History remains available after the database engine is restarted."""

    async def asyncSetUp(self):

        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "restart.sqlite3"
        self.url = f"sqlite+aiosqlite:///{database_path.as_posix()}"

        self.engine = create_async_engine(self.url)
        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with self.SessionLocal() as session:
            user = User(username="phase7e_restart")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            self.user_id = user.id

            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=self.user_id,
                title="Restart check",
            )
            self.conversation_id = conversation.id

            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content="First question",
            )
            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.ASSISTANT,
                content="First answer",
                provider="groq",
                model="test-model",
            )

    async def asyncTearDown(self):

        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def test_history_is_retrievable_after_restart(self):

        await self.engine.dispose()

        restarted_engine = create_async_engine(self.url)
        restarted_sessions = async_sessionmaker(
            bind=restarted_engine,
            expire_on_commit=False,
        )

        try:
            async with restarted_sessions() as session:
                messages, total = await conversation_service.get_messages(
                    session=session,
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                )

                self.assertEqual(total, 2)
                self.assertEqual(
                    [message.role for message in messages],
                    [MessageRole.USER, MessageRole.ASSISTANT],
                )
                self.assertEqual(messages[0].content, "First question")
                self.assertEqual(messages[1].content, "First answer")
                self.assertEqual(messages[1].provider, "groq")
                self.assertEqual(messages[1].model, "test-model")

                recent = await conversation_service.get_recent_messages(
                    session=session,
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                    limit=1,
                )

                self.assertEqual(len(recent), 1)
                self.assertEqual(recent[0].content, "First answer")

        finally:
            await restarted_engine.dispose()


class TransactionRollbackTests(unittest.IsolatedAsyncioTestCase):
    """A failed message commit rolls back without touching other rows."""

    async def test_add_message_rolls_back_on_commit_failure(self):

        session = AsyncMock()
        session.add = Mock()
        session.commit.side_effect = RuntimeError("commit failed")
        conversation = SimpleNamespace(id=42)

        with self.assertLogs(
            "app.services.conversation_service", level="ERROR"
        ):
            with self.assertRaises(RuntimeError):
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=MessageRole.USER,
                    content="hello",
                )

        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        session.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
