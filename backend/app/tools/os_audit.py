"""Metadata-only audit logging for OS automation (Phase 12F).

The audit log answers *who requested which OS capability, when, with what
outcome, and whether a confirmation or rate limit was involved*. It never
records argument values, clipboard text, file contents, file paths, command
lines, window titles, process names, tokens, or secrets.

Audit logging is best effort. A logging failure must never turn a denied action
into an allowed one, and it must never break an execution path. Every call is
therefore wrapped and swallows its own errors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("app.tools.os_audit")

_MAX_FIELD_CHARS = 120


def _safe(value: Any) -> str:
    """Return a bounded, single-line metadata value."""

    if value is None:
        return "-"

    text = str(value).replace("\r", " ").replace("\n", " ").strip()

    if not text:
        return "-"

    return text[:_MAX_FIELD_CHARS]


def _emit(message: str, *args: Any) -> None:
    try:
        logger.info(message, *args)

    except Exception:
        # Never let an audit sink failure change an authorization decision.
        try:
            logger.debug("OS audit logging failed.", exc_info=True)

        except Exception:
            pass


def classify_rate_limit(result: Any, *, enabled: bool) -> str:
    """Derive a safe rate-limit outcome from a normalized tool result."""

    if not enabled:
        return "disabled"

    code = getattr(result, "error_code", None)

    if code == "ToolRateLimitError":
        return "denied"

    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)

    if status_value in {"success", "error", "timeout"}:
        return "allowed"

    return "not_consumed"


def log_execution(
    *,
    user_id: int | None,
    conversation_id: int | None,
    tool_name: str,
    category: Any,
    capability: str | None,
    status: Any,
    lifecycle: Any,
    confirmation: Any,
    duration_ms: float | None,
    retry_count: int | None,
    error_category: Any,
    error_code: str | None,
    rate_limit_outcome: str,
) -> None:
    """Write one metadata-only OS execution audit event."""

    _emit(
        "os_audit event=tool_execution ts=%s user_id=%s conversation_id=%s "
        "tool=%s category=%s capability=%s status=%s lifecycle=%s "
        "confirmation=%s duration_ms=%s retry_count=%s error_category=%s "
        "error_code=%s rate_limit=%s",
        datetime.now(timezone.utc).isoformat(),
        _safe(user_id),
        _safe(conversation_id),
        _safe(tool_name),
        _safe(getattr(category, "value", category)),
        _safe(capability),
        _safe(getattr(status, "value", status)),
        _safe(getattr(lifecycle, "value", lifecycle)),
        _safe(getattr(confirmation, "value", confirmation)),
        _safe(duration_ms),
        _safe(int(retry_count or 0)),
        _safe(getattr(error_category, "value", error_category)),
        _safe(error_code),
        _safe(rate_limit_outcome),
    )


def log_emergency_stop(
    *,
    action: str,
    user_id: int | None,
    engaged_at: datetime | None = None,
    engaged_by_user_id: int | None = None,
) -> None:
    """Write one emergency-stop audit event without exposing the token."""

    _emit(
        "os_audit event=emergency_stop ts=%s action=%s user_id=%s "
        "engaged_at=%s engaged_by=%s",
        datetime.now(timezone.utc).isoformat(),
        _safe(action),
        _safe(user_id),
        _safe(engaged_at.isoformat() if engaged_at else None),
        _safe(engaged_by_user_id),
    )


__all__ = ["classify_rate_limit", "log_emergency_stop", "log_execution"]
