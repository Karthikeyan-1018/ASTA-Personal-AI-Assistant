import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.core.config import settings
from app.database.base import Base
from app.memory.embedding_service import embedding_service
from app.models.ai_cache import AIResponseCache
from app.models.user import User
from app.services.response_cache_service import response_cache_service


class ResponseCacheServiceTests(unittest.IsolatedAsyncioTestCase):

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
            alice = User(username="cache_alice")
            bob = User(username="cache_bob")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

        self.settings_patches = [
            patch.object(settings, "AI_CACHE_ENABLED", True),
            patch.object(settings, "AI_CACHE_SEMANTIC_ENABLED", False),
            patch.object(settings, "AI_CACHE_TTL_SECONDS", 3600),
            patch.object(settings, "AI_CACHE_MAX_ENTRIES_PER_USER", 500),
        ]

        for settings_patch in self.settings_patches:
            settings_patch.start()
            self.addCleanup(settings_patch.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _store(self, query: str, response: str, user_id=None):
        async with self.SessionLocal() as session:
            stored = await response_cache_service.store(
                session=session,
                user_id=(
                    self.alice_id if user_id is None else user_id
                ),
                query=query,
                response=response,
                provider="groq",
                model="test-model",
                complexity="simple",
                routing_policy="groq_first",
            )

        return stored

    async def _lookup(self, query: str, user_id=None):
        async with self.SessionLocal() as session:
            result = await response_cache_service.lookup(
                session=session,
                user_id=(
                    self.alice_id if user_id is None else user_id
                ),
                query=query,
            )
            await session.commit()

        return result

    async def _count(self) -> int:
        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count(AIResponseCache.id))
                )
                or 0
            )

    async def test_exact_lookup_returns_a_stored_answer(self):

        self.assertTrue(await self._store("Hello there", "Cached answer"))

        cached = await self._lookup("  hello   THERE ")

        self.assertIsNotNone(cached)
        self.assertEqual(cached.response, "Cached answer")
        self.assertEqual(cached.provider, "groq")
        self.assertEqual(cached.model, "test-model")
        self.assertEqual(cached.hit_type, "exact")

    async def test_lookup_increments_hit_metadata(self):

        await self._store("Hello there", "Cached answer")
        await self._lookup("Hello there")

        async with self.SessionLocal() as session:
            entry = await session.scalar(select(AIResponseCache))

        self.assertEqual(entry.hit_count, 1)
        self.assertIsNotNone(entry.last_used_at)

    async def test_cache_is_user_scoped(self):

        await self._store("Hello there", "Alice answer")

        self.assertIsNone(
            await self._lookup("Hello there", user_id=self.bob_id)
        )

    async def test_expired_entries_are_not_returned(self):

        await self._store("Hello there", "Cached answer")

        async with self.SessionLocal() as session:
            entry = await session.scalar(select(AIResponseCache))
            entry.expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            await session.commit()

        self.assertIsNone(await self._lookup("Hello there"))

    async def test_per_user_cache_cap_evicts_oldest_entries(self):

        with patch.object(settings, "AI_CACHE_MAX_ENTRIES_PER_USER", 2):
            await self._store("first query", "one")
            await self._store("second query", "two")
            await self._store("third query", "three")

        self.assertEqual(await self._count(), 2)
        self.assertIsNone(await self._lookup("first query"))
        self.assertIsNotNone(await self._lookup("third query"))

    async def test_disabled_cache_never_stores_or_returns(self):

        with patch.object(settings, "AI_CACHE_ENABLED", False):
            self.assertFalse(
                await self._store("Hello there", "Cached answer")
            )
            self.assertIsNone(await self._lookup("Hello there"))

        self.assertEqual(await self._count(), 0)

    async def test_sqlite_cache_never_loads_the_embedding_model(self):

        embed_mock = AsyncMock(return_value=[1.0, 0.0, 0.0])

        with patch.object(
            embedding_service, "embed_query", new=embed_mock
        ):
            await self._store("Hello there", "Cached answer")
            await self._lookup("Hello there")

        embed_mock.assert_not_awaited()

    async def test_purge_expired_removes_only_expired_rows(self):

        await self._store("expired query", "old")

        async with self.SessionLocal() as session:
            entry = await session.scalar(select(AIResponseCache))
            entry.expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            await session.commit()

        async with self.SessionLocal() as session:
            removed = await response_cache_service.purge_expired(session)

        self.assertEqual(removed, 1)
        self.assertEqual(await self._count(), 0)


if __name__ == "__main__":
    unittest.main()
