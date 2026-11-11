"""Database-backed recurrence generation worker.

The worker claims rules, generates at most a bounded number of occurrences per
run, and advances the rule's ``last_generated_date`` transactionally. Stale
claims are returned to the queue after the configured lease, so a restart or
crashed process resumes without duplicating occurrences.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.models.recurrence import TaskRecurrence
from app.services.recurrence_service import recurrence_service
from app.services.task_deadlines import utcnow


logger = logging.getLogger(__name__)

UTC = timezone.utc


@dataclass(frozen=True)
class ClaimedRecurrence:
    recurrence_id: int
    claim_token: str


def _supports_skip_locked(session: AsyncSession) -> bool:
    bind = session.get_bind()

    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


def _safe_error(error: BaseException) -> str:
    return f"recurrence generation failed: {type(error).__name__}"[:255]


class RecurrenceWorker:
    """Generate due recurring tasks safely and idempotently."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        *,
        batch_size: int | None = None,
        lease_seconds: int | None = None,
        horizon_days: int | None = None,
        max_generations: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.batch_size = max(
            1,
            batch_size or settings.RECURRENCE_GENERATION_BATCH_SIZE,
        )
        self.lease_seconds = max(
            1,
            lease_seconds or settings.RECURRENCE_PROCESSING_LEASE_SECONDS,
        )
        self.horizon_days = max(
            1,
            horizon_days or settings.RECURRENCE_GENERATION_HORIZON_DAYS,
        )
        self.max_generations = max(
            1,
            max_generations or settings.RECURRENCE_MAX_GENERATIONS_PER_RUN,
        )

    async def run_once(self, now: datetime | None = None) -> dict[str, int]:
        current = (now or utcnow()).astimezone(UTC)
        stats = {
            "recovered": 0,
            "claimed": 0,
            "generated": 0,
            "failed": 0,
            "skipped": 0,
        }

        stats["recovered"] = await self.recover_stale_claims(current)

        claimed = await self.claim_batch(current)
        stats["claimed"] = len(claimed)

        for item in claimed:
            outcome, generated = await self.process_claimed(item, current)

            if outcome == "generated":
                # ``generated`` counts occurrence tasks, not rules processed.
                stats["generated"] += generated
            elif outcome in stats:
                stats[outcome] += 1

        return stats

    async def recover_stale_claims(
        self,
        now: datetime | None = None,
    ) -> int:
        current = (now or utcnow()).astimezone(UTC)
        cutoff = current - timedelta(seconds=self.lease_seconds)
        recovered = 0

        async with self.session_factory() as session:
            stmt = select(TaskRecurrence).where(
                TaskRecurrence.is_active.is_(True),
                TaskRecurrence.processing_started_at.is_not(None),
                TaskRecurrence.processing_started_at <= cutoff,
            )

            if _supports_skip_locked(session):
                stmt = stmt.with_for_update(skip_locked=True)

            rules = list((await session.scalars(stmt)).all())

            for rule in rules:
                rule.claim_token = None
                rule.processing_started_at = None
                rule.next_generation_at = current
                rule.last_error = "processing lease expired"
                recovered += 1

            if recovered:
                await session.commit()
            else:
                await session.rollback()

        if recovered:
            logger.warning(
                "Recovered %s expired recurrence lease(s).",
                recovered,
            )

        return recovered

    async def claim_batch(
        self,
        now: datetime | None = None,
    ) -> list[ClaimedRecurrence]:
        current = (now or utcnow()).astimezone(UTC)

        async with self.session_factory() as session:
            stmt = (
                select(TaskRecurrence.id)
                .where(
                    TaskRecurrence.is_active.is_(True),
                    (
                        TaskRecurrence.next_generation_at.is_(None)
                        | (TaskRecurrence.next_generation_at <= current)
                    ),
                    TaskRecurrence.claim_token.is_(None),
                )
                .order_by(
                    TaskRecurrence.next_generation_at.asc().nulls_first(),
                    TaskRecurrence.id.asc(),
                )
                .limit(self.batch_size)
            )

            if _supports_skip_locked(session):
                stmt = stmt.with_for_update(skip_locked=True)

            candidate_ids = list((await session.scalars(stmt)).all())

            if not candidate_ids:
                await session.rollback()
                return []

            token = secrets.token_hex(24)

            result = await session.execute(
                update(TaskRecurrence)
                .where(
                    TaskRecurrence.id.in_(candidate_ids),
                    TaskRecurrence.is_active.is_(True),
                    TaskRecurrence.claim_token.is_(None),
                )
                .values(
                    claim_token=token,
                    processing_started_at=current,
                    next_generation_at=(
                        current + timedelta(seconds=self.lease_seconds)
                    ),
                    updated_at=current,
                )
                .returning(TaskRecurrence.id)
            )

            claimed_ids = list(result.scalars().all())
            await session.commit()

        return [
            ClaimedRecurrence(
                recurrence_id=recurrence_id,
                claim_token=token,
            )
            for recurrence_id in claimed_ids
        ]

    async def process_claimed(
        self,
        claimed: ClaimedRecurrence,
        now: datetime | None = None,
    ) -> tuple[str, int]:
        current = (now or utcnow()).astimezone(UTC)

        try:
            async with self.session_factory() as session:
                rule = await session.scalar(
                    select(TaskRecurrence).where(
                        TaskRecurrence.id == claimed.recurrence_id,
                        TaskRecurrence.claim_token == claimed.claim_token,
                    )
                )

                if rule is None:
                    return "skipped", 0

                generated = await recurrence_service.generate_due(
                    session=session,
                    rule=rule,
                    now=current,
                    horizon_days=self.horizon_days,
                    max_generations=self.max_generations,
                )

                rule.claim_token = None
                rule.processing_started_at = None
                rule.last_error = None
                await session.commit()

                return "generated", len(generated)

        except asyncio.CancelledError:
            raise

        except Exception as error:
            logger.error(
                "Recurrence %s generation failed (%s).",
                claimed.recurrence_id,
                type(error).__name__,
            )
            await self._record_failure(claimed, current, error)
            return "failed", 0

    async def _record_failure(
        self,
        claimed: ClaimedRecurrence,
        now: datetime,
        error: BaseException,
    ) -> None:
        try:
            async with self.session_factory() as session:
                rule = await session.scalar(
                    select(TaskRecurrence).where(
                        TaskRecurrence.id == claimed.recurrence_id,
                        TaskRecurrence.claim_token == claimed.claim_token,
                    )
                )

                if rule is None:
                    return

                rule.claim_token = None
                rule.processing_started_at = None
                rule.last_error = _safe_error(error)
                rule.next_generation_at = now + timedelta(
                    seconds=max(
                        1,
                        settings.RECURRENCE_RETRY_BACKOFF_SECONDS,
                    )
                )
                await session.commit()

        except Exception:
            logger.exception(
                "Unable to record recurrence failure for %s.",
                claimed.recurrence_id,
            )


__all__ = ["ClaimedRecurrence", "RecurrenceWorker"]
