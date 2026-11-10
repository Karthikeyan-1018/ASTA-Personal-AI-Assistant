"""Context providers for each independent context source.

Adding a new context source means implementing ``ContextProvider`` and adding
it to ``UnifiedContextBuilder``'s provider list. ``ChatService`` does not need
to change.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.context.entries import ContextEntry, ContextRequest, ContextSource
from app.context.task_selector import (
    render_deadline,
    render_task,
    select_deadline_items,
    select_task_items,
)
from app.memory.memory_retriever import memory_retriever
from app.memory.preference_retriever import preference_retriever


logger = logging.getLogger(__name__)


@runtime_checkable
class ContextProvider(Protocol):
    """Interface implemented by every context source."""

    source: ContextSource

    async def retrieve(self, request: ContextRequest) -> list[ContextEntry]:
        ...


def _is_private_message(message) -> bool:
    """Return whether a history item is marked private."""

    value = getattr(message, "is_private", None)

    if value is None and isinstance(message, dict):
        value = message.get("is_private")

    return bool(value)


class HistoryContextProvider:
    """Provide bounded short-term conversation history."""

    source = ContextSource.HISTORY

    async def retrieve(self, request: ContextRequest) -> list[ContextEntry]:

        entries = []

        for message in request.history or []:
            # Defence in depth: private messages must never enter a normal
            # (cloud-eligible) prompt, even when a caller bypasses ChatService.
            if not request.private and _is_private_message(message):
                continue

            role = getattr(message, "role", None)
            content = getattr(message, "content", None)

            if role is None and isinstance(message, dict):
                role = message.get("role")
                content = message.get("content")

            role = getattr(role, "value", role)

            if role is None or content is None:
                continue

            entries.append(
                ContextEntry(
                    source=self.source,
                    content=str(content),
                    metadata={"role": str(role).lower()}
                )
            )

        return entries


class PreferenceContextProvider:
    """Provide active preferences relevant to the current message."""

    source = ContextSource.PREFERENCE

    async def retrieve(self, request: ContextRequest) -> list[ContextEntry]:

        try:
            preferences = await preference_retriever.retrieve(
                session=request.session,
                user_id=request.user_id,
                query=request.query
            )

        except Exception:
            # Chat must remain available if preference retrieval fails.
            # Never log preference values.
            logger.exception(
                "Preference retrieval failed; continuing without "
                "preference context."
            )
            return []

        return [
            ContextEntry(
                source=self.source,
                content=preference.value,
                relevance=preference.relevance_score,
                metadata={
                    "id": preference.id,
                    "key": preference.key,
                    "category": preference.category
                }
            )
            for preference in preferences
        ]


class MemoryContextProvider:
    """Provide approved long-term memories relevant to the current message."""

    source = ContextSource.MEMORY

    async def retrieve(self, request: ContextRequest) -> list[ContextEntry]:

        memories = request.memories

        if memories is None:
            try:
                memories = await memory_retriever.retrieve(
                    session=request.session,
                    user_id=request.user_id,
                    query=request.query
                )

            except Exception:
                logger.exception(
                    "Memory retrieval failed; continuing without "
                    "memory context."
                )
                return []

        return [
            ContextEntry(
                source=self.source,
                content=memory.content,
                relevance=float(memory.relevance_score),
                metadata={"category": memory.category}
            )
            for memory in memories
        ]


class TaskContextProvider:
    """Provide relevant structured task context for the current user."""

    source = ContextSource.TASK

    async def retrieve(self, request: ContextRequest) -> list[ContextEntry]:

        if request.private:
            # Private requests must not inject stored task data.
            return []

        try:
            tasks = await select_task_items(
                session=request.session,
                user_id=request.user_id,
                query=request.query,
            )

        except Exception:
            # Chat must remain available if task retrieval fails.
            # Never log task titles or descriptions here.
            logger.exception(
                "Task context retrieval failed; continuing without "
                "task context."
            )
            return []

        return [
            ContextEntry(
                source=self.source,
                content=render_task(task),
                relevance=relevance,
                metadata={
                    "task_id": task.id,
                    "category": getattr(task.category, "value", task.category),
                    "priority": getattr(task.priority, "value", task.priority),
                    "status": getattr(task.status, "value", task.status),
                },
            )
            for task, relevance in tasks
        ]


class DeadlineContextProvider:
    """Provide relevant structured deadline context for the current user."""

    source = ContextSource.DEADLINE

    async def retrieve(self, request: ContextRequest) -> list[ContextEntry]:

        if request.private:
            # Private requests must not inject stored deadline data.
            return []

        try:
            items = await select_deadline_items(
                session=request.session,
                user_id=request.user_id,
                query=request.query,
            )

        except Exception:
            # Chat must remain available if deadline retrieval fails.
            # Never log task titles or descriptions here.
            logger.exception(
                "Deadline context retrieval failed; continuing without "
                "deadline context."
            )
            return []

        return [
            ContextEntry(
                source=self.source,
                content=render_deadline(item),
                relevance=item.relevance,
                metadata={
                    "task_id": item.task.id,
                    "category": getattr(
                        item.task.category,
                        "value",
                        item.task.category,
                    ),
                    "priority": getattr(
                        item.task.priority,
                        "value",
                        item.task.priority,
                    ),
                    "status": getattr(
                        item.task.status,
                        "value",
                        item.task.status,
                    ),
                    "deadline_state": item.state.value,
                },
            )
            for item in items
        ]
