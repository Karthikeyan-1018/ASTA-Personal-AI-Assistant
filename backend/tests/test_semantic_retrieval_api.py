import unittest
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import get_current_user_id
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.memory_exceptions import EmbeddingServiceError
from app.main import app
from app.memory.embedding_service import embedding_service
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


ALIGNED = [1.0, 0.0, 0.0]
WEAK = [0.5, 0.8660254037844386, 0.0]


class SemanticRetrievalAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise POST /api/memories/retrieve with semantic mode."""

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
            alice = User(username="semantic_api_alice")
            bob = User(username="semantic_api_bob")

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

        self.override_alice = override_alice
        self.override_bob = override_bob

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_alice

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

    async def _retrieve(self, payload: dict):
        return await self.client.post(
            "/api/memories/retrieve",
            json=payload
        )

    # ------------------------------------------------------------------
    # 1. Semantic retrieval through the API
    # ------------------------------------------------------------------

    async def test_semantic_mode_returns_ranked_memories(self):

        await self._add_memory(
            "I want to become a machine learning engineer.",
            category="career",
            embedding=ALIGNED
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(return_value=ALIGNED)
        ):
            response = await self._retrieve({
                "query": "What is my profession?",
                "mode": "semantic"
            })

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()

        self.assertEqual(body["total"], 1)
        self.assertEqual(
            body["memories"][0]["content"],
            "I want to become a machine learning engineer."
        )
        self.assertGreaterEqual(
            body["memories"][0]["relevance_score"],
            0.75
        )
        self.assertLessEqual(
            body["memories"][0]["relevance_score"],
            1.0
        )

    async def test_semantic_mode_applies_the_similarity_threshold(self):

        await self._add_memory(
            "Strong semantic memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Weak semantic memory.",
            category="general",
            embedding=WEAK
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(return_value=ALIGNED)
        ):
            response = await self._retrieve({
                "query": "query",
                "mode": "semantic"
            })

        self.assertEqual(response.status_code, 200, response.text)

        contents = [
            item["content"]
            for item in response.json()["memories"]
        ]

        self.assertEqual(contents, ["Strong semantic memory."])

    async def test_default_mode_remains_keyword(self):

        await self._add_memory(
            "I want to become a machine learning engineer.",
            category="career",
            embedding=ALIGNED
        )

        embed_mock = AsyncMock(return_value=ALIGNED)

        with patch.object(
            embedding_service,
            "embed_query",
            new=embed_mock
        ):
            response = await self._retrieve({
                "query": "vocational ambition"
            })

        embed_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"memories": [], "total": 0}
        )

    # ------------------------------------------------------------------
    # 2. Status and user isolation
    # ------------------------------------------------------------------

    async def test_semantic_mode_excludes_pending_and_rejected(self):

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

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(return_value=ALIGNED)
        ):
            response = await self._retrieve({
                "query": "query",
                "mode": "semantic"
            })

        self.assertEqual(response.status_code, 200, response.text)

        contents = [
            item["content"]
            for item in response.json()["memories"]
        ]

        self.assertEqual(contents, ["Approved semantic memory."])

    async def test_semantic_mode_user_isolation(self):

        await self._add_memory(
            "Alice embedded memory.",
            category="general",
            user_id=self.alice_id,
            embedding=ALIGNED
        )
        await self._add_memory(
            "Bob embedded memory.",
            category="general",
            user_id=self.bob_id,
            embedding=ALIGNED
        )

        app.dependency_overrides[get_current_user_id] = self.override_bob

        try:
            with patch.object(
                embedding_service,
                "embed_query",
                new=AsyncMock(return_value=ALIGNED)
            ):
                response = await self._retrieve({
                    "query": "query",
                    "mode": "semantic"
                })
        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

        self.assertEqual(response.status_code, 200, response.text)

        contents = [
            item["content"]
            for item in response.json()["memories"]
        ]

        self.assertEqual(contents, ["Bob embedded memory."])
        self.assertNotIn("Alice embedded memory.", contents)

    async def test_semantic_category_filter(self):

        await self._add_memory(
            "Career semantic memory.",
            category="career",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Education semantic memory.",
            category="education",
            embedding=ALIGNED
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(return_value=ALIGNED)
        ):
            response = await self._retrieve({
                "query": "query",
                "mode": "semantic",
                "category": "education"
            })

        self.assertEqual(response.status_code, 200, response.text)

        contents = [
            item["content"]
            for item in response.json()["memories"]
        ]

        self.assertEqual(contents, ["Education semantic memory."])

    # ------------------------------------------------------------------
    # 3. Unavailable semantic retrieval
    # ------------------------------------------------------------------

    async def test_semantic_mode_without_embeddings_returns_503(self):

        await self._add_memory(
            "Approved but unindexed memory.",
            category="general"
        )

        response = await self._retrieve({
            "query": "query",
            "mode": "semantic"
        })

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Semantic memory retrieval is currently unavailable."
        )

    async def test_semantic_embedding_failure_returns_503(self):

        await self._add_memory(
            "Approved and indexed memory.",
            category="general",
            embedding=ALIGNED
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(
                side_effect=EmbeddingServiceError("model failed")
            )
        ):
            response = await self._retrieve({
                "query": "query",
                "mode": "semantic"
            })

        self.assertEqual(response.status_code, 503)

    # ------------------------------------------------------------------
    # 4. Validation
    # ------------------------------------------------------------------

    async def test_invalid_mode_returns_422(self):

        response = await self._retrieve({
            "query": "query",
            "mode": "hybrid"
        })

        self.assertEqual(response.status_code, 422)

    async def test_semantic_empty_query_returns_400(self):

        response = await self._retrieve({
            "query": "   ",
            "mode": "semantic"
        })

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
