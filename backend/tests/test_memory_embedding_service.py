import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.database.base import Base
from app.exceptions.memory_exceptions import EmbeddingServiceError
from app.memory.embedding_service import embedding_service
from app.models.memory import Memory, MemoryStatus
from app.models.user import User
from app.services.memory_embedding_service import memory_embedding_service
from app.services.memory_service import memory_service


TEST_VECTOR = [1.0, 0.0, 0.0]
TEST_MODEL = embedding_service.model_name


class MemoryEmbeddingServiceTests(unittest.IsolatedAsyncioTestCase):
    """Exercise indexing, clearing, and backfill with a mocked model."""

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
            alice = User(username="embedding_alice")
            session.add(alice)
            await session.commit()
            await session.refresh(alice)
            self.alice_id = alice.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _create_memory(
        self,
        content: str,
        category: str = "general",
        status: MemoryStatus = MemoryStatus.APPROVED
    ) -> int:

        async with self.SessionLocal() as session:
            memory = await memory_service.create_memory(
                session=session,
                user_id=self.alice_id,
                content=content,
                category=category
            )

            if status == MemoryStatus.APPROVED:
                await memory_service.approve_memory(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=memory.id
                )

            elif status == MemoryStatus.REJECTED:
                await memory_service.reject_memory(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=memory.id
                )

            return memory.id

    async def _load(self, memory_id: int) -> Memory:
        async with self.SessionLocal() as session:
            memory = await session.get(Memory, memory_id)
            self.assertIsNotNone(memory)
            return memory

    async def _ensure(self, memory_id: int, force: bool = False) -> bool:
        async with self.SessionLocal() as session:
            memory = await session.get(Memory, memory_id)

            return await memory_embedding_service.ensure_embedding(
                session=session,
                memory=memory,
                force=force
            )

    # ------------------------------------------------------------------
    # 1. Approved memory indexing
    # ------------------------------------------------------------------

    async def test_approved_memory_is_indexed(self):

        memory_id = await self._create_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            created = await self._ensure(memory_id)

        self.assertTrue(created)
        embed_mock.assert_awaited_once()

        memory = await self._load(memory_id)

        self.assertEqual(memory.embedding, TEST_VECTOR)
        self.assertEqual(memory.embedding_model, TEST_MODEL)
        self.assertIsNotNone(memory.embedding_updated_at)

    async def test_embedding_uses_passage_method_only(self):

        memory_id = await self._create_memory(
            "I studied computer science at Stanford University.",
            category="education"
        )

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            await self._ensure(memory_id)

        embed_mock.assert_awaited_once_with(
            "I studied computer science at Stanford University."
        )

    # ------------------------------------------------------------------
    # 2. Only approved memories are indexed
    # ------------------------------------------------------------------

    async def test_pending_memory_is_not_indexed(self):

        memory_id = await self._create_memory(
            "My career goal is to become a manager.",
            category="career",
            status=MemoryStatus.PENDING
        )

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            created = await self._ensure(memory_id)

        self.assertFalse(created)
        embed_mock.assert_not_awaited()

        memory = await self._load(memory_id)

        self.assertIsNone(memory.embedding)
        self.assertIsNone(memory.embedding_model)

    async def test_rejected_memory_is_not_indexed(self):

        memory_id = await self._create_memory(
            "My career goal is to become a pilot.",
            category="career",
            status=MemoryStatus.REJECTED
        )

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            created = await self._ensure(memory_id)

        self.assertFalse(created)
        embed_mock.assert_not_awaited()

        memory = await self._load(memory_id)

        self.assertIsNone(memory.embedding)

    async def test_rejection_clears_an_existing_embedding(self):

        memory_id = await self._create_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(return_value=TEST_VECTOR)
        ):
            await self._ensure(memory_id)

        async with self.SessionLocal() as session:
            await memory_service.reject_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=memory_id
            )

        memory = await self._load(memory_id)

        self.assertEqual(memory.status, MemoryStatus.REJECTED)
        self.assertIsNone(memory.embedding)
        self.assertIsNone(memory.embedding_model)
        self.assertIsNone(memory.embedding_updated_at)

    # ------------------------------------------------------------------
    # 3. Avoid unnecessary regeneration
    # ------------------------------------------------------------------

    async def test_current_embedding_is_not_regenerated(self):

        memory_id = await self._create_memory(
            "I live in London.",
            category="personal"
        )

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            first = await self._ensure(memory_id)
            second = await self._ensure(memory_id)

        self.assertTrue(first)
        self.assertFalse(second)
        embed_mock.assert_awaited_once()

    async def test_stale_model_embedding_is_regenerated(self):

        memory_id = await self._create_memory(
            "I live in London.",
            category="personal"
        )

        async with self.SessionLocal() as session:
            memory = await session.get(Memory, memory_id)
            memory.embedding = TEST_VECTOR
            memory.embedding_model = "an-older-embedding-model"
            await session.commit()

        embed_mock = AsyncMock(return_value=[0.0, 1.0, 0.0])

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            created = await self._ensure(memory_id)

        self.assertTrue(created)
        embed_mock.assert_awaited_once()

        memory = await self._load(memory_id)

        self.assertEqual(memory.embedding, [0.0, 1.0, 0.0])
        self.assertEqual(memory.embedding_model, TEST_MODEL)

    async def test_force_regenerates_a_current_embedding(self):

        memory_id = await self._create_memory(
            "I live in London.",
            category="personal"
        )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(return_value=TEST_VECTOR)
        ):
            await self._ensure(memory_id)

        embed_mock = AsyncMock(return_value=[0.0, 0.0, 1.0])

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            created = await self._ensure(memory_id, force=True)

        self.assertTrue(created)
        embed_mock.assert_awaited_once()

        memory = await self._load(memory_id)

        self.assertEqual(memory.embedding, [0.0, 0.0, 1.0])

    # ------------------------------------------------------------------
    # 4. Failure handling
    # ------------------------------------------------------------------

    async def test_failed_embedding_is_not_marked_successful(self):

        memory_id = await self._create_memory(
            "I live in London.",
            category="personal"
        )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(
                side_effect=EmbeddingServiceError("model failed")
            )
        ):
            with self.assertRaises(EmbeddingServiceError):
                await self._ensure(memory_id)

        memory = await self._load(memory_id)

        self.assertIsNone(memory.embedding)
        self.assertIsNone(memory.embedding_model)
        self.assertIsNone(memory.embedding_updated_at)

    # ------------------------------------------------------------------
    # 5. Backfill
    # ------------------------------------------------------------------

    async def test_backfill_indexes_only_approved_memories(self):

        approved_one = await self._create_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )
        approved_two = await self._create_memory(
            "I studied computer science.",
            category="education"
        )
        pending = await self._create_memory(
            "A pending fact.",
            category="general",
            status=MemoryStatus.PENDING
        )
        rejected = await self._create_memory(
            "A rejected fact.",
            category="general",
            status=MemoryStatus.REJECTED
        )

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            async with self.SessionLocal() as session:
                result = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=10
                )

        self.assertEqual(result.indexed, 2)
        self.assertEqual(result.failed, 0)
        self.assertEqual(embed_mock.await_count, 2)

        for memory_id in (approved_one, approved_two):
            self.assertEqual((await self._load(memory_id)).embedding, TEST_VECTOR)

        for memory_id in (pending, rejected):
            self.assertIsNone((await self._load(memory_id)).embedding)

    async def test_backfill_is_idempotent(self):

        memory_ids = [
            await self._create_memory(
                f"Approved fact {index}.",
                category="general"
            )
            for index in range(3)
        ]

        embed_mock = AsyncMock(return_value=TEST_VECTOR)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=embed_mock
        ):
            async with self.SessionLocal() as session:
                first = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=2
                )

            async with self.SessionLocal() as session:
                second = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=2
                )

        self.assertEqual(first.indexed, 3)
        self.assertEqual(second.indexed, 0)
        self.assertEqual(second.skipped, 0)
        self.assertEqual(embed_mock.await_count, 3)

        for memory_id in memory_ids:
            self.assertEqual((await self._load(memory_id)).embedding, TEST_VECTOR)

    async def test_backfill_does_not_change_approval_status(self):

        approved = await self._create_memory(
            "An approved fact.",
            category="general"
        )
        pending = await self._create_memory(
            "A pending fact.",
            category="general",
            status=MemoryStatus.PENDING
        )
        rejected = await self._create_memory(
            "A rejected fact.",
            category="general",
            status=MemoryStatus.REJECTED
        )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(return_value=TEST_VECTOR)
        ):
            async with self.SessionLocal() as session:
                await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=10
                )

        self.assertEqual(
            (await self._load(approved)).status,
            MemoryStatus.APPROVED
        )
        self.assertEqual(
            (await self._load(pending)).status,
            MemoryStatus.PENDING
        )
        self.assertEqual(
            (await self._load(rejected)).status,
            MemoryStatus.REJECTED
        )

    async def test_backfill_retries_failed_records_on_next_run(self):

        memory_id = await self._create_memory(
            "An approved fact.",
            category="general"
        )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(
                side_effect=EmbeddingServiceError("model failed")
            )
        ):
            async with self.SessionLocal() as session:
                failed_run = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=10
                )

        self.assertEqual(failed_run.failed, 1)
        self.assertEqual(failed_run.indexed, 0)
        self.assertIsNone((await self._load(memory_id)).embedding)

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(return_value=TEST_VECTOR)
        ):
            async with self.SessionLocal() as session:
                retry_run = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=10
                )

        self.assertEqual(retry_run.failed, 0)
        self.assertEqual(retry_run.indexed, 1)
        self.assertEqual((await self._load(memory_id)).embedding, TEST_VECTOR)

    async def test_backfill_processes_records_in_batches(self):

        for index in range(5):
            await self._create_memory(
                f"Approved fact {index}.",
                category="general"
            )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(return_value=TEST_VECTOR)
        ):
            async with self.SessionLocal() as session:
                result = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=2
                )

        self.assertEqual(result.indexed, 5)
        self.assertEqual(result.batches, 3)

    async def test_backfill_honours_limit(self):

        for index in range(5):
            await self._create_memory(
                f"Approved fact {index}.",
                category="general"
            )

        with patch.object(
            embedding_service,
            "embed_passage",
            new=AsyncMock(return_value=TEST_VECTOR)
        ):
            async with self.SessionLocal() as session:
                result = await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=2,
                    limit=3
                )

        self.assertEqual(result.indexed, 3)
        self.assertEqual(result.processed, 3)

    async def test_only_approved_memories_are_queried_for_backfill(self):

        await self._create_memory(
            "An approved fact.",
            category="general"
        )
        await self._create_memory(
            "A pending fact.",
            category="general",
            status=MemoryStatus.PENDING
        )

        async with self.SessionLocal() as session:
            approved = list(
                (
                    await session.execute(
                        select(Memory).where(
                            Memory.status == MemoryStatus.APPROVED
                        )
                    )
                ).scalars().all()
            )

        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].content, "An approved fact.")

    async def test_backfill_rejects_invalid_batch_size(self):

        async with self.SessionLocal() as session:
            with self.assertRaises(ValueError):
                await memory_embedding_service.backfill_embeddings(
                    session=session,
                    batch_size=0
                )


if __name__ == "__main__":
    unittest.main()
