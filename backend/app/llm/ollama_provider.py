
import asyncio
import logging

import httpx
import ollama

from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
)


# Configure logging
logger = logging.getLogger(__name__)


class OllamaProvider:

    def __init__(self):

        self.client = ollama.AsyncClient(
            host=settings.OLLAMA_BASE_URL
        )

        self.model = settings.OLLAMA_MODEL

    # Generate response with timeout handling
    async def generate_response(self, prompt: str) -> str:

        try:

            response = await asyncio.wait_for(
                self.client.chat(
                    model=self.model,
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

            return response.message.content

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
