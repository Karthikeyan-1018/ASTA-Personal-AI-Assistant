"""Controlled lifecycle for the background reminder and recurrence workers.

The scheduler is database-backed: all state lives in PostgreSQL, so a restart
resumes pending work. Startup and shutdown are best-effort and isolated from the
request path; a worker failure is logged and retried on the next poll.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.scheduler.notification_delivery import (
    deliver_in_app_notification,
)
from app.scheduler.recurrence_worker import RecurrenceWorker
from app.scheduler.reminder_worker import ReminderWorker
from app.services.notification_service import notification_service
from app.services.task_deadlines import utcnow
from app.database.connection import AsyncSessionLocal


logger = logging.getLogger(__name__)

UTC = timezone.utc


class BackgroundScheduler:
    """Run reminder and recurrence work on a bounded polling loop."""

    def __init__(
        self,
        *,
        reminder_worker: ReminderWorker | None = None,
        recurrence_worker: RecurrenceWorker | None = None,
        poll_seconds: int | None = None,
        shutdown_timeout_seconds: int | None = None,
        session_factory=AsyncSessionLocal,
    ) -> None:
        self.reminder_worker = reminder_worker or ReminderWorker(
            delivery_handler=deliver_in_app_notification,
        )
        self.recurrence_worker = recurrence_worker or RecurrenceWorker()
        self.session_factory = session_factory
        self.poll_seconds = max(
            1,
            poll_seconds or settings.REMINDER_SCHEDULER_POLL_SECONDS,
        )
        self.shutdown_timeout_seconds = max(
            1,
            shutdown_timeout_seconds
            or settings.REMINDER_SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS,
        )

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_purge_at: datetime | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return

        self._stop_event = asyncio.Event()
        self._running = True
        self._task = asyncio.create_task(
            self._loop(),
            name="asta-background-scheduler",
        )
        logger.info(
            "Reminder scheduler started (poll=%ss, batch=%s).",
            self.poll_seconds,
            self.reminder_worker.batch_size,
        )

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()

        task = self._task
        self._task = None

        if task is None:
            return

        try:
            await asyncio.wait_for(
                task,
                timeout=self.shutdown_timeout_seconds,
            )

        except asyncio.TimeoutError:
            task.cancel()

            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

            logger.warning("Reminder scheduler forced to stop after timeout.")

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception("Reminder scheduler stopped with an error.")

        logger.info("Reminder scheduler stopped.")

    async def run_once(self, now: datetime | None = None) -> dict[str, dict]:
        """Run one cycle. Exposed for tests and operational tooling."""

        current = (now or utcnow()).astimezone(UTC)
        results: dict[str, dict] = {}

        results["reminders"] = await self.reminder_worker.run_once(current)

        if settings.RECURRENCE_GENERATION_ENABLED:
            results["recurrences"] = await self.recurrence_worker.run_once(
                current
            )

        try:
            async with self.session_factory() as session:
                recovered = await notification_service.recover_pending_deliveries(
                    session=session,
                    now=current,
                )

                if recovered:
                    logger.warning(
                        "Recovered %s notification delivery marker(s).",
                        recovered,
                    )
        except Exception:
            logger.exception("Notification recovery failed; will retry.")

        if (
            self._last_purge_at is None
            or current - self._last_purge_at >= timedelta(hours=1)
        ):
            self._last_purge_at = current

            try:
                async with self.session_factory() as session:
                    purged = await notification_service.purge_expired(
                        session=session,
                        now=current,
                    )

                    if purged:
                        logger.info("Purged %s old notification(s).", purged)
            except Exception:
                logger.exception("Notification retention purge failed.")

        return results

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Background scheduler cycle failed; retrying after poll."
                )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except asyncio.TimeoutError:
                continue


__all__ = ["BackgroundScheduler"]
