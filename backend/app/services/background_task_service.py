"""In-process, durable background task execution.

The service deliberately uses only Python's asyncio and the existing database.
No Celery, Redis, RabbitMQ, or other external broker is required. Work is
persisted in ``background_tasks`` so status, ownership, and results survive a
process restart.

Safety properties:

* only allowlisted task types and parameters may be submitted;
* handlers reuse existing ASTA services and never execute shell commands;
* a task is always owner-scoped, and cross-owner access is indistinguishable
  from a missing task;
* errors stored in the database are generic and never contain exception text,
  prompts, credentials, or tool output;
* concurrency is bounded, and shutdown cancels incomplete work.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.exceptions.background_task_exceptions import (
    BackgroundTaskLimitError,
    BackgroundTaskNotFoundError,
    BackgroundTaskValidationError,
)
from app.exceptions.conversation_exceptions import ConversationNotFoundError
from app.memory.memory_extractor import memory_extractor
from app.models.background_task import (
    BackgroundTask,
    BackgroundTaskStatus,
    TERMINAL_BACKGROUND_TASK_STATUSES,
)
from app.models.memory import Memory, MemoryStatus
from app.services.conversation_service import conversation_service
from app.services.memory_embedding_service import memory_embedding_service


logger = logging.getLogger(__name__)


QUEUED = BackgroundTaskStatus.QUEUED.value
RUNNING = BackgroundTaskStatus.RUNNING.value
COMPLETED = BackgroundTaskStatus.COMPLETED.value
FAILED = BackgroundTaskStatus.FAILED.value
CANCELLED = BackgroundTaskStatus.CANCELLED.value

TERMINAL_STATUSES = frozenset(TERMINAL_BACKGROUND_TASK_STATUSES)

ALLOWED_TASK_TYPES = frozenset({
    "memory_embedding",
    "memory_extraction",
})

_ALLOWED_PAYLOAD_KEYS = {
    "memory_embedding": frozenset({"memory_id", "force"}),
    "memory_extraction": frozenset({"conversation_id", "private"}),
}

MAX_PAGE_SIZE = 100
MAX_ERROR_MESSAGE_CHARS = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 1 else None


def _validate_payload(task_type: str, payload) -> dict:
    """Return a normalized, strictly allowlisted payload."""

    if payload is None:
        payload = {}

    if not isinstance(payload, dict):
        raise BackgroundTaskValidationError(
            "Task parameters must be an object."
        )

    allowed = _ALLOWED_PAYLOAD_KEYS[task_type]

    if set(payload) - allowed:
        raise BackgroundTaskValidationError(
            "Task parameters contain an unsupported field."
        )

    if task_type == "memory_embedding":

        memory_id = _positive_int(payload.get("memory_id"))

        if memory_id is None:
            raise BackgroundTaskValidationError(
                "memory_id must be a positive integer."
            )

        force = payload.get("force", False)

        if not isinstance(force, bool):
            raise BackgroundTaskValidationError(
                "force must be a boolean."
            )

        return {"memory_id": memory_id, "force": force}

    if task_type == "memory_extraction":

        conversation_id = _positive_int(payload.get("conversation_id"))

        if conversation_id is None:
            raise BackgroundTaskValidationError(
                "conversation_id must be a positive integer."
            )

        private = payload.get("private", False)

        if not isinstance(private, bool):
            raise BackgroundTaskValidationError(
                "private must be a boolean."
            )

        return {
            "conversation_id": conversation_id,
            "private": private,
        }

    raise BackgroundTaskValidationError(
        "Unsupported background task type."
    )


def _safe_result(result) -> dict | None:
    """Return a JSON-serializable metadata mapping, or ``None``."""

    if not isinstance(result, dict):
        return None

    try:
        json.dumps(result)

    except (TypeError, ValueError):
        return None

    return result


def _render_conversation(messages: list, max_chars: int) -> str:
    """Render bounded conversation text without logging its content."""

    lines: list[str] = []
    total = 0
    limit = max(0, int(max_chars or 0))

    for message in messages:

        content = (message.content or "").strip()

        if not content:
            continue

        role = getattr(message.role, "value", message.role)
        line = f"{role}: {content}"

        if total + len(line) + 1 > limit:
            break

        lines.append(line)
        total += len(line) + 1

    return "\n".join(lines)


@dataclass(frozen=True)
class _ClaimedTask:
    task_id: int
    user_id: int
    task_type: str
    payload: dict


class BackgroundTaskService:
    """Create, run, list, cancel, and recover durable background tasks."""

    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory
        self._tasks: dict[int, asyncio.Task] = {}
        self._semaphore: asyncio.Semaphore | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(
                max(1, int(settings.BACKGROUND_TASK_MAX_CONCURRENT or 1))
            )

        return self._semaphore

    def reset_for_tests(self) -> None:
        """Clear process-local state between isolated tests."""

        self._tasks.clear()
        self._semaphore = None

    async def wait_for_tasks(self, timeout: float | None = None) -> None:
        """Await currently tracked runner tasks. Intended for tests/tooling."""

        tasks = [task for task in self._tasks.values() if not task.done()]

        if not tasks:
            return

        await asyncio.wait(tasks, timeout=timeout)

    # ------------------------------------------------------------------
    # Submission and query
    # ------------------------------------------------------------------

    async def create_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_type: str,
        payload: dict | None = None,
    ) -> BackgroundTask:
        """Persist and schedule one allowlisted task for its owner."""

        if not settings.BACKGROUND_TASKS_ENABLED:
            raise BackgroundTaskValidationError(
                "Background task processing is disabled."
            )

        cleaned_type = str(task_type or "").strip().lower()

        if cleaned_type not in ALLOWED_TASK_TYPES:
            raise BackgroundTaskValidationError(
                "Unsupported background task type."
            )

        cleaned_payload = _validate_payload(cleaned_type, payload)

        pending = await session.scalar(
            select(func.count())
            .select_from(BackgroundTask)
            .where(
                BackgroundTask.user_id == user_id,
                BackgroundTask.status.in_([QUEUED, RUNNING]),
            )
        )

        maximum = max(
            1,
            int(settings.BACKGROUND_TASK_MAX_QUEUED_PER_USER or 1),
        )

        if int(pending or 0) >= maximum:
            raise BackgroundTaskLimitError(
                "Too many background tasks are already pending."
            )

        row = BackgroundTask(
            user_id=user_id,
            task_type=cleaned_type,
            status=QUEUED,
            payload=cleaned_payload,
        )

        try:
            session.add(row)
            await session.commit()
            await session.refresh(row)

        except Exception as error:
            await session.rollback()
            logger.error(
                "Failed to create a background task for user %s (%s).",
                user_id,
                type(error).__name__,
            )
            raise

        self._schedule(row.id)

        return row

    async def get_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
    ) -> BackgroundTask:
        """Return one owned task or raise a not-found error."""

        row = await session.scalar(
            select(BackgroundTask).where(
                BackgroundTask.id == task_id,
                BackgroundTask.user_id == user_id,
            )
        )

        if row is None:
            raise BackgroundTaskNotFoundError(
                "Background task not found."
            )

        return row

    async def list_tasks(
        self,
        session: AsyncSession,
        user_id: int,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BackgroundTask], int]:
        """Return a page of owned tasks in newest-first order."""

        cleaned_status = (status or "").strip().lower() or None

        if cleaned_status is not None and cleaned_status not in {
            QUEUED,
            RUNNING,
            COMPLETED,
            FAILED,
            CANCELLED,
        }:
            raise BackgroundTaskValidationError(
                "Unknown background task status."
            )

        conditions = [BackgroundTask.user_id == user_id]

        if cleaned_status is not None:
            conditions.append(BackgroundTask.status == cleaned_status)

        effective_limit = max(1, min(int(limit or 50), MAX_PAGE_SIZE))
        effective_offset = max(0, int(offset or 0))

        total = await session.scalar(
            select(func.count())
            .select_from(BackgroundTask)
            .where(*conditions)
        )

        result = await session.execute(
            select(BackgroundTask)
            .where(*conditions)
            .order_by(
                BackgroundTask.created_at.desc(),
                BackgroundTask.id.desc(),
            )
            .limit(effective_limit)
            .offset(effective_offset)
        )

        return list(result.scalars().all()), int(total or 0)

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
    ) -> BackgroundTask:
        """Cancel an owned queued or running task.

        Cancelling an already terminal task is idempotent and returns the
        current row. Cross-owner access raises a not-found error.
        """

        row = await self.get_task(session, user_id, task_id)

        if row.status in TERMINAL_STATUSES:
            return row

        now = _utcnow()

        result = await session.execute(
            update(BackgroundTask)
            .where(
                BackgroundTask.id == task_id,
                BackgroundTask.user_id == user_id,
                BackgroundTask.status.in_([QUEUED, RUNNING]),
            )
            .values(
                status=CANCELLED,
                cancel_requested=True,
                error_code="cancelled",
                error_message="The task was cancelled.",
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )

        await session.commit()

        if result.rowcount:
            runner = self._tasks.get(task_id)

            if runner is not None and not runner.done():
                runner.cancel()

        await session.refresh(row)

        return row

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _schedule(self, task_id: int) -> None:
        try:
            loop = asyncio.get_running_loop()

        except RuntimeError:
            # No event loop is running. The row remains queued and is
            # cancelled by startup recovery on the next process start.
            logger.warning(
                "Background task %s was queued without a running event loop.",
                task_id,
            )
            return

        task = loop.create_task(
            self._run(task_id),
            name=f"asta-background-task-{task_id}",
        )

        self._tasks[task_id] = task
        task.add_done_callback(
            lambda _task, identifier=task_id: self._tasks.pop(identifier, None)
        )

    async def _run(self, task_id: int) -> None:
        try:
            async with self._get_semaphore():
                claimed = await self._claim(task_id)

                if claimed is None:
                    return

                await self._execute_claimed(claimed)

        except asyncio.CancelledError:
            await self._mark_cancelled(task_id)
            raise

        except Exception as error:
            logger.error(
                "Background task runner failed for task %s (%s).",
                task_id,
                type(error).__name__,
            )
            await self._finish_failed(
                task_id,
                "task_failed",
                "The background task failed.",
            )

    async def _claim(self, task_id: int) -> _ClaimedTask | None:
        """Atomically move one queued row to running."""

        now = _utcnow()

        async with self.session_factory() as session:

            result = await session.execute(
                update(BackgroundTask)
                .where(
                    BackgroundTask.id == task_id,
                    BackgroundTask.status == QUEUED,
                )
                .values(
                    status=RUNNING,
                    started_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )

            await session.commit()

            if result.rowcount != 1:
                return None

            row = await session.scalar(
                select(BackgroundTask).where(
                    BackgroundTask.id == task_id
                )
            )

            if row is None:
                return None

            return _ClaimedTask(
                task_id=row.id,
                user_id=row.user_id,
                task_type=row.task_type,
                payload=dict(row.payload or {}),
            )

    async def _execute_claimed(self, claimed: _ClaimedTask) -> None:
        timeout = max(
            0.05,
            float(settings.BACKGROUND_TASK_MAX_EXECUTION_SECONDS or 0),
        )

        try:
            result = await asyncio.wait_for(
                self._execute(
                    user_id=claimed.user_id,
                    task_type=claimed.task_type,
                    payload=claimed.payload,
                ),
                timeout=timeout,
            )

        except asyncio.CancelledError:
            raise

        except asyncio.TimeoutError:
            await self._finish_failed(
                claimed.task_id,
                "timeout",
                "The task exceeded its execution time limit.",
            )

        except BackgroundTaskValidationError as error:
            await self._finish_failed(
                claimed.task_id,
                "validation_error",
                str(error)[:MAX_ERROR_MESSAGE_CHARS],
            )

        except BackgroundTaskNotFoundError as error:
            await self._finish_failed(
                claimed.task_id,
                "not_found",
                str(error)[:MAX_ERROR_MESSAGE_CHARS],
            )

        except Exception as error:
            logger.error(
                "Background task %s failed (%s).",
                claimed.task_id,
                type(error).__name__,
            )
            await self._finish_failed(
                claimed.task_id,
                "task_failed",
                "The background task failed.",
            )

        else:
            await self._finish_completed(claimed.task_id, result)

    async def _execute(
        self,
        *,
        user_id: int,
        task_type: str,
        payload: dict,
    ) -> dict:
        async with self.session_factory() as session:

            if task_type == "memory_embedding":
                return await self._run_memory_embedding(
                    session=session,
                    user_id=user_id,
                    payload=payload,
                )

            if task_type == "memory_extraction":
                return await self._run_memory_extraction(
                    session=session,
                    user_id=user_id,
                    payload=payload,
                )

            raise BackgroundTaskValidationError(
                "Unsupported background task type."
            )

    async def _run_memory_embedding(
        self,
        *,
        session: AsyncSession,
        user_id: int,
        payload: dict,
    ) -> dict:

        memory_id = int(payload["memory_id"])

        memory = await session.scalar(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
            )
        )

        if memory is None:
            raise BackgroundTaskNotFoundError(
                "The requested memory was not found."
            )

        if memory.status != MemoryStatus.APPROVED:
            raise BackgroundTaskValidationError(
                "Only approved memories can be embedded."
            )

        stored = await memory_embedding_service.ensure_embedding(
            session,
            memory,
            force=bool(payload.get("force", False)),
        )

        return {
            "memory_id": memory_id,
            "embedded": bool(stored),
        }

    async def _run_memory_extraction(
        self,
        *,
        session: AsyncSession,
        user_id: int,
        payload: dict,
    ) -> dict:

        conversation_id = int(payload["conversation_id"])

        try:
            messages, _total = await conversation_service.get_messages(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
                limit=200,
                offset=0,
            )

        except ConversationNotFoundError as error:
            raise BackgroundTaskNotFoundError(
                "The requested conversation was not found."
            ) from error

        text = _render_conversation(
            messages,
            settings.BACKGROUND_TASK_MAX_CONVERSATION_CHARS,
        )

        if not text.strip():
            raise BackgroundTaskValidationError(
                "The conversation has no messages to extract from."
            )

        candidates = await memory_extractor.extract(
            text,
            private=bool(payload.get("private", False)),
        )

        return {
            "conversation_id": conversation_id,
            "candidates": len(candidates),
        }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    async def _finish(
        self,
        task_id: int,
        *,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:

        now = _utcnow()

        async with self.session_factory() as session:

            await session.execute(
                update(BackgroundTask)
                .where(
                    BackgroundTask.id == task_id,
                    BackgroundTask.status == RUNNING,
                )
                .values(
                    status=status,
                    result=_safe_result(result),
                    error_code=error_code,
                    error_message=(
                        error_message[:MAX_ERROR_MESSAGE_CHARS]
                        if error_message
                        else None
                    ),
                    completed_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )

            await session.commit()

    async def _finish_completed(self, task_id: int, result) -> None:
        await self._finish(
            task_id,
            status=COMPLETED,
            result=result,
        )

    async def _finish_failed(
        self,
        task_id: int,
        code: str,
        message: str,
    ) -> None:
        await self._finish(
            task_id,
            status=FAILED,
            error_code=code,
            error_message=message,
        )

    async def _mark_cancelled(self, task_id: int) -> None:
        now = _utcnow()

        async with self.session_factory() as session:

            await session.execute(
                update(BackgroundTask)
                .where(
                    BackgroundTask.id == task_id,
                    BackgroundTask.status.in_([QUEUED, RUNNING]),
                )
                .values(
                    status=CANCELLED,
                    cancel_requested=True,
                    error_code="cancelled",
                    error_message="The task was cancelled.",
                    completed_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )

            await session.commit()

    # ------------------------------------------------------------------
    # Startup recovery, retention, and shutdown
    # ------------------------------------------------------------------

    async def recover_interrupted(self, session: AsyncSession) -> dict:
        """Resolve rows left behind by a previous process.

        Running work cannot be resumed safely, so it becomes failed. Queued
        work was never started and becomes cancelled. Nothing is retried
        automatically.
        """

        now = _utcnow()

        running = await session.execute(
            update(BackgroundTask)
            .where(BackgroundTask.status == RUNNING)
            .values(
                status=FAILED,
                error_code="interrupted",
                error_message=(
                    "The task was interrupted by an application restart."
                ),
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )

        queued = await session.execute(
            update(BackgroundTask)
            .where(BackgroundTask.status == QUEUED)
            .values(
                status=CANCELLED,
                error_code="restart",
                error_message=(
                    "The task was cancelled by an application restart."
                ),
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )

        await session.commit()

        return {
            "failed": int(running.rowcount or 0),
            "cancelled": int(queued.rowcount or 0),
        }

    async def purge_expired(
        self,
        session: AsyncSession,
        now: datetime | None = None,
    ) -> int:
        """Delete terminal tasks older than the configured retention."""

        current = now or _utcnow()
        retention = max(1, int(settings.BACKGROUND_TASK_RETENTION_DAYS or 1))
        cutoff = current - timedelta(days=retention)

        result = await session.execute(
            delete(BackgroundTask)
            .where(
                BackgroundTask.status.in_(list(TERMINAL_STATUSES)),
                BackgroundTask.updated_at < cutoff,
            )
            .execution_options(synchronize_session=False)
        )

        await session.commit()

        return int(result.rowcount or 0)

    async def shutdown(self) -> None:
        """Cancel in-flight work and mark every incomplete row cancelled."""

        tasks = [task for task in self._tasks.values() if not task.done()]

        for task in tasks:
            task.cancel()

        if tasks:

            timeout = max(
                0.1,
                float(
                    settings.BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS or 0
                ),
            )

            _done, pending = await asyncio.wait(tasks, timeout=timeout)

            for task in pending:
                task.cancel()

            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        self._tasks.clear()

        try:
            async with self.session_factory() as session:

                now = _utcnow()

                await session.execute(
                    update(BackgroundTask)
                    .where(
                        BackgroundTask.status.in_([QUEUED, RUNNING])
                    )
                    .values(
                        status=CANCELLED,
                        cancel_requested=True,
                        error_code="shutdown",
                        error_message=(
                            "The task was cancelled during application "
                            "shutdown."
                        ),
                        completed_at=now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )

                await session.commit()

        except Exception as error:
            logger.error(
                "Failed to mark incomplete background tasks cancelled during "
                "shutdown (%s).",
                type(error).__name__,
            )


# Create a reusable service instance
background_task_service = BackgroundTaskService()


__all__ = [
    "ALLOWED_TASK_TYPES",
    "BackgroundTaskService",
    "background_task_service",
]
