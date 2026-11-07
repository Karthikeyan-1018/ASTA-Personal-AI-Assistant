import unittest

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
from app.main import app
from app.models.memory import MemoryStatus
from app.models.user import User
from app.services.memory_service import memory_service


class MemoryRetrievalAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise POST /api/memories/retrieve with an isolated database."""

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
            alice = User(username="retrieval_alice")
            bob = User(username="retrieval_bob")

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

    async def _retrieve(self, payload: dict):
        return await self.client.post(
            "/api/memories/retrieve",
            json=payload
        )

    # ------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------

    async def test_retrieve_returns_ranked_career_memory(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        response = await self._retrieve({
            "query": "What is my career goal?",
            "limit": 5
        })

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()

        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["memories"]), 1)

        item = body["memories"][0]

        self.assertEqual(
            set(item.keys()),
            {"content", "category", "relevance_score"}
        )
        self.assertEqual(
            item["content"],
            "My career goal is to become an AI/ML Engineer."
        )
        self.assertEqual(item["category"], "career")
        self.assertGreater(item["relevance_score"], 0.0)
        self.assertLessEqual(item["relevance_score"], 1.0)

    async def test_no_matching_memories_returns_empty_response(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career"
        )

        response = await self._retrieve({
            "query": "quantum physics"
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"memories": [], "total": 0})

    async def test_category_filter(self):

        await self._add_memory(
            "My career goal is to become an engineer.",
            category="career"
        )
        await self._add_memory(
            "My goal is to finish my degree.",
            category="education"
        )

        response = await self._retrieve({
            "query": "goal",
            "category": "education"
        })

        self.assertEqual(response.status_code, 200)

        body = response.json()

        self.assertEqual(body["total"], 1)
        self.assertEqual(
            body["memories"][0]["content"],
            "My goal is to finish my degree."
        )

    async def test_maximum_result_limit(self):

        for index in range(4):
            await self._add_memory(
                f"Career goal number {index}.",
                category="career"
            )

        response = await self._retrieve({
            "query": "career goal",
            "limit": 2
        })

        self.assertEqual(response.status_code, 200)

        body = response.json()

        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["memories"]), 2)

    async def test_pending_and_rejected_memories_are_excluded(self):

        await self._add_memory(
            "I work as a data scientist.",
            category="career",
            status=MemoryStatus.APPROVED
        )
        await self._add_memory(
            "My career goal is to become a manager.",
            category="career",
            status=MemoryStatus.PENDING
        )
        await self._add_memory(
            "My rejected career goal is to become a pilot.",
            category="career",
            status=MemoryStatus.REJECTED
        )

        response = await self._retrieve({"query": "career goal"})

        self.assertEqual(response.status_code, 200)

        contents = [
            item["content"] for item in response.json()["memories"]
        ]

        self.assertEqual(contents, ["I work as a data scientist."])
        self.assertNotIn(
            "My career goal is to become a manager.",
            contents
        )
        self.assertNotIn(
            "My rejected career goal is to become a pilot.",
            contents
        )

    async def test_retrieval_does_not_change_memory_statuses(self):

        memory_id = await self._add_memory(
            "My career goal is to become a manager.",
            category="career",
            status=MemoryStatus.PENDING
        )

        await self._retrieve({"query": "career goal"})

        response = await self.client.get(
            f"/api/memories/{memory_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")

    # ------------------------------------------------------------------
    # Validation and security
    # ------------------------------------------------------------------

    async def test_empty_query_returns_400(self):

        for query in ("", "   ", "\n\t"):

            with self.subTest(query=query):

                response = await self._retrieve({"query": query})

                self.assertEqual(response.status_code, 400)

    async def test_missing_query_returns_422(self):

        response = await self._retrieve({})

        self.assertEqual(response.status_code, 422)

    async def test_invalid_limit_returns_422(self):

        for limit in (0, -1, 51):

            with self.subTest(limit=limit):

                response = await self._retrieve({
                    "query": "career goal",
                    "limit": limit
                })

                self.assertEqual(response.status_code, 422)

    async def test_client_supplied_user_id_is_rejected(self):

        response = await self._retrieve({
            "query": "career goal",
            "user_id": self.bob_id
        })

        self.assertEqual(response.status_code, 422)

    async def test_unauthenticated_request_returns_401(self):

        del app.dependency_overrides[get_current_user_id]

        try:
            response = await self._retrieve({"query": "career goal"})
        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

        self.assertEqual(response.status_code, 401)

    async def test_user_isolation(self):

        await self._add_memory(
            "My career goal is to become an AI/ML Engineer.",
            category="career",
            user_id=self.alice_id
        )

        app.dependency_overrides[get_current_user_id] = self.override_bob

        try:
            bob_response = await self._retrieve({
                "query": "career goal"
            })
        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

        self.assertEqual(bob_response.status_code, 200)
        self.assertEqual(
            bob_response.json(),
            {"memories": [], "total": 0}
        )


if __name__ == "__main__":
    unittest.main()
