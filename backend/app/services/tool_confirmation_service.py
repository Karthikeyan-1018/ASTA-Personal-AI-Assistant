"""Persistence and lifecycle for tool confirmations.

Every confirmation is owner-scoped, expires, is single-use, and is bound to the
canonical argument digest. The executor always uses the stored arguments, so an
approval cannot be replayed with different arguments or by another user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tool_execution import ToolConfirmation
from app.services.tool_metrics_service import tool_metrics_service
from app.tools.enums import ToolCategory, ToolConfirmationStatus
from app.tools.exceptions import (
    ToolConfirmationExpiredError,
    ToolConfirmationLimitError,
    ToolConfirmationMismatchError,
    ToolConfirmationNotFoundError,
    ToolConfirmationResolvedError,
)
from app.tools.schemas import arguments_digest


logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600
MIN_TTL_SECONDS = 30


def _utcnow() -> datetime:
    """Clock hook used by the service; tests patch this function."""

    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _ttl_seconds() -> int:
    try:
        value = int(settings.TOOL_CONFIRMATION_TTL_SECONDS)

    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS

    return max(MIN_TTL_SECONDS, value)


def _max_pending() -> int:
    try:
        value = int(settings.TOOL_CONFIRMATION_MAX_PENDING)

    except (TypeError, ValueError):
        return 10

    return max(1, value)


@dataclass(frozen=True, slots=True)
class ClaimedConfirmation:
    """An immutable snapshot passed to the executor.

    ``arguments`` is intentionally not read back from the row after the claim
    update; the row clears its argument payload atomically.
    """

    id: int
    user_id: int
    conversation_id: int | None
    tool_name: str
    category: ToolCategory
    arguments: dict[str, Any]
    arguments_hash: str


class ToolConfirmationService:

    async def create_pending(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        conversation_id: int | None,
        tool_name: str,
        category: ToolCategory,
        arguments: dict[str, Any],
        arguments_preview: str,
        ttl_seconds: int | None = None,
    ) -> ToolConfirmation:
        """Create one pending confirmation and enforce the pending limit."""

        await self.expire_stale(session=session, user_id=user_id)

        pending = int(
            await session.scalar(
                select(func.count(ToolConfirmation.id)).where(
                    ToolConfirmation.user_id == user_id,
                    ToolConfirmation.status
                    == ToolConfirmationStatus.PENDING,
                )
            )
            or 0
        )

        if pending >= _max_pending():
            raise ToolConfirmationLimitError(
                "Too many confirmations are waiting. Resolve or reject one "
                "before requesting another tool action."
            )

        now = _utcnow()
        ttl = _ttl_seconds() if ttl_seconds is None else max(
            MIN_TTL_SECONDS,
            int(ttl_seconds),
        )

        confirmation = ToolConfirmation(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            category=category,
            status=ToolConfirmationStatus.PENDING,
            arguments=dict(arguments),
            arguments_hash=arguments_digest(arguments),
            arguments_preview=(arguments_preview or "")[:1000],
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            resolved_at=None,
        )

        try:
            session.add(confirmation)
            await session.commit()
            await session.refresh(confirmation)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create a tool confirmation for user %s.",
                user_id,
            )
            raise

        return confirmation

    async def get(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        confirmation_id: int,
    ) -> ToolConfirmation:
        result = await session.execute(
            select(ToolConfirmation).where(
                ToolConfirmation.id == confirmation_id,
                ToolConfirmation.user_id == user_id,
            )
        )

        confirmation = result.scalar_one_or_none()

        if confirmation is None:
            raise ToolConfirmationNotFoundError(
                "Tool confirmation not found."
            )

        return confirmation

    async def list_pending(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        conversation_id: int | None = None,
    ) -> list[ToolConfirmation]:
        await self.expire_stale(session=session, user_id=user_id)

        conditions = [
            ToolConfirmation.user_id == user_id,
            ToolConfirmation.status == ToolConfirmationStatus.PENDING,
        ]

        if conversation_id is not None:
            conditions.append(
                ToolConfirmation.conversation_id == conversation_id
            )

        result = await session.execute(
            select(ToolConfirmation)
            .where(*conditions)
            .order_by(ToolConfirmation.created_at.desc())
        )

        return list(result.scalars().all())

    async def claim(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        confirmation_id: int,
        expected_tool_name: str | None = None,
        expected_arguments: dict[str, Any] | None = None,
        expected_conversation_id: int | None = None,
    ) -> ClaimedConfirmation:
        """Atomically consume a pending confirmation.

        Only one caller can win the ``pending -> executed`` transition. A
        failed or replayed confirm therefore cannot execute a second time.

        A confirmation created inside a conversation remains bound to that
        conversation: a caller presenting a different conversation id is
        rejected even when it owns the confirmation row.
        """

        confirmation = await self.get(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

        now = _utcnow()

        if confirmation.status == ToolConfirmationStatus.EXPIRED:
            raise ToolConfirmationExpiredError(
                "That tool confirmation has expired."
            )

        if confirmation.status != ToolConfirmationStatus.PENDING:
            raise ToolConfirmationResolvedError(
                "That tool confirmation has already been resolved."
            )

        if _aware(confirmation.expires_at) <= now:
            await self._expire_one(
                session=session,
                confirmation=confirmation,
                now=now,
            )
            raise ToolConfirmationExpiredError(
                "That tool confirmation has expired."
            )

        if (
            expected_tool_name is not None
            and confirmation.tool_name != expected_tool_name
        ):
            raise ToolConfirmationMismatchError(
                "That confirmation belongs to a different tool."
            )

        if (
            confirmation.conversation_id is not None
            and expected_conversation_id != confirmation.conversation_id
        ):
            raise ToolConfirmationMismatchError(
                "That confirmation belongs to a different conversation."
            )

        digest = arguments_digest(confirmation.arguments or {})

        if digest != confirmation.arguments_hash:
            raise ToolConfirmationMismatchError(
                "That confirmation no longer matches its original request."
            )

        if (
            expected_arguments is not None
            and arguments_digest(expected_arguments) != digest
        ):
            raise ToolConfirmationMismatchError(
                "Tool arguments changed after approval."
            )

        snapshot_arguments = dict(confirmation.arguments or {})
        snapshot_hash = confirmation.arguments_hash

        result = await session.execute(
            update(ToolConfirmation)
            .where(
                ToolConfirmation.id == confirmation_id,
                ToolConfirmation.user_id == user_id,
                ToolConfirmation.status
                == ToolConfirmationStatus.PENDING,
            )
            .values(
                status=ToolConfirmationStatus.EXECUTED,
                resolved_at=now,
                arguments={},
            )
        )

        if result.rowcount != 1:
            await session.rollback()

            fresh = await self.get(
                session=session,
                user_id=user_id,
                confirmation_id=confirmation_id,
            )

            if fresh.status == ToolConfirmationStatus.EXPIRED:
                raise ToolConfirmationExpiredError(
                    "That tool confirmation has expired."
                )

            raise ToolConfirmationResolvedError(
                "That tool confirmation has already been resolved."
            )

        await session.commit()

        return ClaimedConfirmation(
            id=confirmation_id,
            user_id=user_id,
            conversation_id=confirmation.conversation_id,
            tool_name=confirmation.tool_name,
            category=ToolCategory(confirmation.category),
            arguments=snapshot_arguments,
            arguments_hash=snapshot_hash,
        )

    async def reject(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        confirmation_id: int,
    ) -> ToolConfirmation:
        confirmation = await self.get(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

        now = _utcnow()

        if confirmation.status == ToolConfirmationStatus.EXPIRED:
            raise ToolConfirmationExpiredError(
                "That tool confirmation has expired."
            )

        if confirmation.status != ToolConfirmationStatus.PENDING:
            raise ToolConfirmationResolvedError(
                "That tool confirmation has already been resolved."
            )

        if _aware(confirmation.expires_at) <= now:
            await self._expire_one(
                session=session,
                confirmation=confirmation,
                now=now,
            )
            raise ToolConfirmationExpiredError(
                "That tool confirmation has expired."
            )

        result = await session.execute(
            update(ToolConfirmation)
            .where(
                ToolConfirmation.id == confirmation_id,
                ToolConfirmation.user_id == user_id,
                ToolConfirmation.status
                == ToolConfirmationStatus.PENDING,
            )
            .values(
                status=ToolConfirmationStatus.REJECTED,
                resolved_at=now,
                arguments={},
            )
        )

        if result.rowcount != 1:
            await session.rollback()

            fresh = await self.get(
                session=session,
                user_id=user_id,
                confirmation_id=confirmation_id,
            )

            if fresh.status == ToolConfirmationStatus.EXPIRED:
                raise ToolConfirmationExpiredError(
                    "That tool confirmation has expired."
                )

            raise ToolConfirmationResolvedError(
                "That tool confirmation has already been resolved."
            )

        await session.commit()

        try:
            tool_metrics_service.record_confirmation_rejection()

        except Exception:
            logger.debug(
                "Failed to record confirmation rejection metrics.",
                exc_info=True,
            )

        return await self.get(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

    async def reject_all_pending(
        self,
        session: AsyncSession,
    ) -> int:
        """Invalidate every pending confirmation without executing anything.

        Used by the Phase 12F emergency stop. It is deliberately not
        owner-scoped: the emergency stop is a system-wide safety control.
        """

        now = _utcnow()

        result = await session.execute(
            update(ToolConfirmation)
            .where(
                ToolConfirmation.status == ToolConfirmationStatus.PENDING,
            )
            .values(
                status=ToolConfirmationStatus.REJECTED,
                resolved_at=now,
                arguments={},
            )
        )

        if result.rowcount:
            await session.commit()

        return int(result.rowcount or 0)

    async def mark_failed(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        confirmation_id: int,
        execution_id: int | None = None,
    ) -> None:
        now = _utcnow()

        await session.execute(
            update(ToolConfirmation)
            .where(
                ToolConfirmation.id == confirmation_id,
                ToolConfirmation.user_id == user_id,
                ToolConfirmation.status.in_(
                    (
                        ToolConfirmationStatus.EXECUTED,
                        ToolConfirmationStatus.PENDING,
                    )
                ),
            )
            .values(
                status=ToolConfirmationStatus.FAILED,
                resolved_at=now,
                arguments={},
                execution_id=execution_id,
            )
        )

        await session.commit()

    async def attach_execution(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        confirmation_id: int,
        execution_id: int | None,
    ) -> None:
        if execution_id is None:
            return

        await session.execute(
            update(ToolConfirmation)
            .where(
                ToolConfirmation.id == confirmation_id,
                ToolConfirmation.user_id == user_id,
            )
            .values(execution_id=execution_id)
        )

        await session.commit()

    async def expire_stale(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> int:
        now = _utcnow()

        result = await session.execute(
            update(ToolConfirmation)
            .where(
                ToolConfirmation.user_id == user_id,
                ToolConfirmation.status
                == ToolConfirmationStatus.PENDING,
                ToolConfirmation.expires_at <= now,
            )
            .values(
                status=ToolConfirmationStatus.EXPIRED,
                resolved_at=now,
                arguments={},
            )
        )

        if result.rowcount:
            await session.commit()

        return int(result.rowcount or 0)

    async def _expire_one(
        self,
        session: AsyncSession,
        *,
        confirmation: ToolConfirmation,
        now: datetime,
    ) -> None:
        confirmation.status = ToolConfirmationStatus.EXPIRED
        confirmation.resolved_at = now
        confirmation.arguments = {}
        await session.commit()


tool_confirmation_service = ToolConfirmationService()


__all__ = [
    "ClaimedConfirmation",
    "ToolConfirmationService",
    "tool_confirmation_service",
]
