"""Enumerations shared by the tool registry, executor, models, and API.

These values are stored in PostgreSQL as short strings. They are intentionally
stable because execution history and confirmation rows outlive code changes.
"""

from __future__ import annotations

import enum


class ToolCategory(str, enum.Enum):
    """Risk classification for a registered tool.

    The category is metadata, never an authorization decision made by the LLM.
    The permission policy evaluates it server-side immediately before any
    execution.
    """

    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    SENSITIVE = "sensitive"


class ToolExecutionStatus(str, enum.Enum):
    """Outcome status of one execution attempt.

    ``PENDING`` and ``RUNNING`` describe an in-flight attempt. Every completed
    attempt still ends in one of the original terminal values, so existing
    history readers and API clients keep working. ``CANCELLED`` records a
    cooperative cancellation or an attempt interrupted by a restart.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CANCELLED = "cancelled"


class ToolExecutionLifecycle(str, enum.Enum):
    """Fine-grained lifecycle state for a tool execution attempt.

    ``status`` remains the coarse outcome used by existing callers. This enum
    models the full lifecycle so transitions can be validated and interrupted
    executions can be recovered after a restart.
    """

    PENDING = "pending"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    APPROVED = "approved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ToolConfirmationStatus(str, enum.Enum):
    """Lifecycle of a user confirmation bound to one exact tool call."""

    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class ToolErrorCategory(str, enum.Enum):
    """Stable, non-sensitive error categories safe to persist."""

    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    DISABLED = "disabled"
    PERMISSION = "permission"
    CONFIRMATION = "confirmation"
    TIMEOUT = "timeout"
    EXECUTION = "execution"
    PROVIDER = "provider"
    MALFORMED_CALL = "malformed_call"
    SIZE_LIMIT = "size_limit"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"


class ConfirmationState(str, enum.Enum):
    """Coarse confirmation state recorded on execution history rows."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


__all__ = [
    "ConfirmationState",
    "ToolCategory",
    "ToolConfirmationStatus",
    "ToolErrorCategory",
    "ToolExecutionLifecycle",
    "ToolExecutionStatus",
]
