"""Owner-scoped tool execution history, statistics, and retention.

History rows contain metadata only: tool name, category, outcome, duration,
confirmation state, and safe error categories. Arguments, results, file
contents, prompts, and credentials are never stored here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tool_execution import ToolExecution
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolHistoryService:

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
    ) -> ToolExecution | None:
        """Persist one execution record without ever storing argument values."""

        if not settings.TOOL_HISTORY_ENABLED:
            return None

        record = ToolExecution(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_name=tool_name[:100],
            category=category,
            status=status,
            confirmation_status=confirmation_status,
            duration_ms=(
                None
                if duration_ms is None
                else max(0, int(round(duration_ms)))
            ),
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
    ) -> tuple[list[ToolExecution], int]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        conditions = [ToolExecution.user_id == user_id]

        if tool_name:
            conditions.append(ToolExecution.tool_name == tool_name)

        if status is not None:
            conditions.append(ToolExecution.status == status)

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

        average_duration = await session.scalar(
            select(func.avg(ToolExecution.duration_ms)).where(
                ToolExecution.user_id == user_id
            )
        )

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


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


tool_history_service = ToolHistoryService()


__all__ = ["ToolHistoryService", "tool_history_service"]
