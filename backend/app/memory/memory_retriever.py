"""Context-aware retrieval of approved long-term memories.

Keyword retrieval remains the deterministic default. Semantic retrieval uses
pgvector cosine distance when PostgreSQL/pgvector is available and an
application-side cosine fallback for portable test databases. The public
retrieval API keeps the existing keyword behaviour unless semantic mode is
explicitly requested.
"""

import enum
import logging
import math
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secret_patterns import SECRET_PATTERNS
from app.exceptions.memory_exceptions import (
    EmbeddingValidationError,
    MemoryValidationError,
    SemanticRetrievalUnavailable,
)
from app.memory.embedding_service import embedding_service
from app.memory.semantic_support import is_semantic_storage_available
from app.models.memory import Memory, MemoryStatus
from app.services.memory_service import memory_service


logger = logging.getLogger(__name__)


DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 50
MAX_QUERY_LENGTH = 10000


class RetrievalMode(str, enum.Enum):
    """Supported memory retrieval strategies."""

    KEYWORD = "keyword"
    SEMANTIC = "semantic"

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


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def _sql_category_values(normalised_filter: str) -> list[str]:
    """Return stored category spellings that match a normalised filter."""

    values = {normalised_filter}

    for alias, canonical in _CATEGORY_ALIASES.items():
        if canonical == normalised_filter:
            values.add(alias)

    return sorted(values)


def cosine_similarity(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...]
) -> float:
    """Return the cosine similarity between two embedding vectors."""

    if left is None or right is None:
        raise EmbeddingValidationError("Embedding vector cannot be missing.")

    if len(left) != len(right):
        raise EmbeddingValidationError(
            "Embedding vectors must have the same dimension."
        )

    if not left:
        raise EmbeddingValidationError("Embedding vector cannot be empty.")

    dot = sum(
        float(left_value) * float(right_value)
        for left_value, right_value in zip(left, right)
    )

    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))

    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


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
        if _contains_secret(memory.content):
            continue

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

    @staticmethod
    def _validate_request(
        query: str,
        limit: int,
        category: str | None
    ) -> tuple[str, int, str | None]:

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

        return cleaned_query, limit, normalised_filter

    async def retrieve_keyword(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        category: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT
    ) -> list[RetrievedMemory]:
        """Retrieve memories using the existing deterministic keyword scorer."""

        cleaned_query, limit, normalised_filter = self._validate_request(
            query=query,
            limit=limit,
            category=category
        )

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

    async def retrieve_semantic(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        category: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
        similarity_threshold: float | None = None
    ) -> list[RetrievedMemory]:
        """Retrieve approved memories by embedding cosine similarity."""

        cleaned_query, limit, normalised_filter = self._validate_request(
            query=query,
            limit=limit,
            category=category
        )

        threshold = (
            settings.SEMANTIC_SIMILARITY_THRESHOLD
            if similarity_threshold is None
            else similarity_threshold
        )

        if not 0.0 <= threshold <= 1.0:
            raise MemoryValidationError(
                "Similarity threshold must be between 0.0 and 1.0."
            )

        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""

        if dialect == "postgresql":
            return await self._retrieve_semantic_pgvector(
                session=session,
                user_id=user_id,
                cleaned_query=cleaned_query,
                normalised_filter=normalised_filter,
                limit=limit,
                threshold=threshold
            )

        return await self._retrieve_semantic_python(
            session=session,
            user_id=user_id,
            cleaned_query=cleaned_query,
            normalised_filter=normalised_filter,
            limit=limit,
            threshold=threshold
        )

    async def retrieve(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        category: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
        mode: RetrievalMode | str = RetrievalMode.KEYWORD,
        similarity_threshold: float | None = None,
        allow_keyword_fallback: bool = False
    ) -> list[RetrievedMemory]:
        """Dispatch to keyword or semantic retrieval.

        Keyword retrieval remains the default so existing callers and the
        existing retrieval API keep their current behaviour. Chat can request
        semantic mode with keyword fallback when pgvector or the embedding
        model is unavailable.
        """

        mode_value = (
            mode.value
            if isinstance(mode, RetrievalMode)
            else str(mode or "").strip().lower()
        )

        if mode_value == RetrievalMode.KEYWORD.value:
            return await self.retrieve_keyword(
                session=session,
                user_id=user_id,
                query=query,
                category=category,
                limit=limit
            )

        if mode_value != RetrievalMode.SEMANTIC.value:
            raise MemoryValidationError(
                "Unsupported memory retrieval mode."
            )

        try:
            return await self.retrieve_semantic(
                session=session,
                user_id=user_id,
                query=query,
                category=category,
                limit=limit,
                similarity_threshold=similarity_threshold
            )

        except MemoryValidationError:
            raise

        except Exception:
            if not allow_keyword_fallback:
                raise

            logger.info(
                "Semantic memory retrieval unavailable; using keyword "
                "retrieval fallback."
            )

            return await self.retrieve_keyword(
                session=session,
                user_id=user_id,
                query=query,
                category=category,
                limit=limit
            )

    async def _retrieve_semantic_pgvector(
        self,
        session: AsyncSession,
        user_id: int,
        cleaned_query: str,
        normalised_filter: str | None,
        limit: int,
        threshold: float
    ) -> list[RetrievedMemory]:

        if not await is_semantic_storage_available(session):
            raise SemanticRetrievalUnavailable(
                "The pgvector extension is not enabled."
            )

        has_embedding_query = (
            select(Memory.id)
            .where(
                Memory.user_id == user_id,
                Memory.status == MemoryStatus.APPROVED,
                Memory.embedding.is_not(None)
            )
            .limit(1)
        )

        try:
            has_embedding = await session.execute(has_embedding_query)
            has_embedding = has_embedding.scalar_one_or_none()

        except SQLAlchemyError as error:
            raise SemanticRetrievalUnavailable(
                "Semantic memory storage is not available."
            ) from error

        if has_embedding is None:
            raise SemanticRetrievalUnavailable(
                "No approved memories have embeddings."
            )

        query_vector = await embedding_service.embed_query(cleaned_query)

        distance = Memory.embedding.cosine_distance(query_vector)

        statement = select(Memory, distance.label("distance")).where(
            Memory.user_id == user_id,
            Memory.status == MemoryStatus.APPROVED,
            Memory.embedding.is_not(None),
            distance <= (1.0 - threshold)
        )

        if normalised_filter is not None:
            statement = statement.where(
                func.lower(func.trim(Memory.category)).in_(
                    _sql_category_values(normalised_filter)
                )
            )

        statement = statement.order_by(
            distance.asc(),
            Memory.id.asc()
        ).limit(limit)

        try:
            rows = (await session.execute(statement)).all()

        except SQLAlchemyError as error:
            raise SemanticRetrievalUnavailable(
                "Semantic memory storage is not available."
            ) from error

        results: list[RetrievedMemory] = []

        for memory, distance_value in rows:
            if _contains_secret(memory.content):
                continue

            similarity = max(0.0, min(1.0, 1.0 - float(distance_value)))

            results.append(
                RetrievedMemory(
                    content=memory.content,
                    category=memory.category,
                    relevance_score=round(similarity, 4)
                )
            )

        return results

    async def _retrieve_semantic_python(
        self,
        session: AsyncSession,
        user_id: int,
        cleaned_query: str,
        normalised_filter: str | None,
        limit: int,
        threshold: float
    ) -> list[RetrievedMemory]:
        """Portable cosine path used by test databases without pgvector."""

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

        embedded = [
            memory
            for memory in memories
            if memory.embedding is not None
            and not _contains_secret(memory.content)
        ]

        if not embedded:
            raise SemanticRetrievalUnavailable(
                "No approved memories have embeddings."
            )

        query_vector = await embedding_service.embed_query(cleaned_query)

        scored: list[tuple[float, int, Memory]] = []

        for memory in embedded:
            try:
                similarity = cosine_similarity(
                    query_vector,
                    memory.embedding
                )

            except EmbeddingValidationError:
                logger.warning(
                    "Skipping memory %s with an invalid embedding.",
                    memory.id
                )
                continue

            if similarity < threshold:
                continue

            scored.append(
                (
                    round(max(0.0, min(1.0, similarity)), 4),
                    memory.id,
                    memory
                )
            )

        scored.sort(key=lambda item: (-item[0], item[1]))

        return [
            RetrievedMemory(
                content=memory.content,
                category=memory.category,
                relevance_score=score
            )
            for score, _memory_id, memory in scored[:limit]
        ]


# Create a reusable retriever instance
memory_retriever = MemoryRetriever()
