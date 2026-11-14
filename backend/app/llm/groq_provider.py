
import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
from groq import APITimeoutError, AsyncGroq, GroqError

from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
)
from app.llm.streaming import iterate_with_timeouts
from app.llm.tool_calling import (
    ProviderToolTurn,
    extract_groq_tool_calls,
    to_groq_messages,
    to_groq_tools,
)


# Configure logging
logger = logging.getLogger(__name__)


def _as_positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 0 else None


def _extract_usage(response) -> dict | None:
    """Return provider-reported token counts, or ``None`` when absent.

    Only numeric counts are stored. Response text is never copied here.
    """

    usage = getattr(response, "usage", None)

    if usage is None:
        return None

    input_tokens = _as_positive_int(
        getattr(usage, "prompt_tokens", None)
    )
    output_tokens = _as_positive_int(
        getattr(usage, "completion_tokens", None)
    )
    total_tokens = _as_positive_int(
        getattr(usage, "total_tokens", None)
    )

    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
    ):
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "source": "provider",
    }


class GroqProvider:

    def __init__(self):

        self.client = AsyncGroq(
            api_key=settings.GROQ_API_KEY,
            timeout=settings.GROQ_TIMEOUT_SECONDS
        )

        self.model = settings.GROQ_MODEL

        # Populated after a successful call when the SDK reports token usage.
        # It contains counts only, never prompt or response content.
        self.last_usage = None

    async def generate_response(
        self,
        prompt: str,
        model: str | None = None
    ) -> str:

        self.last_usage = None
        target_model = model or self.model

        try:

            response = await self.client.chat.completions.create(
                model=target_model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            self.last_usage = _extract_usage(response)

            return response.choices[0].message.content

        # The Groq SDK wraps HTTPX timeouts in APITimeoutError.
        # Catch HTTPX timeouts as well for extra reliability.
        except (APITimeoutError, httpx.TimeoutException) as error:

            logger.warning(
                "Groq request timed out after %s seconds.",
                settings.GROQ_TIMEOUT_SECONDS
            )

            raise LLMTimeoutError(
                "Groq request timed out."
            ) from error

        # All other Groq SDK errors are provider failures.
        except GroqError as error:

            logger.error(
                "Groq provider error (%s), status code: %s",
                type(error).__name__,
                getattr(error, "status_code", "unknown")
            )

            raise LLMProviderError(
                "Groq provider request failed."
            ) from error

        # Handle any remaining HTTPX errors that were not wrapped by Groq.
        except httpx.HTTPError as error:

            logger.error(
                "Groq HTTP error: %s",
                type(error).__name__
            )

            raise LLMProviderError(
                "Groq provider connection failed."
            ) from error

        except Exception as error:

            logger.exception(
                "Unexpected Groq provider error."
            )

            raise LLMProviderError(
                "Groq provider failed unexpectedly."
            ) from error

    async def stream_response(
        self,
        prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield Groq answer text as it is generated.

        Errors are mapped to the same structured exceptions used by the
        non-streaming path. The SDK stream is closed on every exit path,
        including caller cancellation. Only answer text is yielded; reasoning
        fields, tool calls, and provider metadata are ignored here.
        """

        self.last_usage = None
        target_model = model or self.model
        stream = None

        try:

            stream = await self.client.chat.completions.create(
                model=target_model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                stream=True,
            )

            async for chunk in iterate_with_timeouts(
                stream,
                idle_timeout=settings.STREAM_IDLE_TIMEOUT_SECONDS,
                overall_timeout=settings.STREAM_TIMEOUT_SECONDS,
            ):

                usage = _extract_usage(chunk)

                if usage is not None:
                    self.last_usage = usage

                choices = getattr(chunk, "choices", None)

                if not choices:
                    continue

                delta = getattr(choices[0], "delta", None)
                content = (
                    getattr(delta, "content", None)
                    if delta is not None
                    else None
                )

                if content:
                    yield content

        except asyncio.CancelledError:
            raise

        except (APITimeoutError, httpx.TimeoutException, asyncio.TimeoutError) as error:

            logger.warning(
                "Groq streaming request timed out after %s seconds.",
                settings.STREAM_TIMEOUT_SECONDS,
            )

            raise LLMTimeoutError(
                "Groq request timed out."
            ) from error

        except GroqError as error:

            logger.error(
                "Groq streaming error (%s), status code: %s",
                type(error).__name__,
                getattr(error, "status_code", "unknown"),
            )

            raise LLMProviderError(
                "Groq provider request failed."
            ) from error

        except httpx.HTTPError as error:

            logger.error(
                "Groq HTTP error during streaming: %s",
                type(error).__name__,
            )

            raise LLMProviderError(
                "Groq provider connection failed."
            ) from error

        except (LLMTimeoutError, LLMProviderError):
            raise

        except Exception as error:

            logger.error(
                "Unexpected Groq streaming error (%s).",
                type(error).__name__,
            )

            raise LLMProviderError(
                "Groq provider failed unexpectedly."
            ) from error

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> ProviderToolTurn:
        """Run one chat completion turn that may request tool calls."""

        self.last_usage = None
        target_model = model or self.model

        # Registry definitions are provider-independent. Convert them once at
        # the provider boundary so a malformed definition can never reach the
        # SDK request.
        provider_tools = to_groq_tools(tools)

        kwargs: dict = {
            "model": target_model,
            "messages": to_groq_messages(messages),
        }

        if provider_tools:
            kwargs["tools"] = provider_tools
            kwargs["tool_choice"] = "auto"

        try:

            response = await self.client.chat.completions.create(**kwargs)

            self.last_usage = _extract_usage(response)

            choices = getattr(response, "choices", None)

            if not choices:
                raise LLMProviderError("Groq returned no response choices.")

            message = choices[0].message
            content = getattr(message, "content", None) or ""

            return ProviderToolTurn(
                content=content,
                tool_calls=extract_groq_tool_calls(message),
                usage=self.last_usage,
                model=target_model,
            )

        except LLMProviderError:
            raise

        except (APITimeoutError, httpx.TimeoutException) as error:

            logger.warning(
                "Groq tool-calling request timed out after %s seconds.",
                settings.GROQ_TIMEOUT_SECONDS,
            )

            raise LLMTimeoutError(
                "Groq request timed out."
            ) from error

        except GroqError as error:

            logger.error(
                "Groq tool-calling error (%s), status code: %s",
                type(error).__name__,
                getattr(error, "status_code", "unknown"),
            )

            raise LLMProviderError(
                "Groq provider request failed."
            ) from error

        except httpx.HTTPError as error:

            logger.error(
                "Groq HTTP error during tool calling: %s",
                type(error).__name__,
            )

            raise LLMProviderError(
                "Groq provider connection failed."
            ) from error

        except Exception as error:

            logger.exception(
                "Unexpected Groq tool-calling error."
            )

            raise LLMProviderError(
                "Groq provider failed unexpectedly."
            ) from error

    # Check whether Groq API is accessible
    async def health_check(self) -> bool:

        try:

            await self.client.models.list()

            return True

        except Exception as error:

            logger.warning(
                "Groq health check failed: %s",
                type(error).__name__
            )

            return False


# Create a reusable provider instance
groq_provider = GroqProvider()
