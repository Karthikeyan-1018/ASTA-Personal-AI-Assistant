"""Unified context builder and character-budget manager.

The builder collects independent context sources, removes duplicate context,
applies a configurable priority order and total character budget, and renders
one prompt with source separation preserved.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.ai.tokenization import (
    resolve_token_budget,
    safe_estimate_tokens,
)
from app.context.entries import ContextEntry, ContextRequest, ContextSource
from app.context.providers import (
    ContextProvider,
    DeadlineContextProvider,
    HistoryContextProvider,
    MemoryContextProvider,
    PreferenceContextProvider,
    TaskContextProvider,
)
from app.core.config import settings
from app.memory.context_builder import build_chat_prompt
from app.memory.memory_retriever import RetrievedMemory
from app.memory.preference_retriever import RetrievedPreference


logger = logging.getLogger(__name__)


ALL_SOURCES = (
    ContextSource.HISTORY,
    ContextSource.PREFERENCE,
    ContextSource.MEMORY,
    ContextSource.TASK,
    ContextSource.DEADLINE,
)

SECTION_HEADERS = {
    ContextSource.HISTORY: "CONVERSATION HISTORY",
    ContextSource.PREFERENCE: "USER PREFERENCES",
    ContextSource.MEMORY: "RETRIEVED MEMORIES",
    ContextSource.TASK: "TASK CONTEXT",
    ContextSource.DEADLINE: "DEADLINE CONTEXT",
}


@dataclass(frozen=True)
class ContextInspection:
    """Internal, user-scoped view of one assembled context prompt.

    Intentionally exposes only the final rendered prompt and source counts, not
    raw retrieved rows. Callers must pass the same trusted ``user_id`` used for
    the chat turn; this helper is not wired to any public endpoint.
    """

    user_id: int
    private: bool
    prompt: str
    total_chars: int
    max_chars: int
    source_counts: dict[str, int]
    rendered_sources: tuple[str, ...]

    # Phase 9 token-aware inspection fields. They are additive so existing
    # callers keep working unchanged.
    estimated_tokens: int | None = None
    max_tokens: int | None = None
    token_counting_method: str | None = None
    trimmed_sources: tuple[str, ...] = ()
    token_budget_enabled: bool = True


def _normalise_content(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _priority_order() -> list[ContextSource]:
    """Return the configured source priority from highest to lowest."""

    configured = [
        item.strip().lower()
        for item in (settings.CONTEXT_PRIORITY_ORDER or "").split(",")
        if item.strip()
    ]

    by_value = {source.value: source for source in ALL_SOURCES}
    order: list[ContextSource] = []

    for name in configured:
        source = by_value.get(name)

        if source is not None and source not in order:
            order.append(source)

    for source in ALL_SOURCES:
        if source not in order:
            order.append(source)

    return order


def _deduplicate(
    entries: list[ContextEntry],
    priority: list[ContextSource]
) -> list[ContextEntry]:
    """Remove exact duplicates and preference/memory duplication."""

    rank = {source: index for index, source in enumerate(priority)}
    ordered = sorted(
        entries,
        key=lambda entry: (
            rank.get(entry.source, len(rank)),
            -entry.relevance,
        )
    )

    seen: set[str] = set()
    unique: list[ContextEntry] = []

    for entry in ordered:
        content = _normalise_content(entry.content)

        if not content or content in seen:
            continue

        seen.add(content)
        unique.append(entry)

    preference_values = [
        _normalise_content(entry.content)
        for entry in unique
        if entry.source == ContextSource.PREFERENCE
    ]

    if preference_values:
        unique = [
            entry
            for entry in unique
            if not (
                entry.source == ContextSource.MEMORY
                and any(
                    len(value) >= 4
                    and value in _normalise_content(entry.content)
                    for value in preference_values
                )
            )
        ]

    return unique


class UnifiedContextBuilder:
    """Combine every context source into one bounded, untrusted-safe prompt."""

    def __init__(
        self,
        providers: list[ContextProvider] | None = None
    ) -> None:

        self.providers = providers if providers is not None else [
            HistoryContextProvider(),
            PreferenceContextProvider(),
            MemoryContextProvider(),
            TaskContextProvider(),
            DeadlineContextProvider(),
        ]

    async def build(
        self,
        session,
        user_id: int,
        user_message: str,
        private: bool = False,
        history: list | None = None,
        memories: list | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:

        request = ContextRequest(
            session=session,
            user_id=user_id,
            query=user_message,
            private=private,
            history=history,
            memories=memories
        )

        _grouped, prompt, _trimmed = await self._assemble(
            request,
            provider=provider,
            model=model,
        )

        return prompt

    async def inspect(
        self,
        session,
        user_id: int,
        user_message: str,
        private: bool = False,
        history: list | None = None,
        memories: list | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> ContextInspection:
        """Return the exact assembled prompt and source summary.

        This is a development/test helper. It is user-scoped and never logs or
        serialises raw context rows, so it cannot leak another user's data or
        secrets beyond the already-sanitised prompt itself.
        """

        request = ContextRequest(
            session=session,
            user_id=user_id,
            query=user_message,
            private=private,
            history=history,
            memories=memories
        )

        grouped, prompt, trimmed = await self._assemble(
            request,
            provider=provider,
            model=model,
        )

        budget = self._resolve_budget(provider=provider, model=model)

        return ContextInspection(
            user_id=user_id,
            private=private,
            prompt=prompt,
            total_chars=len(prompt),
            max_chars=max(1, settings.CONTEXT_MAX_CHARS),
            source_counts={
                source.value: len(grouped[source])
                for source in ALL_SOURCES
            },
            rendered_sources=tuple(
                source.value
                for source in ALL_SOURCES
                if SECTION_HEADERS[source] in prompt
            ),
            estimated_tokens=safe_estimate_tokens(prompt),
            max_tokens=budget.available if budget else None,
            token_counting_method=budget.method if budget else None,
            trimmed_sources=tuple(
                source.value for source in trimmed
            ),
            token_budget_enabled=budget is not None,
        )

    @staticmethod
    def _resolve_budget(
        provider: str | None = None,
        model: str | None = None,
    ):
        """Return a token budget, or ``None`` when budgeting is disabled."""

        if not settings.AI_TOKEN_BUDGET_ENABLED:
            return None

        try:
            return resolve_token_budget(model=model, provider=provider)
        except Exception:
            logger.warning(
                "Token budget resolution failed; using character budgets.",
                exc_info=True,
            )
            return None

    async def _assemble(
        self,
        request: ContextRequest,
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[dict[ContextSource, list], str, tuple[ContextSource, ...]]:
        """Retrieve, deduplicate, group, and render one context request."""

        entries: list[ContextEntry] = []

        for context_provider in self.providers:
            try:
                provider_entries = await context_provider.retrieve(request)

            except Exception:
                # A single context source must never take down chat.
                logger.exception(
                    "Context provider %s failed; continuing without it.",
                    getattr(
                        context_provider,
                        "source",
                        type(context_provider).__name__,
                    )
                )
                continue

            entries.extend(provider_entries or [])

        unique = _deduplicate(entries, _priority_order())

        grouped = self._group(unique)

        prompt, trimmed = self._render_with_budget(
            grouped,
            request.query,
            budget=self._resolve_budget(provider=provider, model=model),
        )

        return grouped, prompt, trimmed

    @staticmethod
    def _group(entries: list[ContextEntry]) -> dict[ContextSource, list]:
        grouped: dict[ContextSource, list] = {
            source: [] for source in ALL_SOURCES
        }

        for entry in entries:
            metadata = entry.metadata or {}

            if entry.source == ContextSource.PREFERENCE:
                grouped[ContextSource.PREFERENCE].append(
                    RetrievedPreference(
                        id=int(metadata.get("id") or 0),
                        key=str(metadata.get("key") or ""),
                        value=entry.content,
                        category=str(metadata.get("category") or "general"),
                        relevance_score=entry.relevance
                    )
                )

            elif entry.source == ContextSource.MEMORY:
                grouped[ContextSource.MEMORY].append(
                    RetrievedMemory(
                        content=entry.content,
                        category=str(
                            metadata.get("category") or "general"
                        ),
                        relevance_score=entry.relevance
                    )
                )

            elif entry.source == ContextSource.HISTORY:
                grouped[ContextSource.HISTORY].append({
                    "role": str(metadata.get("role") or "user"),
                    "content": entry.content
                })

            elif entry.source == ContextSource.TASK:
                grouped[ContextSource.TASK].append(entry.content)

            elif entry.source == ContextSource.DEADLINE:
                grouped[ContextSource.DEADLINE].append(entry.content)

        return grouped

    @staticmethod
    def _render(
        grouped: dict[ContextSource, list],
        user_message: str
    ) -> str:

        return build_chat_prompt(
            user_message=user_message,
            memories=grouped[ContextSource.MEMORY],
            history=grouped[ContextSource.HISTORY] or None,
            preferences=grouped[ContextSource.PREFERENCE] or None,
            task_context=grouped[ContextSource.TASK] or None,
            deadline_context=grouped[ContextSource.DEADLINE] or None
        )

    def _render_with_budget(
        self,
        grouped: dict[ContextSource, list],
        user_message: str,
        budget=None,
    ) -> tuple[str, tuple[ContextSource, ...]]:
        """Trim lowest-priority context until character and token budgets fit.

        The current user message is never trimmed. If it alone exceeds the
        budget, the prompt is returned as-is rather than truncated, so the
        request cannot silently lose the user's question.
        """

        prompt = self._render(grouped, user_message)
        max_chars = max(1, settings.CONTEXT_MAX_CHARS)
        max_tokens = budget.available if budget is not None else None

        def over_budget(value: str) -> bool:

            if len(value) > max_chars:
                return True

            if max_tokens is None:
                return False

            estimated = safe_estimate_tokens(value)

            # If counting fails, rely on the character budget.
            return estimated is not None and estimated > max_tokens

        if not over_budget(prompt):
            return prompt, ()

        # Lowest priority first. The current user message is never trimmed.
        removal_order = list(reversed(_priority_order()))
        trimmed: list[ContextSource] = []

        while over_budget(prompt):
            removed = False

            for source in removal_order:
                values = grouped[source]

                if not values:
                    continue

                if source == ContextSource.HISTORY:
                    # Keep the newest messages when trimming history.
                    values.pop(0)
                else:
                    values.pop()

                if source not in trimmed:
                    trimmed.append(source)

                removed = True
                break

            if not removed:
                break

            prompt = self._render(grouped, user_message)

        return prompt, tuple(trimmed)


unified_context_builder = UnifiedContextBuilder()
