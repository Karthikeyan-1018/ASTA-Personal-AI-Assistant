
import logging

import httpx
from groq import APITimeoutError, AsyncGroq, GroqError

from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
)


# Configure logging
logger = logging.getLogger(__name__)


class GroqProvider:

    def __init__(self):

        self.client = AsyncGroq(
            api_key=settings.GROQ_API_KEY,
            timeout=settings.GROQ_TIMEOUT_SECONDS
        )

        self.model = settings.GROQ_MODEL

    async def generate_response(self, prompt: str) -> str:

        try:

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

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
