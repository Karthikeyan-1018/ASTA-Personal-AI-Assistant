"""Helpers that build normalized tool results consistently."""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.tools.enums import (
    ConfirmationState,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.schemas import ToolResult


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
        error_message=message,
        error_category=category,
        status=status,
        duration_ms=duration_ms,
        provider=provider,
        confirmation_required=confirmation_required,
        confirmation_id=confirmation_id,
        confirmation_status=confirmation_status,
        result_bytes=0,
        metadata=metadata or {},
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
        maximum = max(1, int(settings.TOOL_MAX_RESULT_BYTES))

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


__all__ = ["enforce_result_limit", "error_result", "success_result"]
