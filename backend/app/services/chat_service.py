import asyncio
import logging
import time
from collections.abc import AsyncIterator

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
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.llm_router import llm_router
from app.llm.streaming import close_async_stream
from app.memory.context_builder import build_chat_prompt
from app.memory.memory_retriever import memory_retriever
from app.models.message import MessageRole
from app.services.conversation_service import (
    conversation_service,
    derive_title,
)
from app.services.response_cache_service import response_cache_service
from app.services.tool_orchestrator import tool_orchestrator
from app.services.usage_tracking_service import usage_tracking_service
from app.task_intent.chat_handler import task_intent_chat_handler


logger = logging.getLogger(__name__)


CHAT_MEMORY_LIMIT = 5


class _StreamTooLong(Exception):
    """Raised internally when a streamed answer exceeds its configured bound."""


def _stream_event(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


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

        # Tool-enabled chat is never served from the cache and its responses
        # are never stored. Tool results depend on live state and cannot be
        # invalidated safely with the current cache design.
        if settings.TOOL_CALLING_ENABLED:
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
            tool_result = None

            if (
                settings.TOOL_CALLING_ENABLED
                and not private
                and user_id is not None
                and session is not None
                and conversation is not None
            ):
                tool_result = await tool_orchestrator.run(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    private=False,
                    prompt=contextual_prompt,
                    complexity=classification.complexity,
                )

            if tool_result is None:
                result = await llm_router.generate_response(
                    prompt=contextual_prompt,
                    private=private,
                    complexity=classification.complexity,
                    request_type="chat",
                )
            else:
                result = tool_result

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

            # Persist the safe tool transcript first so message ordering is
            # stable: the tool call/result entries appear before the assistant
            # answer that summarises them. The transcript is metadata-only and
            # is excluded from future prompt history.
            await conversation_service.add_tool_events(
                session=session,
                conversation=conversation,
                events=result.get("tool_events"),
                provider=result.get("provider"),
                model=result.get("model"),
                is_private=private,
            )

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

        if (
            cache_eligible
            and not result.get("tool_calling")
            and result.get("response")
        ):
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

    async def _record_stream_usage(
        self,
        *,
        session: AsyncSession | None,
        user_id: int | None,
        route,
        classification: ClassificationResult,
        started: float,
        success: bool,
        provider: str | None = None,
        model: str | None = None,
        usage: dict | None = None,
        fallback_used: bool = False,
        error_type: str | None = None,
    ) -> None:
        """Record metadata-only streaming usage; never break the stream."""

        if session is None:
            return

        try:
            await usage_tracking_service.record(
                session=session,
                user_id=user_id,
                request_type="chat",
                provider=provider or route.primary,
                model=model or route.primary_model,
                complexity=classification.complexity.value,
                routing_policy=route.policy,
                usage=usage,
                duration_ms=round(
                    max(0.0, (time.perf_counter() - started) * 1000.0),
                    2,
                ),
                fallback_used=fallback_used,
                cache_hit=False,
                success=success,
                error_type=error_type,
            )

        except Exception as error:
            # Analytics is best effort and must never break a live stream.
            logger.warning(
                "Failed to record streaming usage metadata (%s).",
                type(error).__name__,
            )

    async def stream_response(
        self,
        prompt: str,
        private: bool = False,
        user_id: int | None = None,
        session: AsyncSession | None = None,
        memory_limit: int = CHAT_MEMORY_LIMIT,
        conversation_id: int | None = None,
    ) -> AsyncIterator[dict]:
        """Stream one chat turn as a sequence of structured events.

        Event shapes are intentionally small and stable:

        * ``start``: ``conversation_id``, selected ``provider``/``model``;
        * ``delta``: one ``text`` fragment;
        * ``done``: ``conversation_id``, actual provider/model, finish reason,
          duration, usage counts, and fallback flag;
        * ``error``: safe ``code``/``message`` and ``conversation_id``.

        The user message is persisted before the provider is called. The
        assistant message is persisted exactly once, only after a normal,
        non-empty completion. Cancelled, failed, empty, or oversized responses
        are never stored as successful answers.
        """

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
                conversation_id=conversation_id,
            )

            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content=cleaned_prompt,
                is_private=private,
            )

        elif conversation_id is not None:
            raise ConversationNotFoundError("Conversation not found.")

        conversation_identifier = (
            conversation.id if conversation is not None else None
        )

        # Deterministic task-intent replies never call an LLM. They are
        # persisted once here and emitted as a single delta.
        if conversation is not None:

            try:
                task_result = await task_intent_chat_handler.handle(
                    session=session,
                    user_id=user_id,
                    conversation=conversation,
                    message=cleaned_prompt,
                    private=private,
                )

            except Exception as error:
                logger.error(
                    "Task-intent handling failed during streaming (%s); "
                    "continuing with normal chat.",
                    type(error).__name__,
                )
                task_result = None

            if task_result is not None:

                response_text = (
                    task_result.get("response") or ""
                ).strip()

                if not response_text:
                    yield _stream_event(
                        "error",
                        {
                            "code": "empty_response",
                            "message": (
                                "The assistant returned an empty response."
                            ),
                            "conversation_id": conversation_identifier,
                        },
                    )
                    return

                try:
                    await conversation_service.add_message(
                        session=session,
                        conversation=conversation,
                        role=MessageRole.ASSISTANT,
                        content=response_text,
                        provider=task_result.get("provider"),
                        model=task_result.get("model"),
                        is_private=private,
                    )

                except Exception as error:
                    logger.error(
                        "Failed to persist the streamed task reply for "
                        "conversation %s (%s).",
                        conversation.id,
                        type(error).__name__,
                    )

                provider = task_result.get("provider")
                model = task_result.get("model")

                yield _stream_event(
                    "start",
                    {
                        "conversation_id": conversation_identifier,
                        "provider": provider,
                        "model": model,
                    },
                )
                yield _stream_event("delta", {"text": response_text})
                yield _stream_event(
                    "done",
                    {
                        "conversation_id": conversation_identifier,
                        "provider": provider,
                        "model": model,
                        "finish_reason": "stop",
                        "duration_ms": task_result.get("duration_ms"),
                        "usage": task_result.get("usage"),
                        "fallback_used": bool(
                            task_result.get("fallback_used")
                        ),
                    },
                )
                return

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
            contextual_prompt = build_chat_prompt(
                user_message=cleaned_prompt,
                memories=[],
            )

        started = time.perf_counter()

        # Tool-enabled chat reuses the existing bounded orchestrator. Its
        # final answer is emitted as a single delta because tool turns are not
        # token streams. Tool permissions, confirmations, and persistence
        # semantics are unchanged.
        if (
            settings.TOOL_CALLING_ENABLED
            and not private
            and user_id is not None
            and session is not None
            and conversation is not None
        ):

            try:
                tool_result = await tool_orchestrator.run(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    private=False,
                    prompt=contextual_prompt,
                    complexity=classification.complexity,
                )

            except asyncio.CancelledError:
                raise

            except Exception as error:
                await self._record_stream_usage(
                    session=session,
                    user_id=user_id,
                    route=route,
                    classification=classification,
                    started=started,
                    success=False,
                    error_type=type(error).__name__,
                )
                yield _stream_event(
                    "error",
                    {
                        "code": "internal_error",
                        "message": (
                            "An unexpected error occurred while processing "
                            "your request."
                        ),
                        "conversation_id": conversation_identifier,
                    },
                )
                return

            if tool_result is not None:

                response_text = (
                    tool_result.get("response") or ""
                ).strip()

                if not response_text:
                    await self._record_stream_usage(
                        session=session,
                        user_id=user_id,
                        route=route,
                        classification=classification,
                        started=started,
                        success=False,
                        error_type="EmptyResponse",
                    )
                    yield _stream_event(
                        "error",
                        {
                            "code": "empty_response",
                            "message": (
                                "The assistant returned an empty response."
                            ),
                            "conversation_id": conversation_identifier,
                        },
                    )
                    return

                await conversation_service.add_tool_events(
                    session=session,
                    conversation=conversation,
                    events=tool_result.get("tool_events"),
                    provider=tool_result.get("provider"),
                    model=tool_result.get("model"),
                    is_private=private,
                )

                try:
                    await conversation_service.add_message(
                        session=session,
                        conversation=conversation,
                        role=MessageRole.ASSISTANT,
                        content=response_text,
                        provider=tool_result.get("provider"),
                        model=tool_result.get("model"),
                        is_private=private,
                    )

                except Exception as error:
                    logger.error(
                        "Failed to persist the streamed tool answer for "
                        "conversation %s (%s).",
                        conversation.id,
                        type(error).__name__,
                    )

                provider = tool_result.get("provider")
                model = tool_result.get("model")

                yield _stream_event(
                    "start",
                    {
                        "conversation_id": conversation_identifier,
                        "provider": provider,
                        "model": model,
                    },
                )
                yield _stream_event("delta", {"text": response_text})
                yield _stream_event(
                    "done",
                    {
                        "conversation_id": conversation_identifier,
                        "provider": provider,
                        "model": model,
                        "finish_reason": "stop",
                        "duration_ms": tool_result.get("duration_ms"),
                        "usage": tool_result.get("usage"),
                        "fallback_used": bool(
                            tool_result.get("fallback_used")
                        ),
                    },
                )

                await self._record_stream_usage(
                    session=session,
                    user_id=user_id,
                    route=route,
                    classification=classification,
                    started=started,
                    success=True,
                    provider=provider,
                    model=model,
                    usage=tool_result.get("usage"),
                    fallback_used=bool(tool_result.get("fallback_used")),
                )
                return

        # Normal provider streaming path.
        yield _stream_event(
            "start",
            {
                "conversation_id": conversation_identifier,
                "provider": route.primary,
                "model": route.primary_model,
            },
        )

        metadata: dict = {}

        stream = llm_router.stream_response(
            prompt=contextual_prompt,
            private=private,
            complexity=classification.complexity,
            request_type="chat",
            metadata=metadata,
        )

        parts: list[str] = []
        total_chars = 0
        limit = max(0, int(settings.STREAM_MAX_RESPONSE_CHARS or 0))

        try:

            async for chunk in stream:

                if not chunk:
                    continue

                total_chars += len(chunk)

                if total_chars > limit:
                    raise _StreamTooLong()

                parts.append(chunk)
                yield _stream_event("delta", {"text": chunk})

        except asyncio.CancelledError:
            # Client disconnect or caller cancellation. The user message is
            # intentionally retained; no partial assistant answer is stored.
            raise

        except _StreamTooLong:

            await self._record_stream_usage(
                session=session,
                user_id=user_id,
                route=route,
                classification=classification,
                started=started,
                success=False,
                error_type="ResponseTooLong",
            )

            yield _stream_event(
                "error",
                {
                    "code": "response_too_large",
                    "message": (
                        "The response exceeded the maximum allowed size."
                    ),
                    "conversation_id": conversation_identifier,
                },
            )
            return

        except LLMTimeoutError:

            await self._record_stream_usage(
                session=session,
                user_id=user_id,
                route=route,
                classification=classification,
                started=started,
                success=False,
                error_type="LLMTimeoutError",
            )

            yield _stream_event(
                "error",
                {
                    "code": "timeout",
                    "message": (
                        "The AI provider timed out while streaming the "
                        "response."
                    ),
                    "conversation_id": conversation_identifier,
                },
            )
            return

        except LLMProviderError:

            await self._record_stream_usage(
                session=session,
                user_id=user_id,
                route=route,
                classification=classification,
                started=started,
                success=False,
                error_type="LLMProviderError",
            )

            yield _stream_event(
                "error",
                {
                    "code": "provider_error",
                    "message": (
                        "The AI provider failed while streaming the response."
                    ),
                    "conversation_id": conversation_identifier,
                },
            )
            return

        except LLMUnavailableError:

            await self._record_stream_usage(
                session=session,
                user_id=user_id,
                route=route,
                classification=classification,
                started=started,
                success=False,
                error_type="LLMUnavailableError",
            )

            yield _stream_event(
                "error",
                {
                    "code": "unavailable",
                    "message": (
                        "The AI service is currently unavailable. Please try "
                        "again later."
                    ),
                    "conversation_id": conversation_identifier,
                },
            )
            return

        except Exception as error:

            logger.error(
                "Unexpected error while streaming a chat response (%s).",
                type(error).__name__,
            )

            await self._record_stream_usage(
                session=session,
                user_id=user_id,
                route=route,
                classification=classification,
                started=started,
                success=False,
                error_type="UnexpectedError",
            )

            yield _stream_event(
                "error",
                {
                    "code": "internal_error",
                    "message": (
                        "An unexpected error occurred while streaming the "
                        "response."
                    ),
                    "conversation_id": conversation_identifier,
                },
            )
            return

        finally:
            await close_async_stream(stream)

        response_text = "".join(parts).strip()

        if not response_text:

            await self._record_stream_usage(
                session=session,
                user_id=user_id,
                route=route,
                classification=classification,
                started=started,
                success=False,
                error_type="EmptyResponse",
            )

            yield _stream_event(
                "error",
                {
                    "code": "empty_response",
                    "message": "The assistant returned an empty response.",
                    "conversation_id": conversation_identifier,
                },
            )
            return

        provider = metadata.get("provider") or route.primary
        model = metadata.get("model") or route.primary_model

        # Persist the assistant answer exactly once, only after a normal
        # completion. A failure to persist must not discard the answer that
        # was already streamed to the client.
        if conversation is not None:

            try:
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=MessageRole.ASSISTANT,
                    content=response_text,
                    provider=provider,
                    model=model,
                    is_private=private,
                )

            except Exception as error:
                logger.error(
                    "Failed to persist the streamed assistant message for "
                    "conversation %s (%s).",
                    conversation.id,
                    type(error).__name__,
                )

        duration_ms = metadata.get("duration_ms")

        if duration_ms is None:
            duration_ms = round(
                max(0.0, (time.perf_counter() - started) * 1000.0),
                2,
            )

        await self._record_stream_usage(
            session=session,
            user_id=user_id,
            route=route,
            classification=classification,
            started=started,
            success=True,
            provider=provider,
            model=model,
            usage=metadata.get("usage"),
            fallback_used=bool(metadata.get("fallback_used")),
        )

        yield _stream_event(
            "done",
            {
                "conversation_id": conversation_identifier,
                "provider": provider,
                "model": model,
                "finish_reason": "stop",
                "duration_ms": duration_ms,
                "usage": metadata.get("usage"),
                "fallback_used": bool(metadata.get("fallback_used")),
            },
        )


# Create a reusable service instance
chat_service = ChatService()
