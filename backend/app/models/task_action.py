"""Server-side pending actions awaiting explicit user confirmation."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PendingActionType(str, enum.Enum):
    """Task mutation types that require confirmation."""

    CREATE = "create"
    UPDATE = "update"
    COMPLETE = "complete"
    REOPEN = "reopen"
    CANCEL = "cancel"
    DELETE = "delete"


class PendingActionStatus(str, enum.Enum):
    """Lifecycle of a pending confirmation."""

    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    FAILED = "failed"


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


def _action_enum(enum_cls, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=_enum_values,
    )


class TaskPendingAction(Base):
    """A validated, user-scoped task operation awaiting confirmation.

    The stored payload contains only already-validated task fields. Raw LLM
    output, credentials, and private conversation history are never stored.
    Each row belongs to exactly one user and one conversation, expires after a
    short window, and can be resolved at most once.
    """

    __tablename__ = "task_pending_actions"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    action_type: Mapped[PendingActionType] = mapped_column(
        _action_enum(PendingActionType, "task_pending_action_type"),
        nullable=False,
    )

    status: Mapped[PendingActionStatus] = mapped_column(
        _action_enum(PendingActionStatus, "task_pending_action_status"),
        nullable=False,
        default=PendingActionStatus.PENDING,
        server_default=PendingActionStatus.PENDING.value,
    )

    target_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    result_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )

    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_task_pending_actions_expiry_after_creation",
        ),
        Index(
            "ix_task_pending_actions_user_conversation_status",
            "user_id",
            "conversation_id",
            "status",
        ),
        Index("ix_task_pending_actions_status", "status"),
        Index("ix_task_pending_actions_expires_at", "expires_at"),
    )


__all__ = [
    "PendingActionStatus",
    "PendingActionType",
    "TaskPendingAction",
]
