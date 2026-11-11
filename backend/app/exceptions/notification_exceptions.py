"""Errors raised by in-app notification operations."""

from __future__ import annotations


class NotificationError(Exception):
    """Base class for notification-specific failures."""


class NotificationNotFoundError(NotificationError):
    """The notification does not exist or is not owned by the caller."""


class NotificationValidationError(NotificationError):
    """The supplied notification data is invalid."""


class NotificationConflictError(NotificationError):
    """The notification conflicts with its current state or an existing row."""
