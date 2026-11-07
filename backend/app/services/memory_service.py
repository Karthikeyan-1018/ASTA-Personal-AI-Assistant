import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.memory_exceptions import (
    MemoryNotFoundError,
    MemoryValidationError,
)
from app.models.memory import Memory, MemoryStatus


logger = logging.getLogger(__name__)


MAX_CONTENT_LENGTH = 10000
MAX_CATEGORY_LENGTH = 50
DEFAULT_CATEGORY = "general"


# Conservative patterns for values that look like credentials. Memory
# content matching these patterns is rejected instead of being stored.
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|gsk|pk|rk)[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|passwd|secret|token|"
        r"access[_-]?token|auth[_-]?token)\s*[:=]\s*\S+"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{8,}=*"),
)


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

        memory = Memory(
            user_id=user_id,
            content=_validate_content(content),
            category=_normalise_category(category),
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

        return await self._set_status(
            session=session,
            user_id=user_id,
            memory_id=memory_id,
            status=MemoryStatus.APPROVED
        )

    async def reject_memory(
        self,
        session: AsyncSession,
        user_id: int,
        memory_id: int
    ) -> Memory:

        return await self._set_status(
            session=session,
            user_id=user_id,
            memory_id=memory_id,
            status=MemoryStatus.REJECTED
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
