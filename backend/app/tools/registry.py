"""Central registry for ASTA tools.

Tools register once at import time. The registry validates every registration,
rejects duplicates, and produces provider-independent definitions for the LLM.
The execution engine only looks tools up here.
"""

from __future__ import annotations

import re
import threading
from typing import Iterable, Iterator

from pydantic import BaseModel, ValidationError

from app.tools.base import BaseTool
from app.tools.enums import ToolCategory
from app.tools.exceptions import ToolNotFoundError, ToolValidationError
from app.tools.permissions import permission_policy
from app.tools.schemas import ToolDefinition


_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_METADATA_FLAGS = ("requires_confirmation", "allowed_in_private")
_BUILTINS_LOCK = threading.RLock()
_BUILTINS_LOADED = False
_BUILTINS_LOADING = False


class ToolRegistry:
    """An ordered, validated collection of tool instances."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._lock = threading.RLock()

    def _validate_tool(self, tool: BaseTool) -> str:
        """Validate one tool and return its canonical name.

        Definition construction is deliberately performed here. It is the
        single place that verifies the tool's category, input JSON Schema, and
        output schema. A defect therefore fails registration immediately
        instead of surfacing later during chat or an API request.
        """

        if not isinstance(tool, BaseTool):
            raise ToolValidationError(
                "Registered tools must implement the BaseTool interface."
            )

        name = getattr(tool, "name", None)

        if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
            raise ToolValidationError(
                "Tool names must be lower_snake_case and 2-64 characters."
            )

        description = getattr(tool, "description", None)

        if not isinstance(description, str) or not description.strip():
            raise ToolValidationError(
                "Every tool must have a non-empty description."
            )

        category = getattr(tool, "category", None)

        if not isinstance(category, ToolCategory):
            raise ToolValidationError(
                f"Tool '{name}' must declare a ToolCategory."
            )

        input_model = getattr(tool, "input_model", None)

        if not isinstance(input_model, type) or not issubclass(
            input_model,
            BaseModel,
        ):
            raise ToolValidationError(
                f"Tool '{name}' must define a Pydantic input_model."
            )

        output_schema = getattr(tool, "output_schema", None)

        if output_schema is not None and not isinstance(output_schema, dict):
            raise ToolValidationError(
                f"Tool '{name}' output_schema must be a dictionary."
            )

        for attribute in _METADATA_FLAGS:
            if not isinstance(getattr(tool, attribute, None), bool):
                raise ToolValidationError(
                    f"Tool '{name}' {attribute} must be a boolean."
                )

        provider_label = getattr(tool, "provider_label", None)

        if provider_label is not None and not isinstance(provider_label, str):
            raise ToolValidationError(
                f"Tool '{name}' provider_label must be a string or None."
            )

        try:
            tool.definition()

        except ValidationError as error:
            raise ToolValidationError(
                f"Tool '{name}' has an invalid definition: {error}"
            ) from error

        except Exception as error:
            raise ToolValidationError(
                f"Tool '{name}' has an invalid definition: {error}"
            ) from error

        return name

    def register(self, tool: BaseTool) -> BaseTool:
        """Register one tool, rejecting duplicates and invalid definitions."""

        name = self._validate_tool(tool)

        with self._lock:
            if name in self._tools:
                raise ToolValidationError(
                    f"A tool named '{name}' is already registered."
                )

            self._tools[name] = tool

        return tool

    def register_all(self, tools: Iterable[BaseTool]) -> None:
        """Register a batch atomically.

        Nothing is added unless every definition is valid and no name collides
        with the registry or another member of the batch.
        """

        prepared: list[tuple[str, BaseTool]] = []
        batch_names: set[str] = set()

        for tool in tools:
            name = self._validate_tool(tool)

            if name in batch_names:
                raise ToolValidationError(
                    f"A tool named '{name}' is already registered."
                )

            batch_names.add(name)
            prepared.append((name, tool))

        with self._lock:
            for name, _tool in prepared:
                if name in self._tools:
                    raise ToolValidationError(
                        f"A tool named '{name}' is already registered."
                    )

            for name, tool in prepared:
                self._tools[name] = tool

    def get(self, name: str) -> BaseTool:
        ensure_builtin_tools()

        with self._lock:
            tool = self._tools.get(name)

        if tool is None:
            raise ToolNotFoundError(f"Unknown tool: {name}")

        return tool

    def maybe_get(self, name: str) -> BaseTool | None:
        ensure_builtin_tools()

        with self._lock:
            return self._tools.get(name)

    def names(self) -> list[str]:
        ensure_builtin_tools()

        with self._lock:
            return sorted(self._tools)

    def all(self) -> list[BaseTool]:
        ensure_builtin_tools()

        with self._lock:
            return [
                self._tools[name] for name in sorted(self._tools)
            ]

    def is_enabled(self, name: str) -> bool:
        """Return the current configuration state for a registered tool."""

        return bool(self.get(name).enabled)

    def enabled_tools(self) -> list[BaseTool]:
        return [tool for tool in self.all() if tool.enabled]

    def disabled_tools(self) -> list[BaseTool]:
        return [tool for tool in self.all() if not tool.enabled]

    def enabled_names(self) -> list[str]:
        return [tool.name for tool in self.enabled_tools()]

    def disabled_names(self) -> list[str]:
        return [tool.name for tool in self.disabled_tools()]

    def __len__(self) -> int:
        ensure_builtin_tools()

        with self._lock:
            return len(self._tools)

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False

        ensure_builtin_tools()

        with self._lock:
            return name in self._tools

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self.all())

    def definitions(
        self,
        *,
        include_disabled: bool = False,
        private: bool = False,
    ) -> list[ToolDefinition]:
        ensure_builtin_tools()

        definitions: list[ToolDefinition] = []

        for tool in self.all():
            definition = tool.definition()

            if not definition.enabled and not include_disabled:
                continue

            if private and not definition.allowed_in_private:
                continue

            definitions.append(definition)

        return definitions

    def llm_definitions(
        self,
        *,
        private: bool = False,
    ) -> list[dict]:
        """Return only tools the current policy would actually allow."""

        ensure_builtin_tools()
        result: list[dict] = []

        for tool in self.all():
            if not tool.enabled:
                continue

            decision = permission_policy.evaluate(tool, private=private)

            if not decision.allowed:
                continue

            result.append(tool.definition().provider_schema())

        return result


tool_registry = ToolRegistry()


def ensure_builtin_tools() -> None:
    """Load built-in tool modules exactly once, lazily.

    Lazy loading avoids an import cycle: ORM models import the tool enums, and
    tool implementations import services that import ORM models.
    """

    global _BUILTINS_LOADED, _BUILTINS_LOADING

    with _BUILTINS_LOCK:
        if _BUILTINS_LOADED or _BUILTINS_LOADING:
            return

        _BUILTINS_LOADING = True

        try:
            from app.tools import definitions

            definitions.register_all(tool_registry)
            _BUILTINS_LOADED = True

        finally:
            _BUILTINS_LOADING = False


__all__ = [
    "ToolRegistry",
    "ensure_builtin_tools",
    "tool_registry",
]
