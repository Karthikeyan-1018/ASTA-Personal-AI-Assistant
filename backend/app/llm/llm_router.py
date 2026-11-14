"""Provider selection and failover for ASTA.

Phase 9 adds complexity-aware routing on top of the historical Groq-first
behaviour. The public contract is unchanged: ``generate_response`` accepts a
prompt and a ``private`` flag and returns a provider/model/response mapping.
Additional metadata keys (routing policy, fallback flag, duration, usage) are
available to internal callers and are ignored by the chat API response model.

Private requests are always resolved to Ollama only. Their execution path
never consults a cloud provider, not even for a health check.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from app.ai.routing_policy import select_route
from app.ai.tokenization import safe_estimate_tokens
from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider


# Configure logging
logger = logging.getLogger(__name__)


_PROVIDERS = {
    "groq": groq_provider,
    "ollama": ollama_provider,
}


def _is_blank_answer(value) -> bool:
    """Return whether a provider answer is unusable.

    Providers can return ``None``, an empty string, or whitespace instead of an
    answer. Treating that as success would persist or return an empty assistant
    message, so it is handled as a provider failure.
    """

    return not isinstance(value, str) or not value.strip()


def _provider(name: str):
    provider = _PROVIDERS.get(name)

    if provider is None:
        raise LLMUnavailableError(
            "No configured AI provider can handle this request."
        )

    return provider


def _usage_metadata(provider, prompt: str, response: str) -> dict | None:
    """Return token-count metadata without ever copying prompt content."""

    reported = getattr(provider, "last_usage", None)

    if isinstance(reported, dict) and reported:
        usage = dict(reported)
        usage["estimated"] = False
        return usage

    input_tokens = safe_estimate_tokens(prompt)
    output_tokens = safe_estimate_tokens(response)

    if input_tokens is None or output_tokens is None:
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "source": "estimated",
        "estimated": True,
    }


def _success_result(
    *,
    decision,
    provider_name: str,
    model: str | None,
    response: str,
    fallback_used: bool,
    started: float,
    prompt: str,
    request_type: str | None,
) -> dict:
    provider = _provider(provider_name)

    return {
        "provider": provider_name,
        "model": model,
        "response": response,
        "routing_policy": decision.policy,
        "complexity": decision.complexity,
        "provider_order": list(decision.provider_order),
        "fallback_used": fallback_used,
        "duration_ms": round(
            max(0.0, (time.perf_counter() - started) * 1000.0),
            2,
        ),
        "usage": _usage_metadata(provider, prompt, response),
        "request_type": request_type,
    }


def _success_tool_result(
    *,
    decision,
    provider_name: str,
    model: str | None,
    turn,
    fallback_used: bool,
    started: float,
    prompt: str,
    request_type: str | None,
) -> dict:
    return {
        "provider": provider_name,
        "model": model,
        "response": turn.content,
        "tool_calls": list(turn.tool_calls),
        "tool_call_schemas": turn.tool_call_schemas,
        "routing_policy": decision.policy,
        "complexity": decision.complexity,
        "provider_order": list(decision.provider_order),
        "fallback_used": fallback_used,
        "duration_ms": round(
            max(0.0, (time.perf_counter() - started) * 1000.0),
            2,
        ),
        "usage": turn.usage or _usage_metadata(
            _provider(provider_name),
            prompt,
            turn.content,
        ),
        "request_type": request_type,
    }


class LLMRouter:

    async def check_providers(self) -> dict:
        """
        Check the availability of both AI providers.
        """

        groq_status = await groq_provider.health_check()
        ollama_status = await ollama_provider.health_check()

        return {
            "groq": groq_status,
            "ollama": ollama_status
        }

    async def _invoke(self, provider, prompt: str, model: str | None) -> str:
        """Call a provider and reset its per-call usage metadata first."""

        try:
            provider.last_usage = None
        except Exception:
            # Provider doubles used in tests may be read-only; usage capture
            # is optional and must never break a request.
            pass

        return await provider.generate_response(prompt, model=model)

    async def _generate_private(
        self,
        prompt: str,
        decision,
        *,
        started: float,
        request_type: str | None,
    ) -> dict:
        """Execute a private request against Ollama only."""

        logger.info("Routing private request to Ollama only.")

        if not await ollama_provider.health_check():

            logger.error("Ollama is unavailable for a private request.")

            raise LLMUnavailableError(
                "Local AI provider is unavailable. "
                "Private request was not sent to the cloud."
            )

        try:

            response = await self._invoke(
                ollama_provider,
                prompt,
                decision.primary_model,
            )

        except LLMTimeoutError:

            logger.error(
                "Ollama timed out while handling a private request.",
                exc_info=True
            )

            # Preserve the timeout so the API can return HTTP 504.
            raise

        except LLMProviderError as error:

            logger.error(
                "Ollama failed while handling a private request.",
                exc_info=True
            )

            raise LLMUnavailableError(
                "Local AI provider failed. "
                "Private request was not sent to the cloud."
            ) from error

        except Exception as error:

            logger.exception(
                "Unexpected Ollama failure while handling a private request."
            )

            raise LLMUnavailableError(
                "Local AI provider failed. "
                "Private request was not sent to the cloud."
            ) from error

        if _is_blank_answer(response):

            logger.error(
                "Ollama returned an empty response for a private request."
            )

            raise LLMUnavailableError(
                "Local AI provider returned an empty response. "
                "Private request was not sent to the cloud."
            )

        return _success_result(
            decision=decision,
            provider_name="ollama",
            model=decision.primary_model,
            response=response,
            fallback_used=False,
            started=started,
            prompt=prompt,
            request_type=request_type,
        )

    async def generate_response(
        self,
        prompt: str,
        private: bool = False,
        complexity=None,
        policy: str | None = None,
        request_type: str | None = None,
    ) -> dict:

        started = time.perf_counter()

        # Private routing is decided before any policy is read. Groq is never
        # contacted for a private prompt.
        decision = select_route(
            private=private,
            complexity=complexity,
            policy=policy,
        )

        if private:
            return await self._generate_private(
                prompt,
                decision,
                started=started,
                request_type=request_type,
            )

        attempted_provider = False

        for index, provider_name in enumerate(decision.provider_order):

            provider = _provider(provider_name)
            is_last = index == len(decision.provider_order) - 1
            fallback_used = provider_name == decision.fallback

            model = decision.model_for(provider_name)

            if not await provider.health_check():

                logger.warning(
                    "%s provider is unavailable.",
                    provider_name.capitalize(),
                )

                continue

            attempted_provider = True

            try:

                response = await self._invoke(provider, prompt, model)

                if not _is_blank_answer(response):
                    return _success_result(
                        decision=decision,
                        provider_name=provider_name,
                        model=model,
                        response=response,
                        fallback_used=fallback_used,
                        started=started,
                        prompt=prompt,
                        request_type=request_type,
                    )

                logger.warning(
                    "%s returned an empty response.",
                    provider_name.capitalize(),
                )

            except LLMTimeoutError:

                logger.warning(
                    "%s timed out.",
                    provider_name.capitalize(),
                    exc_info=True,
                )

            except LLMProviderError:

                logger.warning(
                    "%s provider failed.",
                    provider_name.capitalize(),
                    exc_info=True,
                )

            except Exception:

                logger.exception(
                    "Unexpected %s failure.",
                    provider_name.capitalize(),
                )

            if is_last:
                break

        if attempted_provider:
            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            )

        raise LLMUnavailableError(
            "Both Groq and Ollama providers are unavailable."
        )


    async def stream_response(
        self,
        prompt: str,
        private: bool = False,
        complexity=None,
        policy: str | None = None,
        request_type: str | None = None,
        metadata: dict | None = None,
    ) -> AsyncIterator[str]:
        """Stream a response using the existing provider routing rules.

        ``metadata`` is an optional caller-owned mapping. It is populated with
        the same safe routing facts returned by ``generate_response``: the
        selected provider and model, policy, complexity, fallback flag, usage
        counts, and duration. It never contains prompt or response text.

        Fallback is only attempted before the first token of a provider. Once
        a provider has emitted text, a mid-stream failure is surfaced as
        ``LLMUnavailableError`` instead of silently concatenating output from a
        second provider. Private requests remain Ollama-only.
        """

        started = time.perf_counter()

        decision = select_route(
            private=private,
            complexity=complexity,
            policy=policy,
        )

        meta = metadata if metadata is not None else {}
        meta.clear()
        meta.update({
            "routing_policy": decision.policy,
            "complexity": getattr(
                decision.complexity,
                "value",
                decision.complexity,
            ),
            "provider_order": list(decision.provider_order),
            "request_type": request_type,
            "provider": None,
            "model": None,
            "fallback_used": False,
            "usage": None,
            "duration_ms": None,
        })

        response_limit = max(
            0,
            int(settings.STREAM_MAX_RESPONSE_CHARS or 0),
        )

        if private:

            logger.info("Routing private streaming request to Ollama only.")

            if not await ollama_provider.health_check():

                logger.error(
                    "Ollama is unavailable for a private streaming request."
                )

                raise LLMUnavailableError(
                    "Local AI provider is unavailable. "
                    "Private request was not sent to the cloud."
                )

            model = decision.primary_model
            meta.update({"provider": "ollama", "model": model})

            emitted = False
            collected: list[str] = []
            collected_chars = 0

            try:

                try:
                    ollama_provider.last_usage = None
                except Exception:
                    pass

                async for chunk in ollama_provider.stream_response(
                    prompt,
                    model=model,
                ):

                    emitted = True

                    if collected_chars < response_limit:
                        take = chunk[: response_limit - collected_chars]
                        collected.append(take)
                        collected_chars += len(take)

                    yield chunk

            except asyncio.CancelledError:
                raise

            except LLMTimeoutError:

                logger.error(
                    "Ollama timed out while streaming a private request.",
                    exc_info=True,
                )

                raise

            except LLMProviderError as error:

                logger.error(
                    "Ollama failed while streaming a private request.",
                    exc_info=True,
                )

                raise LLMUnavailableError(
                    "Local AI provider failed. "
                    "Private request was not sent to the cloud."
                ) from error

            except Exception as error:

                logger.exception(
                    "Unexpected Ollama failure while streaming a private "
                    "request."
                )

                raise LLMUnavailableError(
                    "Local AI provider failed. "
                    "Private request was not sent to the cloud."
                ) from error

            if not emitted:
                raise LLMUnavailableError(
                    "Local AI provider returned an empty response. "
                    "Private request was not sent to the cloud."
                )

            meta["duration_ms"] = round(
                max(0.0, (time.perf_counter() - started) * 1000.0),
                2,
            )
            meta["usage"] = _usage_metadata(
                ollama_provider,
                prompt,
                "".join(collected),
            )

            return

        attempted_provider = False

        for index, provider_name in enumerate(decision.provider_order):

            provider = _provider(provider_name)
            is_last = index == len(decision.provider_order) - 1
            fallback_used = provider_name == decision.fallback
            model = decision.model_for(provider_name)

            if not await provider.health_check():

                logger.warning(
                    "%s provider is unavailable for streaming.",
                    provider_name.capitalize(),
                )

                continue

            attempted_provider = True
            emitted = False
            collected = []
            collected_chars = 0

            try:

                try:
                    provider.last_usage = None
                except Exception:
                    pass

                async for chunk in provider.stream_response(
                    prompt,
                    model=model,
                ):

                    if not emitted:
                        emitted = True
                        meta.update({
                            "provider": provider_name,
                            "model": model,
                            "fallback_used": fallback_used,
                        })

                    if collected_chars < response_limit:
                        take = chunk[: response_limit - collected_chars]
                        collected.append(take)
                        collected_chars += len(take)

                    yield chunk

            except asyncio.CancelledError:
                raise

            except Exception as error:

                if emitted:

                    logger.warning(
                        "%s failed after streaming began; fallback is not "
                        "safe.",
                        provider_name.capitalize(),
                        exc_info=True,
                    )

                    raise LLMUnavailableError(
                        "The AI provider failed while streaming the response."
                    ) from error

                logger.warning(
                    "%s failed before streaming began.",
                    provider_name.capitalize(),
                    exc_info=True,
                )

            if emitted:

                meta["duration_ms"] = round(
                    max(0.0, (time.perf_counter() - started) * 1000.0),
                    2,
                )
                meta["usage"] = _usage_metadata(
                    provider,
                    prompt,
                    "".join(collected),
                )

                return

            logger.warning(
                "%s returned an empty stream.",
                provider_name.capitalize(),
            )

            if is_last:
                break

        if attempted_provider:
            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            )

        raise LLMUnavailableError(
            "Both Groq and Ollama providers are unavailable."
        )

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        private: bool = False,
        complexity=None,
        policy: str | None = None,
        request_type: str | None = "chat",
    ) -> dict:
        """Run one provider turn that may request tool calls.

        The private path is Ollama-only and never consults Groq. The returned
        mapping carries normalized ``tool_calls`` objects that only the
        backend executor may act on.
        """

        started = time.perf_counter()

        decision = select_route(
            private=private,
            complexity=complexity,
            policy=policy,
        )

        prompt = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if message.get("role") in {"system", "user"}
        )

        if private:

            if not await ollama_provider.health_check():

                raise LLMUnavailableError(
                    "Local AI provider is unavailable. "
                    "Private request was not sent to the cloud."
                )

            try:
                ollama_provider.last_usage = None
                turn = await ollama_provider.generate_with_tools(
                    messages,
                    tools=[],
                    model=decision.primary_model,
                )

            except LLMTimeoutError:
                raise

            except Exception as error:
                logger.error(
                    "Ollama failed during a private tool-calling turn.",
                    exc_info=True,
                )
                raise LLMUnavailableError(
                    "Local AI provider failed. "
                    "Private request was not sent to the cloud."
                ) from error

            if _is_blank_answer(turn.content) and not turn.tool_calls:
                raise LLMUnavailableError(
                    "Local AI provider returned an empty response. "
                    "Private request was not sent to the cloud."
                )

            return _success_tool_result(
                decision=decision,
                provider_name="ollama",
                model=decision.primary_model,
                turn=turn,
                fallback_used=False,
                started=started,
                prompt=prompt,
                request_type=request_type,
            )

        attempted_provider = False

        for index, provider_name in enumerate(decision.provider_order):

            provider = _provider(provider_name)
            is_last = index == len(decision.provider_order) - 1
            fallback_used = provider_name == decision.fallback
            model = decision.model_for(provider_name)

            if not await provider.health_check():

                logger.warning(
                    "%s provider is unavailable.",
                    provider_name.capitalize(),
                )

                continue

            attempted_provider = True

            try:
                provider.last_usage = None

                turn = await provider.generate_with_tools(
                    messages,
                    tools=tools,
                    model=model,
                )

                if turn.tool_calls or not _is_blank_answer(turn.content):
                    return _success_tool_result(
                        decision=decision,
                        provider_name=provider_name,
                        model=model,
                        turn=turn,
                        fallback_used=fallback_used,
                        started=started,
                        prompt=prompt,
                        request_type=request_type,
                    )

                logger.warning(
                    "%s returned an empty response.",
                    provider_name.capitalize(),
                )

            except LLMTimeoutError:
                logger.warning(
                    "%s timed out during tool calling.",
                    provider_name.capitalize(),
                    exc_info=True,
                )

            except LLMProviderError:
                logger.warning(
                    "%s provider failed during tool calling.",
                    provider_name.capitalize(),
                    exc_info=True,
                )

            except Exception:
                logger.exception(
                    "Unexpected %s failure during tool calling.",
                    provider_name.capitalize(),
                )

            if is_last:
                break

        if attempted_provider:
            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            )

        raise LLMUnavailableError(
            "Both Groq and Ollama providers are unavailable."
        )


# Create a reusable router instance
llm_router = LLMRouter()
