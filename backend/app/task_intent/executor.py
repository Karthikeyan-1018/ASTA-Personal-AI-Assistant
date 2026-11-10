"""Execution of a confirmed pending action through the existing TaskService.

This module is the only place where a natural-language action becomes a
database mutation. It is called exclusively after a pending action has been
atomically claimed, and it revalidates ownership and current task state first.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.task_exceptions import TaskValidationError
from app.models.task import Task, TaskStatus
from app.models.task_action import PendingActionType, TaskPendingAction
from app.services.task_service import task_service
from app.task_intent.schemas import TaskChanges, TaskDraft


logger = logging.getLogger(__name__)


class TaskActionExecutor:
    """Apply a validated pending action using the existing task service."""

    async def execute(
        self,
        session: AsyncSession,
        user_id: int,
        action: TaskPendingAction,
    ) -> Task | None:

        action_type = PendingActionType(action.action_type)
        payload = dict(action.payload or {})

        if action_type == PendingActionType.CREATE:
            draft = TaskDraft.model_validate(payload)

            if not draft.title:
                raise TaskValidationError(
                    "A task title is required."
                )

            return await task_service.create_task(
                session=session,
                user_id=user_id,
                title=draft.title,
                description=draft.description,
                category=draft.category,
                priority=draft.priority,
                due_date=draft.due_date,
                due_time=draft.due_time,
                timezone=draft.timezone,
            )

        if action.target_task_id is None:
            raise TaskValidationError(
                "This action no longer has a target task."
            )

        # Ownership and existence are revalidated immediately before the
        # mutation. ``get_task`` filters by ``user_id``.
        task = await task_service.get_task(
            session=session,
            user_id=user_id,
            task_id=action.target_task_id,
        )

        if action_type == PendingActionType.UPDATE:
            changes = TaskChanges.model_validate(payload).model_dump(
                exclude_none=True,
                mode="python",
            )

            if not changes:
                raise TaskValidationError(
                    "There are no changes to apply."
                )

            return await task_service.update_task(
                session=session,
                user_id=user_id,
                task_id=task.id,
                changes=changes,
            )

        if action_type == PendingActionType.COMPLETE:
            if task.status == TaskStatus.CANCELLED:
                raise TaskValidationError(
                    "A cancelled task cannot be completed."
                )

            return await task_service.complete_task(
                session=session,
                user_id=user_id,
                task_id=task.id,
            )

        if action_type == PendingActionType.REOPEN:
            return await task_service.reopen_task(
                session=session,
                user_id=user_id,
                task_id=task.id,
            )

        if action_type == PendingActionType.CANCEL:
            if task.status == TaskStatus.COMPLETED:
                raise TaskValidationError(
                    "A completed task cannot be cancelled."
                )

            return await task_service.change_status(
                session=session,
                user_id=user_id,
                task_id=task.id,
                status=TaskStatus.CANCELLED,
            )

        if action_type == PendingActionType.DELETE:
            await task_service.delete_task(
                session=session,
                user_id=user_id,
                task_id=task.id,
            )
            return None

        raise TaskValidationError("Unsupported task action.")


task_action_executor = TaskActionExecutor()


__all__ = ["TaskActionExecutor", "task_action_executor"]
