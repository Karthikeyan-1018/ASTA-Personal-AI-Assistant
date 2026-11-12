
import asyncio
import logging

import httpx
import ollama

from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
)
from app.llm.tool_calling import (
    ProviderToolTurn,
    extract_ollama_tool_calls,
    to_ollama_messages,
    to_ollama_tools,
)


# Configure logging
logger = logging.getLogger(__name__)


def _as_positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 0 else None


def _field(response, name):
    """Read a field from either a mapping or an object response."""

    if isinstance(response, dict):
        return response.get(name)

    return getattr(response, name, None)


def _extract_usage(response) -> dict | None:
    """Return provider-reported token counts, or ``None`` when absent.

    Ollama reports prompt and completion eval counts. Only numeric counts are
    stored; response text is never copied here.
    """

    input_tokens = _as_positive_int(_field(response, "prompt_eval_count"))
    output_tokens = _as_positive_int(_field(response, "eval_count"))

    if input_tokens is None and output_tokens is None:
        return None

    total_tokens = (
        (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None or output_tokens is not None
        else None
    )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "source": "provider",
    }


class OllamaProvider:

    def __init__(self):

        self.client = ollama.AsyncClient(
            host=settings.OLLAMA_BASE_URL
        )

        self.model = settings.OLLAMA_MODEL

        # Populated after a successful call when the server reports token
        # usage. It contains counts only, never prompt or response content.
        self.last_usage = None

    # Generate response with timeout handling
    async def generate_response(
        self,
        prompt: str,
        model: str | None = None
    ) -> str:

        self.last_usage = None
        target_model = model or self.model

        try:

            response = await asyncio.wait_for(
                self.client.chat(
                    model=target_model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    options={
                        "num_ctx": settings.OLLAMA_NUM_CTX
                    }
                ),
                timeout=settings.OLLAMA_TIMEOUT_SECONDS
            )

            content = response.message.content
            self.last_usage = _extract_usage(response)

            return content

        except (asyncio.TimeoutError, httpx.TimeoutException) as error:

            logger.warning(
                "Ollama request timed out after %s seconds.",
                settings.OLLAMA_TIMEOUT_SECONDS
            )

            raise LLMTimeoutError(
                "Ollama request timed out."
            ) from error

        except (ollama.ResponseError, ollama.RequestError) as error:

            logger.error(
                "Ollama provider error: %s",
                type(error).__name__
            )

            raise LLMProviderError(
                "Ollama provider request failed."
            ) from error

        # The Ollama client raises a built-in ConnectionError when the
        # server cannot be reached. HTTPX errors are handled as well.
        except (ConnectionError, httpx.HTTPError) as error:

            logger.error(
                "Ollama connection error: %s",
                type(error).__name__
            )

            raise LLMProviderError(
                "Ollama provider connection failed."
            ) from error

        except Exception as error:

            logger.exception(
                "Unexpected Ollama provider error."
            )

            raise LLMProviderError(
                "Ollama provider failed unexpectedly."
            ) from error

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> ProviderToolTurn:
        """Run one chat turn with tool definitions using Ollama's format."""

        self.last_usage = None
        target_model = model or self.model

        # Sanitize registry definitions before they reach the Ollama client.
        provider_tools = to_ollama_tools(tools)

        kwargs: dict = {
            "model": target_model,
            "messages": to_ollama_messages(messages),
            "options": {"num_ctx": settings.OLLAMA_NUM_CTX},
        }

        if provider_tools:
            kwargs["tools"] = provider_tools

        try:

            response = await asyncio.wait_for(
                self.client.chat(**kwargs),
                timeout=settings.OLLAMA_TIMEOUT_SECONDS,
            )

            self.last_usage = _extract_usage(response)

            message = _field(response, "message")
            content = _field(message, "content") or ""

            return ProviderToolTurn(
                content=content,
                tool_calls=extract_ollama_tool_calls(message),
                usage=self.last_usage,
                model=target_model,
            )

        except (asyncio.TimeoutError, httpx.TimeoutException) as error:

            logger.warning(
                "Ollama tool-calling request timed out after %s seconds.",
                settings.OLLAMA_TIMEOUT_SECONDS,
            )

            raise LLMTimeoutError(
                "Ollama request timed out."
            ) from error

        except (ollama.ResponseError, ollama.RequestError) as error:

            logger.error(
                "Ollama tool-calling error: %s",
                type(error).__name__,
            )

            raise LLMProviderError(
                "Ollama provider request failed."
            ) from error

        except (ConnectionError, httpx.HTTPError) as error:

            logger.error(
                "Ollama connection error during tool calling: %s",
                type(error).__name__,
            )

            raise LLMProviderError(
                "Ollama provider connection failed."
            ) from error

        except Exception as error:

            logger.exception(
                "Unexpected Ollama tool-calling error."
            )

            raise LLMProviderError(
                "Ollama provider failed unexpectedly."
            ) from error

    # Check whether Ollama server is accessible
    async def health_check(self) -> bool:

        try:

            await self.client.list()

            return True

        except Exception as error:

            logger.warning(
                "Ollama health check failed: %s",
                type(error).__name__
            )

            return False


# Create a reusable provider instance
ollama_provider = OllamaProvider()
