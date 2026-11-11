import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.core.config import settings
from app.database.base import Base
from app.models.ai_usage import AIUsageRecord
from app.models.user import User
from app.services.usage_tracking_service import (
    estimate_cost_usd,
    usage_tracking_service,
)


class UsageTrackingServiceTests(unittest.IsolatedAsyncioTestCase):

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
            user = User(username="usage_user")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            self.user_id = user.id

        self.settings_patches = [
            patch.object(settings, "AI_USAGE_TRACKING_ENABLED", True),
            patch.object(settings, "AI_USAGE_RETENTION_DAYS", 90),
            patch.object(settings, "AI_USAGE_RECENT_LIMIT", 50),
            patch.object(
                settings, "AI_GROQ_INPUT_COST_PER_MILLION", 0.0
            ),
            patch.object(
                settings, "AI_GROQ_OUTPUT_COST_PER_MILLION", 0.0
            ),
            patch.object(
                settings, "AI_OLLAMA_INPUT_COST_PER_MILLION", 0.0
            ),
            patch.object(
                settings, "AI_OLLAMA_OUTPUT_COST_PER_MILLION", 0.0
            ),
        ]

        for settings_patch in self.settings_patches:
            settings_patch.start()
            self.addCleanup(settings_patch.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _record(self, **kwargs):
        async with self.SessionLocal() as session:
            return await usage_tracking_service.record(
                session=session,
                user_id=self.user_id,
                request_type=kwargs.pop("request_type", "chat"),
                **kwargs,
            )

    async def test_record_persists_metadata_and_token_counts(self):

        row = await self._record(
            provider="groq",
            model="test-model",
            complexity="moderate",
            routing_policy="groq_first",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "source": "provider",
            },
            duration_ms=12.5,
            success=True,
        )

        self.assertIsNotNone(row)
        self.assertEqual(row.input_tokens, 100)
        self.assertEqual(row.output_tokens, 50)
        self.assertEqual(row.total_tokens, 150)
        self.assertEqual(row.token_source, "provider")

    async def test_record_derives_total_from_partial_counts(self):

        row = await self._record(
            provider="ollama",
            usage={
                "input_tokens": 20,
                "output_tokens": 5,
                "source": "estimated",
            },
        )

        self.assertEqual(row.total_tokens, 25)
        self.assertEqual(row.token_source, "estimated")

    async def test_unknown_token_source_is_not_persisted(self):

        row = await self._record(
            usage={
                "input_tokens": 1,
                "output_tokens": 1,
                "source": "made-up",
            },
        )

        self.assertIsNone(row.token_source)

    async def test_recorded_token_counts_never_include_prompt_content(self):

        await self._record(
            provider="groq",
            usage={
                "input_tokens": 3,
                "output_tokens": 4,
                "total_tokens": 7,
                "source": "provider",
            },
        )

        async with self.SessionLocal() as session:
            row = await session.scalar(select(AIUsageRecord))

        self.assertFalse(hasattr(row, "prompt"))
        self.assertFalse(hasattr(row, "response"))

    async def test_summary_aggregates_usage_and_cache_hits(self):

        await self._record(
            provider="groq",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "source": "provider",
            },
            duration_ms=10.0,
            cache_hit=False,
        )
        await self._record(
            provider="groq",
            usage=None,
            duration_ms=2.0,
            cache_hit=True,
        )
        await self._record(
            provider="ollama",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "source": "estimated",
            },
            duration_ms=20.0,
            fallback_used=True,
        )

        async with self.SessionLocal() as session:
            summary = await usage_tracking_service.summary(
                session=session,
                user_id=self.user_id,
            )

        self.assertEqual(summary["total_requests"], 3)
        self.assertEqual(summary["cache_hits"], 1)
        self.assertEqual(summary["fallback_requests"], 1)
        self.assertEqual(summary["total_input_tokens"], 110)
        self.assertEqual(summary["total_output_tokens"], 55)
        self.assertEqual(summary["total_tokens"], 165)
        self.assertEqual(summary["provider_reported_usage"], 1)
        self.assertEqual(summary["estimated_usage"], 1)
        self.assertEqual(summary["providers"]["groq"], 2)
        self.assertAlmostEqual(summary["average_duration_ms"], 10.67)

    async def test_recent_returns_newest_first_and_is_owner_scoped(self):

        await self._record(provider="groq")
        await self._record(provider="ollama")

        async with self.SessionLocal() as session:
            items = await usage_tracking_service.recent(
                session=session,
                user_id=self.user_id,
                limit=1,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["provider"], "ollama")

    async def test_disabled_tracking_does_not_write_a_record(self):

        with patch.object(settings, "AI_USAGE_TRACKING_ENABLED", False):
            row = await self._record(provider="groq")

        self.assertIsNone(row)

        async with self.SessionLocal() as session:
            count = len(
                (
                    await session.execute(select(AIUsageRecord))
                ).scalars().all()
            )

        self.assertEqual(count, 0)

    async def test_retention_purge_removes_old_rows_only(self):

        await self._record(provider="groq")

        async with self.SessionLocal() as session:
            row = await session.scalar(select(AIUsageRecord))
            row.created_at = datetime.now(timezone.utc) - timedelta(days=400)
            await session.commit()

        async with self.SessionLocal() as session:
            removed = await usage_tracking_service.purge_older_than(
                session=session,
                days=90,
            )

        self.assertEqual(removed, 1)

    def test_cost_estimation_uses_configured_prices(self):

        with patch.object(
            settings, "AI_GROQ_INPUT_COST_PER_MILLION", 1.0
        ), patch.object(
            settings, "AI_GROQ_OUTPUT_COST_PER_MILLION", 2.0
        ):
            cost = estimate_cost_usd("groq", 1000, 500)

        self.assertAlmostEqual(cost, 0.002)

    def test_cost_estimation_is_unknown_without_prices(self):

        with patch.object(
            settings, "AI_GROQ_INPUT_COST_PER_MILLION", 0.0
        ), patch.object(
            settings, "AI_GROQ_OUTPUT_COST_PER_MILLION", 0.0
        ):
            self.assertIsNone(estimate_cost_usd("groq", 1000, 500))


if __name__ == "__main__":
    unittest.main()
