import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.llm.llm_router import llm_router
from app.memory.context_builder import build_chat_prompt
from app.memory.memory_retriever import memory_retriever


logger = logging.getLogger(__name__)


CHAT_MEMORY_LIMIT = 5


class ChatService:

    async def get_response(
        self,
        prompt: str,
        private: bool = False,
        user_id: int | None = None,
        session: AsyncSession | None = None,
        memory_limit: int = CHAT_MEMORY_LIMIT
    ) -> dict:

        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        cleaned_prompt = prompt.strip()

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

        contextual_prompt = build_chat_prompt(
            user_message=cleaned_prompt,
            memories=memories
        )

        result = await llm_router.generate_response(
            prompt=contextual_prompt,
            private=private
        )

        return result


# Create a reusable service instance
chat_service = ChatService()
