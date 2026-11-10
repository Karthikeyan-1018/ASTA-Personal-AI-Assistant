"""Exceptions raised by natural-language task intent handling."""


class TaskIntentError(Exception):
    """Base exception for task-intent failures."""


class PendingActionNotFoundError(TaskIntentError):
    """Raised when a pending action does not exist for the requesting user."""


class PendingActionExpiredError(TaskIntentError):
    """Raised when a confirmation window has elapsed."""


class PendingActionAlreadyResolvedError(TaskIntentError):
    """Raised when a pending action was already executed or rejected."""


class TaskSelectionError(TaskIntentError):
    """Raised when a task target is missing or ambiguous."""
