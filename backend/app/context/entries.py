"""Typed building blocks for the unified context architecture."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class ContextSource(str, enum.Enum):
    """Independent sources combined by the unified context builder."""

    HISTORY = "history"
    PREFERENCE = "preference"
    MEMORY = "memory"
    TASK = "task"
    DEADLINE = "deadline"


@dataclass(frozen=True)
class ContextEntry:
    """A single piece of context from one source.

    ``content`` is always treated as untrusted data. ``metadata`` holds
    source-specific fields (for example a preference key) without coupling
    the builder to a particular model.
    """

    source: ContextSource
    content: str
    relevance: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextRequest:
    """Inputs shared by all context providers for one chat turn."""

    session: AsyncSession
    user_id: int
    query: str
    private: bool = False
    history: list | None = None
    memories: list | None = None
