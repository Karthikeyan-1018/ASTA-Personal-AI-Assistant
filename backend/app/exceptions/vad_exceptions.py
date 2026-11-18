"""Exceptions raised by local voice activity detection operations."""


class VADServiceError(Exception):
    """Base exception for voice activity detection failures."""


class VADDisabledError(VADServiceError):
    """Raised when VAD is disabled by server configuration."""


class VADValidationError(VADServiceError):
    """Raised when audio frames are empty, malformed, or out of limits."""


class VADModelUnavailableError(VADServiceError):
    """Raised when the bundled local VAD engine or model is unavailable."""


class VADInferenceError(VADServiceError):
    """Raised when local voice activity inference fails."""


class VADBusyError(VADServiceError):
    """Raised when the serialized VAD inference slot cannot be obtained."""


class VADSessionError(VADServiceError):
    """Raised when a VAD session is unknown, expired, or closed."""
