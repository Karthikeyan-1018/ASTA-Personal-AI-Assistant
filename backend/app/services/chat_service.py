import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.context.builder import unified_context_builder
from app.core.config import settings
from app.exceptions.conversation_exceptions import ConversationNotFoundError
from app.llm.llm_router import llm_router
from app.memory.context_builder import build_chat_prompt
from app.memory.memory_retriever import memory_retriever
from app.models.message import MessageRole
from app.services.conversation_service import (
    conversation_service,
    derive_title,
)
from app.task_intent.chat_handler import task_intent_chat_handler


logger = logging.getLogger(__name__)


CHAT_MEMORY_LIMIT = 5


class ChatService:

    async def _prepare_conversation(
        self,
        session: AsyncSession,
        user_id: int,
        prompt: str,
        private: bool,
        conversation_id: int | None
    ):
        """Resolve/create the conversation and load prior messages."""

        if conversation_id is not None:
            conversation = await conversation_service.get_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id
            )
        else:
            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=user_id,
                title=derive_title(prompt)
            )

        # Load history *before* saving the current user message so the
        # current turn is not duplicated in the prompt.
        history = await conversation_service.get_recent_messages(
            session=session,
            user_id=user_id,
            conversation_id=conversation.id
        )

        # A private message must never leak into a later cloud prompt.
        if not private:
            history = [
                message for message in history if not message.is_private
            ]

        return conversation, history

    async def get_response(
        self,
        prompt: str,
        private: bool = False,
        user_id: int | None = None,
        session: AsyncSession | None = None,
        memory_limit: int = CHAT_MEMORY_LIMIT,
        conversation_id: int | None = None
    ) -> dict:

        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        cleaned_prompt = prompt.strip()

        conversation = None
        history = []

        if user_id is not None and session is not None:

            conversation, history = await self._prepare_conversation(
                session=session,
                user_id=user_id,
                prompt=cleaned_prompt,
                private=private,
                conversation_id=conversation_id
            )

            # Persist the user's message before calling the LLM. If the
            # provider fails this message is intentionally preserved.
            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content=cleaned_prompt,
                is_private=private
            )

        elif conversation_id is not None:
            # Without a trusted identity ownership cannot be validated.
            raise ConversationNotFoundError("Conversation not found.")

        # Task-intent handling runs only after the user message is persisted
        # and before memory/context construction. Deterministic task replies
        # never call an LLM. A failure here must not break normal chat.
        if conversation is not None:

            try:
                task_result = await task_intent_chat_handler.handle(
                    session=session,
                    user_id=user_id,
                    conversation=conversation,
                    message=cleaned_prompt,
                    private=private,
                )

            except Exception:
                logger.exception(
                    "Task-intent handling failed; continuing with normal "
                    "chat."
                )
                task_result = None

            if task_result is not None:

                try:
                    await conversation_service.add_message(
                        session=session,
                        conversation=conversation,
                        role=MessageRole.ASSISTANT,
                        content=task_result.get("response") or "",
                        provider=task_result.get("provider"),
                        is_private=private,
                    )

                except Exception:
                    logger.exception(
                        "Failed to persist the task reply for "
                        "conversation %s.",
                        conversation.id,
                    )

                result = dict(task_result)
                result["conversation_id"] = conversation.id

                return result

        memories = []

        if user_id is not None and session is not None:

            try:
                if settings.SEMANTIC_RETRIEVAL_ENABLED:
                    memories = await memory_retriever.retrieve(
                        session=session,
                        user_id=user_id,
                        query=cleaned_prompt,
                        limit=memory_limit,
                        mode="semantic",
                        similarity_threshold=(
                            settings.SEMANTIC_SIMILARITY_THRESHOLD
                        ),
                        allow_keyword_fallback=True
                    )
                else:
                    memories = await memory_retriever.retrieve(
                        session=session,
                        user_id=user_id,
                        query=cleaned_prompt,
                        limit=memory_limit
                    )

            except Exception:
                # Chat should remain available if memory retrieval fails.
                # Do not log the prompt or any retrieved memory content.
                logger.exception(
                    "Memory retrieval failed; continuing without "
                    "memory context."
                )
                memories = []

        if user_id is not None and session is not None:
            contextual_prompt = await unified_context_builder.build(
                session=session,
                user_id=user_id,
                user_message=cleaned_prompt,
                private=private,
                history=history,
                memories=memories
            )
        else:
            # No trusted identity: keep unauthenticated chat context-free.
            contextual_prompt = build_chat_prompt(
                user_message=cleaned_prompt,
                memories=[]
            )

        result = await llm_router.generate_response(
            prompt=contextual_prompt,
            private=private
        )

        if conversation is not None:

            try:
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=MessageRole.ASSISTANT,
                    content=result.get("response") or "",
                    provider=result.get("provider"),
                    is_private=private
                )

            except Exception:
                # The answer was generated successfully. Do not discard it
                # just because persistence of the assistant message failed.
                logger.exception(
                    "Failed to persist the assistant message for "
                    "conversation %s.",
                    conversation.id
                )

            result = dict(result)
            result["conversation_id"] = conversation.id

        return result


# Create a reusable service instance
chat_service = ChatService()
