import json
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
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.llm_router import llm_router
from app.main import app
from app.models.user import User


class MemoryExtractionAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise POST /api/memories/extract with a mocked LLM."""

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
            alice = User(username="extract_alice")
            bob = User(username="extract_bob")

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

        self.generate_response = AsyncMock()
        self.router_patcher = patch.object(
            llm_router,
            "generate_response",
            new=self.generate_response
        )
        self.router_patcher.start()

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.router_patcher.stop()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    def _respond_with(self, payload):
        raw = payload if isinstance(payload, str) else json.dumps(payload)

        self.generate_response.return_value = {
            "provider": "test",
            "model": "test-model",
            "response": raw
        }

    async def _extract(self, conversation: str, **extra):
        return await self.client.post(
            "/api/memories/extract",
            json={"conversation": conversation, **extra}
        )

    # ------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------

    async def test_extract_returns_candidates_only(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Works as a data scientist.",
                    "category": "career",
                    "explanation": "Useful career context."
                }
            ]
        })

        response = await self._extract("I work as a data scientist.")

        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()

        self.assertEqual(len(body["candidates"]), 1)
        candidate = body["candidates"][0]
        self.assertEqual(candidate["content"], "Works as a data scientist.")
        self.assertEqual(candidate["category"], "career")
        self.assertEqual(candidate["explanation"], "Useful career context.")

        # Candidates are suggestions only: no persistence metadata.
        self.assertNotIn("id", candidate)
        self.assertNotIn("status", candidate)
        self.assertNotIn("user_id", candidate)

    async def test_no_useful_information_returns_empty_candidates(self):

        self._respond_with({"memories": []})

        response = await self._extract("I had a sandwich for lunch.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"candidates": []})

    async def test_secret_candidates_are_not_returned(self):

        self._respond_with({
            "memories": [
                {
                    "content": "password=hunter2",
                    "category": "personal",
                    "explanation": "Secret."
                },
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "Useful context."
                }
            ]
        })

        response = await self._extract("I live in London.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["content"] for item in response.json()["candidates"]],
            ["Lives in London."]
        )

    async def test_extraction_does_not_save_or_approve_memories(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "Useful context."
                }
            ]
        })

        extract_response = await self._extract("I live in London.")
        self.assertEqual(extract_response.status_code, 200)

        memories_response = await self.client.get("/api/memories/")

        self.assertEqual(memories_response.status_code, 200)
        self.assertEqual(memories_response.json(), [])

    async def test_private_flag_is_forwarded_to_router(self):

        self._respond_with({"memories": []})

        response = await self._extract("I live in London.", private=True)

        self.assertEqual(response.status_code, 200)

        _, kwargs = self.generate_response.await_args
        self.assertTrue(kwargs["private"])

    # ------------------------------------------------------------------
    # Invalid input
    # ------------------------------------------------------------------

    async def test_empty_conversation_returns_400(self):

        for conversation in ("", "   ", "\n\t"):

            with self.subTest(conversation=conversation):

                response = await self._extract(conversation)

                self.assertEqual(response.status_code, 400)

        self.generate_response.assert_not_awaited()

    async def test_missing_conversation_returns_422(self):

        response = await self.client.post(
            "/api/memories/extract",
            json={}
        )

        self.assertEqual(response.status_code, 422)

    async def test_client_supplied_user_id_is_rejected(self):

        response = await self._extract(
            "I live in London.",
            user_id=self.bob_id
        )

        self.assertEqual(response.status_code, 422)
        self.generate_response.assert_not_awaited()

    # ------------------------------------------------------------------
    # LLM failure mapping
    # ------------------------------------------------------------------

    async def test_malformed_llm_response_returns_502(self):

        self._respond_with("not valid json")

        response = await self._extract("I live in London.")

        self.assertEqual(response.status_code, 502)

    async def test_llm_timeout_returns_504(self):

        self.generate_response.side_effect = LLMTimeoutError("timeout")

        response = await self._extract("I live in London.")

        self.assertEqual(response.status_code, 504)

    async def test_llm_provider_failure_returns_503(self):

        self.generate_response.side_effect = LLMProviderError("failed")

        response = await self._extract("I live in London.")

        self.assertEqual(response.status_code, 503)

    async def test_all_providers_unavailable_returns_503(self):

        self.generate_response.side_effect = LLMUnavailableError(
            "unavailable"
        )

        response = await self._extract("I live in London.")

        self.assertEqual(response.status_code, 503)

    # ------------------------------------------------------------------
    # Authentication and isolation
    # ------------------------------------------------------------------

    async def test_unauthenticated_request_returns_401(self):

        del app.dependency_overrides[get_current_user_id]

        try:
            response = await self._extract("I live in London.")
        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

        self.assertEqual(response.status_code, 401)
        self.generate_response.assert_not_awaited()

    async def test_extraction_is_scoped_to_authenticated_user(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "Useful context."
                }
            ]
        })

        alice_response = await self._extract("I live in London.")
        self.assertEqual(alice_response.status_code, 200)

        app.dependency_overrides[get_current_user_id] = self.override_bob

        try:
            bob_list = await self.client.get("/api/memories/")
            bob_extract = await self._extract(
                "I live in London and work as a teacher."
            )
        finally:
            app.dependency_overrides[get_current_user_id] = (
                self.override_alice
            )

        self.assertEqual(bob_list.status_code, 200)
        self.assertEqual(bob_list.json(), [])
        self.assertEqual(bob_extract.status_code, 200)
        self.assertEqual(len(bob_extract.json()["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
