"""Indexing and backfill operations for semantic memory embeddings."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.embedding_service import embedding_service
from app.models.memory import Memory, MemoryStatus


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillResult:
    """Counts produced by a memory embedding backfill run."""

    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    batches: int = 0

    @property
    def processed(self) -> int:
        return self.indexed + self.skipped + self.failed


class MemoryEmbeddingService:
    """Generate and maintain embeddings for approved memories."""

    @property
    def model_name(self) -> str:
        return embedding_service.model_name

    async def ensure_embedding(
        self,
        session: AsyncSession,
        memory: Memory,
        force: bool = False
    ) -> bool:
        """Ensure an approved memory has a current embedding.

        Returns True when a new embedding was stored. Pending and rejected
        memories are never indexed. A failed generation is raised to the
        caller and never recorded as a successful embedding.
        """

        if memory.status != MemoryStatus.APPROVED:
            await self.clear_embedding(session, memory)
            return False

        if (
            not force
            and memory.embedding is not None
            and memory.embedding_model == self.model_name
        ):
            return False

        # Capture scalar values before any commit or rollback. A rollback
        # expires ORM instances, and touching them afterwards would trigger
        # lazy IO outside the async greenlet context.
        memory_id = memory.id
        content = memory.content

        try:
            vector = await embedding_service.embed_passage(content)

            memory.embedding = vector
            memory.embedding_model = self.model_name
            memory.embedding_updated_at = datetime.now(timezone.utc)

            await session.commit()
            await session.refresh(memory)

            return True

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to generate an embedding for memory %s.",
                memory_id
            )
            raise

    async def clear_embedding(
        self,
        session: AsyncSession,
        memory: Memory
    ) -> None:
        """Remove a memory's embedding, for example after rejection."""

        if (
            memory.embedding is None
            and memory.embedding_model is None
            and memory.embedding_updated_at is None
        ):
            return

        memory.embedding = None
        memory.embedding_model = None
        memory.embedding_updated_at = None

        memory_id = memory.id

        try:
            await session.commit()
            await session.refresh(memory)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to clear the embedding for memory %s.",
                memory_id
            )
            raise

    async def backfill_embeddings(
        self,
        session: AsyncSession,
        batch_size: int = 25,
        limit: int | None = None,
        force: bool = False
    ) -> BackfillResult:
        """Embed approved memories without current embeddings.

        Records are processed in batches by ascending id. Re-running the
        operation naturally skips memories that already have an embedding
        for the configured model. Memory content is never written to logs.
        """

        if batch_size < 1:
            raise ValueError("batch_size must be at least 1.")

        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative.")

        indexed = 0
        skipped = 0
        failed = 0
        batches = 0
        processed = 0
        last_id = 0

        while limit is None or processed < limit:

            remaining = None if limit is None else limit - processed
            query_size = batch_size if remaining is None else min(
                batch_size,
                remaining
            )

            query = select(Memory).where(
                Memory.status == MemoryStatus.APPROVED,
                Memory.id > last_id
            )

            if not force:
                query = query.where(
                    or_(
                        Memory.embedding.is_(None),
                        Memory.embedding_model.is_(None),
                        Memory.embedding_model != self.model_name
                    )
                )

            query = query.order_by(Memory.id.asc()).limit(query_size)

            memories = list((await session.execute(query)).scalars().all())

            if not memories:
                break

            batches += 1

            memory_ids = [memory.id for memory in memories]

            for memory_id in memory_ids:
                processed += 1
                last_id = memory_id

                try:
                    # Reload by id. A rollback from an earlier failed record
                    # expires every instance in the session, so reusing the
                    # batch objects could trigger lazy IO at the wrong time.
                    memory = await session.get(
                        Memory,
                        memory_id,
                        populate_existing=True
                    )

                    if memory is None:
                        skipped += 1
                        continue

                    created = await self.ensure_embedding(
                        session=session,
                        memory=memory,
                        force=force
                    )

                    if created:
                        indexed += 1
                    else:
                        skipped += 1

                except Exception as error:
                    failed += 1

                    logger.warning(
                        "Embedding backfill failed for memory %s (%s).",
                        memory_id,
                        type(error).__name__
                    )

            if len(memories) < query_size:
                break

        return BackfillResult(
            indexed=indexed,
            skipped=skipped,
            failed=failed,
            batches=batches
        )


# Create a reusable service instance
memory_embedding_service = MemoryEmbeddingService()
