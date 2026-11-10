"""Context-aware retrieval of active user preferences.

Preferences are deterministic structured records. Retrieval therefore uses the
same local keyword scorer as long-term memories rather than sending preference
data to an embedding provider. Only active preferences owned by the requesting
user are ever considered.
"""

import logging
import unicodedata
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secret_patterns import contains_secret
from app.memory.memory_retriever import _tokenize
from app.models.preference import UserPreference
from app.services.preference_service import preference_service


logger = logging.getLogger(__name__)


MAX_QUERY_LENGTH = 10000
MAX_RESULT_LIMIT = 50
KEYWORD_WEIGHT = 0.8
CATEGORY_WEIGHT = 0.2


CATEGORY_KEYWORDS = {
    "communication": frozenset({
        "communication", "style", "tone", "format", "explain", "explanation",
        "beginner", "simple", "detailed", "concise", "response", "answer",
        "technical", "guide",
    }),
    "learning": frozenset({
        "learn", "learning", "study", "studying", "course", "education",
        "understand", "tutorial", "beginner", "practice", "lesson",
    }),
    "programming": frozenset({
        "programming", "program", "code", "coding", "language", "python",
        "javascript", "java", "typescript", "framework", "library",
        "developer", "software",
    }),
    "career": frozenset({
        "career", "job", "work", "profession", "professional", "role",
        "industry", "engineering", "engineer", "ml", "ai", "research",
    }),
    "productivity": frozenset({
        "productivity", "productive", "workflow", "plan", "planning",
        "schedule", "step", "steps", "organise", "organize", "focus",
        "routine", "task",
    }),
    "general": frozenset({
        "preference", "preferences", "prefer", "like", "dislike", "want",
        "generally", "usual", "default",
    }),
}


@dataclass(frozen=True)
class RetrievedPreference:
    """A ranked preference returned by the retriever."""

    id: int
    key: str
    value: str
    category: str
    relevance_score: float


def _normalise_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def _category_tokens(category: str) -> frozenset[str]:
    return CATEGORY_KEYWORDS.get(
        (category or "").strip().lower(),
        frozenset()
    )


def rank_preferences(
    preferences: list[UserPreference],
    query: str,
    limit: int | None = None,
    threshold: float | None = None
) -> list[RetrievedPreference]:
    """Rank active preferences by keyword relevance to a query."""

    effective_limit = (
        settings.PREFERENCE_RETRIEVAL_LIMIT
        if limit is None
        else limit
    )
    effective_limit = max(1, min(effective_limit, MAX_RESULT_LIMIT))

    effective_threshold = (
        settings.PREFERENCE_RELEVANCE_THRESHOLD
        if threshold is None
        else max(0.0, min(float(threshold), 1.0))
    )

    query_tokens = _tokenize(query)

    if not query_tokens:
        return []

    scored: list[tuple[float, UserPreference]] = []
    seen: set[str] = set()

    for preference in preferences:
        if contains_secret(preference.value):
            continue

        key_text = _normalise_text(preference.key.replace("_", " "))
        value_text = _normalise_text(preference.value)
        category_text = _normalise_text(preference.category)

        preference_tokens = _tokenize(
            f"{key_text} {value_text} {category_text}"
        )
        overlap = query_tokens & preference_tokens

        category_hint = bool(
            query_tokens & _category_tokens(preference.category)
        )

        if not overlap and not category_hint:
            continue

        coverage = (
            len(overlap) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        score = (
            KEYWORD_WEIGHT * coverage
            + CATEGORY_WEIGHT * (1.0 if category_hint else 0.0)
        )
        score = round(min(score, 1.0), 4)

        if score < effective_threshold:
            continue

        dedupe_key = f"{preference.key.casefold()}::{value_text}"

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        scored.append((score, preference))

    scored.sort(key=lambda item: (-item[0], item[1].id))

    return [
        RetrievedPreference(
            id=preference.id,
            key=preference.key,
            value=preference.value,
            category=preference.category,
            relevance_score=score
        )
        for score, preference in scored[:effective_limit]
    ]


class PreferenceRetriever:
    """Retrieve active preferences relevant to a user's query."""

    async def retrieve(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        limit: int | None = None,
        threshold: float | None = None
    ) -> list[RetrievedPreference]:

        cleaned_query = (query or "").strip()

        if not cleaned_query:
            return []

        if len(cleaned_query) > MAX_QUERY_LENGTH:
            cleaned_query = cleaned_query[:MAX_QUERY_LENGTH]

        preferences = await preference_service.get_active_preferences(
            session=session,
            user_id=user_id
        )

        return rank_preferences(
            preferences=preferences,
            query=cleaned_query,
            limit=limit,
            threshold=threshold
        )


preference_retriever = PreferenceRetriever()
