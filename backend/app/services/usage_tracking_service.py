"""Phase 9 AI usage and performance tracking.

Records contain metadata and token counts only. Prompt text, response text,
credentials, and provider error messages are never persisted here.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_usage import AIUsageRecord


logger = logging.getLogger(__name__)


def _price_per_million(provider: str | None, direction: str) -> float:
    name = (provider or "").strip().lower()

    if name == "groq":
        value = (
            settings.AI_GROQ_INPUT_COST_PER_MILLION
            if direction == "input"
            else settings.AI_GROQ_OUTPUT_COST_PER_MILLION
        )
    elif name == "ollama":
        value = (
            settings.AI_OLLAMA_INPUT_COST_PER_MILLION
            if direction == "input"
            else settings.AI_OLLAMA_OUTPUT_COST_PER_MILLION
        )
    else:
        return 0.0

    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def estimate_cost_usd(
    provider: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """Return a configured cost, or ``None`` when prices are unknown."""

    input_price = _price_per_million(provider, "input")
    output_price = _price_per_million(provider, "output")

    if input_price <= 0.0 and output_price <= 0.0:
        return None

    cost = 0.0

    if input_tokens is not None and input_price > 0.0:
        cost += (max(0, int(input_tokens)) / 1_000_000.0) * input_price

    if output_tokens is not None and output_price > 0.0:
        cost += (max(0, int(output_tokens)) / 1_000_000.0) * output_price

    return round(cost, 8)


def _token_value(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return max(0, number)


class UsageTrackingService:
    """Record and summarise AI request outcomes."""

    async def record(
        self,
        session: AsyncSession,
        *,
        user_id: int | None,
        request_type: str = "chat",
        provider: str | None = None,
        model: str | None = None,
        complexity: str | None = None,
        routing_policy: str | None = None,
        usage: dict | None = None,
        duration_ms: float | None = None,
        fallback_used: bool = False,
        cache_hit: bool = False,
        success: bool = True,
        error_type: str | None = None,
    ) -> AIUsageRecord | None:
        """Persist one metadata-only usage row.

        Failures are swallowed so analytics can never break a chat response.
        """

        if not settings.AI_USAGE_TRACKING_ENABLED:
            return None

        try:
            row = self._build_record(
                user_id=user_id,
                request_type=request_type,
                provider=provider,
                model=model,
                complexity=complexity,
                routing_policy=routing_policy,
                usage=usage,
                duration_ms=duration_ms,
                fallback_used=fallback_used,
                cache_hit=cache_hit,
                success=success,
                error_type=error_type,
            )

            session.add(row)
            await session.commit()

            return row

        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass

            logger.warning(
                "AI usage tracking failed; continuing without a record.",
                exc_info=True,
            )
            return None

    @staticmethod
    def _build_record(
        *,
        user_id: int | None,
        request_type: str,
        provider: str | None,
        model: str | None,
        complexity: str | None,
        routing_policy: str | None,
        usage: dict | None,
        duration_ms: float | None,
        fallback_used: bool,
        cache_hit: bool,
        success: bool,
        error_type: str | None,
    ) -> AIUsageRecord:

        usage = usage or {}
        input_tokens = _token_value(usage.get("input_tokens"))
        output_tokens = _token_value(usage.get("output_tokens"))
        total_tokens = _token_value(usage.get("total_tokens"))

        if total_tokens is None and (
            input_tokens is not None or output_tokens is not None
        ):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        source = usage.get("source")

        if not isinstance(source, str) or source not in {
            "provider",
            "estimated",
        }:
            source = None

        return AIUsageRecord(
            user_id=user_id,
            request_type=request_type,
            provider=provider,
            model=model,
            complexity=complexity,
            routing_policy=routing_policy,
            cache_hit=bool(cache_hit),
            fallback_used=bool(fallback_used),
            success=bool(success),
            error_type=error_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            token_source=source,
            cost_usd=estimate_cost_usd(
                provider,
                input_tokens,
                output_tokens,
            ),
            duration_ms=(
                round(float(duration_ms), 2)
                if duration_ms is not None
                else None
            ),
        )

    async def summary(
        self,
        session: AsyncSession,
        user_id: int,
        days: int | None = None,
    ) -> dict:
        """Return an aggregate usage summary for one user."""

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=max(1, int(days or settings.AI_USAGE_RETENTION_DAYS))
        )

        rows = (
            await session.execute(
                select(AIUsageRecord)
                .where(
                    AIUsageRecord.user_id == user_id,
                    AIUsageRecord.created_at >= cutoff,
                )
                .order_by(AIUsageRecord.created_at.asc())
            )
        ).scalars().all()

        return self._summarise(rows)

    @staticmethod
    def _summarise(rows) -> dict:
        total_requests = len(rows)
        cache_hits = sum(1 for row in rows if row.cache_hit)
        successes = sum(1 for row in rows if row.success)
        fallbacks = sum(1 for row in rows if row.fallback_used)

        input_tokens = sum(row.input_tokens or 0 for row in rows)
        output_tokens = sum(row.output_tokens or 0 for row in rows)
        total_tokens = sum(row.total_tokens or 0 for row in rows)

        durations = [
            float(row.duration_ms)
            for row in rows
            if row.duration_ms is not None
        ]

        configured_costs = [
            float(row.cost_usd)
            for row in rows
            if row.cost_usd is not None
        ]

        provider_counts = Counter(
            row.provider or "unknown" for row in rows
        )
        model_counts = Counter(row.model or "unknown" for row in rows)
        request_type_counts = Counter(row.request_type for row in rows)

        estimated_tokens = sum(
            1 for row in rows if row.token_source == "estimated"
        )
        provider_tokens = sum(
            1 for row in rows if row.token_source == "provider"
        )

        return {
            "total_requests": total_requests,
            "successful_requests": successes,
            "failed_requests": total_requests - successes,
            "cache_hits": cache_hits,
            "cache_hit_rate": (
                round(cache_hits / total_requests, 4)
                if total_requests
                else 0.0
            ),
            "fallback_requests": fallbacks,
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "provider_reported_usage": provider_tokens,
            "estimated_usage": estimated_tokens,
            "estimated_cost_usd": (
                round(sum(configured_costs), 8)
                if configured_costs
                else None
            ),
            "average_duration_ms": (
                round(sum(durations) / len(durations), 2)
                if durations
                else None
            ),
            "providers": dict(provider_counts),
            "models": dict(model_counts),
            "request_types": dict(request_type_counts),
        }

    async def recent(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int | None = None,
    ) -> list[dict]:
        """Return recent metadata-only usage rows, newest first."""

        limit = max(
            1,
            min(
                int(limit or settings.AI_USAGE_RECENT_LIMIT),
                500,
            ),
        )

        rows = (
            await session.execute(
                select(AIUsageRecord)
                .where(AIUsageRecord.user_id == user_id)
                .order_by(
                    AIUsageRecord.created_at.desc(),
                    AIUsageRecord.id.desc(),
                )
                .limit(limit)
            )
        ).scalars().all()

        return [
            {
                "id": row.id,
                "request_type": row.request_type,
                "provider": row.provider,
                "model": row.model,
                "complexity": row.complexity,
                "routing_policy": row.routing_policy,
                "cache_hit": row.cache_hit,
                "fallback_used": row.fallback_used,
                "success": row.success,
                "error_type": row.error_type,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "total_tokens": row.total_tokens,
                "token_source": row.token_source,
                "cost_usd": row.cost_usd,
                "duration_ms": row.duration_ms,
                "created_at": (
                    row.created_at.isoformat()
                    if row.created_at is not None
                    else None
                ),
            }
            for row in rows
        ]

    async def purge_older_than(
        self,
        session: AsyncSession,
        days: int | None = None,
    ) -> int:
        """Delete usage rows older than the configured retention window."""

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=max(1, int(days or settings.AI_USAGE_RETENTION_DAYS))
        )

        try:
            result = await session.execute(
                delete(AIUsageRecord).where(
                    AIUsageRecord.created_at < cutoff
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

        except Exception:
            await session.rollback()
            logger.warning(
                "AI usage retention cleanup failed.",
                exc_info=True,
            )
            return 0


usage_tracking_service = UsageTrackingService()
