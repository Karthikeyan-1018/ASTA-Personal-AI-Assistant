"""Exceptions raised by local text-to-speech operations."""


class TTSServiceError(Exception):
    """Base exception for text-to-speech failures."""


class TTSDisabledError(TTSServiceError):
    """Raised when text-to-speech is disabled by server configuration."""


class TTSValidationError(TTSServiceError):
    """Raised when synthesis text is empty or too large."""


class TTSAudioTooLargeError(TTSServiceError):
    """Raised when generated audio would exceed the configured byte limit."""


class TTSVoiceNotFoundError(TTSServiceError):
    """Raised when the configured local voice model is unavailable."""


class TTSInferenceError(TTSServiceError):
    """Raised when local speech inference fails."""
