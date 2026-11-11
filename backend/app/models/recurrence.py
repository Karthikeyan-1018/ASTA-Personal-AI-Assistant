"""Recurring task rules and links to generated task occurrences.

Recurrence is intentionally represented in separate tables rather than by
altering ``tasks``. A rule belongs to one template task and keeps enough schedule
metadata to generate independent occurrence tasks. Generated occurrences remain
normal ``tasks`` rows and are never deleted when the rule is deactivated, which
preserves history.

Occurrence tasks are linked through ``task_recurrence_occurrences``. The unique
``(recurrence_id, occurrence_date)`` constraint prevents duplicate generation
across retries or concurrent workers.
"""

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
    Integer,
    String,
    Time,
    UniqueConstraint,
    true,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

if TYPE_CHECKING:
    pass


class RecurrenceFrequency(str, enum.Enum):
    """Supported recurrence cadences."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


def _recurrence_enum(enum_cls, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=_enum_values,
    )


class TaskRecurrence(Base):
    """A recurring-task rule attached to one owned template task."""

    __tablename__ = "task_recurrences"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Deleting the template ends its rule. Generated occurrences are separate
    # tasks and remain untouched.
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    frequency: Mapped[RecurrenceFrequency] = mapped_column(
        _recurrence_enum(RecurrenceFrequency, "recurrence_frequency"),
        nullable=False,
    )

    interval: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    # Comma-separated weekday numbers (Monday=0 .. Sunday=6). Only used for
    # weekly rules; NULL means the template's weekday.
    weekdays: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    timezone: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    anchor_time: Mapped[time | None] = mapped_column(
        Time(timezone=False),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    last_generated_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    next_generation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

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

    __table_args__ = (
        CheckConstraint(
            "interval >= 1",
            name="ck_task_recurrences_interval_positive",
        ),
        UniqueConstraint(
            "task_id",
            name="uq_task_recurrences_task",
        ),
        Index(
            "ix_task_recurrences_user_active",
            "user_id",
            "is_active",
        ),
        Index(
            "ix_task_recurrences_due",
            "is_active",
            "next_generation_at",
        ),
        Index(
            "ix_task_recurrences_processing",
            "is_active",
            "processing_started_at",
        ),
    )


class TaskRecurrenceOccurrence(Base):
    """Link between a recurrence rule and one generated task occurrence."""

    __tablename__ = "task_recurrence_occurrences"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    recurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_recurrences.id", ondelete="SET NULL"),
        nullable=True,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    occurrence_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "recurrence_id",
            "occurrence_date",
            name="uq_recurrence_occurrences_date",
        ),
        UniqueConstraint(
            "task_id",
            name="uq_recurrence_occurrences_task",
        ),
        Index(
            "ix_recurrence_occurrences_user_date",
            "user_id",
            "occurrence_date",
        ),
        Index(
            "ix_recurrence_occurrences_recurrence",
            "recurrence_id",
        ),
    )


__all__ = [
    "RecurrenceFrequency",
    "TaskRecurrence",
    "TaskRecurrenceOccurrence",
]
