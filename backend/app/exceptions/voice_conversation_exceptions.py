"""Exceptions raised by the local voice conversation manager."""


class VoiceConversationError(Exception):
    """Base exception for voice conversation orchestration failures."""


class VoiceConversationDisabledError(VoiceConversationError):
    """Raised when voice conversations are disabled by configuration."""


class VoiceComponentUnavailableError(VoiceConversationError):
    """Raised when a required local Stage 1-3 component is unavailable."""


class VoiceSessionError(VoiceConversationError):
    """Raised when a session is unknown, expired, or owned by another user."""


class VoiceDuplicateSessionError(VoiceConversationError):
    """Raised when a user already has an active voice conversation."""


class VoiceInvalidStateError(VoiceConversationError):
    """Raised when a requested operation is invalid for the current state."""


class VoiceTurnError(VoiceConversationError):
    """Raised when one VAD-to-STT-to-chat turn cannot be completed."""


class VoiceEmptyTranscriptionError(VoiceTurnError):
    """Raised when local STT returns no usable recognized text."""


class VoiceTTSError(VoiceTurnError):
    """Raised when Piper synthesis fails after a successful chat response."""
