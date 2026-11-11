import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.core.config import settings
from app.database.base import Base
from app.exceptions.memory_exceptions import SemanticRetrievalUnavailable
from app.memory.embedding_service import embedding_service
from app.memory.memory_retriever import (
    MemoryRetriever,
    RetrievedMemory,
    RetrievalMode,
    memory_retriever,
)
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


def item(content: str, score: float, category: str = "general"):
    return RetrievedMemory(
        content=content,
        category=category,
        relevance_score=score,
    )


class HybridBlendingTests(unittest.IsolatedAsyncioTestCase):

    async def test_semantic_and_keyword_candidates_are_blended(self):

        calls = []

        async def fake_retrieve(*, mode=RetrievalMode.KEYWORD, **kwargs):
            mode_value = (
                mode.value if isinstance(mode, RetrievalMode) else str(mode)
            )
            calls.append(mode_value)

            if mode_value == RetrievalMode.SEMANTIC.value:
                return [
                    item("shared memory", 1.0),
                    item("semantic only", 1.0),
                ]

            return [
                item("shared memory", 1.0),
                item("keyword only", 1.0),
            ]

        with patch.object(
            memory_retriever, "retrieve", new=AsyncMock(
                side_effect=fake_retrieve
            )
        ):
            results = await memory_retriever.retrieve_hybrid(
                session=object(),
                user_id=1,
                query="career goal",
                limit=5,
            )

        self.assertEqual(calls, ["semantic", "keyword"])

        scores = {result.content: result.relevance_score for result in results}

        self.assertAlmostEqual(scores["shared memory"], 1.0)
        self.assertAlmostEqual(scores["semantic only"], 0.7)
        self.assertAlmostEqual(scores["keyword only"], 0.3)

    async def test_results_are_deduplicated_case_insensitively(self):

        async def fake_retrieve(*, mode=RetrievalMode.KEYWORD, **kwargs):
            mode_value = (
                mode.value if isinstance(mode, RetrievalMode) else str(mode)
            )

            if mode_value == RetrievalMode.SEMANTIC.value:
                return [item("Career Goal", 0.9)]

            return [item("career goal", 0.8)]

        with patch.object(
            memory_retriever, "retrieve", new=AsyncMock(
                side_effect=fake_retrieve
            )
        ):
            results = await memory_retriever.retrieve_hybrid(
                session=object(),
                user_id=1,
                query="career goal",
                limit=5,
            )

        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].relevance_score, 0.87)

    async def test_minimum_score_filters_weak_candidates(self):

        async def fake_retrieve(*, mode=RetrievalMode.KEYWORD, **kwargs):
            mode_value = (
                mode.value if isinstance(mode, RetrievalMode) else str(mode)
            )

            if mode_value == RetrievalMode.SEMANTIC.value:
                return [item("semantic only", 1.0)]

            return [item("keyword only", 1.0)]

        with patch.object(
            memory_retriever, "retrieve", new=AsyncMock(
                side_effect=fake_retrieve
            )
        ), patch.object(
            settings, "AI_MEMORY_MIN_HYBRID_SCORE", 0.5
        ):
            results = await memory_retriever.retrieve_hybrid(
                session=object(),
                user_id=1,
                query="career goal",
                limit=5,
            )

        self.assertEqual(
            [result.content for result in results],
            ["semantic only"],
        )

    async def test_semantic_unavailability_falls_back_to_keyword(self):

        calls = []

        async def fake_retrieve(*, mode=RetrievalMode.KEYWORD, **kwargs):
            mode_value = (
                mode.value if isinstance(mode, RetrievalMode) else str(mode)
            )
            calls.append(mode_value)

            if mode_value == RetrievalMode.SEMANTIC.value:
                raise SemanticRetrievalUnavailable("no embeddings")

            return [item("keyword memory", 1.0)]

        with patch.object(
            memory_retriever, "retrieve", new=AsyncMock(
                side_effect=fake_retrieve
            )
        ):
            results = await memory_retriever.retrieve_hybrid(
                session=object(),
                user_id=1,
                query="career goal",
                limit=5,
            )

        self.assertEqual(calls, ["semantic", "keyword"])
        self.assertEqual(
            [result.content for result in results],
            ["keyword memory"],
        )

    async def test_unexpected_database_failure_propagates(self):

        retrieve_mock = AsyncMock(
            side_effect=RuntimeError("database unavailable")
        )

        with patch.object(
            memory_retriever, "retrieve", new=retrieve_mock
        ):
            with self.assertRaises(RuntimeError):
                await memory_retriever.retrieve_hybrid(
                    session=object(),
                    user_id=1,
                    query="career goal",
                    limit=5,
                )

        retrieve_mock.assert_awaited_once()

    async def test_disabled_semantic_retrieval_uses_keyword_only(self):

        calls = []

        async def fake_retrieve(*, mode=RetrievalMode.KEYWORD, **kwargs):
            mode_value = (
                mode.value if isinstance(mode, RetrievalMode) else str(mode)
            )
            calls.append(mode_value)
            return [item("keyword memory", 1.0)]

        embed_mock = AsyncMock(return_value=[1.0, 0.0, 0.0])

        with patch.object(
            settings, "SEMANTIC_RETRIEVAL_ENABLED", False
        ), patch.object(
            memory_retriever, "retrieve", new=AsyncMock(
                side_effect=fake_retrieve
            )
        ), patch.object(
            embedding_service, "embed_query", new=embed_mock
        ):
            results = await memory_retriever.retrieve_hybrid(
                session=object(),
                user_id=1,
                query="career goal",
                limit=5,
            )

        self.assertEqual(calls, ["keyword"])
        self.assertEqual(
            [result.content for result in results],
            ["keyword memory"],
        )
        embed_mock.assert_not_awaited()


class HybridRetrievalDatabaseTests(unittest.IsolatedAsyncioTestCase):
    """Verify hybrid retrieval keeps the approved-only rule end to end."""

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
            user = User(username="hybrid_user")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            self.user_id = user.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _add_memory(
        self,
        content: str,
        status: MemoryStatus,
        embedding: list[float],
    ):

        async with self.SessionLocal() as session:
            memory = await memory_service.create_memory(
                session=session,
                user_id=self.user_id,
                content=content,
                category="general",
            )

            if status == MemoryStatus.APPROVED:
                await memory_service.approve_memory(
                    session=session,
                    user_id=self.user_id,
                    memory_id=memory.id,
                )

            memory.embedding = embedding
            memory.embedding_model = embedding_service.model_name
            await session.commit()

    async def test_only_approved_memories_are_returned(self):

        await self._add_memory(
            "Approved career memory.",
            status=MemoryStatus.APPROVED,
            embedding=[1.0, 0.0, 0.0],
        )
        await self._add_memory(
            "Pending career memory.",
            status=MemoryStatus.PENDING,
            embedding=[1.0, 0.0, 0.0],
        )
        await self._add_memory(
            "Rejected career memory.",
            status=MemoryStatus.REJECTED,
            embedding=[1.0, 0.0, 0.0],
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(return_value=[1.0, 0.0, 0.0]),
        ):
            async with self.SessionLocal() as session:
                results = await MemoryRetriever().retrieve_hybrid(
                    session=session,
                    user_id=self.user_id,
                    query="career memory",
                    limit=10,
                    similarity_threshold=0.0,
                )

        contents = {result.content for result in results}

        self.assertIn("Approved career memory.", contents)
        self.assertNotIn("Pending career memory.", contents)
        self.assertNotIn("Rejected career memory.", contents)


if __name__ == "__main__":
    unittest.main()
