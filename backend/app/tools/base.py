"""Base interface implemented by every ASTA tool.

A tool is a small, independently testable capability. The core executor never
imports a specific implementation; it only knows this interface. Adding a new
tool therefore requires a new module and a registration call, not a change to
the execution engine.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.enums import ToolCategory
from app.tools.exceptions import ToolConfigurationError, ToolValidationError
from app.tools.schemas import ToolDefinition, ToolResult


@dataclass(slots=True)
class ToolContext:
    """Trusted execution context resolved by the backend.

    ``user_id`` comes exclusively from the validated server-side session.
    Tool arguments can never override it. The private flag is also decided by
    the request, never by the LLM.
    """

    session: AsyncSession
    user_id: int
    conversation_id: int | None = None
    private: bool = False
    provider: str | None = None

    def require_user_id(self) -> int:
        if (
            not isinstance(self.user_id, int)
            or isinstance(self.user_id, bool)
            or self.user_id <= 0
        ):
            raise ToolConfigurationError(
                "A trusted owner identity is required to execute tools."
            )

        return self.user_id


class BaseTool(ABC):
    """Consistent interface for all registered tools."""

    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[ToolCategory]
    input_model: ClassVar[type[BaseModel]]
    output_schema: ClassVar[dict[str, Any]] = {}

    # A tool may force confirmation even when its category would not require
    # it. Sensitive tools always require confirmation regardless of this flag.
    requires_confirmation: ClassVar[bool] = False

    # Private requests are local-only. No built-in external tool opts in.
    allowed_in_private: ClassVar[bool] = False

    # Safe label stored in execution history. Never a credential.
    provider_label: ClassVar[str | None] = None

    @property
    def enabled(self) -> bool:
        """Return whether configuration currently enables this tool."""

        return True

    def definition(self) -> ToolDefinition:
        schema = self.input_model.model_json_schema()

        # Pydantic emits a title and may emit $defs. Provider function-calling
        # accepts a standard JSON Schema object, so the schema is passed
        # through unchanged after making the top level strict.
        schema.setdefault("type", "object")
        schema.setdefault("additionalProperties", False)

        return ToolDefinition(
            name=self.name,
            description=self.description,
            category=self.category,
            enabled=self.enabled,
            requires_confirmation=self.requires_confirmation,
            allowed_in_private=self.allowed_in_private,
            parameters=schema,
            output_schema=dict(self.output_schema or {}),
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> BaseModel:
        """Validate raw provider arguments against the input schema.

        Validation messages list field names and reasons only. Provider-supplied
        values are never echoed back, so a malformed call cannot leak a secret
        into logs or an error response.
        """

        if not isinstance(arguments, dict):
            raise ToolValidationError("Tool arguments must be an object.")

        try:
            return self.input_model.model_validate(arguments)

        except ValidationError as error:
            details = []
            seen: set[str] = set()

            for item in error.errors():
                location = ".".join(
                    str(part) for part in item.get("loc", ())
                ) or "arguments"
                message = str(item.get("msg", "invalid value"))

                detail = f"{location}: {message}"

                if detail not in seen:
                    seen.add(detail)
                    details.append(detail)

            raise ToolValidationError(
                "Invalid tool arguments: " + "; ".join(details[:8])
            ) from error

    def summarize(self, arguments: BaseModel) -> str:
        """Return a safe, human-readable preview for a confirmation prompt.

        The default hides values whose field names look sensitive and truncates
        long strings. Implementations handling file content or credentials must
        override this method.
        """

        payload = arguments.model_dump(mode="json", exclude_none=True)
        safe: dict[str, Any] = {}

        for key, value in payload.items():
            lowered = key.lower()

            if any(
                token in lowered
                for token in (
                    "password",
                    "secret",
                    "token",
                    "api_key",
                    "apikey",
                    "key",
                    "content",
                    "code",
                )
            ):
                safe[key] = "<redacted>"
            elif isinstance(value, str) and len(value) > 120:
                safe[key] = value[:117] + "..."
            else:
                safe[key] = value

        return json_preview(safe)

    @abstractmethod
    async def execute(
        self,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolResult:
        """Perform the tool operation and return a normalized result."""


def json_preview(value: Any, limit: int = 400) -> str:
    """Return a compact, length-limited preview string."""

    try:
        text = json.dumps(value, ensure_ascii=False, default=str)

    except Exception:
        text = str(value)

    if len(text) <= limit:
        return text

    return text[: limit - 3] + "..."


__all__ = ["BaseTool", "ToolContext", "json_preview"]
