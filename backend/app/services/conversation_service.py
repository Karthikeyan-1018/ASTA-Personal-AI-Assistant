"""Persistence operations for conversations and their messages."""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.conversation_exceptions import (
    ConversationNotFoundError,
    ConversationValidationError,
)
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole


logger = logging.getLogger(__name__)


DEFAULT_TITLE = "New conversation"
MAX_TITLE_LENGTH = 255
MAX_DERIVED_TITLE_CHARS = 60
MAX_PAGE_SIZE = 100
MAX_HISTORY_LIMIT = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_title(title: str | None, fallback: str = DEFAULT_TITLE) -> str:
    cleaned = (title or "").strip()

    if not cleaned:
        cleaned = fallback

    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ConversationValidationError(
            "Conversation title cannot exceed "
            f"{MAX_TITLE_LENGTH} characters."
        )

    return cleaned


def derive_title(content: str) -> str:
    """Derive a short title from the first user message."""

    cleaned = " ".join((content or "").split())

    if not cleaned:
        return DEFAULT_TITLE

    if len(cleaned) <= MAX_DERIVED_TITLE_CHARS:
        return cleaned

    return cleaned[:MAX_DERIVED_TITLE_CHARS].rstrip() + "..."


class ConversationService:
    """Asynchronous, user-scoped conversation persistence."""

    async def create_conversation(
        self,
        session: AsyncSession,
        user_id: int,
        title: str | None = None
    ) -> Conversation:

        conversation = Conversation(
            user_id=user_id,
            title=_clean_title(title)
        )

        try:
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to create a conversation for user %s.",
                user_id
            )
            raise

        return conversation

    async def list_conversations(
        self,
        session: AsyncSession,
        user_id: int
    ) -> list[Conversation]:

        result = await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(
                Conversation.updated_at.desc(),
                Conversation.id.desc()
            )
        )

        return list(result.scalars().all())

    async def get_conversation(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int
    ) -> Conversation:

        result = await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id
            )
        )

        conversation = result.scalar_one_or_none()

        if conversation is None:
            raise ConversationNotFoundError("Conversation not found.")

        return conversation

    async def update_title(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int,
        title: str
    ) -> Conversation:

        conversation = await self.get_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id
        )

        conversation.title = _clean_title(title)
        conversation.updated_at = _utcnow()

        try:
            await session.commit()
            await session.refresh(conversation)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update conversation %s for user %s.",
                conversation_id,
                user_id
            )
            raise

        return conversation

    async def delete_conversation(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int
    ) -> None:

        conversation = await self.get_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id
        )

        try:
            await session.delete(conversation)
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to delete conversation %s for user %s.",
                conversation_id,
                user_id
            )
            raise

    async def add_message(
        self,
        session: AsyncSession,
        conversation: Conversation,
        role: MessageRole | str,
        content: str,
        provider: str | None = None,
        is_private: bool = False
    ) -> Message:

        cleaned = (content or "").strip()

        if not cleaned:
            raise ConversationValidationError(
                "Message content cannot be empty."
            )

        message = Message(
            conversation_id=conversation.id,
            role=MessageRole(role),
            content=cleaned,
            provider=provider,
            is_private=is_private
        )

        conversation.updated_at = _utcnow()

        try:
            session.add(message)
            await session.commit()
            await session.refresh(message)
            await session.refresh(conversation)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to persist a message in conversation %s.",
                conversation.id
            )
            raise

        return message

    async def get_messages(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int,
        limit: int = 50,
        offset: int = 0
    ) -> tuple[list[Message], int]:

        await self.get_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id
        )

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        total = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )

        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all()), int(total or 0)

    async def get_recent_messages(
        self,
        session: AsyncSession,
        user_id: int,
        conversation_id: int,
        limit: int | None = None
    ) -> list[Message]:
        """Return the most recent messages in chronological order."""

        await self.get_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id
        )

        effective_limit = (
            settings.CONVERSATION_HISTORY_LIMIT
            if limit is None
            else limit
        )
        effective_limit = max(1, min(effective_limit, MAX_HISTORY_LIMIT))

        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(effective_limit)
        )

        return list(reversed(result.scalars().all()))


conversation_service = ConversationService()
