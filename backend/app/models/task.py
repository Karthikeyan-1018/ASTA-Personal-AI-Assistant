from __future__ import annotations

import enum
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.reminder import TaskReminder
    from app.models.user import User


class TaskCategory(str, enum.Enum):
    """Stable categories for user tasks."""

    COLLEGE = "college"
    CAREER = "career"
    PERSONAL = "personal"
    FITNESS = "fitness"
    PROJECT = "project"
    OTHER = "other"


class TaskPriority(str, enum.Enum):
    """Task priority levels, from lowest to highest urgency."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(str, enum.Enum):
    """Lifecycle states for a task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


def _task_enum(enum_cls, name: str) -> SAEnum:
    """Return a portable, value-backed enum column type."""

    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=_enum_values,
    )


class Task(Base):
    """A structured, user-owned task and optional deadline.

    ``due_date`` and ``due_time`` represent the deadline. A task without a
    ``due_date`` has no deadline. ``timezone`` is an IANA timezone name that
    describes how the wall-clock deadline should be interpreted. ``is_completed``
    mirrors ``status`` and is retained as a convenient, queryable flag.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    category: Mapped[TaskCategory] = mapped_column(
        _task_enum(TaskCategory, "task_category"),
        nullable=False,
        default=TaskCategory.OTHER,
        server_default=TaskCategory.OTHER.value,
    )

    priority: Mapped[TaskPriority] = mapped_column(
        _task_enum(TaskPriority, "task_priority"),
        nullable=False,
        default=TaskPriority.MEDIUM,
        server_default=TaskPriority.MEDIUM.value,
    )

    status: Mapped[TaskStatus] = mapped_column(
        _task_enum(TaskStatus, "task_status"),
        nullable=False,
        default=TaskStatus.PENDING,
        server_default=TaskStatus.PENDING.value,
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Wall-clock time only; the timezone column supplies the offset context.
    due_time: Mapped[time | None] = mapped_column(
        Time(timezone=False),
        nullable=True,
    )

    # IANA timezone name (for example "America/New_York"). Null means the
    # deadline is interpreted in UTC, which is a neutral fallback rather than
    # an assumed user timezone.
    timezone: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped["User"] = relationship(back_populates="tasks")

    reminders: Mapped[list["TaskReminder"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "due_time IS NULL OR due_date IS NOT NULL",
            name="ck_tasks_due_time_requires_due_date",
        ),
        CheckConstraint(
            "timezone IS NULL OR due_date IS NOT NULL",
            name="ck_tasks_timezone_requires_due_date",
        ),
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_category", "user_id", "category"),
        Index("ix_tasks_user_due_date", "user_id", "due_date"),
        Index("ix_tasks_status_due_date", "status", "due_date"),
    )
