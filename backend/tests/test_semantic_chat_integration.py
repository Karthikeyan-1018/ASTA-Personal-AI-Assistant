import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import get_optional_user_id
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.llm_exceptions import LLMProviderError
from app.exceptions.memory_exceptions import EmbeddingServiceError
from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider
from app.main import app
from app.memory.embedding_service import embedding_service
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


ALIGNED = [1.0, 0.0, 0.0]


class SemanticChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise chat with semantic memory retrieval and mocked providers."""

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
            alice = User(username="semantic_chat_alice")
            bob = User(username="semantic_chat_bob")

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

        async def override_bob():
            return self.bob_id

        async def override_none():
            return None

        self.override_alice = override_alice
        self.override_bob = override_bob
        self.override_none = override_none

        app.dependency_overrides[get_db] = override_get_db
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

    async def _add_memory(
        self,
        content: str,
        category: str = "general",
        status: MemoryStatus = MemoryStatus.APPROVED,
        user_id: int | None = None,
        embedding: list[float] | None = None
    ) -> int:

        owner_id = user_id if user_id is not None else self.alice_id

        async with self.SessionLocal() as session:
            memory = await memory_service.create_memory(
                session=session,
                user_id=owner_id,
                content=content,
                category=category
            )

            if status == MemoryStatus.APPROVED:
                await memory_service.approve_memory(
                    session=session,
                    user_id=owner_id,
                    memory_id=memory.id
                )

            elif status == MemoryStatus.REJECTED:
                await memory_service.reject_memory(
                    session=session,
                    user_id=owner_id,
                    memory_id=memory.id
                )

            if embedding is not None:
                memory.embedding = embedding
                memory.embedding_model = embedding_service.model_name
                await session.commit()

            return memory.id

    async def _chat(self, payload: dict):
        return await self.client.post("/api/chat/", json=payload)

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
        return generate_mock.await_args.args[0]

    # ------------------------------------------------------------------
    # 1. Semantic context
    # ------------------------------------------------------------------

    async def test_chat_uses_semantic_memory_without_keyword_overlap(self):

        content = "I want to become a machine learning engineer."

        await self._add_memory(
            content,
            category="general",
            embedding=ALIGNED
        )

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with (
            stack,
            patch.object(
                embedding_service,
                "embed_query",
                new=AsyncMock(return_value=ALIGNED)
            )
        ):
            response = await self._chat(
                {"message": "What do I do for a living?"}
            )

        self.assertEqual(response.status_code, 200, response.text)

        prompt = self._prompt(groq_generate)

        self.assertIn("RETRIEVED MEMORIES", prompt)
        self.assertIn(content, prompt)

    async def test_chat_semantic_context_respects_user_isolation(self):

        content = "Alice wants to become a machine learning engineer."

        await self._add_memory(
            content,
            category="general",
            user_id=self.alice_id,
            embedding=ALIGNED
        )

        app.dependency_overrides[get_optional_user_id] = self.override_bob

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        try:
            with (
                stack,
                patch.object(
                    embedding_service,
                    "embed_query",
                    new=AsyncMock(return_value=ALIGNED)
                )
            ):
                response = await self._chat(
                    {"message": "What do I do for a living?"}
                )
        finally:
            app.dependency_overrides[get_optional_user_id] = (
                self.override_alice
            )

        self.assertEqual(response.status_code, 200)

        prompt = self._prompt(groq_generate)

        self.assertNotIn(content, prompt)
        self.assertEqual(prompt, "What do I do for a living?")

    async def test_chat_semantic_context_excludes_pending_and_rejected(self):

        await self._add_memory(
            "Approved semantic memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Pending semantic memory.",
            category="general",
            status=MemoryStatus.PENDING,
            embedding=ALIGNED
        )
        await self._add_memory(
            "Rejected semantic memory.",
            category="general",
            status=MemoryStatus.REJECTED,
            embedding=ALIGNED
        )

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with (
            stack,
            patch.object(
                embedding_service,
                "embed_query",
                new=AsyncMock(return_value=ALIGNED)
            )
        ):
            response = await self._chat({"message": "What do I do?"})

        self.assertEqual(response.status_code, 200)

        prompt = self._prompt(groq_generate)

        self.assertIn("Approved semantic memory.", prompt)
        self.assertNotIn("Pending semantic memory.", prompt)
        self.assertNotIn("Rejected semantic memory.", prompt)

    # ------------------------------------------------------------------
    # 2. Fallback behaviour
    # ------------------------------------------------------------------

    async def test_chat_without_embeddings_falls_back_to_keyword(self):

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
            response = await self._chat(
                {"message": "What is my career goal?"}
            )

        self.assertEqual(response.status_code, 200)

        prompt = self._prompt(groq_generate)

        self.assertIn(content, prompt)

    async def test_chat_embedding_failure_falls_back_to_keyword(self):

        content = "My career goal is to become an AI/ML Engineer."

        await self._add_memory(
            content,
            category="career",
            embedding=ALIGNED
        )

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with (
            stack,
            patch.object(
                embedding_service,
                "embed_query",
                new=AsyncMock(
                    side_effect=EmbeddingServiceError("model failed")
                )
            )
        ):
            response = await self._chat(
                {"message": "What is my career goal?"}
            )

        self.assertEqual(response.status_code, 200, response.text)

        prompt = self._prompt(groq_generate)

        self.assertIn(content, prompt)

    async def test_semantic_disabled_uses_keyword_without_embedding_call(self):

        content = "My career goal is to become an AI/ML Engineer."

        await self._add_memory(
            content,
            category="career",
            embedding=ALIGNED
        )

        embed_mock = AsyncMock(return_value=ALIGNED)

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        with (
            stack,
            patch.object(
                settings,
                "SEMANTIC_RETRIEVAL_ENABLED",
                False
            ),
            patch.object(
                embedding_service,
                "embed_query",
                new=embed_mock
            )
        ):
            response = await self._chat(
                {"message": "What is my career goal?"}
            )

        self.assertEqual(response.status_code, 200)

        embed_mock.assert_not_awaited()

        prompt = self._prompt(groq_generate)

        self.assertIn(content, prompt)

    # ------------------------------------------------------------------
    # 3. Privacy and provider routing
    # ------------------------------------------------------------------

    async def test_private_chat_uses_semantic_context_with_ollama_only(self):

        content = "I want to become a machine learning engineer."

        await self._add_memory(
            content,
            category="general",
            embedding=ALIGNED
        )

        (
            stack,
            groq_health,
            groq_generate,
            _ollama_health,
            ollama_generate
        ) = self._patch_providers()

        with (
            stack,
            patch.object(
                embedding_service,
                "embed_query",
                new=AsyncMock(return_value=ALIGNED)
            )
        ):
            response = await self._chat({
                "message": "What do I do for a living?",
                "private": True
            })

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()

        self.assertEqual(body["provider"], "ollama")

        groq_health.assert_not_awaited()
        groq_generate.assert_not_awaited()
        ollama_generate.assert_awaited_once()

        prompt = self._prompt(ollama_generate)

        self.assertIn(content, prompt)

    async def test_groq_failure_falls_back_to_ollama_with_semantic_context(self):

        content = "I want to become a machine learning engineer."

        await self._add_memory(
            content,
            category="general",
            embedding=ALIGNED
        )

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            ollama_generate
        ) = self._patch_providers(
            groq_error=LLMProviderError("Groq failed."),
            ollama_response="ollama fallback"
        )

        with (
            stack,
            patch.object(
                embedding_service,
                "embed_query",
                new=AsyncMock(return_value=ALIGNED)
            )
        ):
            response = await self._chat(
                {"message": "What do I do for a living?"}
            )

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()

        self.assertEqual(body["provider"], "ollama")
        self.assertEqual(body["response"], "ollama fallback")

        groq_generate.assert_awaited_once()
        ollama_generate.assert_awaited_once()

        prompt = self._prompt(ollama_generate)

        self.assertIn(content, prompt)

    async def test_unauthenticated_semantic_chat_ignores_memories(self):

        await self._add_memory(
            "I want to become a machine learning engineer.",
            category="general",
            embedding=ALIGNED
        )

        app.dependency_overrides[get_optional_user_id] = self.override_none

        embed_mock = AsyncMock(return_value=ALIGNED)

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers()

        try:
            with (
                stack,
                patch.object(
                    embedding_service,
                    "embed_query",
                    new=embed_mock
                )
            ):
                response = await self._chat(
                    {"message": "What do I do for a living?"}
                )
        finally:
            app.dependency_overrides[get_optional_user_id] = (
                self.override_alice
            )

        self.assertEqual(response.status_code, 200)

        embed_mock.assert_not_awaited()

        prompt = self._prompt(groq_generate)

        self.assertEqual(prompt, "What do I do for a living?")

    # ------------------------------------------------------------------
    # 4. Existing chat contract
    # ------------------------------------------------------------------

    async def test_public_chat_response_shape_is_preserved(self):

        (
            stack,
            _groq_health,
            _groq_generate,
            _ollama_health,
            _ollama_generate
        ) = self._patch_providers(groq_response="Hello from Groq")

        with stack:
            response = await self._chat({"message": "Hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json().keys()),
            {"provider", "model", "response"}
        )
        self.assertEqual(response.json()["response"], "Hello from Groq")

    async def test_empty_prompt_still_returns_400(self):

        response = await self._chat({"message": "   "})

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
