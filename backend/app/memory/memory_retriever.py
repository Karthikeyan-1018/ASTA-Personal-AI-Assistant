"""Context-aware retrieval of approved long-term memories.

The first implementation uses a deterministic, local keyword and category
scoring function. It deliberately keeps scoring separate from database access
so a future semantic (pgvector) scorer can be added without changing the API.
"""

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.memory_exceptions import MemoryValidationError
from app.models.memory import Memory
from app.services.memory_service import memory_service


DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 50
MAX_QUERY_LENGTH = 10000

# Relevance = KEYWORD_WEIGHT * query keyword coverage
#           + CATEGORY_WEIGHT * category match
#
# Query keyword coverage is the fraction of meaningful query tokens that also
# appear in the memory. Category match is 1 when the query implies the memory's
# category (by naming it or using one of its keywords), otherwise 0.
KEYWORD_WEIGHT = 0.75
CATEGORY_WEIGHT = 0.25

# Function words that carry no retrieval signal.
STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "also", "am",
    "an", "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "could", "d",
    "did", "do", "does", "doing", "done", "down", "during", "each", "few",
    "for", "from", "further", "give", "had", "has", "have", "having", "he",
    "her", "here", "hers", "herself", "him", "himself", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "itself", "just", "know", "ll",
    "m", "may", "me", "might", "more", "most", "much", "must", "my", "myself",
    "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours",
    "ourselves", "out", "over", "own", "please", "re", "recall", "remember",
    "s", "same", "shall", "she", "should", "show", "so", "some", "such",
    "t", "tell", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "under", "until", "up", "us", "ve", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "whose", "why", "will", "with", "would", "you", "your", "yours",
    "yourself", "yourselves",
})

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


def _normalise_token(token: str) -> str:
    """Apply a small, predictable amount of stemming."""

    token = token.casefold()

    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"

    if token.endswith("ied") and len(token) > 4:
        return token[:-3] + "y"

    if (
        token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
        and len(token) > 3
    ):
        return token[:-1]

    return token


def _tokenize(text: str) -> set[str]:
    """Return meaningful, normalised tokens from text."""

    normalised = unicodedata.normalize("NFKC", text).casefold()

    tokens = set()

    for raw_token in _TOKEN_PATTERN.findall(normalised):
        if raw_token in STOP_WORDS:
            continue

        token = _normalise_token(raw_token)

        if len(token) < 2 or token in STOP_WORDS:
            continue

        tokens.add(token)

    return tokens


def _normalise_category_keywords(values: set[str]) -> frozenset[str]:
    keywords: set[str] = set()

    for value in values:
        keywords.update(_tokenize(value))
        keywords.add(_normalise_token(value.casefold()))

    return frozenset(keywords)


# Terms that indicate a query is about a particular memory category. These are
# hints only: scoring still requires a keyword hit or a category match.
CATEGORY_KEYWORDS = {
    "personal": _normalise_category_keywords({
        "personal", "identity", "profile", "name", "live", "location",
        "home", "family", "born", "birthday", "age", "relationship",
        "partner", "spouse",
    }),
    "education": _normalise_category_keywords({
        "education", "study", "school", "college", "university", "degree",
        "course", "class", "qualification", "exam", "student", "academic",
        "learn", "training", "certificate",
    }),
    "career": _normalise_category_keywords({
        "career", "job", "work", "employer", "employee", "company",
        "profession", "professional", "role", "position", "title",
        "engineer", "developer", "manager", "scientist",
    }),
    "projects": _normalise_category_keywords({
        "project", "build", "building", "app", "application", "product",
        "startup", "repository",
    }),
    "preferences": _normalise_category_keywords({
        "preference", "like", "love", "enjoy", "favorite", "favourite",
        "dislike", "hate", "hobby", "interest", "music", "food", "color",
        "colour", "book", "movie", "sport",
    }),
    "goals": _normalise_category_keywords({
        "goal", "aim", "aspiration", "dream", "plan", "want", "wish",
        "target", "future", "objective", "ambition",
    }),
}

# Only singular/plural forms are normalised. Arbitrary user-defined categories
# such as "work" are otherwise preserved for filtering.
_CATEGORY_ALIASES = {
    "project": "projects",
    "preference": "preferences",
    "goal": "goals",
}


def _normalise_category(value: str) -> str:
    cleaned = value.strip().casefold()

    return _CATEGORY_ALIASES.get(cleaned, cleaned)


def _infer_query_categories(query_tokens: set[str]) -> set[str]:
    categories: set[str] = set()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if query_tokens & keywords:
            categories.add(category)

    return categories


@dataclass(frozen=True)
class RetrievedMemory:
    """A ranked memory returned by the retriever."""

    content: str
    category: str
    relevance_score: float


def rank_memories(
    memories: list[Memory],
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT
) -> list[RetrievedMemory]:
    """Rank approved memories against a query using local scoring.

    Deterministic ordering is achieved by sorting on score descending and then
    memory id ascending. Memories with neither a keyword overlap nor a category
    match are excluded entirely.
    """

    if limit < 1:
        raise MemoryValidationError("Limit must be at least 1.")

    limit = min(limit, MAX_RESULT_LIMIT)

    query_tokens = _tokenize(query)

    if not query_tokens:
        return []

    query_categories = _infer_query_categories(query_tokens)

    scored: list[tuple[float, Memory]] = []

    for memory in memories:
        memory_tokens = _tokenize(memory.content)
        overlap = query_tokens & memory_tokens

        category_match = (
            _normalise_category(memory.category) in query_categories
        )

        if not overlap and not category_match:
            continue

        coverage = len(overlap) / len(query_tokens)
        score = (
            KEYWORD_WEIGHT * coverage
            + CATEGORY_WEIGHT * (1.0 if category_match else 0.0)
        )

        scored.append((round(min(score, 1.0), 4), memory))

    scored.sort(key=lambda item: (-item[0], item[1].id))

    return [
        RetrievedMemory(
            content=memory.content,
            category=memory.category,
            relevance_score=score
        )
        for score, memory in scored[:limit]
    ]


class MemoryRetriever:
    """Retrieve the most relevant approved memories for a user's query."""

    async def retrieve(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        category: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT
    ) -> list[RetrievedMemory]:

        cleaned_query = (query or "").strip()

        if not cleaned_query:
            raise MemoryValidationError("Search query cannot be empty.")

        if len(cleaned_query) > MAX_QUERY_LENGTH:
            raise MemoryValidationError(
                "Search query cannot exceed "
                f"{MAX_QUERY_LENGTH} characters."
            )

        if limit < 1:
            raise MemoryValidationError("Limit must be at least 1.")

        limit = min(limit, MAX_RESULT_LIMIT)

        normalised_filter: str | None = None

        if category is not None:
            cleaned_category = category.strip()

            if not cleaned_category:
                raise MemoryValidationError(
                    "Category filter cannot be empty."
                )

            normalised_filter = _normalise_category(cleaned_category)

        # Reuse the existing service so user isolation and the approved-only
        # rule are enforced in one place.
        memories = await memory_service.get_approved_memories(
            session=session,
            user_id=user_id
        )

        if normalised_filter is not None:
            memories = [
                memory
                for memory in memories
                if _normalise_category(memory.category) == normalised_filter
            ]

        return rank_memories(
            memories=memories,
            query=cleaned_query,
            limit=limit
        )


# Create a reusable retriever instance
memory_retriever = MemoryRetriever()
