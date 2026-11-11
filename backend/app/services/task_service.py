"""User-scoped task persistence, lifecycle, and deadline queries."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.task_exceptions import (
    TaskNotFoundError,
    TaskValidationError,
)
from app.models.task import Task, TaskCategory, TaskPriority, TaskStatus
from app.services.task_deadlines import (
    OPEN_STATUSES,
    DeadlineState,
    canonical_timezone,
    classify_deadline,
    deadline_instant,
    local_date,
    utcnow,
)


logger = logging.getLogger(__name__)


MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_UPCOMING_DAYS = 365

ALLOWED_SORT_FIELDS = {
    "created_at",
    "updated_at",
    "due_date",
    "title",
    "category",
    "priority",
    "status",
}

ALLOWED_SORT_ORDERS = {"asc", "desc"}

ALLOWED_UPDATE_FIELDS = {
    "title",
    "description",
    "category",
    "priority",
    "status",
    "due_date",
    "due_time",
    "timezone",
}

PRIORITY_ORDER = case(
    (Task.priority == TaskPriority.URGENT, 0),
    (Task.priority == TaskPriority.HIGH, 1),
    (Task.priority == TaskPriority.MEDIUM, 2),
    (Task.priority == TaskPriority.LOW, 3),
    else_=4,
)

STATUS_ORDER = case(
    (Task.status == TaskStatus.IN_PROGRESS, 0),
    (Task.status == TaskStatus.PENDING, 1),
    (Task.status == TaskStatus.COMPLETED, 2),
    (Task.status == TaskStatus.CANCELLED, 3),
    else_=4,
)


def _utcnow() -> datetime:
    """Clock hook used by API calls; tests can patch this function."""

    return utcnow()


def _normalise_title(title: Any) -> str:
    cleaned = str(title or "").strip()

    if not cleaned:
        raise TaskValidationError("Task title cannot be empty.")

    if len(cleaned) > MAX_TITLE_LENGTH:
        raise TaskValidationError(
            "Task title cannot exceed "
            f"{MAX_TITLE_LENGTH} characters."
        )

    return cleaned


def _normalise_description(description: Any) -> str | None:
    if description is None:
        return None

    cleaned = str(description).strip()

    if not cleaned:
        return None

    if len(cleaned) > MAX_DESCRIPTION_LENGTH:
        raise TaskValidationError(
            "Task description cannot exceed "
            f"{MAX_DESCRIPTION_LENGTH} characters."
        )

    return cleaned


def _normalise_enum(value: Any, enum_cls, label: str):
    if isinstance(value, enum_cls):
        return value

    try:
        return enum_cls(str(value).strip().lower())

    except (ValueError, TypeError) as error:
        allowed = ", ".join(item.value for item in enum_cls)
        raise TaskValidationError(
            f"Unsupported task {label}. Allowed: {allowed}."
        ) from error


def _validate_due_fields(
    due_date: date | None,
    due_time: time | None,
    timezone_name: str | None
) -> tuple[date | None, time | None, str | None]:

    if due_time is not None and due_date is None:
        raise TaskValidationError("due_time requires due_date.")

    if timezone_name not in (None, "") and due_date is None:
        raise TaskValidationError("timezone requires due_date.")

    canonical = canonical_timezone(timezone_name) if due_date is not None else None

    return due_date, due_time, canonical


def _normalise_days(days: int | None, default: int) -> int:
    if days is None:
        days = default

    try:
        value = int(days)

    except (TypeError, ValueError) as error:
        raise TaskValidationError("days must be an integer.") from error

    if value < 1 or value > MAX_UPCOMING_DAYS:
        raise TaskValidationError(
            f"days must be between 1 and {MAX_UPCOMING_DAYS}."
        )

    return value


def _apply_status(
    task: Task,
    status: TaskStatus,
    now: datetime
) -> None:
    """Apply a status transition while maintaining completion metadata."""

    task.status = status

    if status == TaskStatus.COMPLETED:
        task.is_completed = True

        if task.completed_at is None:
            task.completed_at = now

    else:
        task.is_completed = False
        task.completed_at = None


class TaskService:
    """Asynchronous CRUD, lifecycle, filtering, and deadline operations."""

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _prepare_task(
        self,
        user_id: int,
        title: str,
        description: str | None = None,
        category: TaskCategory | str = TaskCategory.OTHER,
        priority: TaskPriority | str = TaskPriority.MEDIUM,
        status: TaskStatus | str = TaskStatus.PENDING,
        due_date: date | None = None,
        due_time: time | None = None,
        timezone: str | None = None,
        now: datetime | None = None,
    ) -> Task:
        """Build a validated, unattached task instance."""

        current = now or _utcnow()

        normalised_category = _normalise_enum(
            category, TaskCategory, "category"
        )
        normalised_priority = _normalise_enum(
            priority, TaskPriority, "priority"
        )
        normalised_status = _normalise_enum(status, TaskStatus, "status")

        due_date, due_time, timezone_name = _validate_due_fields(
            due_date=due_date,
            due_time=due_time,
            timezone_name=timezone,
        )

        task = Task(
            user_id=user_id,
            title=_normalise_title(title),
            description=_normalise_description(description),
            category=normalised_category,
            priority=normalised_priority,
            due_date=due_date,
            due_time=due_time,
            timezone=timezone_name,
            is_completed=False,
        )

        _apply_status(task, normalised_status, current)

        return task

    async def create_task(
        self,
        session: AsyncSession,
        user_id: int,
        title: str,
        description: str | None = None,
        category: TaskCategory | str = TaskCategory.OTHER,
        priority: TaskPriority | str = TaskPriority.MEDIUM,
        status: TaskStatus | str = TaskStatus.PENDING,
        due_date: date | None = None,
        due_time: time | None = None,
        timezone: str | None = None,
        now: datetime | None = None,
    ) -> Task:

        task = self._prepare_task(
            user_id=user_id,
            title=title,
            description=description,
            category=category,
            priority=priority,
            status=status,
            due_date=due_date,
            due_time=due_time,
            timezone=timezone,
            now=now,
        )

        try:
            session.add(task)
            await session.commit()
            await session.refresh(task)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create a task for user %s.",
                user_id,
            )
            raise

        return task

    async def create_task_in_session(
        self,
        session: AsyncSession,
        user_id: int,
        title: str,
        description: str | None = None,
        category: TaskCategory | str = TaskCategory.OTHER,
        priority: TaskPriority | str = TaskPriority.MEDIUM,
        status: TaskStatus | str = TaskStatus.PENDING,
        due_date: date | None = None,
        due_time: time | None = None,
        timezone: str | None = None,
        now: datetime | None = None,
    ) -> Task:
        """Build, add, and flush a task without committing.

        Used by recurrence generation so the occurrence task and its unique
        occurrence link are committed atomically.
        """

        task = self._prepare_task(
            user_id=user_id,
            title=title,
            description=description,
            category=category,
            priority=priority,
            status=status,
            due_date=due_date,
            due_time=due_time,
            timezone=timezone,
            now=now,
        )

        session.add(task)
        await session.flush()

        return task

    async def get_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int
    ) -> Task:
        """Return an owned task or raise ``TaskNotFoundError``."""

        result = await session.execute(
            select(Task).where(
                Task.id == task_id,
                Task.user_id == user_id,
            )
        )

        task = result.scalar_one_or_none()

        if task is None:
            raise TaskNotFoundError("Task not found.")

        return task

    async def list_tasks(
        self,
        session: AsyncSession,
        user_id: int,
        category: TaskCategory | str | None = None,
        priority: TaskPriority | str | None = None,
        status: TaskStatus | str | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[Task], int]:
        """List a user's tasks with filters, pagination, and sorting."""

        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise TaskValidationError(
                f"limit must be between 1 and {MAX_PAGE_SIZE}."
            )

        if offset < 0:
            raise TaskValidationError("offset cannot be negative.")

        if sort_by not in ALLOWED_SORT_FIELDS:
            allowed = ", ".join(sorted(ALLOWED_SORT_FIELDS))
            raise TaskValidationError(
                f"Unsupported sort field. Allowed: {allowed}."
            )

        if sort_order not in ALLOWED_SORT_ORDERS:
            raise TaskValidationError("sort_order must be 'asc' or 'desc'.")

        conditions = [Task.user_id == user_id]

        if category is not None:
            conditions.append(
                Task.category == _normalise_enum(
                    category, TaskCategory, "category"
                )
            )

        if priority is not None:
            conditions.append(
                Task.priority == _normalise_enum(
                    priority, TaskPriority, "priority"
                )
            )

        if status is not None:
            conditions.append(
                Task.status == _normalise_enum(status, TaskStatus, "status")
            )

        if due_from is not None:
            conditions.append(Task.due_date >= due_from)

        if due_to is not None:
            conditions.append(Task.due_date <= due_to)

        total = int(
            await session.scalar(
                select(func.count())
                .select_from(Task)
                .where(*conditions)
            ) or 0
        )

        if sort_by == "priority":
            column = PRIORITY_ORDER
        elif sort_by == "status":
            column = STATUS_ORDER
        elif sort_by == "title":
            column = func.lower(Task.title)
        else:
            column = getattr(Task, sort_by)

        if sort_order == "asc":
            ordering = column.asc()
        else:
            ordering = column.desc()

        if sort_by in {"due_date", "title"}:
            ordering = ordering.nulls_last()

        query = (
            select(Task)
            .where(*conditions)
            .order_by(ordering, Task.id.asc())
            .limit(limit)
            .offset(offset)
        )

        result = await session.execute(query)

        return list(result.scalars().all()), total

    async def update_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        changes: dict[str, Any],
        now: datetime | None = None,
    ) -> Task:
        """Partially update an owned task.

        ``changes`` uses only fields supplied by the caller, so explicit
        ``None`` values can clear optional deadline fields.
        """

        changes = dict(changes or {})

        if not changes:
            raise TaskValidationError(
                "Provide at least one field to update."
            )

        unknown = set(changes) - ALLOWED_UPDATE_FIELDS

        if unknown:
            raise TaskValidationError(
                "Unsupported task field(s): "
                + ", ".join(sorted(unknown))
                + "."
            )

        task = await self.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        current = now or _utcnow()

        if "due_date" in changes and changes["due_date"] is None:
            if changes.get("due_time") is not None:
                raise TaskValidationError(
                    "due_time cannot be set without due_date."
                )

            if changes.get("timezone") is not None:
                raise TaskValidationError(
                    "timezone cannot be set without due_date."
                )

            # Clearing a due date also clears its dependent fields.
            changes["due_time"] = None
            changes["timezone"] = None

        due_date, due_time, timezone_name = _validate_due_fields(
            due_date=changes.get("due_date", task.due_date),
            due_time=changes.get("due_time", task.due_time),
            timezone_name=changes.get("timezone", task.timezone),
        )

        if "title" in changes:
            task.title = _normalise_title(changes["title"])

        if "description" in changes:
            task.description = _normalise_description(
                changes["description"]
            )

        if "category" in changes:
            task.category = _normalise_enum(
                changes["category"], TaskCategory, "category"
            )

        if "priority" in changes:
            task.priority = _normalise_enum(
                changes["priority"], TaskPriority, "priority"
            )

        if "status" in changes:
            normalised_status = _normalise_enum(
                changes["status"], TaskStatus, "status"
            )
            _apply_status(task, normalised_status, current)

        task.due_date = due_date
        task.due_time = due_time
        task.timezone = timezone_name

        try:
            await session.commit()
            await session.refresh(task)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update task %s for user %s.",
                task_id,
                user_id,
            )
            raise

        return task

    async def delete_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int
    ) -> None:

        task = await self.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        try:
            await session.delete(task)
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to delete task %s for user %s.",
                task_id,
                user_id,
            )
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def complete_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        now: datetime | None = None,
    ) -> Task:

        task = await self.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        if task.status == TaskStatus.COMPLETED:
            return task

        _apply_status(task, TaskStatus.COMPLETED, now or _utcnow())

        try:
            await session.commit()
            await session.refresh(task)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to complete task %s for user %s.",
                task_id,
                user_id,
            )
            raise

        return task

    async def reopen_task(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        now: datetime | None = None,
    ) -> Task:

        task = await self.get_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
        )

        _apply_status(task, TaskStatus.PENDING, now or _utcnow())

        try:
            await session.commit()
            await session.refresh(task)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to reopen task %s for user %s.",
                task_id,
                user_id,
            )
            raise

        return task

    async def change_priority(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        priority: TaskPriority | str,
    ) -> Task:

        return await self.update_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
            changes={"priority": priority},
        )

    async def change_status(
        self,
        session: AsyncSession,
        user_id: int,
        task_id: int,
        status: TaskStatus | str,
        now: datetime | None = None,
    ) -> Task:

        return await self.update_task(
            session=session,
            user_id=user_id,
            task_id=task_id,
            changes={"status": status},
            now=now,
        )

    # ------------------------------------------------------------------
    # Filtered retrieval
    # ------------------------------------------------------------------

    async def get_by_category(
        self,
        session: AsyncSession,
        user_id: int,
        category: TaskCategory | str,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[Task]:

        tasks, _ = await self.list_tasks(
            session=session,
            user_id=user_id,
            category=category,
            limit=limit,
        )

        return tasks

    async def get_by_priority(
        self,
        session: AsyncSession,
        user_id: int,
        priority: TaskPriority | str,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[Task]:

        tasks, _ = await self.list_tasks(
            session=session,
            user_id=user_id,
            priority=priority,
            limit=limit,
        )

        return tasks

    async def get_by_status(
        self,
        session: AsyncSession,
        user_id: int,
        status: TaskStatus | str,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[Task]:

        tasks, _ = await self.list_tasks(
            session=session,
            user_id=user_id,
            status=status,
            limit=limit,
        )

        return tasks

    async def get_open_tasks(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> list[Task]:
        """Return all actionable (pending/in-progress) tasks."""

        result = await session.execute(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.status.in_(OPEN_STATUSES),
            )
            .order_by(Task.id.asc())
        )

        return list(result.scalars().all())

    async def get_open(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[Task]:
        """Return pending and in-progress tasks, most urgent first."""

        result = await session.execute(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.status.in_(OPEN_STATUSES),
            )
            .order_by(
                PRIORITY_ORDER.asc(),
                Task.due_date.asc().nulls_last(),
                Task.id.asc(),
            )
            .limit(limit)
        )

        return list(result.scalars().all())

    async def get_pending(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[Task]:

        return await self.get_by_status(
            session=session,
            user_id=user_id,
            status=TaskStatus.PENDING,
            limit=limit,
        )

    async def get_in_progress(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int = MAX_PAGE_SIZE,
    ) -> list[Task]:

        return await self.get_by_status(
            session=session,
            user_id=user_id,
            status=TaskStatus.IN_PROGRESS,
            limit=limit,
        )

    async def get_completed(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[Task], int]:

        return await self.list_tasks(
            session=session,
            user_id=user_id,
            status=TaskStatus.COMPLETED,
            limit=limit,
            offset=offset,
            sort_by="updated_at",
            sort_order="desc",
        )

    # ------------------------------------------------------------------
    # Deadline retrieval
    # ------------------------------------------------------------------

    async def _open_deadline_tasks(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> list[Task]:

        result = await session.execute(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.due_date.is_not(None),
                Task.status.in_(OPEN_STATUSES),
            )
            .order_by(Task.id.asc())
        )

        return list(result.scalars().all())

    async def get_overdue(
        self,
        session: AsyncSession,
        user_id: int,
        now: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Task]:
        """Return open tasks whose deadline instant has passed."""

        current = now or _utcnow()
        tasks = await self._open_deadline_tasks(session, user_id)

        overdue = []

        for task in tasks:
            state = classify_deadline(task, current, 1)

            if state == DeadlineState.OVERDUE:
                overdue.append(task)

        overdue.sort(
            key=lambda task: (
                deadline_instant(task),
                task.id,
            )
        )

        return overdue[:max(1, limit)]

    async def get_upcoming(
        self,
        session: AsyncSession,
        user_id: int,
        days: int | None = None,
        now: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Task]:
        """Return open tasks due today, tomorrow, or within ``days`` days."""

        horizon = _normalise_days(days, settings.TASK_DEFAULT_UPCOMING_DAYS)
        current = now or _utcnow()
        tasks = await self._open_deadline_tasks(session, user_id)

        relevant_states = {
            DeadlineState.TODAY,
            DeadlineState.TOMORROW,
            DeadlineState.UPCOMING,
        }

        candidates = [
            task
            for task in tasks
            if classify_deadline(task, current, horizon) in relevant_states
        ]

        candidates.sort(
            key=lambda task: (
                deadline_instant(task),
                task.id,
            )
        )

        return candidates[:max(1, limit)]

    async def _get_due_on_local_date(
        self,
        session: AsyncSession,
        user_id: int,
        target_offset_days: int,
        now: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Task]:

        current = now or _utcnow()
        tasks = await self._open_deadline_tasks(session, user_id)

        matches = [
            task
            for task in tasks
            if task.due_date
            == local_date(task, current) + timedelta(days=target_offset_days)
        ]

        matches.sort(
            key=lambda task: (
                deadline_instant(task),
                task.id,
            )
        )

        return matches[:max(1, limit)]

    async def get_due_today(
        self,
        session: AsyncSession,
        user_id: int,
        now: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Task]:
        """Return open tasks due today in their own timezone."""

        return await self._get_due_on_local_date(
            session=session,
            user_id=user_id,
            target_offset_days=0,
            now=now,
            limit=limit,
        )

    async def get_due_tomorrow(
        self,
        session: AsyncSession,
        user_id: int,
        now: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Task]:
        """Return open tasks due tomorrow in their own timezone."""

        return await self._get_due_on_local_date(
            session=session,
            user_id=user_id,
            target_offset_days=1,
            now=now,
            limit=limit,
        )

    async def get_due_within(
        self,
        session: AsyncSession,
        user_id: int,
        days: int,
        now: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Task]:
        """Return open tasks whose deadline is within the next ``days``."""

        horizon = _normalise_days(days, settings.TASK_DEFAULT_UPCOMING_DAYS)
        current = now or _utcnow()
        cutoff = current + timedelta(days=horizon)
        tasks = await self._open_deadline_tasks(session, user_id)

        candidates = []

        for task in tasks:
            instant = deadline_instant(task)

            if instant is not None and current < instant <= cutoff:
                candidates.append(task)

        candidates.sort(
            key=lambda task: (
                deadline_instant(task),
                task.id,
            )
        )

        return candidates[:max(1, limit)]


task_service = TaskService()
