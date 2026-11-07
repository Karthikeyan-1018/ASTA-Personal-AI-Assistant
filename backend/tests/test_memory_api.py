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
from app.models.user import User


class MemoryAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise the memory HTTP endpoints with an isolated database."""

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
            alice = User(username="api_alice")
            bob = User(username="api_bob")

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

    async def _create_memory(
        self,
        content: str = "Likes Python",
        category: str = "general"
    ) -> dict:

        response = await self.client.post(
            "/api/memories/",
            json={
                "content": content,
                "category": category
            }
        )

        self.assertEqual(response.status_code, 201, response.text)

        return response.json()

    async def test_create_memory(self):

        body = await self._create_memory()

        self.assertEqual(body["user_id"], self.alice_id)
        self.assertEqual(body["content"], "Likes Python")
        self.assertEqual(body["category"], "general")
        self.assertEqual(body["status"], "pending")
        self.assertIn("created_at", body)
        self.assertIn("updated_at", body)

    async def test_list_memories(self):

        await self._create_memory(content="First memory")
        await self._create_memory(content="Second memory")

        response = await self.client.get("/api/memories/")

        self.assertEqual(response.status_code, 200)

        contents = {
            memory["content"] for memory in response.json()
        }

        self.assertEqual(
            contents,
            {"First memory", "Second memory"}
        )

    async def test_get_memory(self):

        created = await self._create_memory()

        response = await self.client.get(
            f"/api/memories/{created['id']}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], created["id"])

    async def test_update_memory(self):

        created = await self._create_memory()

        response = await self.client.put(
            f"/api/memories/{created['id']}",
            json={
                "content": "Updated memory",
                "category": "updated"
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["content"],
            "Updated memory"
        )
        self.assertEqual(
            response.json()["category"],
            "updated"
        )

    async def test_delete_memory(self):

        created = await self._create_memory()

        response = await self.client.delete(
            f"/api/memories/{created['id']}"
        )

        self.assertEqual(response.status_code, 204)

        response = await self.client.get(
            f"/api/memories/{created['id']}"
        )

        self.assertEqual(response.status_code, 404)

    async def test_approve_memory(self):

        created = await self._create_memory()

        response = await self.client.patch(
            f"/api/memories/{created['id']}/approve"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")

    async def test_reject_memory(self):

        created = await self._create_memory()

        response = await self.client.patch(
            f"/api/memories/{created['id']}/reject"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "rejected")

    async def test_user_isolation(self):

        created = await self._create_memory()

        app.dependency_overrides[get_current_user_id] = self.override_bob

        try:

            get_response = await self.client.get(
                f"/api/memories/{created['id']}"
            )
            self.assertEqual(get_response.status_code, 404)

            update_response = await self.client.put(
                f"/api/memories/{created['id']}",
                json={"content": "Hacked"}
            )
            self.assertEqual(update_response.status_code, 404)

            delete_response = await self.client.delete(
                f"/api/memories/{created['id']}"
            )
            self.assertEqual(delete_response.status_code, 404)

            approve_response = await self.client.patch(
                f"/api/memories/{created['id']}/approve"
            )
            self.assertEqual(approve_response.status_code, 404)

        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

    async def test_missing_memory_returns_404(self):

        response = await self.client.get("/api/memories/99999")

        self.assertEqual(response.status_code, 404)

    async def test_secret_content_returns_400(self):

        response = await self.client.post(
            "/api/memories/",
            json={"content": "password=hunter2"}
        )

        self.assertEqual(response.status_code, 400)

    async def test_blank_content_returns_422(self):

        response = await self.client.post(
            "/api/memories/",
            json={"content": "   "}
        )

        self.assertEqual(response.status_code, 422)

    async def test_unauthenticated_request_returns_401(self):

        del app.dependency_overrides[get_current_user_id]

        try:
            response = await self.client.get("/api/memories/")
        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
