"""Provider-independent tool-call and result schemas.

The LLM never supplies an owner identity, a permission decision, or a
confirmation. A provider returns only a tool name plus arguments. The executor
converts that into a validated, owner-scoped call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields.

    Keeping this strict means an LLM cannot smuggle an unregistered argument
    such as ``user_id`` or ``force=True`` into a tool implementation.
    """

    model_config = ConfigDict(extra="forbid")


class ToolCall(StrictModel):
    """One normalized tool call requested by a provider."""

    call_id: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("Tool name cannot be empty.")

        return cleaned


@dataclass(slots=True)
class ToolResult:
    """Normalized result returned by every tool execution path."""

    success: bool
    data: Any = None
    tool_name: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_category: ToolErrorCategory | str | None = None
    status: ToolExecutionStatus | str = ToolExecutionStatus.SUCCESS
    confirmation_required: bool = False
    confirmation_id: int | None = None
    confirmation_status: ConfirmationState | str = (
        ConfirmationState.NOT_REQUIRED
    )
    truncated: bool = False
    duration_ms: float | None = None
    result_bytes: int | None = None
    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation without internal ORM objects."""

        return {
            "success": bool(self.success),
            "status": _value(self.status),
            "tool": self.tool_name,
            "data": _json_safe(self.data),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_category": _value(self.error_category),
            "confirmation_required": bool(self.confirmation_required),
            "confirmation_id": self.confirmation_id,
            "confirmation_status": _value(self.confirmation_status),
            "truncated": bool(self.truncated),
            "duration_ms": self.duration_ms,
            "result_bytes": self.result_bytes,
            "provider": self.provider,
            "metadata": _json_safe(self.metadata),
        }

    def message_content(self) -> str:
        """Return a compact JSON payload suitable for an LLM tool message."""

        return json.dumps(self.as_dict(), ensure_ascii=False, default=str)


class ToolDefinition(StrictModel):
    """Public description of one registered tool."""

    name: str
    description: str
    category: ToolCategory
    enabled: bool = True
    requires_confirmation: bool = False
    allowed_in_private: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    def provider_schema(self) -> dict[str, Any]:
        """Return an OpenAI/Ollama-compatible function definition."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value

    return value


def _json_safe(value: Any) -> Any:
    """Best-effort conversion for dataclasses, datetimes, and enums."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    if hasattr(value, "isoformat"):
        return value.isoformat()

    if hasattr(value, "value"):
        return value.value

    return str(value)


def canonical_arguments(arguments: dict[str, Any]) -> str:
    """Return a deterministic JSON representation used for binding."""

    return json.dumps(
        _json_safe(arguments or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def arguments_digest(arguments: dict[str, Any]) -> str:
    """Return the SHA-256 digest of the canonical argument payload."""

    return hashlib.sha256(
        canonical_arguments(arguments).encode("utf-8")
    ).hexdigest()


def json_size(value: Any) -> int:
    """Return the UTF-8 byte size of a JSON-safe value."""

    return len(canonical_arguments(value).encode("utf-8"))


__all__ = [
    "StrictModel",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "arguments_digest",
    "canonical_arguments",
    "json_size",
]
