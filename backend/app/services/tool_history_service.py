"""Owner-scoped tool execution history, lifecycle, statistics, and retention.

History rows contain metadata only: tool name, category, lifecycle, outcome,
duration, confirmation state, retry count, truncation flag, and safe error
categories. Arguments, results, file contents, prompts, credentials, and raw
idempotency keys are never stored here.

The service supports a two-phase write:

* :meth:`begin` creates a ``pending`` row when a tool call is accepted.
* :meth:`record` finalizes that row with a terminal outcome. Callers that do
  not need restart recovery can keep the legacy one-phase behaviour by calling
  :meth:`record` without an ``execution_id``.

Every write is best effort. A history or metrics failure must never change the
result of a tool call.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tool_execution import ToolExecution
from app.services.tool_metrics_service import tool_metrics_service
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionLifecycle,
    ToolExecutionStatus,
)
from app.tools.lifecycle import (
    IN_FLIGHT_LIFECYCLES,
    LifecycleTransitionError,
    lifecycle_from_status,
    validate_transition,
)


logger = logging.getLogger(__name__)

MAX_IDEMPOTENCY_KEY_CHARS = 200


class DuplicateExecutionError(Exception):
    """The same owner already used this idempotency key."""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__("A duplicate tool request was detected.")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_idempotency_key(value: str | None) -> str | None:
    """Return a stable digest for a client idempotency key.

    Only the digest is persisted. The raw header value never reaches the
    database, logs, or API responses.
    """

    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    cleaned = cleaned[:MAX_IDEMPOTENCY_KEY_CHARS]

    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


class ToolHistoryService:

    async def begin(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        conversation_id: int | None,
        tool_name: str,
        category: ToolCategory,
        lifecycle_status: ToolExecutionLifecycle = (
            ToolExecutionLifecycle.PENDING
        ),
        status: ToolExecutionStatus = ToolExecutionStatus.PENDING,
        confirmation_status: ConfirmationState = (
            ConfirmationState.NOT_REQUIRED
        ),
        started_at: datetime | None = None,
        idempotency_key: str | None = None,
        provider: str | None = None,
    ) -> ToolExecution | None:
        """Create an in-flight history row.

        Raises :class:`DuplicateExecutionError` when an idempotency key is
        supplied and a concurrent writer already claimed it. All other storage
        failures are swallowed so history remains best effort.
        """

        if not settings.TOOL_HISTORY_ENABLED:
            return None

        digest = hash_idempotency_key(idempotency_key)

        record = ToolExecution(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_name=tool_name[:100],
            category=category,
            status=status,
            lifecycle_status=_value(lifecycle_status),
            confirmation_status=confirmation_status,
            started_at=started_at or _utcnow(),
            completed_at=None,
            retry_count=0,
            truncated=False,
            idempotency_key=digest,
            provider=(provider or None) and provider[:50],
        )

        try:
            session.add(record)
            await session.commit()
            await session.refresh(record)

        except IntegrityError:
            await session.rollback()

            if digest is not None:
                raise DuplicateExecutionError(idempotency_key or "")

            logger.exception(
                "Failed to create tool execution history for user %s.",
                user_id,
            )
            return None

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create tool execution history for user %s.",
                user_id,
            )
            return None

        return record

    async def transition(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        execution_id: int,
        lifecycle_status: ToolExecutionLifecycle,
        status: ToolExecutionStatus | None = None,
    ) -> bool:
        """Apply one validated non-terminal lifecycle transition.

        Returns ``False`` when the row is missing, owned by another user, or the
        transition is illegal. The caller must not treat this as an execution
        failure.
        """

        if not settings.TOOL_HISTORY_ENABLED:
            return False

        try:
            record = await self._owned(
                session,
                user_id=user_id,
                execution_id=execution_id,
            )

            if record is None:
                return False

            try:
                validate_transition(
                    record.lifecycle_status,
                    lifecycle_status,
                )

            except LifecycleTransitionError:
                logger.warning(
                    "Ignored illegal tool lifecycle transition %s -> %s "
                    "for execution %s.",
                    _value(record.lifecycle_status),
                    _value(lifecycle_status),
                    execution_id,
                )
                return False

            record.lifecycle_status = _value(lifecycle_status)

            if status is not None:
                record.status = status

            await session.commit()
            await session.refresh(record)
            return True

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update tool execution lifecycle for execution %s.",
                execution_id,
            )
            return False

    async def record(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        conversation_id: int | None,
        tool_name: str,
        category: ToolCategory,
        status: ToolExecutionStatus,
        confirmation_status: ConfirmationState = (
            ConfirmationState.NOT_REQUIRED
        ),
        duration_ms: float | None = None,
        error_category: ToolErrorCategory | str | None = None,
        error_code: str | None = None,
        provider: str | None = None,
        arguments_hash: str | None = None,
        argument_keys: list[str] | None = None,
        result_bytes: int | None = None,
        execution_id: int | None = None,
        lifecycle_status: ToolExecutionLifecycle | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        retry_count: int | None = None,
        truncated: bool | None = None,
        idempotency_key: str | None = None,
    ) -> ToolExecution | None:
        """Persist or finalize one metadata-only execution record.

        When ``execution_id`` refers to the caller's in-flight row, the row is
        updated in place. An illegal or duplicate terminal transition leaves the
        stored lifecycle untouched instead of overwriting a completed outcome.
        """

        if not settings.TOOL_HISTORY_ENABLED:
            return None

        terminal_lifecycle = lifecycle_status or lifecycle_from_status(
            status,
            confirmation_state=confirmation_status,
            error_code=error_code,
        )

        if execution_id is not None:
            updated = await self._finalize(
                session,
                user_id=user_id,
                execution_id=execution_id,
                lifecycle_status=terminal_lifecycle,
                status=status,
                confirmation_status=confirmation_status,
                duration_ms=duration_ms,
                error_category=error_category,
                error_code=error_code,
                provider=provider,
                arguments_hash=arguments_hash,
                argument_keys=argument_keys,
                result_bytes=result_bytes,
                started_at=started_at,
                completed_at=completed_at,
                retry_count=retry_count,
                truncated=truncated,
            )

            if updated is not None:
                return updated

        record = ToolExecution(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_name=tool_name[:100],
            category=category,
            status=status,
            lifecycle_status=_value(terminal_lifecycle),
            confirmation_status=confirmation_status,
            started_at=started_at or _utcnow(),
            completed_at=completed_at or _utcnow(),
            duration_ms=(
                None
                if duration_ms is None
                else max(0, int(round(duration_ms)))
            ),
            retry_count=max(0, int(retry_count or 0)),
            truncated=bool(truncated),
            idempotency_key=hash_idempotency_key(idempotency_key),
            error_category=error_category,
            error_code=(error_code or None) and error_code[:100],
            provider=(provider or None) and provider[:50],
            arguments_hash=arguments_hash,
            argument_keys=sorted(argument_keys or [])[:64] or None,
            result_bytes=(
                None
                if result_bytes is None
                else max(0, int(result_bytes))
            ),
        )

        try:
            session.add(record)
            await session.commit()
            await session.refresh(record)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to record tool execution history for user %s.",
                user_id,
            )
            return None

        return record

    async def list_executions(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        tool_name: str | None = None,
        status: ToolExecutionStatus | None = None,
        lifecycle_status: ToolExecutionLifecycle | None = None,
    ) -> tuple[list[ToolExecution], int]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        conditions = [ToolExecution.user_id == user_id]

        if tool_name:
            conditions.append(ToolExecution.tool_name == tool_name)

        if status is not None:
            conditions.append(ToolExecution.status == status)

        if lifecycle_status is not None:
            conditions.append(
                ToolExecution.lifecycle_status == lifecycle_status
            )

        total = int(
            await session.scalar(
                select(func.count())
                .select_from(ToolExecution)
                .where(*conditions)
            )
            or 0
        )

        result = await session.execute(
            select(ToolExecution)
            .where(*conditions)
            .order_by(
                ToolExecution.created_at.desc(),
                ToolExecution.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all()), total

    async def get_execution(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        execution_id: int,
    ) -> ToolExecution | None:
        """Return one execution row only when it belongs to the owner."""

        return await self._owned(
            session,
            user_id=user_id,
            execution_id=execution_id,
        )

    async def find_by_idempotency_key(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        idempotency_key: str | None,
    ) -> ToolExecution | None:
        """Return the owner's previous execution for a client key, if any."""

        digest = hash_idempotency_key(idempotency_key)

        if digest is None or not settings.TOOL_HISTORY_ENABLED:
            return None

        result = await session.execute(
            select(ToolExecution).where(
                ToolExecution.user_id == user_id,
                ToolExecution.idempotency_key == digest,
            )
        )

        return result.scalar_one_or_none()

    async def recover_interrupted(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
        older_than_seconds: int | None = None,
    ) -> int:
        """Mark stale in-flight attempts as cancelled after a restart.

        Only ``pending``, ``approved``, and ``running`` rows older than the
        configured cutoff are touched. ``awaiting_confirmation`` rows are left
        alone because they are waiting for a user decision, not for a worker.
        """

        if not settings.TOOL_HISTORY_ENABLED:
            return 0

        seconds = (
            settings.TOOL_STALE_EXECUTION_SECONDS
            if older_than_seconds is None
            else int(older_than_seconds)
        )
        seconds = max(0, seconds)

        current = now or _utcnow()
        cutoff = current - timedelta(seconds=seconds)

        result = await session.execute(
            update(ToolExecution)
            .where(
                ToolExecution.lifecycle_status.in_(
                    tuple(item.value for item in IN_FLIGHT_LIFECYCLES)
                ),
                func.coalesce(
                    ToolExecution.started_at,
                    ToolExecution.created_at,
                )
                <= cutoff,
            )
            .values(
                lifecycle_status=ToolExecutionLifecycle.CANCELLED.value,
                status=ToolExecutionStatus.CANCELLED,
                error_category=ToolErrorCategory.EXECUTION,
                error_code="InterruptedExecution",
                completed_at=current,
            )
        )

        recovered = int(result.rowcount or 0)

        if recovered:
            await session.commit()

            try:
                tool_metrics_service.record_recovery(recovered)

            except Exception:
                logger.debug("Failed to update recovery metrics.", exc_info=True)

            logger.warning(
                "Recovered %s interrupted tool execution(s) as cancelled.",
                recovered,
            )

        return recovered

    async def stats(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> dict[str, Any]:
        rows = (
            await session.execute(
                select(
                    ToolExecution.status,
                    func.count(ToolExecution.id),
                    func.avg(ToolExecution.duration_ms),
                )
                .where(ToolExecution.user_id == user_id)
                .group_by(ToolExecution.status)
            )
        ).all()

        by_status = {
            _value(status): int(count)
            for status, count, _average in rows
        }

        total = sum(by_status.values())

        lifecycle_rows = (
            await session.execute(
                select(
                    ToolExecution.lifecycle_status,
                    func.count(ToolExecution.id),
                )
                .where(ToolExecution.user_id == user_id)
                .group_by(ToolExecution.lifecycle_status)
            )
        ).all()

        by_lifecycle = {
            _value(lifecycle): int(count)
            for lifecycle, count in lifecycle_rows
        }

        average_duration = await session.scalar(
            select(func.avg(ToolExecution.duration_ms)).where(
                ToolExecution.user_id == user_id
            )
        )

        provider_failures = int(
            await session.scalar(
                select(func.count(ToolExecution.id)).where(
                    ToolExecution.user_id == user_id,
                    ToolExecution.error_category
                    == ToolErrorCategory.PROVIDER,
                )
            )
            or 0
        )

        total_retries = int(
            await session.scalar(
                select(func.coalesce(func.sum(ToolExecution.retry_count), 0))
                .where(ToolExecution.user_id == user_id)
            )
            or 0
        )

        confirmation_rows = (
            await session.execute(
                select(
                    ToolExecution.confirmation_status,
                    func.count(ToolExecution.id),
                )
                .where(ToolExecution.user_id == user_id)
                .group_by(ToolExecution.confirmation_status)
            )
        ).all()

        confirmation_counts = {
            _value(state): int(count)
            for state, count in confirmation_rows
        }

        success_count = by_status.get("success", 0)
        timeout_count = by_status.get("timeout", 0)
        cancelled_count = by_status.get("cancelled", 0)

        top_rows = (
            await session.execute(
                select(
                    ToolExecution.tool_name,
                    func.count(ToolExecution.id),
                )
                .where(ToolExecution.user_id == user_id)
                .group_by(ToolExecution.tool_name)
                .order_by(func.count(ToolExecution.id).desc())
                .limit(10)
            )
        ).all()

        return {
            "total": total,
            "by_status": by_status,
            "by_lifecycle": by_lifecycle,
            "success_count": success_count,
            "failure_count": max(0, total - success_count),
            "timeout_count": timeout_count,
            "cancelled_count": cancelled_count,
            "provider_failure_count": provider_failures,
            "retry_count": total_retries,
            "confirmation_counts": confirmation_counts,
            "confirmation_approvals": confirmation_counts.get("confirmed", 0),
            "confirmation_rejections": confirmation_counts.get("rejected", 0),
            "average_duration_ms": (
                None
                if average_duration is None
                else round(float(average_duration), 2)
            ),
            "top_tools": [
                {"tool_name": name, "count": int(count)}
                for name, count in top_rows
            ],
        }

    async def purge_expired(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
        retention_days: int | None = None,
    ) -> int:
        days = (
            settings.TOOL_EXECUTION_RETENTION_DAYS
            if retention_days is None
            else int(retention_days)
        )

        if days <= 0:
            return 0

        current = now or _utcnow()
        cutoff = current - timedelta(days=days)

        result = await session.execute(
            delete(ToolExecution).where(ToolExecution.created_at < cutoff)
        )

        await session.commit()

        return int(result.rowcount or 0)

    async def _owned(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        execution_id: int,
    ) -> ToolExecution | None:
        result = await session.execute(
            select(ToolExecution).where(
                ToolExecution.id == execution_id,
                ToolExecution.user_id == user_id,
            )
        )

        return result.scalar_one_or_none()

    async def _finalize(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        execution_id: int,
        lifecycle_status: ToolExecutionLifecycle,
        status: ToolExecutionStatus,
        confirmation_status: ConfirmationState,
        duration_ms: float | None,
        error_category: ToolErrorCategory | str | None,
        error_code: str | None,
        provider: str | None,
        arguments_hash: str | None,
        argument_keys: list[str] | None,
        result_bytes: int | None,
        started_at: datetime | None,
        completed_at: datetime | None,
        retry_count: int | None,
        truncated: bool | None,
    ) -> ToolExecution | None:
        try:
            record = await self._owned(
                session,
                user_id=user_id,
                execution_id=execution_id,
            )

            if record is None:
                return None

            try:
                validate_transition(
                    record.lifecycle_status,
                    lifecycle_status,
                )

            except LifecycleTransitionError:
                logger.warning(
                    "Ignored illegal terminal tool lifecycle transition %s -> "
                    "%s for execution %s.",
                    _value(record.lifecycle_status),
                    _value(lifecycle_status),
                    execution_id,
                )
                return record

            record.status = status
            record.lifecycle_status = _value(lifecycle_status)
            record.confirmation_status = confirmation_status
            record.duration_ms = (
                None
                if duration_ms is None
                else max(0, int(round(duration_ms)))
            )
            record.error_category = error_category
            record.error_code = (error_code or None) and error_code[:100]
            record.provider = (provider or None) and provider[:50]
            record.result_bytes = (
                None
                if result_bytes is None
                else max(0, int(result_bytes))
            )
            record.completed_at = completed_at or _utcnow()

            if started_at is not None:
                record.started_at = record.started_at or started_at

            if retry_count is not None:
                record.retry_count = max(0, int(retry_count))

            if truncated is not None:
                record.truncated = bool(truncated)

            if arguments_hash is not None:
                record.arguments_hash = arguments_hash

            if argument_keys is not None:
                record.argument_keys = (
                    sorted(argument_keys)[:64] or None
                )

            await session.commit()
            await session.refresh(record)
            return record

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to finalize tool execution history for execution %s.",
                execution_id,
            )
            return None


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


tool_history_service = ToolHistoryService()


__all__ = [
    "DuplicateExecutionError",
    "MAX_IDEMPOTENCY_KEY_CHARS",
    "ToolHistoryService",
    "hash_idempotency_key",
    "tool_history_service",
]
