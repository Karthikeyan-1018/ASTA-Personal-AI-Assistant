"""Bounded retry policy for eligible, idempotent tool calls.

Retries are deliberately narrow:

* disabled unless ``TOOL_RETRY_ENABLED`` is true;
* limited to tools that explicitly declare ``idempotent = True``;
* limited to read-only tools that do not require confirmation;
* limited to transient timeout or provider failures;
* never applied to a cancellation, a validation/permission failure, or any
  outcome that could have produced a side effect.

A tool that sends an email or submits a form therefore never retries
automatically, even when retries are enabled globally. An operator can
implement an explicit, state-verifying recovery path instead.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.tools.base import BaseTool
from app.tools.enums import ToolCategory, ToolErrorCategory


DEFAULT_MAX_RETRIES = 1
MAX_RETRIES_CEILING = 3
DEFAULT_BACKOFF_MS = 250
MAX_BACKOFF_MS = 5000

RETRYABLE_CATEGORIES = frozenset(
    {
        ToolErrorCategory.TIMEOUT.value,
        ToolErrorCategory.PROVIDER.value,
    }
)


def _int_setting(name: str, default: int) -> int:
    try:
        value = int(getattr(settings, name, default))

    except (TypeError, ValueError):
        return default

    return value


def retries_enabled() -> bool:
    return bool(getattr(settings, "TOOL_RETRY_ENABLED", False))


def max_retries() -> int:
    """Return the bounded retry count for one execution attempt."""

    if not retries_enabled():
        return 0

    configured = _int_setting("TOOL_MAX_RETRIES", DEFAULT_MAX_RETRIES)

    return max(0, min(configured, MAX_RETRIES_CEILING))


def backoff_seconds(attempt: int) -> float:
    """Return a bounded exponential backoff for retry number ``attempt``."""

    configured = _int_setting("TOOL_RETRY_BACKOFF_MS", DEFAULT_BACKOFF_MS)
    base_ms = max(0, min(configured, MAX_BACKOFF_MS))
    delay_ms = min(base_ms * (2 ** max(0, attempt - 1)), MAX_BACKOFF_MS)

    return delay_ms / 1000.0


def retry_eligible(
    tool: BaseTool,
    result: Any,
    *,
    allow_retry: bool,
) -> bool:
    """Return whether a failed attempt may be retried safely."""

    if not allow_retry or max_retries() <= 0:
        return False

    if not getattr(tool, "idempotent", False):
        return False

    if getattr(tool, "requires_confirmation", False):
        return False

    if getattr(tool, "category", None) != ToolCategory.READ_ONLY:
        return False

    if getattr(result, "success", False):
        return False

    error_code = getattr(result, "error_code", None)

    if error_code == "ToolCancelled":
        return False

    if getattr(result, "confirmation_required", False):
        return False

    category = getattr(result, "error_category", None)
    value = category.value if hasattr(category, "value") else category

    return value in RETRYABLE_CATEGORIES


__all__ = [
    "DEFAULT_BACKOFF_MS",
    "DEFAULT_MAX_RETRIES",
    "MAX_BACKOFF_MS",
    "MAX_RETRIES_CEILING",
    "RETRYABLE_CATEGORIES",
    "backoff_seconds",
    "max_retries",
    "retries_enabled",
    "retry_eligible",
]
