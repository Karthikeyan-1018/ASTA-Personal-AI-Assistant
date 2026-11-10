
import logging

from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider


# Configure logging
logger = logging.getLogger(__name__)


def _is_blank_answer(value) -> bool:
    """Return whether a provider answer is unusable.

    Providers can return ``None``, an empty string, or whitespace instead of an
    answer. Treating that as success would persist or return an empty assistant
    message, so it is handled as a provider failure.
    """

    return not isinstance(value, str) or not value.strip()


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

    async def generate_response(
        self,
        prompt: str,
        private: bool = False
    ) -> dict:

        # Private requests must use local Ollama only.
        # Groq must never receive a private prompt.
        if private:

            logger.info(
                "Routing private request to Ollama only."
            )

            if not await ollama_provider.health_check():

                logger.error(
                    "Ollama is unavailable for a private request."
                )

                raise LLMUnavailableError(
                    "Local AI provider is unavailable. "
                    "Private request was not sent to the cloud."
                )

            try:

                response = await ollama_provider.generate_response(prompt)

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

            return {
                "provider": "ollama",
                "model": ollama_provider.model,
                "response": response
            }

        # Try Groq first for normal requests.
        if await groq_provider.health_check():

            try:

                response = await groq_provider.generate_response(prompt)

                if not _is_blank_answer(response):

                    return {
                        "provider": "groq",
                        "model": groq_provider.model,
                        "response": response
                    }

                logger.warning(
                    "Groq returned an empty response. Trying Ollama fallback."
                )

            except LLMTimeoutError:

                logger.warning(
                    "Groq timed out. Trying Ollama fallback.",
                    exc_info=True
                )

            except LLMProviderError:

                logger.warning(
                    "Groq provider failed. Trying Ollama fallback.",
                    exc_info=True
                )

            except Exception:

                logger.exception(
                    "Unexpected Groq failure. Trying Ollama fallback."
                )

        else:

            logger.warning(
                "Groq provider is unavailable. Trying Ollama fallback."
            )

        # Fall back to local Ollama.
        if not await ollama_provider.health_check():

            logger.error(
                "Ollama fallback provider is unavailable."
            )

            raise LLMUnavailableError(
                "Both Groq and Ollama providers are unavailable."
            )

        try:

            response = await ollama_provider.generate_response(prompt)

        except LLMTimeoutError as error:

            logger.error(
                "Ollama timed out after Groq failed.",
                exc_info=True
            )

            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            ) from error

        except LLMProviderError as error:

            logger.error(
                "Ollama failed after Groq failed.",
                exc_info=True
            )

            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            ) from error

        except Exception as error:

            logger.exception(
                "Unexpected Ollama failure after Groq failed."
            )

            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            ) from error

        if _is_blank_answer(response):

            logger.error(
                "Ollama returned an empty response after Groq failed."
            )

            raise LLMUnavailableError(
                "Both Groq and Ollama providers failed to generate a response."
            )

        return {
            "provider": "ollama",
            "model": ollama_provider.model,
            "response": response
        }


# Create a reusable router instance
llm_router = LLMRouter()
