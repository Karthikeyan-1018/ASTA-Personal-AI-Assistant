"""Task-scoped reminders and their scheduling metadata.

A reminder belongs to exactly one task and one owner. It stores an absolute UTC
instant together with the relative offset that produced it (when applicable).
Task deadlines remain wall-clock values interpreted through their IANA timezone;
the reminder service resolves them to UTC using the existing deadline helpers.

No notification delivery is performed in Phase 10A. The status values are
defined now so later scheduler and notification phases can extend behaviour
without changing the stored enum.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.task import Task


class ReminderStatus(str, enum.Enum):
    """Lifecycle of a scheduled task reminder.

    ``processing`` is an internal lease state. A worker claims a pending
    reminder by moving it to ``processing`` with a unique claim token. The
    reminder only becomes ``delivered`` after the delivery handler has
    successfully persisted the in-app notification.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


def _reminder_enum(enum_cls, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=_enum_values,
    )


class TaskReminder(Base):
    """A single scheduled reminder for an owned task.

    ``remind_at`` is the authoritative scheduling instant in UTC.
    ``offset_minutes`` records how the reminder relates to the task deadline:
    ``0`` means at the deadline, positive values mean before it, and ``NULL``
    means the user supplied an explicit absolute time.

    No free-form notification body is stored here. The later notification
    phase derives a minimal, owner-only message from the task title and
    deadline; conversation, memory, and credential data are never copied.
    """

    __tablename__ = "task_reminders"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    remind_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    offset_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    status: Mapped[ReminderStatus] = mapped_column(
        _reminder_enum(ReminderStatus, "task_reminder_status"),
        nullable=False,
        default=ReminderStatus.PENDING,
        server_default=ReminderStatus.PENDING.value,
    )

    # Deterministic per-owner key used to prevent duplicate reminders and, in a
    # later phase, duplicate notifications after a retry or restart.
    dedupe_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Claim metadata used by the database-backed reminder worker. A lease
    # token prevents one worker from finalising a reminder claimed by another.
    claim_token: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    task: Mapped["Task"] = relationship(back_populates="reminders")

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_task_reminders_attempt_count_non_negative",
        ),
        UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_task_reminders_user_dedupe",
        ),
        Index(
            "ix_task_reminders_status_remind_at",
            "status",
            "remind_at",
        ),
        Index(
            "ix_task_reminders_user_remind_at",
            "user_id",
            "remind_at",
        ),
        Index("ix_task_reminders_task_id", "task_id"),
        Index(
            "ix_task_reminders_due",
            "status",
            "next_attempt_at",
            "remind_at",
        ),
        Index(
            "ix_task_reminders_processing",
            "status",
            "processing_started_at",
        ),
    )


__all__ = ["ReminderStatus", "TaskReminder"]
