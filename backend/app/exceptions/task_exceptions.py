"""Exceptions raised by task and deadline operations."""


class TaskServiceError(Exception):
    """Base exception for task management failures."""


class TaskNotFoundError(TaskServiceError):
    """Raised when a task does not exist for the requesting user."""


class TaskValidationError(TaskServiceError):
    """Raised when a task request is invalid."""
