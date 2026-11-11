"""Provider selection and failover for ASTA.

Phase 9 adds complexity-aware routing on top of the historical Groq-first
behaviour. The public contract is unchanged: ``generate_response`` accepts a
prompt and a ``private`` flag and returns a provider/model/response mapping.
Additional metadata keys (routing policy, fallback flag, duration, usage) are
available to internal callers and are ignored by the chat API response model.

Private requests are always resolved to Ollama only. Their execution path
never consults a cloud provider, not even for a health check.
"""

import logging
import time

from app.ai.routing_policy import select_route
from app.ai.tokenization import safe_estimate_tokens
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


# Create a reusable router instance
llm_router = LLMRouter()
