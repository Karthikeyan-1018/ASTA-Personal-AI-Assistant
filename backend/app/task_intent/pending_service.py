"""Persistence and lifecycle for confirmation-gated task actions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.task_intent_exceptions import (
    PendingActionAlreadyResolvedError,
    PendingActionExpiredError,
    PendingActionNotFoundError,
)
from app.models.task_action import (
    PendingActionStatus,
    PendingActionType,
    TaskPendingAction,
)
from app.task_intent import clock


logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600
MIN_TTL_SECONDS = 30


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _ttl_seconds() -> int:
    try:
        value = int(settings.TASK_ACTION_CONFIRMATION_TTL_SECONDS)

    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS

    return max(MIN_TTL_SECONDS, value)


class PendingActionService:
    """Database-backed, single-use confirmation records."""

    async def supersede_active(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int,
    ) -> None:
        """Mark every still-pending action in a conversation as superseded."""

        now = clock.utcnow()

        result = await session.execute(
            select(TaskPendingAction).where(
                TaskPendingAction.user_id == user_id,
                TaskPendingAction.conversation_id == conversation_id,
                TaskPendingAction.status == PendingActionStatus.PENDING,
            )
        )

        changed = False

        for action in result.scalars().all():
            if _aware(action.expires_at) <= now:
                action.status = PendingActionStatus.EXPIRED
            else:
                action.status = PendingActionStatus.SUPERSEDED

            action.resolved_at = now
            changed = True

        if changed:
            await session.commit()

    async def create_pending(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int,
        action_type: PendingActionType | str,
        payload: dict[str, Any],
        summary: str,
        target_task_id: int | None = None,
        ttl_seconds: int | None = None,
    ) -> TaskPendingAction:
        """Create one active pending action for a user and conversation."""

        await self.supersede_active(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        now = clock.utcnow()
        ttl = _ttl_seconds() if ttl_seconds is None else max(
            MIN_TTL_SECONDS,
            int(ttl_seconds),
        )

        action = TaskPendingAction(
            user_id=user_id,
            conversation_id=conversation_id,
            action_type=PendingActionType(action_type),
            status=PendingActionStatus.PENDING,
            target_task_id=target_task_id,
            result_task_id=None,
            payload=payload,
            summary=summary,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            resolved_at=None,
        )

        try:
            session.add(action)
            await session.commit()
            await session.refresh(action)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to persist a pending task action for user %s.",
                user_id,
            )
            raise

        return action

    async def get_action(
        self,
        session: AsyncSession,
        user_id: int,
        action_id: int,
    ) -> TaskPendingAction:
        result = await session.execute(
            select(TaskPendingAction).where(
                TaskPendingAction.id == action_id,
                TaskPendingAction.user_id == user_id,
            )
        )

        action = result.scalar_one_or_none()

        if action is None:
            raise PendingActionNotFoundError(
                "Pending action not found."
            )

        return action

    async def list_pending(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int | None = None,
    ) -> list[TaskPendingAction]:
        """Return active pending actions, expiring stale rows first."""

        conditions = [
            TaskPendingAction.user_id == user_id,
            TaskPendingAction.status == PendingActionStatus.PENDING,
        ]

        if conversation_id is not None:
            conditions.append(
                TaskPendingAction.conversation_id == conversation_id
            )

        result = await session.execute(
            select(TaskPendingAction)
            .where(*conditions)
            .order_by(TaskPendingAction.created_at.desc())
        )

        actions = list(result.scalars().all())
        now = clock.utcnow()
        active: list[TaskPendingAction] = []
        changed = False

        for action in actions:
            if _aware(action.expires_at) <= now:
                action.status = PendingActionStatus.EXPIRED
                action.resolved_at = now
                changed = True
            else:
                active.append(action)

        if changed:
            await session.commit()

        return active

    async def get_active(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int,
    ) -> TaskPendingAction | None:
        actions = await self.list_pending(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        return actions[0] if actions else None

    async def claim(
        self,
        session: AsyncSession,
        user_id: int,
        action_id: int,
    ) -> TaskPendingAction:
        """Atomically move a pending action to ``executed``.

        Only one caller can win this transition, which prevents duplicate
        execution when a user confirms twice or confirms through both chat and
        the API at the same time.
        """

        action = await self.get_action(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

        now = clock.utcnow()

        if action.status == PendingActionStatus.PENDING and (
            _aware(action.expires_at) <= now
        ):
            action.status = PendingActionStatus.EXPIRED
            action.resolved_at = now
            await session.commit()

            raise PendingActionExpiredError(
                "That confirmation has expired."
            )

        result = await session.execute(
            update(TaskPendingAction)
            .where(
                TaskPendingAction.id == action_id,
                TaskPendingAction.user_id == user_id,
                TaskPendingAction.status == PendingActionStatus.PENDING,
            )
            .values(
                status=PendingActionStatus.EXECUTED,
                resolved_at=now,
            )
        )

        if result.rowcount != 1:
            await session.rollback()

            fresh = await self.get_action(
                session=session,
                user_id=user_id,
                action_id=action_id,
            )

            if fresh.status == PendingActionStatus.EXPIRED:
                raise PendingActionExpiredError(
                    "That confirmation has expired."
                )

            raise PendingActionAlreadyResolvedError(
                "That action has already been handled."
            )

        await session.commit()

        return await self.get_action(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

    async def reject(
        self,
        session: AsyncSession,
        user_id: int,
        action_id: int,
    ) -> TaskPendingAction:
        action = await self.get_action(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

        now = clock.utcnow()

        if action.status == PendingActionStatus.PENDING and (
            _aware(action.expires_at) <= now
        ):
            action.status = PendingActionStatus.EXPIRED
            action.resolved_at = now
            await session.commit()

            raise PendingActionExpiredError(
                "That confirmation has expired."
            )

        result = await session.execute(
            update(TaskPendingAction)
            .where(
                TaskPendingAction.id == action_id,
                TaskPendingAction.user_id == user_id,
                TaskPendingAction.status == PendingActionStatus.PENDING,
            )
            .values(
                status=PendingActionStatus.REJECTED,
                resolved_at=now,
            )
        )

        if result.rowcount != 1:
            await session.rollback()

            fresh = await self.get_action(
                session=session,
                user_id=user_id,
                action_id=action_id,
            )

            if fresh.status == PendingActionStatus.EXPIRED:
                raise PendingActionExpiredError(
                    "That confirmation has expired."
                )

            raise PendingActionAlreadyResolvedError(
                "That action has already been handled."
            )

        await session.commit()

        return await self.get_action(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

    async def mark_result(
        self,
        session: AsyncSession,
        user_id: int,
        action_id: int,
        result_task_id: int | None,
    ) -> TaskPendingAction:
        result = await session.execute(
            update(TaskPendingAction)
            .where(
                TaskPendingAction.id == action_id,
                TaskPendingAction.user_id == user_id,
                TaskPendingAction.status == PendingActionStatus.EXECUTED,
            )
            .values(result_task_id=result_task_id)
        )

        if result.rowcount == 1:
            await session.commit()

        return await self.get_action(
            session=session,
            user_id=user_id,
            action_id=action_id,
        )

    async def mark_failed(
        self,
        session: AsyncSession,
        user_id: int,
        action_id: int,
    ) -> None:
        now = clock.utcnow()

        await session.execute(
            update(TaskPendingAction)
            .where(
                TaskPendingAction.id == action_id,
                TaskPendingAction.user_id == user_id,
                TaskPendingAction.status == PendingActionStatus.EXECUTED,
            )
            .values(
                status=PendingActionStatus.FAILED,
                resolved_at=now,
            )
        )

        await session.commit()


pending_action_service = PendingActionService()


__all__ = ["PendingActionService", "pending_action_service"]
