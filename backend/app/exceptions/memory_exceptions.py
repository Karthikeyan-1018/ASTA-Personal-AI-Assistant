class MemoryServiceError(Exception):
    """Base exception for ASTA memory service errors."""


class MemoryNotFoundError(MemoryServiceError):
    """Raised when a memory is missing or owned by another user."""


class MemoryValidationError(MemoryServiceError):
    """Raised when memory input is invalid or appears to contain a secret."""


class MemoryExtractionError(MemoryServiceError):
    """Raised when the LLM returns an unusable memory extraction result."""


class EmbeddingServiceError(MemoryServiceError):
    """Base exception for memory embedding failures."""


class EmbeddingValidationError(EmbeddingServiceError):
    """Raised when embedding input or an embedding vector is invalid."""


class EmbeddingModelError(EmbeddingServiceError):
    """Raised when the embedding model cannot be loaded or used."""


class SemanticRetrievalUnavailable(MemoryServiceError):
    """Raised when semantic retrieval cannot run for the current database."""
