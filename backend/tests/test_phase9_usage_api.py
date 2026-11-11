"""Phase 9F owner-only AI usage endpoint tests."""

import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import get_current_user_id
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.user import User
from app.services.usage_tracking_service import usage_tracking_service


class UsageApiTests(unittest.IsolatedAsyncioTestCase):
    """Verify usage endpoints are authenticated, owner-scoped, and bounded."""

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
            owner = User(username="usage_api_owner")
            other = User(username="usage_api_other")
            session.add_all([owner, other])
            await session.commit()
            await session.refresh(owner)
            await session.refresh(other)

            self.owner_id = owner.id
            self.other_id = other.id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_owner():
            return self.owner_id

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_owner

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

        self.settings_patch = patch.object(
            settings, "AI_USAGE_TRACKING_ENABLED", True
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    async def asyncTearDown(self):

        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    async def _record(
        self,
        user_id: int,
        *,
        provider: str,
        cache_hit: bool = False,
    ):

        async with self.SessionLocal() as session:
            await usage_tracking_service.record(
                session=session,
                user_id=user_id,
                request_type="chat",
                provider=provider,
                model="test-model",
                complexity="moderate",
                routing_policy="groq_first",
                usage={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "source": "provider",
                },
                duration_ms=10.0,
                cache_hit=cache_hit,
                success=True,
            )

    async def test_summary_is_owner_scoped(self):

        await self._record(self.owner_id, provider="groq")
        await self._record(
            self.other_id,
            provider="groq",
            cache_hit=True,
        )

        response = await self.client.get("/api/ai/usage/summary")

        self.assertEqual(response.status_code, 200, response.text)

        payload = response.json()

        self.assertEqual(payload["total_requests"], 1)
        self.assertEqual(payload["cache_hits"], 0)
        self.assertEqual(payload["providers"], {"groq": 1})
        self.assertEqual(payload["total_tokens"], 15)
        self.assertEqual(payload["provider_reported_usage"], 1)

    async def test_recent_is_owner_scoped_and_newest_first(self):

        await self._record(self.owner_id, provider="groq")
        await self._record(self.owner_id, provider="ollama")
        await self._record(self.other_id, provider="groq")

        response = await self.client.get(
            "/api/ai/usage/recent", params={"limit": 1}
        )

        self.assertEqual(response.status_code, 200, response.text)

        items = response.json()["items"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["provider"], "ollama")
        self.assertEqual(items[0]["total_tokens"], 15)

    async def test_usage_endpoints_reject_invalid_bounds(self):

        for path, params in (
            ("/api/ai/usage/summary", {"days": 0}),
            ("/api/ai/usage/summary", {"days": 3651}),
            ("/api/ai/usage/recent", {"limit": 0}),
            ("/api/ai/usage/recent", {"limit": 501}),
        ):
            with self.subTest(path=path, params=params):
                response = await self.client.get(path, params=params)
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
