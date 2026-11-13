"""Durable tool execution history and confirmation records.

Neither table stores raw tool arguments, file content, credentials, or full
private results. A confirmation stores exact arguments only for the short
window between proposal and approval; the arguments are cleared atomically when
the confirmation is claimed, rejected, or expired.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolConfirmationStatus,
    ToolErrorCategory,
    ToolExecutionLifecycle,
    ToolExecutionStatus,
)


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


def _tool_enum(enum_cls, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=_enum_values,
    )


class ToolExecution(Base):
    """One auditable tool execution attempt.

    Rows are owner-scoped and intentionally metadata-only. They answer "which
    tool ran, for whom, how long, and with what outcome" without retaining the
    content that was read or written.
    """

    __tablename__ = "tool_executions"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    tool_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    category: Mapped[ToolCategory] = mapped_column(
        _tool_enum(ToolCategory, "tool_execution_category"),
        nullable=False,
    )

    status: Mapped[ToolExecutionStatus] = mapped_column(
        _tool_enum(ToolExecutionStatus, "tool_execution_status"),
        nullable=False,
        default=ToolExecutionStatus.PENDING,
        server_default=ToolExecutionStatus.PENDING.value,
    )

    lifecycle_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ToolExecutionLifecycle.PENDING.value,
        server_default=ToolExecutionLifecycle.PENDING.value,
    )

    confirmation_status: Mapped[ConfirmationState] = mapped_column(
        _tool_enum(ConfirmationState, "tool_confirmation_state"),
        nullable=False,
        default=ConfirmationState.NOT_REQUIRED,
        server_default=ConfirmationState.NOT_REQUIRED.value,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # A client-supplied idempotency key is stored as a SHA-256 digest. The
    # original header value is never retained.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    error_category: Mapped[ToolErrorCategory | None] = mapped_column(
        _tool_enum(ToolErrorCategory, "tool_error_category"),
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    provider: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # A digest and the argument field names are safe to retain. Values are not.
    arguments_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    argument_keys: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    result_bytes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def success(self) -> bool:
        """Return whether the attempt completed successfully.

        This is a derived property so API responses do not have to duplicate
        outcome logic. It never exposes the result payload.
        """

        return self.status == ToolExecutionStatus.SUCCESS

    __table_args__ = (
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_tool_executions_duration_non_negative",
        ),
        CheckConstraint(
            "result_bytes IS NULL OR result_bytes >= 0",
            name="ck_tool_executions_result_bytes_non_negative",
        ),
        CheckConstraint(
            "retry_count >= 0",
            name="ck_tool_executions_retry_count_non_negative",
        ),
        CheckConstraint(
            "lifecycle_status IN ("
            "'pending', 'awaiting_confirmation', 'approved', 'running', "
            "'succeeded', 'failed', 'rejected', 'expired', 'cancelled', "
            "'timed_out')",
            name="tool_execution_lifecycle",
        ),
        Index(
            "ix_tool_executions_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_tool_executions_user_tool",
            "user_id",
            "tool_name",
        ),
        Index("ix_tool_executions_status", "status"),
        Index(
            "ix_tool_executions_user_lifecycle",
            "user_id",
            "lifecycle_status",
        ),
        Index(
            "ix_tool_executions_lifecycle_started",
            "lifecycle_status",
            "started_at",
        ),
        Index(
            "ux_tool_executions_user_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
    )


class ToolConfirmation(Base):
    """A single-use approval bound to one exact tool call.

    The hash binds the confirmation to the canonical argument payload. The
    executor always executes the stored arguments, so a caller cannot change
    arguments after approval. Arguments are cleared on resolution to limit
    retention of temporary content.
    """

    __tablename__ = "tool_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    tool_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    category: Mapped[ToolCategory] = mapped_column(
        _tool_enum(ToolCategory, "tool_confirmation_category"),
        nullable=False,
    )

    status: Mapped[ToolConfirmationStatus] = mapped_column(
        _tool_enum(
            ToolConfirmationStatus,
            "tool_confirmation_status",
        ),
        nullable=False,
        default=ToolConfirmationStatus.PENDING,
        server_default=ToolConfirmationStatus.PENDING.value,
    )

    arguments: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )

    arguments_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    arguments_preview: Mapped[str] = mapped_column(
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

    execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("tool_executions.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_tool_confirmations_expiry_after_creation",
        ),
        Index(
            "ix_tool_confirmations_user_status",
            "user_id",
            "status",
        ),
        Index(
            "ix_tool_confirmations_user_conversation",
            "user_id",
            "conversation_id",
        ),
        Index(
            "ix_tool_confirmations_expires_at",
            "expires_at",
        ),
    )


__all__ = ["ToolConfirmation", "ToolExecution"]
