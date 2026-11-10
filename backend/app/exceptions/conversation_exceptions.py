"""Exceptions raised by conversation persistence operations."""


class ConversationServiceError(Exception):
    """Base exception for conversation persistence failures."""


class ConversationNotFoundError(ConversationServiceError):
    """Raised when a conversation does not exist for the requesting user."""


class ConversationValidationError(ConversationServiceError):
    """Raised when a conversation request is invalid."""
