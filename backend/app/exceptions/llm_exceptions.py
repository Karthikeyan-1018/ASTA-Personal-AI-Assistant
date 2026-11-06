class LLMException(Exception):
    """Base exception for ASTA LLM errors."""
    pass


class LLMTimeoutError(LLMException):
    """Raised when an LLM request times out."""
    pass


class LLMProviderError(LLMException):
    """Raised when an LLM provider fails."""
    pass


class LLMUnavailableError(LLMException):
    """Raised when all LLM providers are unavailable."""
    pass