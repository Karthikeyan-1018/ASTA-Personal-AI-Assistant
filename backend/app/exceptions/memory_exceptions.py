class MemoryServiceError(Exception):
    """Base exception for ASTA memory service errors."""


class MemoryNotFoundError(MemoryServiceError):
    """Raised when a memory is missing or owned by another user."""


class MemoryValidationError(MemoryServiceError):
    """Raised when memory input is invalid or appears to contain a secret."""


class MemoryExtractionError(MemoryServiceError):
    """Raised when the LLM returns an unusable memory extraction result."""
