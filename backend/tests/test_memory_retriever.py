import unittest

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.database.base import Base
from app.exceptions.memory_exceptions import MemoryValidationError
from app.memory.memory_retriever import memory_retriever
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


class MemoryRetrieverTests(unittest.IsolatedAsyncioTestCase):
    """Exercise MemoryRetriever against an isolated in-memory database."""

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
            alice = User(username="retriever_alice")
            bob = User(username="retriever_bob")

            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _add_memory(
        self,
        content: str,
        category: str = "general",
        status: MemoryStatus = MemoryStatus.APPROVED,
        user_id: int | None = None
    ):

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

            return memory.id

    async def _retrieve(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
        user_id: int | None = None
    ):

        async with self.SessionLocal() as session:
            return await memory_retriever.retrieve(
                session=session,
                user_id=user_id if user_id is not None else self.alice_id,
                query=query,
                category=category,
                limit=limit
            )

    # ------------------------------------------------------------------
    # Relevance by category
    # ------------------------------------------------------------------

    async def test_retrieves_relevant_career_memory(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )
        await self._add_memory(
            "I like pizza and movies.",
            category="preferences"
        )

        results = await self._retrieve("What is my career goal?")

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].content,
            "My career goal is to become an AI/ML Engineer."
        )
        self.assertEqual(results[0].category, "career")
        self.assertGreater(results[0].relevance_score, 0.0)

    async def test_retrieves_relevant_education_memory(self):

        await self._add_memory(
            "I studied computer science at Stanford University.",
            category="education"
        )
        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        results = await self._retrieve("What did I study?")

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].content,
            "I studied computer science at Stanford University."
        )
        self.assertEqual(results[0].category, "education")

    async def test_retrieves_relevant_project_memory(self):

        await self._add_memory(
            "I am building ASTA, a personal AI assistant.",
            category="projects"
        )
        await self._add_memory(
            "I studied computer science at Stanford University.",
            category="education"
        )

        results = await self._retrieve("What project am I building?")

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].content,
            "I am building ASTA, a personal AI assistant."
        )
        self.assertEqual(results[0].category, "projects")

    async def test_returns_multiple_relevant_memories_ranked(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )
        await self._add_memory(
            "I want to work as a data scientist.",
            category="career"
        )
        await self._add_memory(
            "I like pizza.",
            category="preferences"
        )

        results = await self._retrieve("career goals")

        self.assertEqual(len(results), 2)
        self.assertEqual(
            results[0].content,
            "My career goal is to become an AI/ML Engineer."
        )
        self.assertEqual(
            results[1].content,
            "I want to work as a data scientist."
        )
        self.assertGreaterEqual(
            results[0].relevance_score,
            results[1].relevance_score
        )

    async def test_irrelevant_memories_are_excluded(self):

        await self._add_memory(
            "I like pizza and movies.",
            category="preferences"
        )
        await self._add_memory(
            "I live in London.",
            category="personal"
        )

        results = await self._retrieve("career goals")

        self.assertEqual(results, [])

    async def test_no_matching_memories_returns_empty_list(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        results = await self._retrieve("quantum physics")

        self.assertEqual(results, [])

    async def test_query_with_only_stop_words_returns_empty_list(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        results = await self._retrieve("what is it?")

        self.assertEqual(results, [])

    # ------------------------------------------------------------------
    # Status rules
    # ------------------------------------------------------------------

    async def test_pending_memories_are_excluded(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            status=MemoryStatus.PENDING
        )
        await self._add_memory(
            "I work as a data scientist.",
            category="career",
            status=MemoryStatus.APPROVED
        )

        results = await self._retrieve("career goal")

        self.assertEqual(
            [item.content for item in results],
            ["I work as a data scientist."]
        )

    async def test_rejected_memories_are_excluded(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            status=MemoryStatus.REJECTED
        )
        await self._add_memory(
            "I work as a data scientist.",
            category="career",
            status=MemoryStatus.APPROVED
        )

        results = await self._retrieve("career goal")

        self.assertEqual(
            [item.content for item in results],
            ["I work as a data scientist."]
        )

    # ------------------------------------------------------------------
    # User isolation
    # ------------------------------------------------------------------

    async def test_user_isolation(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            user_id=self.alice_id
        )

        results = await self._retrieve(
            "career goal",
            user_id=self.bob_id
        )

        self.assertEqual(results, [])

    # ------------------------------------------------------------------
    # Category filtering
    # ------------------------------------------------------------------

    async def test_category_filter_returns_only_matching_category(self):

        await self._add_memory(
            "My career goal is to become an engineer.",
            category="career"
        )
        await self._add_memory(
            "My goal is to finish my degree.",
            category="education"
        )

        results = await self._retrieve(
            "goal",
            category="education"
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].content,
            "My goal is to finish my degree."
        )
        self.assertEqual(results[0].category, "education")

    async def test_category_filter_is_case_insensitive(self):

        await self._add_memory(
            "My goal is to finish my degree.",
            category="Education"
        )

        results = await self._retrieve(
            "goal",
            category="  EDUCATION  "
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, "Education")

    async def test_category_filter_accepts_singular_known_alias(self):

        await self._add_memory(
            "My project is ASTA.",
            category="projects"
        )

        results = await self._retrieve(
            "project",
            category="project"
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, "projects")

    async def test_category_filter_does_not_bypass_approved_rule(self):

        await self._add_memory(
            "My goal is to finish my degree.",
            category="education",
            status=MemoryStatus.PENDING
        )

        results = await self._retrieve(
            "goal",
            category="education"
        )

        self.assertEqual(results, [])

    # ------------------------------------------------------------------
    # Limits and validation
    # ------------------------------------------------------------------

    async def test_maximum_result_limit(self):

        for index in range(4):
            await self._add_memory(
                f"Career goal number {index}.",
                category="career"
            )

        results = await self._retrieve("career goal", limit=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(
            [item.content for item in results],
            [
                "Career goal number 0.",
                "Career goal number 1."
            ]
        )

    async def test_limit_is_capped_at_maximum(self):

        for index in range(3):
            await self._add_memory(
                f"Career goal number {index}.",
                category="career"
            )

        results = await self._retrieve("career goal", limit=10000)

        self.assertEqual(len(results), 3)

    async def test_empty_query_is_rejected(self):

        for query in ("", "   ", "\n\t "):

            with self.subTest(query=query):

                with self.assertRaises(MemoryValidationError):
                    await self._retrieve(query)

    async def test_invalid_limit_is_rejected(self):

        with self.assertRaises(MemoryValidationError):
            await self._retrieve("career goal", limit=0)

    async def test_empty_category_filter_is_rejected(self):

        with self.assertRaises(MemoryValidationError):
            await self._retrieve("career goal", category="   ")

    async def test_oversized_query_is_rejected(self):

        with self.assertRaises(MemoryValidationError):
            await self._retrieve("a" * 10001)

    # ------------------------------------------------------------------
    # Determinism and scoring
    # ------------------------------------------------------------------

    async def test_ranking_is_deterministic(self):

        await self._add_memory(
            "Career goal alpha.",
            category="career"
        )
        await self._add_memory(
            "Career goal beta.",
            category="career"
        )
        await self._add_memory(
            "Career goal gamma.",
            category="career"
        )

        first = await self._retrieve("career goal")
        second = await self._retrieve("career goal")

        self.assertEqual(
            [(item.content, item.relevance_score) for item in first],
            [(item.content, item.relevance_score) for item in second]
        )

    async def test_scores_are_bounded_and_sorted(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )
        await self._add_memory(
            "I want to work as a data scientist.",
            category="career"
        )
        await self._add_memory(
            "I like pizza.",
            category="preferences"
        )

        results = await self._retrieve("career goal")

        scores = [item.relevance_score for item in results]

        self.assertEqual(scores, sorted(scores, reverse=True))

        for score in scores:
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
