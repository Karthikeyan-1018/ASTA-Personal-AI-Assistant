"""Database-backed reminder claim, delivery, and retry worker.

Phase 10B deliberately keeps the reminder lifecycle separate from delivery. A
worker claims due reminders atomically, hands them to a delivery handler, and
only marks a reminder delivered after that handler succeeds. A claimed reminder
that outlives its lease is returned to the pending queue by the next worker.

PostgreSQL uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple workers can
run concurrently without processing the same row. SQLite is supported for
tests; it serialises writers and re-checks the pending status in the claim
update.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.models.reminder import ReminderStatus, TaskReminder
from app.models.task import Task
from app.services.task_deadlines import OPEN_STATUSES, utcnow


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedReminder:
    """A reminder claimed by this worker for one processing attempt."""

    reminder_id: int
    claim_token: str


class DeliveryOutcomeStatus(str):
    DELIVERED = "delivered"
    DEFERRED = "deferred"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class DeliveryOutcome:
    """Result of one delivery attempt.

    ``deferred`` returns the reminder to the pending queue with a new
    ``next_attempt_at``. ``suppressed`` ends the reminder without delivery and
    must carry a safe, non-sensitive reason.
    """

    status: str
    next_attempt_at: datetime | None = None
    reason: str | None = None


class DeliveryHandler(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        reminder: TaskReminder,
        task: Task,
    ) -> DeliveryOutcome:
        ...


def retry_backoff_seconds(attempt_count: int) -> int:
    """Return a bounded exponential backoff for the next retry."""

    base = max(1, settings.REMINDER_RETRY_BACKOFF_SECONDS)
    multiplier = max(1.0, settings.REMINDER_RETRY_BACKOFF_MULTIPLIER)
    maximum = max(base, settings.REMINDER_RETRY_BACKOFF_MAX_SECONDS)
    exponent = max(0, attempt_count - 1)

    try:
        value = base * (multiplier ** exponent)
    except OverflowError:
        return maximum

    return int(min(maximum, value))


def _supports_skip_locked(session: AsyncSession) -> bool:
    bind = session.get_bind()

    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


def _safe_error(error: BaseException) -> str:
    """Return a bounded error label with no user or provider content."""

    label = f"delivery failed: {type(error).__name__}"

    return label[:255]


class ReminderWorker:
    """Process due reminders safely and idempotently."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        delivery_handler: DeliveryHandler | None = None,
        *,
        batch_size: int | None = None,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        if delivery_handler is None:
            raise ValueError("A delivery handler is required.")

        self.session_factory = session_factory
        self.delivery_handler = delivery_handler
        self.batch_size = max(
            1,
            batch_size or settings.REMINDER_SCHEDULER_BATCH_SIZE,
        )
        self.lease_seconds = max(
            1,
            lease_seconds or settings.REMINDER_PROCESSING_LEASE_SECONDS,
        )
        self.max_attempts = max(
            1,
            max_attempts or settings.REMINDER_MAX_ATTEMPTS,
        )

    async def run_once(self, now: datetime | None = None) -> dict[str, int]:
        """Recover stale claims, claim due work, and process one batch."""

        current = (now or utcnow()).astimezone(timezone.utc)
        stats = {
            "recovered": 0,
            "claimed": 0,
            "delivered": 0,
            "deferred": 0,
            "suppressed": 0,
            "failed": 0,
            "skipped": 0,
        }

        stats["recovered"] = await self.recover_stale_claims(current)

        claimed = await self.claim_batch(current)

        stats["claimed"] = len(claimed)

        for item in claimed:
            outcome = await self.process_claimed(item, current)

            if outcome in stats:
                stats[outcome] += 1

        return stats

    async def recover_stale_claims(
        self,
        now: datetime | None = None,
    ) -> int:
        """Return expired processing leases to the pending queue."""

        current = (now or utcnow()).astimezone(timezone.utc)
        cutoff = current - timedelta(seconds=self.lease_seconds)
        recovered = 0

        async with self.session_factory() as session:
            stmt = select(TaskReminder).where(
                TaskReminder.status == ReminderStatus.PROCESSING,
                TaskReminder.processing_started_at.is_not(None),
                TaskReminder.processing_started_at <= cutoff,
            )

            if _supports_skip_locked(session):
                stmt = stmt.with_for_update(skip_locked=True)

            reminders = list((await session.scalars(stmt)).all())

            for reminder in reminders:
                # A stale claim means the previous attempt was interrupted, so
                # count it and return the row to the pending queue for another
                # worker. Without resetting the status the row would remain
                # ``processing`` and could never be re-claimed.
                reminder.attempt_count += 1
                reminder.claim_token = None
                reminder.processing_started_at = None
                reminder.status = ReminderStatus.PENDING
                reminder.next_attempt_at = current
                reminder.last_error = "processing lease expired"

                if reminder.attempt_count >= self.max_attempts:
                    reminder.status = ReminderStatus.FAILED
                    reminder.next_attempt_at = None
                    reminder.last_error = (
                        "processing lease expired after maximum attempts"
                    )

                recovered += 1

            if recovered:
                await session.commit()
            else:
                await session.rollback()

        if recovered:
            logger.warning(
                "Recovered %s expired reminder processing lease(s).",
                recovered,
            )

        return recovered

    async def claim_batch(
        self,
        now: datetime | None = None,
    ) -> list[ClaimedReminder]:
        """Atomically claim up to one batch of due reminders."""

        current = (now or utcnow()).astimezone(timezone.utc)

        async with self.session_factory() as session:
            stmt = (
                select(TaskReminder.id)
                .where(
                    TaskReminder.status == ReminderStatus.PENDING,
                    TaskReminder.remind_at <= current,
                    or_(
                        TaskReminder.next_attempt_at.is_(None),
                        TaskReminder.next_attempt_at <= current,
                    ),
                )
                .order_by(
                    TaskReminder.remind_at.asc(),
                    TaskReminder.id.asc(),
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
                update(TaskReminder)
                .where(
                    TaskReminder.id.in_(candidate_ids),
                    TaskReminder.status == ReminderStatus.PENDING,
                )
                .values(
                    status=ReminderStatus.PROCESSING,
                    claim_token=token,
                    processing_started_at=current,
                    updated_at=current,
                )
                .returning(TaskReminder.id)
            )

            claimed_ids = list(result.scalars().all())

            await session.commit()

        return [
            ClaimedReminder(reminder_id=reminder_id, claim_token=token)
            for reminder_id in claimed_ids
        ]

    async def process_claimed(
        self,
        claimed: ClaimedReminder,
        now: datetime | None = None,
    ) -> str:
        """Deliver one claimed reminder and finalise its lifecycle."""

        current = (now or utcnow()).astimezone(timezone.utc)

        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(TaskReminder).where(
                        TaskReminder.id == claimed.reminder_id,
                        TaskReminder.status == ReminderStatus.PROCESSING,
                        TaskReminder.claim_token == claimed.claim_token,
                    )
                )
                reminder = result.scalar_one_or_none()

                if reminder is None:
                    return "skipped"

                task = await session.scalar(
                    select(Task).where(Task.id == reminder.task_id)
                )

                if task is None:
                    reminder.status = ReminderStatus.SUPPRESSED
                    reminder.claim_token = None
                    reminder.processing_started_at = None
                    reminder.last_error = "task no longer exists"
                    await session.commit()
                    return "suppressed"

                if task.status not in OPEN_STATUSES:
                    # Do not deliver a stale reminder for a task that has since
                    # been completed or cancelled.
                    reminder.status = ReminderStatus.SUPPRESSED
                    reminder.claim_token = None
                    reminder.processing_started_at = None
                    reminder.next_attempt_at = None
                    reminder.last_error = "task is no longer open"
                    await session.commit()
                    return "suppressed"

                outcome = await self.delivery_handler(
                    session,
                    reminder,
                    task,
                )

                reminder.claim_token = None
                reminder.processing_started_at = None

                if outcome.status == DeliveryOutcomeStatus.DELIVERED:
                    reminder.status = ReminderStatus.DELIVERED
                    reminder.delivered_at = current
                    reminder.next_attempt_at = None
                    reminder.last_error = None
                    await session.commit()
                    return "delivered"

                if outcome.status == DeliveryOutcomeStatus.DEFERRED:
                    reminder.status = ReminderStatus.PENDING
                    reminder.next_attempt_at = (
                        outcome.next_attempt_at or current
                    )
                    reminder.last_error = None
                    await session.commit()
                    return "deferred"

                if outcome.status == DeliveryOutcomeStatus.SUPPRESSED:
                    reminder.status = ReminderStatus.SUPPRESSED
                    reminder.next_attempt_at = None
                    reminder.last_error = (
                        outcome.reason or "delivery suppressed"
                    )[:255]
                    await session.commit()
                    return "suppressed"

                raise ValueError("Unsupported delivery outcome.")

        except asyncio.CancelledError:
            raise

        except Exception as error:
            logger.error(
                "Reminder %s delivery failed (%s).",
                claimed.reminder_id,
                type(error).__name__,
            )
            await self._record_failure(
                claimed=claimed,
                now=current,
                error=error,
            )
            return "failed"

    async def _record_failure(
        self,
        claimed: ClaimedReminder,
        now: datetime,
        error: BaseException,
    ) -> None:
        """Record a bounded retry or terminal failure without content leakage."""

        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(TaskReminder).where(
                        TaskReminder.id == claimed.reminder_id,
                        TaskReminder.status == ReminderStatus.PROCESSING,
                        TaskReminder.claim_token == claimed.claim_token,
                    )
                )
                reminder = result.scalar_one_or_none()

                if reminder is None:
                    return

                reminder.attempt_count += 1
                reminder.claim_token = None
                reminder.processing_started_at = None
                reminder.last_error = _safe_error(error)

                if reminder.attempt_count >= self.max_attempts:
                    reminder.status = ReminderStatus.FAILED
                    reminder.next_attempt_at = None
                else:
                    reminder.status = ReminderStatus.PENDING
                    reminder.next_attempt_at = now + timedelta(
                        seconds=retry_backoff_seconds(
                            reminder.attempt_count
                        )
                    )

                await session.commit()

        except Exception:
            logger.exception(
                "Unable to record failure for reminder %s.",
                claimed.reminder_id,
            )


__all__ = [
    "ClaimedReminder",
    "DeliveryHandler",
    "DeliveryOutcome",
    "DeliveryOutcomeStatus",
    "ReminderWorker",
    "retry_backoff_seconds",
]
