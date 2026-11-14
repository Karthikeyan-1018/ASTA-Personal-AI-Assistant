"""Durable, owner-scoped records for in-process background tasks.

Only allowlisted task types may be created. ``payload`` contains the small,
validated parameters needed by a handler (for example a memory id). ``result``
contains metadata-only output. Neither column may contain prompts, credentials,
raw tool output, or arbitrary commands.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class BackgroundTaskStatus(str, enum.Enum):
    """Lifecycle states for one background task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_BACKGROUND_TASK_STATUSES = (
    BackgroundTaskStatus.COMPLETED.value,
    BackgroundTaskStatus.FAILED.value,
    BackgroundTaskStatus.CANCELLED.value,
)


class BackgroundTask(Base):
    """One durable background task owned by a user."""

    __tablename__ = "background_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    task_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=BackgroundTaskStatus.QUEUED.value,
        server_default=BackgroundTaskStatus.QUEUED.value,
    )

    # Handler-specific, validated parameters. Never arbitrary commands.
    payload: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Metadata-only output. Never prompts, responses, or raw tool output.
    result: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    cancel_requested: Mapped[bool] = mapped_column(
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

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', "
            "'cancelled')",
            name="background_task_status",
        ),
        Index("ix_background_tasks_user_id", "user_id"),
        Index("ix_background_tasks_task_type", "task_type"),
        Index("ix_background_tasks_status", "status"),
        Index(
            "ix_background_tasks_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_background_tasks_user_status",
            "user_id",
            "status",
        ),
    )


__all__ = [
    "BackgroundTask",
    "BackgroundTaskStatus",
    "TERMINAL_BACKGROUND_TASK_STATUSES",
]
