"""Provider-independent tool-calling contracts and message translation.

Both providers are exposed through the same neutral shape:

``messages`` is a list of dictionaries with roles ``system``, ``user``,
``assistant``, or ``tool``. Assistant messages may carry ``tool_calls`` with
``{id, name, arguments}`` where ``arguments`` is already a mapping. Tool
messages carry ``tool_call_id``, ``name`` and a string ``content``.

A provider turn is normalized into ``ProviderToolTurn``. Arguments are parsed
as a JSON object; a malformed payload is recorded instead of raised so the
orchestrator can report a structured error back to the model. Valid calls are
also convertible to the public ``ToolCall`` schema.

Registry definitions are already provider-independent. ``to_groq_tools`` and
``to_ollama_tools`` sanitize them into the canonical function shape both
providers accept, dropping malformed or duplicate entries instead of sending
an invalid schema.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.tools.schemas import ToolCall


logger = logging.getLogger(__name__)


MAX_TOOL_ARGUMENT_CHARS = 100_000
MAX_TOOL_CALLS_PER_TURN = 16
MAX_TOOL_NAME_CHARS = 100
MAX_CALL_ID_CHARS = 200

_MISSING_NAME = "The provider returned a tool call without a name."
_UNSUPPORTED_SHAPE = (
    "The provider returned tool_calls in an unsupported shape."
)
_TOO_MANY_CALLS = (
    "The provider returned too many tool calls in one turn."
)
_NAME_TOO_LONG = "The provider returned a tool name that was too long."

# Used only when replaying a malformed assistant call that already has an id.
# Keeping the call and its tool result paired avoids an orphaned tool message
# that strict providers reject. The placeholder can never match a registered
# tool, because registry names must start with a lowercase letter.
UNKNOWN_TOOL_NAME = "__invalid_tool_call__"


@dataclass(slots=True)
class NormalizedToolCall:
    """One tool call requested by a provider."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        """Return whether this call can be handed to an executor."""

        return self.error is None and bool(self.name)

    def as_neutral(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }

    def to_tool_call(self) -> ToolCall | None:
        """Convert a valid call to the public ``ToolCall`` schema.

        Invalid or malformed calls return ``None``. They are never silently
        turned into an empty call, because that would let an unknown tool name
        or corrupt argument payload reach execution.
        """

        if not self.is_valid:
            return None

        try:
            return ToolCall(
                call_id=self.id or None,
                name=self.name,
                arguments=self.arguments,
            )

        except ValueError:
            return None


@dataclass(slots=True)
class ProviderToolTurn:
    """A single provider response in the internal tool-calling format."""

    content: str = ""
    tool_calls: list[NormalizedToolCall] = field(default_factory=list)
    usage: dict | None = None
    model: str | None = None

    @property
    def tool_call_schemas(self) -> list[ToolCall]:
        """Return only the valid calls, validated by ``ToolCall``."""

        return [
            call
            for call in (item.to_tool_call() for item in self.tool_calls)
            if call is not None
        ]


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex}"


def parse_tool_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Parse provider arguments into a mapping.

    Returns ``(arguments, error)``. ``error`` is a short, value-free message
    because the raw payload may contain private data.
    """

    if raw is None:
        return {}, None

    if isinstance(raw, dict):
        return raw, None

    if isinstance(raw, str):
        text = raw.strip()

        if not text:
            return {}, None

        if len(text) > MAX_TOOL_ARGUMENT_CHARS:
            return {}, "Tool arguments exceeded the size limit."

        try:
            parsed = json.loads(text)

        except ValueError:
            return {}, "Tool arguments were not valid JSON."

        if not isinstance(parsed, dict):
            return {}, "Tool arguments must be a JSON object."

        return parsed, None

    return {}, "Tool arguments had an unsupported type."


def assistant_message(
    content: str | None,
    calls: list[NormalizedToolCall],
) -> dict[str, Any]:
    """Build a neutral assistant message that can be replayed to a provider."""

    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [call.as_neutral() for call in calls],
    }


def tool_message(
    *,
    call_id: str,
    name: str,
    content: str,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)

    return getattr(value, name, None)


def _text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    return str(value)


def _content_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    try:
        return json.dumps(value, ensure_ascii=False, default=str)

    except Exception:
        return str(value)


def _normalize_call_id(value: Any, seen: set[str]) -> str:
    """Return a unique, bounded call id, generating one when necessary."""

    text = _text(value).strip()

    if not text or len(text) > MAX_CALL_ID_CHARS or text in seen:
        text = new_call_id()

    seen.add(text)
    return text


def _raw_tool_calls(message: Any) -> tuple[list[Any], str | None]:
    """Return provider ``tool_calls`` as a list, or a shape error."""

    raw = _field(message, "tool_calls")

    if raw is None:
        return [], None

    if isinstance(raw, dict):
        return [raw], None

    if isinstance(raw, (list, tuple)):
        return list(raw), None

    return [], _UNSUPPORTED_SHAPE


def _extract_tool_calls(message: Any) -> list[NormalizedToolCall]:
    """Normalize ``message.tool_calls`` from either provider.

    Groq returns function arguments as a JSON-encoded string. Ollama returns a
    mapping, but older builds may return a string, so both are accepted.
    """

    raw_calls, shape_error = _raw_tool_calls(message)

    if shape_error:
        return [
            NormalizedToolCall(
                id=new_call_id(),
                name="",
                error=shape_error,
            )
        ]

    if not raw_calls:
        return []

    overflow = len(raw_calls) > MAX_TOOL_CALLS_PER_TURN

    if overflow:
        raw_calls = raw_calls[:MAX_TOOL_CALLS_PER_TURN]

    calls: list[NormalizedToolCall] = []
    seen_ids: set[str] = set()

    for raw in raw_calls:
        call_id = _normalize_call_id(_field(raw, "id"), seen_ids)
        function = _field(raw, "function")
        name = _field(function, "name")
        arguments, error = parse_tool_arguments(
            _field(function, "arguments")
        )

        if not isinstance(name, str) or not name.strip():
            calls.append(
                NormalizedToolCall(
                    id=call_id,
                    name="",
                    error=_MISSING_NAME,
                )
            )
            continue

        cleaned_name = name.strip()

        if len(cleaned_name) > MAX_TOOL_NAME_CHARS:
            error = _NAME_TOO_LONG

        calls.append(
            NormalizedToolCall(
                id=call_id,
                name=cleaned_name,
                arguments=arguments,
                error=error,
            )
        )

    if overflow:
        calls.append(
            NormalizedToolCall(
                id=new_call_id(),
                name="",
                error=_TOO_MANY_CALLS,
            )
        )

    return calls


def extract_groq_tool_calls(message: Any) -> list[NormalizedToolCall]:
    """Normalize Groq/OpenAI ``message.tool_calls``."""

    return _extract_tool_calls(message)


def extract_ollama_tool_calls(message: Any) -> list[NormalizedToolCall]:
    """Normalize Ollama ``message.tool_calls``."""

    return _extract_tool_calls(message)


def _normalize_tool_definition(
    definition: Any,
) -> dict[str, Any] | None:
    """Return one canonical function definition, or ``None`` when invalid."""

    if not isinstance(definition, dict):
        return None

    if definition.get("type") != "function":
        return None

    function = definition.get("function")

    if not isinstance(function, dict):
        return None

    name = function.get("name")

    if not isinstance(name, str) or not name.strip():
        return None

    cleaned_name = name.strip()

    if len(cleaned_name) > MAX_TOOL_NAME_CHARS:
        return None

    description = function.get("description")

    if not isinstance(description, str):
        description = ""

    parameters = function.get("parameters")

    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}

    return {
        "type": "function",
        "function": {
            "name": cleaned_name,
            "description": description,
            "parameters": copy.deepcopy(parameters),
        },
    }


def normalize_tool_definitions(
    definitions: Any,
) -> list[dict[str, Any]]:
    """Sanitize registry definitions into the canonical function shape.

    Invalid entries and duplicate names are dropped rather than forwarded to a
    provider. A rejection is logged by count only; definitions contain no user
    data.
    """

    if definitions is None:
        return []

    if not isinstance(definitions, (list, tuple)):
        logger.warning("Ignoring non-list tool definitions.")
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected = 0

    for definition in definitions:
        item = _normalize_tool_definition(definition)

        if item is None:
            rejected += 1
            continue

        name = item["function"]["name"]

        if name in seen:
            rejected += 1
            continue

        seen.add(name)
        normalized.append(item)

    if rejected:
        logger.warning(
            "Discarded %s invalid or duplicate tool definition(s).",
            rejected,
        )

    return normalized


def to_groq_tools(definitions: Any) -> list[dict[str, Any]]:
    """Return Groq/OpenAI-compatible function definitions."""

    return normalize_tool_definitions(definitions)


def to_ollama_tools(definitions: Any) -> list[dict[str, Any]]:
    """Return Ollama-compatible function definitions."""

    return normalize_tool_definitions(definitions)


def _replayable_calls(calls: Any) -> list[dict[str, Any]]:
    """Filter neutral assistant tool calls down to reusable entries."""

    if not isinstance(calls, (list, tuple)):
        return []

    replayable: list[dict[str, Any]] = []

    for call in calls:
        if not isinstance(call, dict):
            continue

        name = call.get("name")
        call_id = call.get("id")

        if not isinstance(name, str) or not name.strip():
            # A malformed call with no id has no tool result to pair with.
            if not call_id:
                continue

            resolved_name = UNKNOWN_TOOL_NAME

        else:
            resolved_name = name.strip()

        arguments = call.get("arguments")

        if not isinstance(arguments, dict):
            arguments = {}

        replayable.append(
            {
                "id": call_id or new_call_id(),
                "name": resolved_name,
                "arguments": arguments,
            }
        )

    return replayable


def to_groq_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate neutral messages to the Groq/OpenAI format."""

    translated: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        role = message.get("role")

        if role == "tool":
            translated.append(
                {
                    "role": "tool",
                    "tool_call_id": _text(message.get("tool_call_id")),
                    "name": _text(message.get("name")),
                    "content": _content_text(message.get("content")),
                }
            )
            continue

        if role == "assistant":
            calls = _replayable_calls(message.get("tool_calls"))

            if calls:
                translated.append(
                    {
                        "role": "assistant",
                        "content": _content_text(message.get("content")),
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(
                                        call["arguments"],
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        default=str,
                                    ),
                                },
                            }
                            for call in calls
                        ],
                    }
                )
                continue

        translated.append(
            {
                "role": role or "user",
                "content": _content_text(message.get("content")),
            }
        )

    return translated


def to_ollama_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate neutral messages to the Ollama chat format."""

    translated: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        role = message.get("role")

        if role == "tool":
            translated.append(
                {
                    "role": "tool",
                    "content": _content_text(message.get("content")),
                    "tool_name": _text(message.get("name")),
                }
            )
            continue

        if role == "assistant":
            calls = _replayable_calls(message.get("tool_calls"))

            if calls:
                translated.append(
                    {
                        "role": "assistant",
                        "content": _content_text(message.get("content")),
                        "tool_calls": [
                            {
                                "function": {
                                    "name": call["name"],
                                    "arguments": call["arguments"],
                                }
                            }
                            for call in calls
                        ],
                    }
                )
                continue

        translated.append(
            {
                "role": role or "user",
                "content": _content_text(message.get("content")),
            }
        )

    return translated


__all__ = [
    "MAX_CALL_ID_CHARS",
    "MAX_TOOL_ARGUMENT_CHARS",
    "MAX_TOOL_CALLS_PER_TURN",
    "MAX_TOOL_NAME_CHARS",
    "NormalizedToolCall",
    "ProviderToolTurn",
    "UNKNOWN_TOOL_NAME",
    "assistant_message",
    "extract_groq_tool_calls",
    "extract_ollama_tool_calls",
    "new_call_id",
    "normalize_tool_definitions",
    "parse_tool_arguments",
    "to_groq_messages",
    "to_groq_tools",
    "to_ollama_messages",
    "to_ollama_tools",
    "tool_message",
]
