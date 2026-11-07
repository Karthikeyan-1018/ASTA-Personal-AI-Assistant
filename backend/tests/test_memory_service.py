import unittest

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.database.base import Base
from app.exceptions.memory_exceptions import (
    MemoryNotFoundError,
    MemoryValidationError,
)
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


class MemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    """Exercise MemoryService against an isolated in-memory database."""

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
            alice = User(username="alice")
            bob = User(username="bob")

            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create(
        self,
        user_id: int | None = None,
        content: str = "Likes Python",
        category: str = "general"
    ):
        async with self.SessionLocal() as session:
            return await memory_service.create_memory(
                session=session,
                user_id=user_id if user_id is not None else self.alice_id,
                content=content,
                category=category
            )

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    async def test_create_memory_stores_pending_memory(self):

        memory = await self._create()

        self.assertEqual(memory.user_id, self.alice_id)
        self.assertEqual(memory.content, "Likes Python")
        self.assertEqual(memory.category, "general")
        self.assertEqual(memory.status, MemoryStatus.PENDING)

    async def test_create_memory_strips_whitespace(self):

        memory = await self._create(
            content="  Likes tea  ",
            category="  preferences  "
        )

        self.assertEqual(memory.content, "Likes tea")
        self.assertEqual(memory.category, "preferences")

    async def test_create_memory_rejects_blank_content(self):

        with self.assertRaises(MemoryValidationError):
            await self._create(content="   ")

    async def test_create_memory_rejects_secrets(self):

        secret_values = [
            "api_key=sk-1234567890abcdef",
            "password=hunter2",
            "gsk_abcdefghijklmnop",
            "-----BEGIN RSA PRIVATE KEY-----"
        ]

        for content in secret_values:

            with self.subTest(content=content):

                with self.assertRaises(MemoryValidationError):
                    await self._create(content=content)

    async def test_create_memory_allows_normal_password_text(self):

        memory = await self._create(
            content="I often forget my password, so remind me gently."
        )

        self.assertIn("password", memory.content)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def test_get_memory_by_id_returns_owned_memory(self):

        created = await self._create()

        async with self.SessionLocal() as session:
            memory = await memory_service.get_memory_by_id(
                session=session,
                user_id=self.alice_id,
                memory_id=created.id
            )

        self.assertEqual(memory.id, created.id)

    async def test_get_memory_by_id_hides_other_users_memory(self):

        created = await self._create()

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.get_memory_by_id(
                    session=session,
                    user_id=self.bob_id,
                    memory_id=created.id
                )

    async def test_get_memory_by_id_missing_raises(self):

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.get_memory_by_id(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=99999
                )

    async def test_get_user_memories_returns_only_owned(self):

        await self._create(content="Alice memory")
        await self._create(
            user_id=self.bob_id,
            content="Bob memory"
        )

        async with self.SessionLocal() as session:
            memories = await memory_service.get_user_memories(
                session=session,
                user_id=self.alice_id
            )

        self.assertEqual(
            [memory.content for memory in memories],
            ["Alice memory"]
        )

    async def test_approved_retrieval_excludes_pending_and_rejected(self):

        pending = await self._create(content="Pending memory")
        approved = await self._create(content="Approved memory")
        rejected = await self._create(content="Rejected memory")

        async with self.SessionLocal() as session:
            await memory_service.approve_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=approved.id
            )
            await memory_service.reject_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=rejected.id
            )

            approved_memories = (
                await memory_service.get_approved_memories(
                    session=session,
                    user_id=self.alice_id
                )
            )

        approved_ids = [memory.id for memory in approved_memories]

        self.assertEqual(approved_ids, [approved.id])
        self.assertNotIn(pending.id, approved_ids)
        self.assertNotIn(rejected.id, approved_ids)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def test_update_memory_changes_content_and_category(self):

        created = await self._create()

        async with self.SessionLocal() as session:
            updated = await memory_service.update_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=created.id,
                content="  Likes FastAPI  ",
                category="  interests  "
            )

        self.assertEqual(updated.content, "Likes FastAPI")
        self.assertEqual(updated.category, "interests")

    async def test_update_memory_requires_a_field(self):

        created = await self._create()

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryValidationError):
                await memory_service.update_memory(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=created.id
                )

    async def test_update_memory_hides_other_users_memory(self):

        created = await self._create()

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.update_memory(
                    session=session,
                    user_id=self.bob_id,
                    memory_id=created.id,
                    content="Hacked"
                )

    async def test_update_memory_missing_raises(self):

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.update_memory(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=99999,
                    content="Missing"
                )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def test_delete_memory_removes_owned_memory(self):

        created = await self._create()

        async with self.SessionLocal() as session:
            await memory_service.delete_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=created.id
            )

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.get_memory_by_id(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=created.id
                )

    async def test_delete_memory_hides_other_users_memory(self):

        created = await self._create()

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.delete_memory(
                    session=session,
                    user_id=self.bob_id,
                    memory_id=created.id
                )

    async def test_delete_memory_missing_raises(self):

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.delete_memory(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=99999
                )

    # ------------------------------------------------------------------
    # Approval and rejection
    # ------------------------------------------------------------------

    async def test_approve_memory_sets_approved_status(self):

        created = await self._create()

        async with self.SessionLocal() as session:
            memory = await memory_service.approve_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=created.id
            )

        self.assertEqual(memory.status, MemoryStatus.APPROVED)

    async def test_reject_memory_sets_rejected_status(self):

        created = await self._create()

        async with self.SessionLocal() as session:
            memory = await memory_service.reject_memory(
                session=session,
                user_id=self.alice_id,
                memory_id=created.id
            )

        self.assertEqual(memory.status, MemoryStatus.REJECTED)

    async def test_approve_memory_hides_other_users_memory(self):

        created = await self._create()

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.approve_memory(
                    session=session,
                    user_id=self.bob_id,
                    memory_id=created.id
                )

    async def test_reject_memory_missing_raises(self):

        async with self.SessionLocal() as session:

            with self.assertRaises(MemoryNotFoundError):
                await memory_service.reject_memory(
                    session=session,
                    user_id=self.alice_id,
                    memory_id=99999
                )


if __name__ == "__main__":
    unittest.main()
