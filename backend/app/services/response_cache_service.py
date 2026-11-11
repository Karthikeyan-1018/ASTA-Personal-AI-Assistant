"""Phase 9 semantic response cache.

The cache is deliberately conservative:

* only user-scoped, stateless, non-private requests are eligible (enforced by
  the caller);
* PostgreSQL with pgvector uses cosine similarity;
* SQLite and other portable test databases use exact query-hash matching and
  never load the embedding model;
* every cache failure is isolated from the chat request.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.memory.embedding_service import embedding_service
from app.memory.semantic_support import is_semantic_storage_available
from app.models.ai_cache import AIResponseCache


logger = logging.getLogger(__name__)


_WHITESPACE = re.compile(r"\s+")
SEMANTIC_CANDIDATE_LIMIT = 10


@dataclass(frozen=True)
class CachedAnswer:
    """A cache hit returned to the chat service."""

    response: str
    provider: str | None
    model: str | None
    complexity: str | None
    routing_policy: str | None
    similarity: float
    hit_type: str
    entry_id: int


def normalise_cache_query(query: str) -> str:
    """Return the stable form used for exact cache matching."""

    return _WHITESPACE.sub(" ", (query or "").strip()).casefold()


def cache_query_hash(query: str) -> str:
    """Return a SHA-256 hash of the normalised query."""

    return hashlib.sha256(
        normalise_cache_query(query).encode("utf-8")
    ).hexdigest()


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def _is_expired(value: datetime | None, now: datetime) -> bool:
    aware = _as_aware(value)

    return aware is not None and aware <= now


def _entry_to_answer(
    entry: AIResponseCache,
    *,
    similarity: float,
    hit_type: str,
) -> CachedAnswer:

    return CachedAnswer(
        response=entry.response,
        provider=entry.provider,
        model=entry.model,
        complexity=entry.complexity,
        routing_policy=entry.routing_policy,
        similarity=similarity,
        hit_type=hit_type,
        entry_id=entry.id,
    )


class ResponseCacheService:
    """Look up and store cached answers for eligible chat requests."""

    async def lookup(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        *,
        prompt_version: str | None = None,
        config_version: str | None = None,
    ) -> CachedAnswer | None:
        """Return a cached answer, or ``None`` on miss or failure."""

        if not settings.AI_CACHE_ENABLED:
            return None

        prompt_version = (
            prompt_version or settings.AI_CACHE_PROMPT_VERSION
        )
        config_version = (
            config_version or settings.AI_CACHE_CONFIG_VERSION
        )

        try:
            answer = await self._lookup_exact(
                session=session,
                user_id=user_id,
                query=query,
                prompt_version=prompt_version,
                config_version=config_version,
            )

            if answer is not None:
                return answer

            if not settings.AI_CACHE_SEMANTIC_ENABLED:
                return None

            return await self._lookup_semantic(
                session=session,
                user_id=user_id,
                query=query,
                prompt_version=prompt_version,
                config_version=config_version,
            )

        except Exception:
            # Cache failures must never break chat. Do not log the query.
            logger.warning(
                "Response cache lookup failed; continuing without cache.",
                exc_info=True,
            )
            return None

    async def _lookup_exact(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        prompt_version: str,
        config_version: str,
    ) -> CachedAnswer | None:

        statement = select(AIResponseCache).where(
            AIResponseCache.user_id == user_id,
            AIResponseCache.query_hash == cache_query_hash(query),
            AIResponseCache.prompt_version == prompt_version,
            AIResponseCache.config_version == config_version,
        ).limit(1)

        entry = await session.scalar(statement)

        if entry is None:
            return None

        now = datetime.now(timezone.utc)

        if _is_expired(entry.expires_at, now):
            return None

        entry.hit_count = (entry.hit_count or 0) + 1
        entry.last_used_at = now

        return _entry_to_answer(
            entry,
            similarity=1.0,
            hit_type="exact",
        )

    async def _lookup_semantic(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        prompt_version: str,
        config_version: str,
    ) -> CachedAnswer | None:

        if not await is_semantic_storage_available(session):
            return None

        try:
            query_vector = await embedding_service.embed_query(query)
        except Exception:
            # No embedding means no semantic match. Exact matching has
            # already been attempted.
            logger.warning(
                "Cache embedding failed; skipping semantic cache lookup."
            )
            return None

        now = datetime.now(timezone.utc)
        distance = AIResponseCache.embedding.cosine_distance(query_vector)

        statement = (
            select(AIResponseCache, distance.label("distance"))
            .where(
                AIResponseCache.user_id == user_id,
                AIResponseCache.prompt_version == prompt_version,
                AIResponseCache.config_version == config_version,
                AIResponseCache.embedding.is_not(None),
                AIResponseCache.expires_at > now,
            )
            .order_by(distance.asc(), AIResponseCache.id.asc())
            .limit(SEMANTIC_CANDIDATE_LIMIT)
        )

        rows = (await session.execute(statement)).all()
        threshold = settings.AI_CACHE_SIMILARITY_THRESHOLD

        for entry, distance_value in rows:
            similarity = max(0.0, min(1.0, 1.0 - float(distance_value)))

            if similarity < threshold:
                continue

            entry.hit_count = (entry.hit_count or 0) + 1
            entry.last_used_at = now

            return _entry_to_answer(
                entry,
                similarity=round(similarity, 4),
                hit_type="semantic",
            )

        return None

    async def store(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        response: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        complexity: str | None = None,
        routing_policy: str | None = None,
        prompt_version: str | None = None,
        config_version: str | None = None,
    ) -> bool:
        """Store one eligible answer. Returns whether a row was written."""

        if not settings.AI_CACHE_ENABLED:
            return False

        if not isinstance(response, str) or not response.strip():
            return False

        prompt_version = (
            prompt_version or settings.AI_CACHE_PROMPT_VERSION
        )
        config_version = (
            config_version or settings.AI_CACHE_CONFIG_VERSION
        )

        try:
            async with session.begin_nested():
                await self._store_row(
                    session=session,
                    user_id=user_id,
                    query=query,
                    response=response,
                    provider=provider,
                    model=model,
                    complexity=complexity,
                    routing_policy=routing_policy,
                    prompt_version=prompt_version,
                    config_version=config_version,
                )

            await session.commit()
            return True

        except Exception:
            await session.rollback()
            logger.warning(
                "Response cache store failed; continuing without cache.",
                exc_info=True,
            )
            return False

    async def _store_row(
        self,
        session: AsyncSession,
        user_id: int,
        query: str,
        response: str,
        provider: str | None,
        model: str | None,
        complexity: str | None,
        routing_policy: str | None,
        prompt_version: str,
        config_version: str,
    ) -> None:

        now = datetime.now(timezone.utc)
        query_hash = cache_query_hash(query)

        statement = select(AIResponseCache).where(
            AIResponseCache.user_id == user_id,
            AIResponseCache.query_hash == query_hash,
            AIResponseCache.prompt_version == prompt_version,
            AIResponseCache.config_version == config_version,
        ).limit(1)

        entry = await session.scalar(statement)
        embedding = await self._safe_embedding(session, query)

        if entry is None:
            entry = AIResponseCache(
                user_id=user_id,
                query_hash=query_hash,
                prompt_version=prompt_version,
                config_version=config_version,
                response=response,
                provider=provider,
                model=model,
                complexity=complexity,
                routing_policy=routing_policy,
                hit_count=0,
                created_at=now,
                expires_at=now + timedelta(
                    seconds=max(1, settings.AI_CACHE_TTL_SECONDS)
                ),
            )
            session.add(entry)

        else:
            entry.response = response
            entry.provider = provider
            entry.model = model
            entry.complexity = complexity
            entry.routing_policy = routing_policy
            entry.expires_at = now + timedelta(
                seconds=max(1, settings.AI_CACHE_TTL_SECONDS)
            )

        if embedding is not None:
            entry.embedding = embedding
            entry.embedding_model = embedding_service.model_name

        await session.flush()
        await self._enforce_user_limit(session, user_id)

    async def _safe_embedding(
        self,
        session: AsyncSession,
        query: str,
    ) -> list[float] | None:
        """Return an embedding only for PostgreSQL/pgvector storage."""

        if not settings.AI_CACHE_SEMANTIC_ENABLED:
            return None

        try:
            if not await is_semantic_storage_available(session):
                return None

            return await embedding_service.embed_query(query)

        except Exception:
            logger.warning(
                "Cache embedding failed; storing an exact-match entry only."
            )
            return None

    async def _enforce_user_limit(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> None:
        """Delete the oldest entries when a user exceeds the configured cap."""

        max_entries = max(1, settings.AI_CACHE_MAX_ENTRIES_PER_USER)

        count = await session.scalar(
            select(func.count(AIResponseCache.id)).where(
                AIResponseCache.user_id == user_id
            )
        )

        excess = int(count or 0) - max_entries

        if excess <= 0:
            return

        oldest_ids = (
            await session.execute(
                select(AIResponseCache.id)
                .where(AIResponseCache.user_id == user_id)
                .order_by(
                    AIResponseCache.created_at.asc(),
                    AIResponseCache.id.asc(),
                )
                .limit(excess)
            )
        ).scalars().all()

        if oldest_ids:
            await session.execute(
                delete(AIResponseCache).where(
                    AIResponseCache.id.in_(oldest_ids)
                )
            )

    async def purge_expired(self, session: AsyncSession) -> int:
        """Delete expired cache rows and return the number removed."""

        try:
            result = await session.execute(
                delete(AIResponseCache).where(
                    AIResponseCache.expires_at
                    <= datetime.now(timezone.utc)
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

        except Exception:
            await session.rollback()
            logger.warning(
                "Response cache purge failed.",
                exc_info=True,
            )
            return 0


response_cache_service = ResponseCacheService()
