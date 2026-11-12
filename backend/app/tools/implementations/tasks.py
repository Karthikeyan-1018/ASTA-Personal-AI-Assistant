"""Task read and write tools.

Read tools are safe and owner-scoped. Creating or updating a task is
reversible but still requires explicit confirmation because it changes the
owner's stored data. The owner identity always comes from ``ToolContext``.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, ClassVar

from pydantic import Field

from app.core.config import settings
from app.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import ToolExecutionError
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


def _task_summary(task: Task) -> dict[str, Any]:
    """Return a compact, non-secret task representation."""

    return {
        "id": task.id,
        "title": task.title,
        "description": (task.description or "")[:500] or None,
        "category": _value(task.category),
        "priority": _value(task.priority),
        "status": _value(task.status),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_time": task.due_time.isoformat() if task.due_time else None,
        "timezone": task.timezone,
        "is_completed": bool(task.is_completed),
    }


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


class TaskListInput(StrictModel):
    status: TaskStatus | None = Field(
        default=None,
        description="Filter by task status.",
    )
    priority: TaskPriority | None = Field(
        default=None,
        description="Filter by task priority.",
    )
    category: TaskCategory | None = Field(
        default=None,
        description="Filter by task category.",
    )
    due_from: date | None = Field(
        default=None,
        description="Only tasks due on or after this date (YYYY-MM-DD).",
    )
    due_to: date | None = Field(
        default=None,
        description="Only tasks due on or before this date (YYYY-MM-DD).",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum tasks to return.",
    )


class TaskListTool(BaseTool):
    name: ClassVar[str] = "task_list"
    description: ClassVar[str] = (
        "List the user's tasks, optionally filtered by status, priority, "
        "category, or due-date range. Use before answering questions about "
        "the user's task list or schedule."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = TaskListInput
    output_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tasks": {"type": "array"},
            "total": {"type": "integer"},
        },
    }

    async def execute(
        self,
        context: ToolContext,
        arguments: TaskListInput,
    ) -> ToolResult:
        from app.services.task_service import task_service

        user_id = context.require_user_id()

        tasks, total = await task_service.list_tasks(
            session=context.session,
            user_id=user_id,
            category=arguments.category,
            priority=arguments.priority,
            status=arguments.status,
            due_from=arguments.due_from,
            due_to=arguments.due_to,
            limit=arguments.limit,
        )

        return success_result(
            tool_name=self.name,
            data={
                "tasks": [_task_summary(task) for task in tasks],
                "total": total,
            },
        )


class TaskGetInput(StrictModel):
    task_id: int = Field(
        ge=1,
        description="Identifier of the task to read.",
    )


class TaskGetTool(BaseTool):
    name: ClassVar[str] = "task_get"
    description: ClassVar[str] = (
        "Read one of the user's tasks by its numeric identifier."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = TaskGetInput

    async def execute(
        self,
        context: ToolContext,
        arguments: TaskGetInput,
    ) -> ToolResult:
        from app.exceptions.task_exceptions import TaskNotFoundError
        from app.services.task_service import task_service

        try:
            task = await task_service.get_task(
                session=context.session,
                user_id=context.require_user_id(),
                task_id=arguments.task_id,
            )

        except TaskNotFoundError as error:
            raise ToolExecutionError("Task not found.") from error

        return success_result(
            tool_name=self.name,
            data={"task": _task_summary(task)},
        )


class TaskCreateInput(StrictModel):
    title: str = Field(
        min_length=1,
        max_length=255,
        description="Short task title.",
    )
    description: str | None = Field(
        default=None,
        max_length=5000,
        description="Optional longer description.",
    )
    category: TaskCategory = Field(
        default=TaskCategory.OTHER,
        description="Task category.",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.MEDIUM,
        description="Task priority.",
    )
    due_date: date | None = Field(
        default=None,
        description="Optional due date (YYYY-MM-DD).",
    )
    due_time: time | None = Field(
        default=None,
        description="Optional due time (HH:MM:SS). Requires due_date.",
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="Optional IANA timezone. Requires due_date.",
    )


class TaskCreateTool(BaseTool):
    name: ClassVar[str] = "task_create"
    description: ClassVar[str] = (
        "Create a new task for the user. The action is not applied until the "
        "user explicitly confirms it."
    )
    category: ClassVar[ToolCategory] = ToolCategory.REVERSIBLE
    requires_confirmation: ClassVar[bool] = True
    input_model: ClassVar[type[StrictModel]] = TaskCreateInput

    async def execute(
        self,
        context: ToolContext,
        arguments: TaskCreateInput,
    ) -> ToolResult:
        from app.services.task_service import task_service

        task = await task_service.create_task(
            session=context.session,
            user_id=context.require_user_id(),
            title=arguments.title,
            description=arguments.description,
            category=arguments.category,
            priority=arguments.priority,
            due_date=arguments.due_date,
            due_time=arguments.due_time,
            timezone=arguments.timezone,
        )

        return success_result(
            tool_name=self.name,
            data={"task": _task_summary(task), "created": True},
        )


class TaskUpdateInput(StrictModel):
    task_id: int = Field(
        ge=1,
        description="Identifier of the task to update.",
    )
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    category: TaskCategory | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: date | None = None
    due_time: time | None = None
    timezone: str | None = Field(default=None, max_length=64)


class TaskUpdateTool(BaseTool):
    name: ClassVar[str] = "task_update"
    description: ClassVar[str] = (
        "Update fields on one of the user's tasks. Only supplied fields are "
        "changed. The action is not applied until the user explicitly "
        "confirms it."
    )
    category: ClassVar[ToolCategory] = ToolCategory.REVERSIBLE
    requires_confirmation: ClassVar[bool] = True
    input_model: ClassVar[type[StrictModel]] = TaskUpdateInput

    async def execute(
        self,
        context: ToolContext,
        arguments: TaskUpdateInput,
    ) -> ToolResult:
        from app.exceptions.task_exceptions import (
            TaskNotFoundError,
            TaskValidationError,
        )
        from app.services.task_service import task_service

        changes = arguments.model_dump(exclude_unset=True)
        changes.pop("task_id", None)

        if not changes:
            raise ToolExecutionError(
                "Provide at least one task field to update."
            )

        try:
            task = await task_service.update_task(
                session=context.session,
                user_id=context.require_user_id(),
                task_id=arguments.task_id,
                changes=changes,
            )

        except TaskNotFoundError as error:
            raise ToolExecutionError("Task not found.") from error

        except TaskValidationError as error:
            raise ToolExecutionError(
                "The task update was rejected: invalid field values."
            ) from error

        return success_result(
            tool_name=self.name,
            data={"task": _task_summary(task), "updated": True},
        )


TASK_TOOLS = (
    TaskListTool(),
    TaskGetTool(),
    TaskCreateTool(),
    TaskUpdateTool(),
)


__all__ = [
    "TASK_TOOLS",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskUpdateTool",
]
