import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.query_classifier import (
    ClassificationResult,
    QueryComplexity,
    classify_query,
)
from app.ai.routing_policy import select_route
from app.context.builder import unified_context_builder
from app.core.config import settings
from app.core.secret_patterns import contains_secret
from app.exceptions.conversation_exceptions import ConversationNotFoundError
from app.llm.llm_router import llm_router
from app.memory.context_builder import build_chat_prompt
from app.memory.memory_retriever import memory_retriever
from app.models.message import MessageRole
from app.services.conversation_service import (
    conversation_service,
    derive_title,
)
from app.services.response_cache_service import response_cache_service
from app.services.usage_tracking_service import usage_tracking_service
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

    async def _classify(
        self,
        prompt: str,
        private: bool
    ) -> ClassificationResult:
        """Classify a request without ever blocking chat."""

        try:
            return await classify_query(prompt, private=private)

        except Exception:
            # Classification is an optimisation hint only. A failure must
            # never affect routing safety or chat availability.
            logger.warning(
                "Query classification failed; using a safe default.",
                exc_info=True,
            )

            return ClassificationResult(
                complexity=(
                    QueryComplexity.PRIVATE
                    if private
                    else QueryComplexity.MODERATE
                ),
                confidence=0.5,
                reasons=("classification fallback",),
            )

    async def _retrieve_memories(
        self,
        session: AsyncSession,
        user_id: int,
        prompt: str,
        limit: int,
    ) -> list:
        """Retrieve approved memories with graceful degradation."""

        try:
            if settings.AI_MEMORY_HYBRID_ENABLED:
                return await memory_retriever.retrieve_hybrid(
                    session=session,
                    user_id=user_id,
                    query=prompt,
                    limit=limit,
                    similarity_threshold=(
                        settings.SEMANTIC_SIMILARITY_THRESHOLD
                    ),
                )

            if settings.SEMANTIC_RETRIEVAL_ENABLED:
                return await memory_retriever.retrieve(
                    session=session,
                    user_id=user_id,
                    query=prompt,
                    limit=limit,
                    mode="semantic",
                    similarity_threshold=(
                        settings.SEMANTIC_SIMILARITY_THRESHOLD
                    ),
                    allow_keyword_fallback=True
                )

            return await memory_retriever.retrieve(
                session=session,
                user_id=user_id,
                query=prompt,
                limit=limit
            )

        except Exception:
            # Chat should remain available if memory retrieval fails.
            # Do not log the prompt or any retrieved memory content.
            logger.exception(
                "Memory retrieval failed; continuing without "
                "memory context."
            )
            return []

    @staticmethod
    def _cache_eligible(
        *,
        private: bool,
        classification: ClassificationResult,
        history: list,
        inspection,
        prompt: str,
    ) -> bool:
        """Return whether this request may use the semantic response cache.

        The cache is user-scoped and context-free only. Any rendered context,
        prior history, private request, task action, dynamic/time-sensitive
        request, or credential-like prompt is excluded.
        """

        if private or not settings.AI_CACHE_ENABLED:
            return False

        if not classification.cache_eligible:
            return False

        allowed = settings.ai_cache_allowed_complexities
        complexity = classification.complexity.value

        if allowed and complexity not in allowed:
            return False

        if contains_secret(prompt):
            return False

        if settings.AI_CACHE_STATELESS_ONLY:
            if history:
                return False

            if inspection is not None and inspection.rendered_sources:
                return False

        return True

    @staticmethod
    def _cached_result(cached, route, classification) -> dict:
        return {
            "provider": cached.provider or route.primary,
            "model": cached.model or route.primary_model,
            "response": cached.response,
            "routing_policy": cached.routing_policy or route.policy,
            "complexity": (
                cached.complexity or classification.complexity.value
            ),
            "cache_hit": True,
            "fallback_used": False,
            "duration_ms": 0.0,
            "usage": None,
            "request_type": "chat",
        }

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
                        model=task_result.get("model"),
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

        # Classification and routing are decided before context assembly so
        # the correct provider/model token budget can be applied. Private
        # requests are resolved to Ollama before any policy is read.
        classification = await self._classify(cleaned_prompt, private)
        route = select_route(
            private=private,
            complexity=classification.complexity,
        )

        memories = []

        if user_id is not None and session is not None:
            memories = await self._retrieve_memories(
                session=session,
                user_id=user_id,
                prompt=cleaned_prompt,
                limit=memory_limit,
            )

        inspection = None

        if user_id is not None and session is not None:
            inspection = await unified_context_builder.inspect(
                session=session,
                user_id=user_id,
                user_message=cleaned_prompt,
                private=private,
                history=history,
                memories=memories,
                provider=route.primary,
                model=route.primary_model,
            )
            contextual_prompt = inspection.prompt
        else:
            # No trusted identity: keep unauthenticated chat context-free.
            contextual_prompt = build_chat_prompt(
                user_message=cleaned_prompt,
                memories=[]
            )

        cache_eligible = (
            user_id is not None
            and session is not None
            and self._cache_eligible(
                private=private,
                classification=classification,
                history=history,
                inspection=inspection,
                prompt=cleaned_prompt,
            )
        )

        cached = None

        if cache_eligible:
            cached = await response_cache_service.lookup(
                session=session,
                user_id=user_id,
                query=cleaned_prompt,
            )

        if cached is not None:
            result = self._cached_result(cached, route, classification)
            started = time.perf_counter()

            if conversation is not None:

                try:
                    await conversation_service.add_message(
                        session=session,
                        conversation=conversation,
                        role=MessageRole.ASSISTANT,
                        content=result["response"],
                        provider=result.get("provider"),
                        model=result.get("model"),
                        is_private=private
                    )

                except Exception:
                    logger.exception(
                        "Failed to persist the cached assistant message for "
                        "conversation %s.",
                        conversation.id
                    )

                result = dict(result)
                result["conversation_id"] = conversation.id

            await usage_tracking_service.record(
                session=session,
                user_id=user_id,
                request_type="chat",
                provider=result.get("provider"),
                model=result.get("model"),
                complexity=classification.complexity.value,
                routing_policy=route.policy,
                usage=None,
                duration_ms=round(
                    max(0.0, (time.perf_counter() - started) * 1000.0),
                    2,
                ),
                fallback_used=False,
                cache_hit=True,
                success=True,
            )

            return result

        started = time.perf_counter()

        try:
            result = await llm_router.generate_response(
                prompt=contextual_prompt,
                private=private,
                complexity=classification.complexity,
                request_type="chat",
            )

        except Exception as error:
            await usage_tracking_service.record(
                session=session,
                user_id=user_id,
                request_type="chat",
                provider=route.primary,
                model=route.primary_model,
                complexity=classification.complexity.value,
                routing_policy=route.policy,
                usage=None,
                duration_ms=round(
                    max(0.0, (time.perf_counter() - started) * 1000.0),
                    2,
                ),
                fallback_used=False,
                cache_hit=False,
                success=False,
                error_type=type(error).__name__,
            )
            raise

        duration_ms = result.get("duration_ms")

        if conversation is not None:

            try:
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=MessageRole.ASSISTANT,
                    content=result.get("response") or "",
                    provider=result.get("provider"),
                    model=result.get("model"),
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

        if cache_eligible and result.get("response"):
            await response_cache_service.store(
                session=session,
                user_id=user_id,
                query=cleaned_prompt,
                response=result["response"],
                provider=result.get("provider"),
                model=result.get("model"),
                complexity=classification.complexity.value,
                routing_policy=route.policy,
            )

        if session is not None:
            await usage_tracking_service.record(
                session=session,
                user_id=user_id,
                request_type="chat",
                provider=result.get("provider"),
                model=result.get("model"),
                complexity=result.get(
                    "complexity",
                    classification.complexity.value,
                ),
                routing_policy=result.get("routing_policy", route.policy),
                usage=result.get("usage"),
                duration_ms=duration_ms,
                fallback_used=bool(result.get("fallback_used")),
                cache_hit=False,
                success=True,
            )

        return result


# Create a reusable service instance
chat_service = ChatService()
