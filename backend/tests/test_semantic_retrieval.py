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
from app.exceptions.memory_exceptions import (
    EmbeddingServiceError,
    EmbeddingValidationError,
    MemoryValidationError,
    SemanticRetrievalUnavailable,
)
from app.memory.embedding_service import embedding_service
from app.memory.memory_retriever import (
    cosine_similarity,
    memory_retriever,
)
from app.models.memory import Memory, MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


ALIGNED = [1.0, 0.0, 0.0]
MEDIUM = [0.8, 0.6, 0.0]
WEAK = [0.5, 0.8660254037844386, 0.0]
ORTHOGONAL = [0.0, 1.0, 0.0]
OPPOSITE = [-1.0, 0.0, 0.0]


class CosineSimilarityTests(unittest.TestCase):
    """Pure similarity calculations do not need a database."""

    def test_identical_vectors_have_similarity_one(self):
        self.assertAlmostEqual(cosine_similarity(ALIGNED, ALIGNED), 1.0)

    def test_orthogonal_vectors_have_similarity_zero(self):
        self.assertAlmostEqual(
            cosine_similarity(ALIGNED, ORTHOGONAL),
            0.0
        )

    def test_opposite_vectors_have_similarity_minus_one(self):
        self.assertAlmostEqual(cosine_similarity(ALIGNED, OPPOSITE), -1.0)

    def test_zero_magnitude_vector_returns_zero(self):
        self.assertEqual(
            cosine_similarity([0.0, 0.0, 0.0], ALIGNED),
            0.0
        )

    def test_dimension_mismatch_is_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])

    def test_empty_vector_is_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            cosine_similarity([], [])

    def test_missing_vector_is_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            cosine_similarity(None, ALIGNED)


class SemanticRetrievalTests(unittest.IsolatedAsyncioTestCase):
    """Exercise semantic retrieval against an isolated SQLite database."""

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
            alice = User(username="semantic_alice")
            bob = User(username="semantic_bob")

            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

    async def asyncTearDown(self):
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

    async def _semantic(
        self,
        query: str,
        query_vector: list[float] | None = None,
        category: str | None = None,
        limit: int = 5,
        threshold: float = 0.0,
        user_id: int | None = None,
        fallback: bool = False
    ):

        embed_mock = AsyncMock(
            return_value=ALIGNED if query_vector is None else query_vector
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=embed_mock
        ):
            async with self.SessionLocal() as session:
                return await memory_retriever.retrieve(
                    session=session,
                    user_id=user_id if user_id is not None else self.alice_id,
                    query=query,
                    category=category,
                    limit=limit,
                    mode="semantic",
                    similarity_threshold=threshold,
                    allow_keyword_fallback=fallback
                ), embed_mock

    # ------------------------------------------------------------------
    # 1. Semantic retrieval and ranking
    # ------------------------------------------------------------------

    async def test_semantic_retrieval_returns_most_relevant_first(self):

        await self._add_memory(
            "I want to become a machine learning engineer.",
            category="career",
            embedding=ALIGNED
        )
        await self._add_memory(
            "I enjoy reading science fiction.",
            category="preferences",
            embedding=MEDIUM
        )
        await self._add_memory(
            "I live in London.",
            category="personal",
            embedding=ORTHOGONAL
        )

        results, _embed_mock = await self._semantic(
            "What is my profession?",
            threshold=0.3
        )

        self.assertEqual(
            [item.content for item in results],
            [
                "I want to become a machine learning engineer.",
                "I enjoy reading science fiction."
            ]
        )

    async def test_scores_are_bounded_and_sorted_descending(self):

        await self._add_memory(
            "Alpha memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Beta memory.",
            category="general",
            embedding=MEDIUM
        )
        await self._add_memory(
            "Gamma memory.",
            category="general",
            embedding=WEAK
        )

        results, _embed_mock = await self._semantic(
            "anything",
            threshold=0.0
        )

        scores = [item.relevance_score for item in results]

        self.assertEqual(scores, sorted(scores, reverse=True))

        for score in scores:
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    async def test_query_is_embedded_once_with_the_original_query(self):

        await self._add_memory(
            "Alpha memory.",
            category="general",
            embedding=ALIGNED
        )

        _results, embed_mock = await self._semantic("find my career aim")

        embed_mock.assert_awaited_once_with("find my career aim")

    # ------------------------------------------------------------------
    # 2. Similarity threshold
    # ------------------------------------------------------------------

    async def test_similarity_threshold_excludes_low_scores(self):

        await self._add_memory(
            "Alpha memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Beta memory.",
            category="general",
            embedding=MEDIUM
        )

        strict, _ = await self._semantic("query", threshold=0.9)
        relaxed, _ = await self._semantic("query", threshold=0.7)

        self.assertEqual([item.content for item in strict], ["Alpha memory."])
        self.assertEqual(
            [item.content for item in relaxed],
            ["Alpha memory.", "Beta memory."]
        )

    async def test_default_similarity_threshold_is_applied(self):

        await self._add_memory(
            "Alpha memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Beta memory.",
            category="general",
            embedding=WEAK
        )

        embed_mock = AsyncMock(return_value=ALIGNED)

        with (
            patch.object(
                settings,
                "SEMANTIC_SIMILARITY_THRESHOLD",
                0.75
            ),
            patch.object(
                embedding_service,
                "embed_query",
                new=embed_mock
            )
        ):
            async with self.SessionLocal() as session:
                results = await memory_retriever.retrieve(
                    session=session,
                    user_id=self.alice_id,
                    query="query",
                    mode="semantic"
                )

        self.assertEqual([item.content for item in results], ["Alpha memory."])

    async def test_invalid_similarity_threshold_is_rejected(self):

        for threshold in (-0.1, 1.1):
            with self.subTest(threshold=threshold):
                with self.assertRaises(MemoryValidationError):
                    await self._semantic("query", threshold=threshold)

    # ------------------------------------------------------------------
    # 3. Status filtering
    # ------------------------------------------------------------------

    async def test_pending_memories_are_excluded(self):

        await self._add_memory(
            "Approved memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Pending memory.",
            category="general",
            status=MemoryStatus.PENDING,
            embedding=ALIGNED
        )

        results, _ = await self._semantic("query", threshold=0.0)

        self.assertEqual(
            [item.content for item in results],
            ["Approved memory."]
        )

    async def test_rejected_memories_are_excluded(self):

        await self._add_memory(
            "Approved memory.",
            category="general",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Rejected memory.",
            category="general",
            status=MemoryStatus.REJECTED,
            embedding=ALIGNED
        )

        results, _ = await self._semantic("query", threshold=0.0)

        self.assertEqual(
            [item.content for item in results],
            ["Approved memory."]
        )

    # ------------------------------------------------------------------
    # 4. User isolation
    # ------------------------------------------------------------------

    async def test_user_isolation(self):

        await self._add_memory(
            "Alice secret career plan.",
            category="career",
            user_id=self.alice_id,
            embedding=ALIGNED
        )
        await self._add_memory(
            "Bob own career plan.",
            category="career",
            user_id=self.bob_id,
            embedding=ALIGNED
        )

        results, _ = await self._semantic(
            "query",
            user_id=self.bob_id,
            threshold=0.0
        )

        self.assertEqual(
            [item.content for item in results],
            ["Bob own career plan."]
        )

    async def test_other_users_memories_never_leak_when_no_own_embeddings(self):

        await self._add_memory(
            "Alice secret career plan.",
            category="career",
            user_id=self.alice_id,
            embedding=ALIGNED
        )

        with self.assertRaises(SemanticRetrievalUnavailable):
            await self._semantic(
                "query",
                user_id=self.bob_id,
                threshold=0.0
            )

    # ------------------------------------------------------------------
    # 5. Category filtering and limits
    # ------------------------------------------------------------------

    async def test_category_filter_returns_only_matching_category(self):

        await self._add_memory(
            "Career memory.",
            category="career",
            embedding=ALIGNED
        )
        await self._add_memory(
            "Education memory.",
            category="education",
            embedding=ALIGNED
        )

        results, _ = await self._semantic(
            "query",
            category="education",
            threshold=0.0
        )

        self.assertEqual(
            [item.content for item in results],
            ["Education memory."]
        )

    async def test_category_filter_is_case_insensitive(self):

        await self._add_memory(
            "Education memory.",
            category="Education",
            embedding=ALIGNED
        )

        results, _ = await self._semantic(
            "query",
            category="  EDUCATION  ",
            threshold=0.0
        )

        self.assertEqual(
            [item.content for item in results],
            ["Education memory."]
        )

    async def test_category_filter_accepts_singular_alias(self):

        await self._add_memory(
            "Project memory.",
            category="projects",
            embedding=ALIGNED
        )

        results, _ = await self._semantic(
            "query",
            category="project",
            threshold=0.0
        )

        self.assertEqual(
            [item.content for item in results],
            ["Project memory."]
        )

    async def test_maximum_result_limit(self):

        for index in range(4):
            await self._add_memory(
                f"Semantic memory {index}.",
                category="general",
                embedding=ALIGNED
            )

        results, _ = await self._semantic(
            "query",
            limit=2,
            threshold=0.0
        )

        self.assertEqual(len(results), 2)

    async def test_limit_is_capped_at_maximum(self):

        for index in range(3):
            await self._add_memory(
                f"Semantic memory {index}.",
                category="general",
                embedding=ALIGNED
            )

        results, _ = await self._semantic(
            "query",
            limit=10000,
            threshold=0.0
        )

        self.assertEqual(len(results), 3)

    # ------------------------------------------------------------------
    # 6. Missing embeddings and embedding failures
    # ------------------------------------------------------------------

    async def test_missing_embeddings_raise_semantic_unavailable(self):

        await self._add_memory(
            "Approved but unindexed memory.",
            category="general"
        )

        with self.assertRaises(SemanticRetrievalUnavailable):
            await self._semantic("query")

    async def test_embedding_failure_is_raised_in_strict_mode(self):

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
            async with self.SessionLocal() as session:
                with self.assertRaises(EmbeddingServiceError):
                    await memory_retriever.retrieve(
                        session=session,
                        user_id=self.alice_id,
                        query="query",
                        mode="semantic",
                        similarity_threshold=0.0
                    )

    async def test_secret_like_content_is_never_returned(self):

        async with self.SessionLocal() as session:
            secret = Memory(
                user_id=self.alice_id,
                content="password: supersecret123",
                category="general",
                status=MemoryStatus.APPROVED,
                embedding=ALIGNED,
                embedding_model=embedding_service.model_name
            )
            session.add(secret)
            await session.commit()

        await self._add_memory(
            "Safe approved memory.",
            category="general",
            embedding=ALIGNED
        )

        results, _ = await self._semantic("query", threshold=0.0)

        contents = [item.content for item in results]

        self.assertIn("Safe approved memory.", contents)
        self.assertNotIn("password: supersecret123", contents)

    # ------------------------------------------------------------------
    # 7. Keyword mode and fallback
    # ------------------------------------------------------------------

    async def test_keyword_mode_is_the_default(self):

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
            async with self.SessionLocal() as session:
                results = await memory_retriever.retrieve(
                    session=session,
                    user_id=self.alice_id,
                    query="vocational ambition"
                )

        self.assertEqual(results, [])

    async def test_explicit_keyword_mode_ignores_embeddings(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            embedding=ORTHOGONAL
        )

        embed_mock = AsyncMock(return_value=ALIGNED)

        with patch.object(
            embedding_service,
            "embed_query",
            new=embed_mock
        ):
            async with self.SessionLocal() as session:
                results = await memory_retriever.retrieve(
                    session=session,
                    user_id=self.alice_id,
                    query="career goal",
                    mode="keyword"
                )

        embed_mock.assert_not_awaited()
        self.assertEqual(
            [item.content for item in results],
            ["My career goal is to become an AI/ML Engineer."]
        )

    async def test_keyword_fallback_when_no_embeddings_exist(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        results, _embed_mock = await self._semantic(
            "career goal",
            fallback=True
        )

        self.assertEqual(
            [item.content for item in results],
            ["My career goal is to become an AI/ML Engineer."]
        )

    async def test_keyword_fallback_when_embedding_fails(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            embedding=ALIGNED
        )

        with patch.object(
            embedding_service,
            "embed_query",
            new=AsyncMock(
                side_effect=EmbeddingServiceError("model failed")
            )
        ):
            async with self.SessionLocal() as session:
                results = await memory_retriever.retrieve(
                    session=session,
                    user_id=self.alice_id,
                    query="career goal",
                    mode="semantic",
                    similarity_threshold=0.0,
                    allow_keyword_fallback=True
                )

        self.assertEqual(
            [item.content for item in results],
            ["My career goal is to become an AI/ML Engineer."]
        )

    async def test_empty_semantic_results_do_not_trigger_fallback(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            embedding=ORTHOGONAL
        )

        results, _embed_mock = await self._semantic(
            "career goal",
            query_vector=ALIGNED,
            threshold=0.75,
            fallback=True
        )

        self.assertEqual(results, [])

    async def test_unsupported_mode_is_rejected(self):

        with self.assertRaises(MemoryValidationError):
            async with self.SessionLocal() as session:
                await memory_retriever.retrieve(
                    session=session,
                    user_id=self.alice_id,
                    query="query",
                    mode="hybrid"
                )

    async def test_missing_embeddings_fallback_preserves_validation(self):

        with self.assertRaises(MemoryValidationError):
            await self._semantic("   ", fallback=True)


if __name__ == "__main__":
    unittest.main()
