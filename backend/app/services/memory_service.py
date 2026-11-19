import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_patterns import SECRET_PATTERNS
from app.exceptions.memory_exceptions import (
    MemoryNotFoundError,
    MemoryValidationError,
)
from app.memory.semantic_support import is_semantic_storage_available
from app.models.memory import Memory, MemoryStatus
from app.services.memory_embedding_service import memory_embedding_service


logger = logging.getLogger(__name__)


MAX_CONTENT_LENGTH = 10000
MAX_CATEGORY_LENGTH = 50
DEFAULT_CATEGORY = "general"


def _normalise_memory_text(value: str) -> str:
    """Collapse whitespace and case for duplicate comparisons."""

    return " ".join(str(value or "").split()).casefold()


def _memory_key(content: str) -> str:
    """Return the stable subject of a ``Key: value`` memory.

    Memories without an explicit key are compared only by their full content,
    so an ordinary sentence can never supersede an unrelated memory.
    """

    cleaned = str(content or "").strip()
    separator = cleaned.find(":")

    if separator <= 0:
        return _normalise_memory_text(cleaned)

    return _normalise_memory_text(cleaned[:separator])


# ``SECRET_PATTERNS`` is re-exported for backwards compatibility. Its single
# definition lives in ``app.core.secret_patterns``.


def _validate_content(content: str) -> str:
    cleaned = content.strip()

    if not cleaned:
        raise MemoryValidationError("Memory content cannot be empty.")

    if len(cleaned) > MAX_CONTENT_LENGTH:
        raise MemoryValidationError(
            "Memory content cannot exceed "
            f"{MAX_CONTENT_LENGTH} characters."
        )

    for pattern in SECRET_PATTERNS:
        if pattern.search(cleaned):
            raise MemoryValidationError(
                "Memory content appears to contain a secret "
                "and was not stored."
            )

    return cleaned


def _normalise_category(category: str) -> str:
    cleaned = category.strip()

    if not cleaned:
        cleaned = DEFAULT_CATEGORY

    if len(cleaned) > MAX_CATEGORY_LENGTH:
        raise MemoryValidationError(
            "Memory category cannot exceed "
            f"{MAX_CATEGORY_LENGTH} characters."
        )

    return cleaned


class MemoryService:
    """Asynchronous operations for a user's long-term memories."""

    async def create_memory(
        self,
        session: AsyncSession,
        user_id: int,
        content: str,
        category: str = DEFAULT_CATEGORY
    ) -> Memory:

        cleaned_content = _validate_content(content)
        cleaned_category = _normalise_category(category)
        content_key = _normalise_memory_text(cleaned_content)
        subject_key = _memory_key(cleaned_content)

        result = await session.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.status.in_(
                    [MemoryStatus.PENDING, MemoryStatus.APPROVED]
                )
            ).order_by(Memory.id.asc())
        )
        existing = list(result.scalars().all())

        for memory in existing:
            if _normalise_memory_text(memory.content) == content_key:
                memory.deduplicated = True
                memory.superseded = False
                return memory

        superseded: Memory | None = None

        if len(subject_key) >= 3:
            candidates = [
                memory
                for memory in existing
                if _memory_key(memory.content) == subject_key
                and _normalise_memory_text(memory.content) != content_key
            ]

            if candidates:
                superseded = candidates[-1]

        if superseded is not None:
            superseded.content = cleaned_content
            superseded.category = cleaned_category

            try:
                await session.commit()
                await session.refresh(superseded)

            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to supersede memory %s for user %s.",
                    superseded.id,
                    user_id
                )
                raise

            superseded.deduplicated = False
            superseded.superseded = True

            await self._refresh_embedding_if_enabled(
                session=session,
                memory=superseded,
                force=True
            )

            return superseded

        memory = Memory(
            user_id=user_id,
            content=cleaned_content,
            category=cleaned_category,
            status=MemoryStatus.PENDING
        )

        try:
            session.add(memory)
            await session.commit()
            await session.refresh(memory)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create memory for user %s.",
                user_id
            )
            raise

        memory.deduplicated = False
        memory.superseded = False

        return memory

    async def get_memory_by_id(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int
    ) -> Memory:

        result = await session.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id
            )
        )

        memory = result.scalar_one_or_none()

        if memory is None:
            raise MemoryNotFoundError("Memory not found.")

        return memory

    async def get_user_memories(
        self,
        session: AsyncSession,
        user_id: int,
        status: MemoryStatus | None = None
    ) -> list[Memory]:

        query = select(Memory).where(Memory.user_id == user_id)

        if status is not None:
            query = query.where(Memory.status == status)

        query = query.order_by(
            Memory.created_at.desc(),
            Memory.id.desc()
        )

        result = await session.execute(query)

        return list(result.scalars().all())

    async def get_approved_memories(
        self,
        session: AsyncSession,
        user_id: int
    ) -> list[Memory]:

        return await self.get_user_memories(
            session=session,
            user_id=user_id,
            status=MemoryStatus.APPROVED
        )

    async def update_memory(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int,
        content: str | None = None,
        category: str | None = None
    ) -> Memory:

        if content is None and category is None:
            raise MemoryValidationError(
                "Provide content or category to update."
            )

        memory = await self.get_memory_by_id(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

        if content is not None:
            memory.content = _validate_content(content)

        if category is not None:
            memory.category = _normalise_category(category)

        try:
            await session.commit()
            await session.refresh(memory)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update memory %s for user %s.",
                memory_id,
                user_id
            )
            raise

        if content is not None:
            await self._refresh_embedding_if_enabled(
                session=session,
                memory=memory,
                force=True
            )

        return memory

    async def delete_memory(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int
    ) -> None:

        memory = await self.get_memory_by_id(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

        try:
            await session.delete(memory)
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to delete memory %s for user %s.",
                memory_id,
                user_id
            )
            raise

    async def approve_memory(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int
    ) -> Memory:

        memory = await self._set_status(
            session=session,
            user_id=user_id,
            memory_id=memory_id,
            status=MemoryStatus.APPROVED
        )

        await self._refresh_embedding_if_enabled(
            session=session,
            memory=memory,
            force=True
        )

        return memory

    async def reject_memory(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int
    ) -> Memory:

        memory = await self._set_status(
            session=session,
            user_id=user_id,
            memory_id=memory_id,
            status=MemoryStatus.REJECTED
        )

        try:
            await memory_embedding_service.clear_embedding(
                session=session,
                memory=memory
            )

        except Exception:
            # The status change is already committed. A stale vector cannot
            # be returned because semantic retrieval filters on status.
            logger.warning(
                "Could not clear the embedding for rejected memory %s.",
                memory.id
            )

        return memory

    async def _refresh_embedding_if_enabled(
        self,
        session: AsyncSession,
        memory: Memory,
        force: bool = False
    ) -> None:
        """Refresh semantic index data when pgvector storage is available."""

        if memory.status != MemoryStatus.APPROVED:
            return

        if not await is_semantic_storage_available(session):
            return

        try:
            await memory_embedding_service.ensure_embedding(
                session=session,
                memory=memory,
                force=force
            )

        except Exception:
            # Approval and updates should still succeed when the embedding
            # model is temporarily unavailable. The missing embedding is
            # visible to the backfill command and semantic retrieval falls
            # back to keyword retrieval.
            logger.warning(
                "Memory %s is not currently indexed for semantic retrieval.",
                memory.id
            )

    async def _set_status(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int,
        status: MemoryStatus
    ) -> Memory:

        memory = await self.get_memory_by_id(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

        memory.status = status

        try:
            await session.commit()
            await session.refresh(memory)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to change status of memory %s for user %s.",
                memory_id,
                user_id
            )
            raise

        return memory


# Create a reusable service instance
memory_service = MemoryService()
