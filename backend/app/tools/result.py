"""Helpers that build normalized tool results consistently."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.core.secret_patterns import SECRET_PATTERNS
from app.tools.enums import (
    ConfirmationState,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.schemas import ToolResult


REDACTION = "[redacted]"
MAX_ERROR_MESSAGE_CHARS = 500
MAX_REDACTION_DEPTH = 8

# A result object may carry a credential under an obvious field name, for
# example ``{"password": "..."}``. Pattern matching alone does not catch that
# shape, so matching keys are redacted wholesale.
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "secret",
        "token",
        "access_token",
        "auth_token",
        "refresh_token",
    }
)

# The shared patterns cover free-form credential text. JSON payloads put a
# quote between the field name and the colon, so they need one extra shape.
_JSON_SECRET_PATTERN = re.compile(
    r"(?i)"
    r"(\"(?:api[_-]?key|password|passwd|secret|token|"
    r"access[_-]?token|auth[_-]?token)\"\s*:\s*)"
    r"\"[^\"]*\""
)


def sanitize_error_message(
    message: Any,
    fallback: str = "The tool request failed.",
) -> str:
    """Return a bounded, credential-free message safe for a caller.

    Tool implementations are trusted code, but provider responses and wrapped
    exceptions are not. Stack traces are replaced by a generic fallback and
    credential-looking values are redacted. Newlines are collapsed so a raw
    traceback cannot be reconstructed from the result.
    """

    if message is None:
        return fallback

    text = str(message).strip()

    if not text:
        return fallback

    if "Traceback (most recent call last)" in text:
        return fallback

    text = _redact_text(text)

    if len(text) > MAX_ERROR_MESSAGE_CHARS:
        text = text[: MAX_ERROR_MESSAGE_CHARS - 3].rstrip() + "..."

    return text or fallback


def redact_secrets(value: Any, _depth: int = 0) -> Any:
    """Recursively redact credential-like strings in JSON-safe tool output.

    Structure and non-string values are preserved. Redaction is shallow-bounded
    to avoid unbounded recursion on an adversarial result object.
    """

    if isinstance(value, str):
        return _redact_text(value)

    if _depth >= MAX_REDACTION_DEPTH:
        return value

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}

        for key, item in value.items():
            text_key = str(key)

            if _is_sensitive_key(text_key) and item is not None:
                redacted[text_key] = REDACTION
            else:
                redacted[text_key] = redact_secrets(item, _depth + 1)

        return redacted

    if isinstance(value, (list, tuple, set)):
        return [
            redact_secrets(item, _depth + 1)
            for item in value
        ]

    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")

    return normalized in _SENSITIVE_KEYS


def _redact_text(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTION, text)

    text = _JSON_SECRET_PATTERN.sub(
        lambda match: match.group(1) + f'"{REDACTION}"',
        text,
    )

    return text


def success_result(
    *,
    tool_name: str,
    data: Any = None,
    duration_ms: float | None = None,
    provider: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    limited, truncated, size = enforce_result_limit(data)

    return ToolResult(
        success=True,
        data=limited,
        tool_name=tool_name,
        status=ToolExecutionStatus.SUCCESS,
        duration_ms=duration_ms,
        result_bytes=size,
        provider=provider,
        truncated=truncated,
        metadata=metadata or {},
    )


def error_result(
    *,
    tool_name: str | None,
    code: str,
    message: str,
    category: ToolErrorCategory | str,
    status: ToolExecutionStatus | str = ToolExecutionStatus.ERROR,
    duration_ms: float | None = None,
    provider: str | None = None,
    confirmation_required: bool = False,
    confirmation_id: int | None = None,
    confirmation_status: ConfirmationState | str = (
        ConfirmationState.NOT_REQUIRED
    ),
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        success=False,
        tool_name=tool_name,
        error_code=code,
        error_message=sanitize_error_message(message),
        error_category=category,
        status=status,
        duration_ms=duration_ms,
        provider=provider,
        confirmation_required=confirmation_required,
        confirmation_id=confirmation_id,
        confirmation_status=confirmation_status,
        result_bytes=0,
        metadata=redact_secrets(metadata or {}),
    )


def enforce_result_limit(
    data: Any,
    limit: int | None = None,
) -> tuple[Any, bool, int]:
    """Bound the serialized size of a tool result.

    A tool may return a large list of search hits or file metadata. The limit
    keeps the value small enough to return to a provider and to store safely in
    memory. The preview is intentionally a scalar string rather than invalid
    JSON.
    """

    maximum = limit

    if maximum is None:
        try:
            maximum = int(settings.TOOL_MAX_RESULT_BYTES)

        except (TypeError, ValueError):
            maximum = 20000

    maximum = max(1, int(maximum))

    try:
        rendered = json.dumps(data, ensure_ascii=False, default=str)

    except Exception:
        rendered = str(data)

    size = len(rendered.encode("utf-8"))

    if size <= maximum:
        return data, False, size

    head = rendered[: max(0, maximum - 100)]

    return (
        {
            "truncated": True,
            "preview": head,
            "original_bytes": size,
        },
        True,
        maximum,
    )


__all__ = [
    "MAX_ERROR_MESSAGE_CHARS",
    "MAX_REDACTION_DEPTH",
    "REDACTION",
    "enforce_result_limit",
    "error_result",
    "redact_secrets",
    "sanitize_error_message",
    "success_result",
]
