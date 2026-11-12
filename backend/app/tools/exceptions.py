"""Exceptions raised inside the tool execution engine.

The API maps these to HTTP status codes. Tool implementations may raise them
to produce a structured, non-sensitive error result. Unexpected exceptions are
caught by the executor and converted into an internal error result so a faulty
tool can never break normal chat processing.
"""

from __future__ import annotations


class ToolError(Exception):
    """Base class for tool-engine failures."""


class ToolNotFoundError(ToolError):
    """No tool with the requested name is registered."""


class ToolDisabledError(ToolError):
    """The tool is registered but disabled by configuration."""


class ToolValidationError(ToolError):
    """Tool arguments did not match the registered input schema."""


class ToolPermissionError(ToolError):
    """The authenticated owner may not execute this tool."""


class ToolPrivateRequestError(ToolPermissionError):
    """A private request attempted to use a non-private tool."""


class ToolConfirmationRequiredError(ToolError):
    """Execution requires an approved, unexpired confirmation."""


class ToolConfirmationLimitError(ToolError):
    """Too many confirmations are waiting for this owner."""


class ToolConfirmationNotFoundError(ToolError):
    """The confirmation does not exist or is not owned by the caller."""


class ToolConfirmationExpiredError(ToolError):
    """The confirmation window elapsed before approval."""


class ToolConfirmationResolvedError(ToolError):
    """The confirmation was already executed, rejected, or failed."""


class ToolConfirmationMismatchError(ToolConfirmationResolvedError):
    """The confirmation is not bound to this tool call."""


class ToolTimeoutError(ToolError):
    """The tool exceeded its configured execution timeout."""


class ToolExecutionError(ToolError):
    """The tool failed while performing its operation."""


class ToolProviderError(ToolExecutionError):
    """An external provider (search, HTTP API) failed."""


class ToolConfigurationError(ToolError):
    """The tool is enabled but its configuration is incomplete."""


class ToolSizeLimitError(ToolError):
    """Arguments, results, or content exceeded a configured limit."""


class ToolMalformedCallError(ToolError):
    """The provider returned a malformed or unsupported tool call."""


__all__ = [
    "ToolConfirmationExpiredError",
    "ToolConfirmationLimitError",
    "ToolConfirmationMismatchError",
    "ToolConfirmationNotFoundError",
    "ToolConfirmationRequiredError",
    "ToolConfirmationResolvedError",
    "ToolConfigurationError",
    "ToolDisabledError",
    "ToolError",
    "ToolExecutionError",
    "ToolMalformedCallError",
    "ToolNotFoundError",
    "ToolPermissionError",
    "ToolPrivateRequestError",
    "ToolProviderError",
    "ToolSizeLimitError",
    "ToolTimeoutError",
    "ToolValidationError",
]
