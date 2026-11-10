"""Conversation persistence, ownership, and chat-continuation tests."""

import unittest
from contextlib import ExitStack
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
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.llm_exceptions import LLMProviderError
from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider
from app.main import app
from app.models.memory import MemoryStatus
from app.models.message import Message, MessageRole
from app.models.user import User
from app.services.conversation_service import conversation_service
from app.services.memory_service import memory_service


class ConversationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Exercise conversation CRUD, message persistence, and continuation."""

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
            alice = User(username="conversation_alice")
            bob = User(username="conversation_bob")

            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_alice():
            return self.alice_id

        self.override_alice = override_alice

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
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _as_user(self, user_id: int | None):
        async def override():
            return user_id

        app.dependency_overrides[get_current_user_id] = override
        app.dependency_overrides[get_optional_user_id] = override

    def _restore_alice(self):
        app.dependency_overrides[get_current_user_id] = self.override_alice
        app.dependency_overrides[get_optional_user_id] = self.override_alice

    def _patch_providers(
        self,
        groq_health: bool = True,
        groq_response: str = "groq answer",
        groq_error: Exception | None = None,
        ollama_health: bool = True,
        ollama_response: str = "ollama answer",
        ollama_error: Exception | None = None
    ):

        stack = ExitStack()

        groq_health_mock = AsyncMock(return_value=groq_health)
        groq_generate_mock = AsyncMock(
            return_value=groq_response,
            side_effect=groq_error
        )

        ollama_health_mock = AsyncMock(return_value=ollama_health)
        ollama_generate_mock = AsyncMock(
            return_value=ollama_response,
            side_effect=ollama_error
        )

        stack.enter_context(
            patch.object(groq_provider, "health_check", new=groq_health_mock)
        )
        stack.enter_context(
            patch.object(
                groq_provider,
                "generate_response",
                new=groq_generate_mock
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider,
                "health_check",
                new=ollama_health_mock
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider,
                "generate_response",
                new=ollama_generate_mock
            )
        )

        return (
            stack,
            groq_health_mock,
            groq_generate_mock,
            ollama_health_mock,
            ollama_generate_mock
        )

    @staticmethod
    def _prompt(generate_mock) -> str:
        if generate_mock.await_args.args:
            return generate_mock.await_args.args[0]

        return generate_mock.await_args.kwargs["prompt"]

    async def _create_conversation(
        self,
        title: str | None = None,
        user_id: int | None = None
    ):
        if user_id is not None:
            self._as_user(user_id)

        try:
            payload = {} if title is None else {"title": title}
            return await self.client.post(
                "/api/conversations/",
                json=payload
            )
        finally:
            self._restore_alice()

    async def _add_memory(self, content: str, category: str = "general"):

        async with self.SessionLocal() as session:
            memory = await memory_service.create_memory(
                session=session,
                user_id=self.alice_id,
                content=content,
                category=category
            )
            await memory_service.approve_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=memory.id
            )

            return memory.id

    async def _messages(self, conversation_id: int):
        return await self.client.get(
            f"/api/conversations/{conversation_id}/messages"
        )

    async def _conversation_rows(self, user_id: int):

        async with self.SessionLocal() as session:
            return await conversation_service.list_conversations(
                session=session,
                user_id=user_id
            )

    # ------------------------------------------------------------------
    # 1-5. Conversation CRUD
    # ------------------------------------------------------------------

    async def test_create_conversation(self):

        response = await self._create_conversation("Trip planning")

        self.assertEqual(response.status_code, 201, response.text)

        body = response.json()
        self.assertIsInstance(body["id"], int)
        self.assertEqual(body["title"], "Trip planning")
        self.assertEqual(body["user_id"], self.alice_id)
        self.assertIn("created_at", body)
        self.assertIn("updated_at", body)

    async def test_create_conversation_uses_default_title(self):

        response = await self._create_conversation()

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["title"], "New conversation")

    async def test_list_conversations_is_user_scoped_and_newest_first(self):

        first = await self._create_conversation("First")
        second = await self._create_conversation("Second")
        bob = await self._create_conversation("Bob only", user_id=self.bob_id)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(bob.status_code, 201)

        # Touching the first conversation must move it to the top.
        update = await self.client.patch(
            f"/api/conversations/{first.json()['id']}",
            json={"title": "First updated"}
        )
        self.assertEqual(update.status_code, 200, update.text)

        response = await self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200, response.text)

        titles = [item["title"] for item in response.json()]
        self.assertEqual(titles, ["First updated", "Second"])

        ids = {item["id"] for item in response.json()}
        self.assertNotIn(bob.json()["id"], ids)
        self.assertTrue(all(item["user_id"] == self.alice_id
                            for item in response.json()))

    async def test_retrieve_conversation_and_reject_foreign_conversation(self):

        created = await self._create_conversation("Mine")
        conversation_id = created.json()["id"]

        own = await self.client.get(
            f"/api/conversations/{conversation_id}"
        )
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["id"], conversation_id)

        foreign = await self._create_conversation(
            "Not yours",
            user_id=self.bob_id
        )

        denied = await self.client.get(
            f"/api/conversations/{foreign.json()['id']}"
        )
        self.assertEqual(denied.status_code, 404)

        missing = await self.client.get("/api/conversations/999999")
        self.assertEqual(missing.status_code, 404)

    async def test_update_conversation_title(self):

        created = await self._create_conversation("Old title")
        conversation_id = created.json()["id"]

        response = await self.client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "  New title  "}
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["title"], "New title")

        missing = await self.client.patch(
            "/api/conversations/999999",
            json={"title": "Nope"}
        )
        self.assertEqual(missing.status_code, 404)

        foreign = await self._create_conversation(
            "Bob title",
            user_id=self.bob_id
        )
        denied = await self.client.patch(
            f"/api/conversations/{foreign.json()['id']}",
            json={"title": "Hijacked"}
        )
        self.assertEqual(denied.status_code, 404)

    async def test_delete_conversation_cascades_messages(self):

        (
            stack,
            _groq_health,
            _groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with stack:
            chat = await self.client.post(
                "/api/chat/",
                json={"message": "Remember this"}
            )

        conversation_id = chat.json()["conversation_id"]

        response = await self.client.delete(
            f"/api/conversations/{conversation_id}"
        )

        self.assertEqual(response.status_code, 204, response.text)

        gone = await self.client.get(
            f"/api/conversations/{conversation_id}"
        )
        self.assertEqual(gone.status_code, 404)

        messages = await self._messages(conversation_id)
        self.assertEqual(messages.status_code, 404)

        async with self.SessionLocal() as session:
            remaining = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )

        self.assertEqual(remaining, 0)

    # ------------------------------------------------------------------
    # 6-9. Message persistence and continuation
    # ------------------------------------------------------------------

    async def test_chat_saves_user_and_assistant_messages(self):

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers(groq_response="The answer")

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "  What is ASTA?  "}
            )

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        conversation_id = body["conversation_id"]

        self.assertEqual(body["provider"], "groq")
        self.assertEqual(body["response"], "The answer")
        self.assertEqual(self._prompt(groq_generate), "What is ASTA?")

        messages = await self._messages(conversation_id)

        self.assertEqual(messages.status_code, 200, messages.text)

        payload = messages.json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual([m["role"] for m in payload["messages"]],
                         ["user", "assistant"])
        self.assertEqual(payload["messages"][0]["content"], "What is ASTA?")
        self.assertEqual(payload["messages"][1]["content"], "The answer")
        self.assertIsNone(payload["messages"][0]["provider"])
        self.assertEqual(payload["messages"][1]["provider"], "groq")
        self.assertFalse(payload["messages"][0]["is_private"])
        self.assertFalse(payload["messages"][1]["is_private"])

        conversations = await self.client.get("/api/conversations/")
        self.assertEqual(conversations.json()[0]["id"], conversation_id)
        self.assertEqual(conversations.json()[0]["title"], "What is ASTA?")

    async def test_continue_existing_conversation_loads_previous_messages(self):

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers(
            groq_response="My name is ASTA."
        )

        with stack:
            first = await self.client.post(
                "/api/chat/",
                json={"message": "Hello, who are you?"}
            )

        conversation_id = first.json()["conversation_id"]

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers(
            groq_response="You asked who I am."
        )

        with stack:
            second = await self.client.post(
                "/api/chat/",
                json={
                    "message": "What did I ask first?",
                    "conversation_id": conversation_id
                }
            )

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["conversation_id"], conversation_id)

        prompt = self._prompt(groq_generate)
        self.assertIn("CONVERSATION HISTORY", prompt)
        self.assertIn("Hello, who are you?", prompt)
        self.assertIn("My name is ASTA.", prompt)
        self.assertIn("What did I ask first?", prompt)
        self.assertLess(
            prompt.index("Hello, who are you?"),
            prompt.index("What did I ask first?")
        )

        messages = (await self._messages(conversation_id)).json()
        self.assertEqual(messages["total"], 4)
        self.assertEqual(
            [m["role"] for m in messages["messages"]],
            ["user", "assistant", "user", "assistant"]
        )

        conversations = await self.client.get("/api/conversations/")
        self.assertEqual(len(conversations.json()), 1)

    async def test_new_conversation_does_not_include_history_context(self):

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "First ever message"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._prompt(groq_generate), "First ever message")

    async def test_history_limit_is_bounded(self):

        created = await self._create_conversation("Bounded history")
        conversation_id = created.json()["id"]

        async with self.SessionLocal() as session:
            conversation = await conversation_service.get_conversation(
                session=session,
                user_id=self.alice_id,
                conversation_id=conversation_id
            )

            for index in range(25):
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=(
                        MessageRole.USER
                        if index % 2 == 0
                        else MessageRole.ASSISTANT
                    ),
                    content=f"message {index}"
                )

        async with self.SessionLocal() as session:
            history = await conversation_service.get_recent_messages(
                session=session,
                user_id=self.alice_id,
                conversation_id=conversation_id,
                limit=10
            )

        self.assertEqual(len(history), 10)
        self.assertEqual(history[-1].content, "message 24")
        self.assertEqual(history[0].content, "message 15")

    # ------------------------------------------------------------------
    # 10. Pagination and chronological ordering
    # ------------------------------------------------------------------

    async def test_messages_pagination_and_chronological_order(self):

        created = await self._create_conversation("Paged")
        conversation_id = created.json()["id"]

        async with self.SessionLocal() as session:
            conversation = await conversation_service.get_conversation(
                session=session,
                user_id=self.alice_id,
                conversation_id=conversation_id
            )

            for index in range(5):
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=(
                        MessageRole.USER
                        if index % 2 == 0
                        else MessageRole.ASSISTANT
                    ),
                    content=f"msg {index}"
                )

        response = await self.client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"limit": 2, "offset": 1}
        )

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 1)
        self.assertEqual(
            [m["content"] for m in body["messages"]],
            ["msg 1", "msg 2"]
        )

        invalid = await self.client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"limit": 0}
        )
        self.assertEqual(invalid.status_code, 422)

    # ------------------------------------------------------------------
    # 11-13. Ownership, invalid ids, and failure handling
    # ------------------------------------------------------------------

    async def test_chat_rejects_another_users_conversation(self):

        created = await self._create_conversation(
            "Bob private",
            user_id=self.bob_id
        )
        bob_conversation_id = created.json()["id"]

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={
                    "message": "Steal this conversation",
                    "conversation_id": bob_conversation_id
                }
            )

        self.assertEqual(response.status_code, 404)
        groq_generate.assert_not_awaited()

        messages = await self._messages(bob_conversation_id)
        self.assertEqual(messages.status_code, 404)

        async with self.SessionLocal() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == bob_conversation_id)
            )

        self.assertEqual(count, 0)

    async def test_chat_rejects_invalid_conversation_id(self):

        (
            stack,
            _groq_health,
            _groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={
                    "message": "Hello?",
                    "conversation_id": 999999
                }
            )

        self.assertEqual(response.status_code, 404)

    async def test_unauthenticated_chat_cannot_continue_conversation(self):

        created = await self._create_conversation("Owned")
        conversation_id = created.json()["id"]

        self._as_user(None)

        try:
            (
                stack,
                _groq_health,
                _groq_generate,
                _ollama_health,
                _ollama_generate
            ) = self._patch_providers()

            with stack:
                response = await self.client.post(
                    "/api/chat/",
                    json={
                        "message": "No identity",
                        "conversation_id": conversation_id
                    }
                )
        finally:
            self._restore_alice()

        self.assertEqual(response.status_code, 404)

    async def test_llm_failure_preserves_user_message_without_assistant(self):

        (
            stack,
            _groq_health,
            _groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers(
            groq_error=LLMProviderError("Groq failed."),
            ollama_error=LLMProviderError("Ollama failed.")
        )

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "This must be kept"}
            )

        self.assertEqual(response.status_code, 503)

        conversations = await self._conversation_rows(self.alice_id)
        self.assertEqual(len(conversations), 1)

        conversation_id = conversations[0].id
        messages = (await self._messages(conversation_id)).json()

        self.assertEqual(messages["total"], 1)
        self.assertEqual(messages["messages"][0]["role"], "user")
        self.assertEqual(
            messages["messages"][0]["content"],
            "This must be kept"
        )

        retry = await self.client.get(
            f"/api/conversations/{conversation_id}"
        )
        self.assertEqual(retry.status_code, 200)

    # ------------------------------------------------------------------
    # 14. Private requests remain Ollama-only and persist metadata
    # ------------------------------------------------------------------

    async def test_private_chat_uses_ollama_only_and_marks_messages(self):

        (
            stack,
            _groq_health,
            groq_generate,
            ollama_health,
            ollama_generate
        ) = self._patch_providers(
            groq_response="must not be used",
            ollama_response="private answer"
        )

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={
                    "message": "A private question",
                    "private": True
                }
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provider"], "ollama")
        self.assertEqual(response.json()["response"], "private answer")

        groq_generate.assert_not_awaited()
        ollama_generate.assert_awaited_once()

        body = (await self._messages(
            response.json()["conversation_id"]
        )).json()

        self.assertEqual(body["total"], 2)
        self.assertTrue(all(m["is_private"] for m in body["messages"]))
        self.assertEqual(body["messages"][0]["provider"], None)
        self.assertEqual(body["messages"][1]["provider"], "ollama")

    async def test_private_history_is_not_sent_to_groq_on_later_turn(self):

        (
            stack,
            _groq_health,
            _groq_generate,
            _ollama_health,
            ollama_generate
        ) = self._patch_providers(
            ollama_response="private reply"
        )

        with stack:
            first = await self.client.post(
                "/api/chat/",
                json={
                    "message": "secret medical detail",
                    "private": True
                }
            )

        conversation_id = first.json()["conversation_id"]

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers(
            groq_response="public reply"
        )

        with stack:
            second = await self.client.post(
                "/api/chat/",
                json={
                    "message": "public follow up",
                    "conversation_id": conversation_id
                }
            )

        self.assertEqual(second.status_code, 200, second.text)

        prompt = self._prompt(groq_generate)
        self.assertNotIn("secret medical detail", prompt)
        self.assertNotIn("private reply", prompt)
        self.assertIn("public follow up", prompt)

        messages = (await self._messages(conversation_id)).json()
        self.assertEqual(messages["total"], 4)
        self.assertTrue(messages["messages"][0]["is_private"])
        self.assertTrue(messages["messages"][1]["is_private"])
        self.assertFalse(messages["messages"][2]["is_private"])
        self.assertFalse(messages["messages"][3]["is_private"])

    # ------------------------------------------------------------------
    # 15. Phase 5 semantic memory integration remains functional
    # ------------------------------------------------------------------

    async def test_semantic_memory_context_still_reaches_the_prompt(self):

        content = "My career goal is to become an AI/ML Engineer."

        await self._add_memory(content, category="career")

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with stack:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "What is my career goal?"}
            )

        self.assertEqual(response.status_code, 200, response.text)

        prompt = self._prompt(groq_generate)
        self.assertIn("RETRIEVED MEMORIES", prompt)
        self.assertIn(content, prompt)
        self.assertIn("CURRENT USER MESSAGE", prompt)

        messages = await self._messages(
            response.json()["conversation_id"]
        )
        self.assertEqual(messages.json()["total"], 2)


if __name__ == "__main__":
    unittest.main()
