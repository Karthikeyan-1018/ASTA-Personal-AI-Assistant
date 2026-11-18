"""Exceptions raised by local speech-to-text operations."""


class STTServiceError(Exception):
    """Base exception for speech-to-text failures."""


class STTDisabledError(STTServiceError):
    """Raised when speech-to-text is disabled by server configuration."""


class STTValidationError(STTServiceError):
    """Raised when uploaded audio is empty, invalid, or out of limits."""


class STTModelNotFoundError(STTServiceError):
    """Raised when the local STT model is unavailable."""


class STTInferenceError(STTServiceError):
    """Raised when local speech inference fails."""


class STTBusyError(STTServiceError):
    """Raised when the serialized inference slot is unavailable."""
