"""Exceptions raised by the in-process background task service."""


class BackgroundTaskError(Exception):
    """Base exception for background task operations."""


class BackgroundTaskNotFoundError(BackgroundTaskError):
    """Raised when a task does not exist for the requesting user."""


class BackgroundTaskValidationError(BackgroundTaskError):
    """Raised when a task type or its parameters are not allowed."""


class BackgroundTaskLimitError(BackgroundTaskError):
    """Raised when a user already has the maximum number of pending tasks."""


__all__ = [
    "BackgroundTaskError",
    "BackgroundTaskLimitError",
    "BackgroundTaskNotFoundError",
    "BackgroundTaskValidationError",
]
