"""Exceptions raised by user-preference operations."""


class PreferenceServiceError(Exception):
    """Base exception for preference management failures."""


class PreferenceNotFoundError(PreferenceServiceError):
    """Raised when a preference does not exist for the requesting user."""


class PreferenceValidationError(PreferenceServiceError):
    """Raised when a preference request is invalid."""


class PreferenceConflictError(PreferenceServiceError):
    """Raised when a preference already exists for the same key."""


class PreferenceExtractionError(PreferenceServiceError):
    """Raised when preference extraction returns an unusable response."""
