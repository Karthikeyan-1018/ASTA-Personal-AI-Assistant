import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import get_current_user_id
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider
from app.main import app
from app.models.ai_cache import AIResponseCache
from app.models.ai_usage import AIUsageRecord
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


class Phase9ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):

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
            owner = User(username="phase9_chat_owner")
            session.add(owner)
            await session.commit()
            await session.refresh(owner)
            self.user_id = owner.id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_owner():
            return self.user_id

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_owner

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

        for name, value in (
            ("AI_CACHE_ENABLED", True),
            ("AI_CACHE_STATELESS_ONLY", True),
            ("AI_CACHE_SEMANTIC_ENABLED", False),
            ("AI_MEMORY_HYBRID_ENABLED", True),
            ("SEMANTIC_RETRIEVAL_ENABLED", False),
            ("AI_ROUTING_POLICY", "groq_first"),
            ("AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED", False),
        ):
            settings_patch = patch.object(settings, name, value)
            settings_patch.start()
            self.addCleanup(settings_patch.stop)

    async def asyncTearDown(self):

        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    async def _chat(self, payload: dict):
        return await self.client.post("/api/chat/", json=payload)

    async def _add_memory(self, content: str, category: str = "general"):

        async with self.SessionLocal() as session:
            memory = await memory_service.create_memory(
                session=session,
                user_id=self.user_id,
                content=content,
                category=category,
            )
            await memory_service.approve_memory(
                session=session,
                user_id=self.user_id,
                memory_id=memory.id,
            )
            return memory.id

    def _patch_providers(
        self,
        groq_response: str = "groq answer",
        ollama_response: str = "ollama answer",
    ):

        stack = ExitStack()

        groq_health = AsyncMock(return_value=True)
        groq_generate = AsyncMock(return_value=groq_response)
        ollama_health = AsyncMock(return_value=True)
        ollama_generate = AsyncMock(return_value=ollama_response)

        stack.enter_context(
            patch.object(groq_provider, "health_check", new=groq_health)
        )
        stack.enter_context(
            patch.object(
                groq_provider, "generate_response", new=groq_generate
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider, "health_check", new=ollama_health
            )
        )
        stack.enter_context(
            patch.object(
                ollama_provider, "generate_response", new=ollama_generate
            )
        )

        return (
            stack,
            groq_health,
            groq_generate,
            ollama_health,
            ollama_generate,
        )

    async def _cache_count(self) -> int:
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count(AIResponseCache.id))
                )
                or 0
            )

    async def _usage_rows(self):

        async with self.SessionLocal() as session:
            return (
                await session.execute(
                    select(AIUsageRecord).order_by(AIUsageRecord.id)
                )
            ).scalars().all()

    async def test_repeated_stateless_query_is_served_from_cache(self):

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate,
        ) = self._patch_providers(groq_response="cached answer")

        with stack:
            first = await self._chat({"message": "Hello there"})
            second = await self._chat({"message": "Hello there"})

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["response"], "cached answer")
        self.assertEqual(second.json()["response"], "cached answer")

        groq_generate.assert_awaited_once()

        self.assertEqual(await self._cache_count(), 1)

        rows = await self._usage_rows()

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(1 for row in rows if row.cache_hit), 1)

    async def test_private_requests_are_never_cached(self):

        (
            stack,
            groq_health,
            groq_generate,
            _ollama_health,
            ollama_generate,
        ) = self._patch_providers(ollama_response="private answer")

        with stack:
            first = await self._chat(
                {"message": "I feel overwhelmed lately", "private": True}
            )
            second = await self._chat(
                {"message": "I feel overwhelmed lately", "private": True}
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["provider"], "ollama")
        self.assertEqual(second.json()["provider"], "ollama")

        groq_health.assert_not_awaited()
        groq_generate.assert_not_awaited()
        self.assertEqual(ollama_generate.await_count, 2)

        self.assertEqual(await self._cache_count(), 0)

        rows = await self._usage_rows()

        self.assertEqual(len(rows), 2)
        self.assertFalse(any(row.cache_hit for row in rows))

    async def test_context_bearing_requests_are_not_cached(self):

        await self._add_memory(
            "My career goal is to become an AI engineer.",
            category="career",
        )

        (
            stack,
            _groq_health,
            groq_generate,
            _ollama_health,
            _ollama_generate,
        ) = self._patch_providers(groq_response="contextual answer")

        with stack:
            first = await self._chat(
                {"message": "What is my career goal?"}
            )
            second = await self._chat(
                {"message": "What is my career goal?"}
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        self.assertEqual(groq_generate.await_count, 2)
        self.assertEqual(await self._cache_count(), 0)


if __name__ == "__main__":
    unittest.main()
