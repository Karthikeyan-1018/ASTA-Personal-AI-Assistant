
import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import ollama
from fastapi.testclient import TestClient
from groq import APIConnectionError, APITimeoutError

from app.api.dependencies import get_current_user_id
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.groq_provider import GroqProvider, groq_provider
from app.llm.llm_router import llm_router
from app.llm.ollama_provider import OllamaProvider, ollama_provider
from app.main import app
from app.services.chat_service import chat_service


def groq_chat_response(text: str):
    """Build the small part of a Groq response used by the provider."""

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text)
            )
        ]
    )


def make_groq_provider(create_mock):
    """Create a GroqProvider without opening a real HTTP client."""

    provider = GroqProvider.__new__(GroqProvider)
    provider.model = "test-groq-model"
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create_mock)
        )
    )

    return provider


def make_ollama_provider(chat_mock):
    """Create an OllamaProvider without opening a real HTTP client."""

    provider = OllamaProvider.__new__(OllamaProvider)
    provider.model = "test-ollama-model"
    provider.client = SimpleNamespace(chat=chat_mock)

    return provider


def mock_providers(
    stack: ExitStack,
    groq_health: bool = True,
    groq_response: str = "groq answer",
    groq_error: Exception = None,
    ollama_health: bool = True,
    ollama_response: str = "ollama answer",
    ollama_error: Exception = None
):
    """Patch both providers on the shared router singletons."""

    groq_health_mock = AsyncMock(return_value=groq_health)
    groq_generate_mock = AsyncMock(
        return_value=groq_response,
        side_effect=groq_error
    )

    ollama_health_mock = AsyncMock(return_value=ollama_health)
    ollama_generate_mock = AsyncMock(
        return_value=ollama_response,
        side_effect=ollama_error
    )

    stack.enter_context(
        patch.object(groq_provider, "health_check", new=groq_health_mock)
    )
    stack.enter_context(
        patch.object(
            groq_provider,
            "generate_response",
            new=groq_generate_mock
        )
    )
    stack.enter_context(
        patch.object(
            ollama_provider,
            "health_check",
            new=ollama_health_mock
        )
    )
    stack.enter_context(
        patch.object(
            ollama_provider,
            "generate_response",
            new=ollama_generate_mock
        )
    )

    return (
        groq_health_mock,
        groq_generate_mock,
        ollama_health_mock,
        ollama_generate_mock
    )


class GroqProviderExceptionTests(unittest.IsolatedAsyncioTestCase):

    async def test_successful_response(self):

        create_mock = AsyncMock(
            return_value=groq_chat_response("hello from groq")
        )

        provider = make_groq_provider(create_mock)

        result = await provider.generate_response("hello")

        self.assertEqual(result, "hello from groq")
        create_mock.assert_awaited_once()

    async def test_groq_api_timeout_is_classified(self):

        request = httpx.Request(
            "POST",
            "https://api.groq.com/openai/v1/chat/completions"
        )

        create_mock = AsyncMock(
            side_effect=APITimeoutError(request=request)
        )

        provider = make_groq_provider(create_mock)

        with self.assertRaises(LLMTimeoutError):
            await provider.generate_response("hello")

    async def test_httpx_timeout_is_classified(self):

        request = httpx.Request(
            "POST",
            "https://api.groq.com/openai/v1/chat/completions"
        )

        create_mock = AsyncMock(
            side_effect=httpx.ReadTimeout(
                "timed out",
                request=request
            )
        )

        provider = make_groq_provider(create_mock)

        with self.assertRaises(LLMTimeoutError):
            await provider.generate_response("hello")

    async def test_groq_provider_error_is_classified(self):

        request = httpx.Request(
            "POST",
            "https://api.groq.com/openai/v1/chat/completions"
        )

        create_mock = AsyncMock(
            side_effect=APIConnectionError(request=request)
        )

        provider = make_groq_provider(create_mock)

        with self.assertRaises(LLMProviderError):
            await provider.generate_response("hello")


class OllamaProviderExceptionTests(unittest.IsolatedAsyncioTestCase):

    async def test_successful_response(self):

        chat_mock = AsyncMock(
            return_value=SimpleNamespace(
                message=SimpleNamespace(content="hello from ollama")
            )
        )

        provider = make_ollama_provider(chat_mock)

        result = await provider.generate_response("hello")

        self.assertEqual(result, "hello from ollama")
        chat_mock.assert_awaited_once()

    async def test_timeout_is_classified(self):

        chat_mock = AsyncMock(side_effect=asyncio.TimeoutError)

        provider = make_ollama_provider(chat_mock)

        with self.assertRaises(LLMTimeoutError):
            await provider.generate_response("hello")

    async def test_httpx_timeout_is_classified(self):

        request = httpx.Request(
            "POST",
            "http://localhost:11434/api/chat"
        )

        chat_mock = AsyncMock(
            side_effect=httpx.ReadTimeout(
                "timed out",
                request=request
            )
        )

        provider = make_ollama_provider(chat_mock)

        with self.assertRaises(LLMTimeoutError):
            await provider.generate_response("hello")

    async def test_response_error_is_classified(self):

        chat_mock = AsyncMock(
            side_effect=ollama.ResponseError(
                "model not found",
                404
            )
        )

        provider = make_ollama_provider(chat_mock)

        with self.assertRaises(LLMProviderError):
            await provider.generate_response("hello")

    async def test_connection_error_is_classified(self):

        chat_mock = AsyncMock(
            side_effect=ConnectionError("Ollama is not running")
        )

        provider = make_ollama_provider(chat_mock)

        with self.assertRaises(LLMProviderError):
            await provider.generate_response("hello")


class LLMRouterTests(unittest.IsolatedAsyncioTestCase):

    async def test_normal_request_selects_groq(self):

        with ExitStack() as stack:

            (
                _groq_health,
                groq_generate,
                _ollama_health,
                ollama_generate
            ) = mock_providers(
                stack,
                groq_response="cloud answer",
                ollama_response="local answer"
            )

            result = await llm_router.generate_response(
                prompt="hello",
                private=False
            )

        self.assertEqual(result["provider"], "groq")
        self.assertEqual(result["model"], groq_provider.model)
        self.assertEqual(result["response"], "cloud answer")
        groq_generate.assert_awaited_once()
        ollama_generate.assert_not_awaited()

    async def test_private_request_selects_ollama(self):

        with ExitStack() as stack:

            (
                groq_health,
                groq_generate,
                _ollama_health,
                _ollama_generate
            ) = mock_providers(
                stack,
                groq_response="cloud answer",
                ollama_response="local answer"
            )

            result = await llm_router.generate_response(
                prompt="private hello",
                private=True
            )

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["model"], ollama_provider.model)
        self.assertEqual(result["response"], "local answer")

        # A private prompt must never touch Groq, not even its health check.
        groq_health.assert_not_awaited()
        groq_generate.assert_not_awaited()

    async def test_groq_timeout_triggers_ollama_fallback(self):

        with ExitStack() as stack:

            (
                _groq_health,
                groq_generate,
                _ollama_health,
                _ollama_generate
            ) = mock_providers(
                stack,
                groq_error=LLMTimeoutError("Groq timed out."),
                ollama_response="local fallback answer"
            )

            result = await llm_router.generate_response(
                prompt="hello",
                private=False
            )

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["response"], "local fallback answer")
        groq_generate.assert_awaited_once()

    async def test_groq_provider_failure_triggers_ollama_fallback(self):

        with ExitStack() as stack:

            (
                _groq_health,
                groq_generate,
                _ollama_health,
                _ollama_generate
            ) = mock_providers(
                stack,
                groq_error=LLMProviderError("Groq failed."),
                ollama_response="local fallback answer"
            )

            result = await llm_router.generate_response(
                prompt="hello",
                private=False
            )

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["response"], "local fallback answer")
        groq_generate.assert_awaited_once()

    async def test_private_request_never_falls_back_to_groq(self):

        with ExitStack() as stack:

            (
                groq_health,
                groq_generate,
                _ollama_health,
                _ollama_generate
            ) = mock_providers(
                stack,
                groq_response="cloud answer",
                ollama_error=LLMProviderError("Ollama failed.")
            )

            with self.assertRaises(LLMUnavailableError):
                await llm_router.generate_response(
                    prompt="private hello",
                    private=True
                )

        # Groq must not be contacted after a private Ollama failure.
        groq_health.assert_not_awaited()
        groq_generate.assert_not_awaited()

    async def test_private_timeout_preserves_timeout_error(self):

        with ExitStack() as stack:

            (
                groq_health,
                groq_generate,
                _ollama_health,
                _ollama_generate
            ) = mock_providers(
                stack,
                groq_response="cloud answer",
                ollama_error=LLMTimeoutError("Ollama timed out.")
            )

            with self.assertRaises(LLMTimeoutError):
                await llm_router.generate_response(
                    prompt="private hello",
                    private=True
                )

        groq_health.assert_not_awaited()
        groq_generate.assert_not_awaited()

    async def test_both_providers_unavailable_raises_unavailable_error(self):

        with ExitStack() as stack:

            (
                _groq_health,
                groq_generate,
                _ollama_health,
                ollama_generate
            ) = mock_providers(
                stack,
                groq_health=False,
                ollama_health=False
            )

            with self.assertRaises(LLMUnavailableError):
                await llm_router.generate_response(
                    prompt="hello",
                    private=False
                )

        groq_generate.assert_not_awaited()
        ollama_generate.assert_not_awaited()

    async def test_ollama_failure_after_groq_failure_raises_unavailable_error(
        self
    ):

        with ExitStack() as stack:

            (
                _groq_health,
                _groq_generate,
                _ollama_health,
                ollama_generate
            ) = mock_providers(
                stack,
                groq_error=LLMProviderError("Groq failed."),
                ollama_error=LLMProviderError("Ollama failed.")
            )

            with self.assertRaises(LLMUnavailableError):
                await llm_router.generate_response(
                    prompt="hello",
                    private=False
                )

        ollama_generate.assert_awaited_once()


class ChatAPITests(unittest.TestCase):

    def setUp(self):

        async def override_owner():
            return 1

        app.dependency_overrides[get_current_user_id] = override_owner
        self.client = TestClient(app)

    def tearDown(self):

        app.dependency_overrides.clear()

    def test_empty_prompt_returns_400(self):

        response = self.client.post(
            "/api/chat/",
            json={"message": "   "}
        )

        self.assertEqual(response.status_code, 400)

    def test_missing_message_returns_400(self):

        response = self.client.post(
            "/api/chat/",
            json={"private": False}
        )

        self.assertEqual(response.status_code, 400)

    def test_timeout_returns_504(self):

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(
                side_effect=LLMTimeoutError("technical timeout detail")
            )
        ):

            response = self.client.post(
                "/api/chat/",
                json={"message": "hello"}
            )

        self.assertEqual(response.status_code, 504)

    def test_provider_error_returns_503(self):

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(
                side_effect=LLMProviderError(
                    "technical provider detail"
                )
            )
        ):

            response = self.client.post(
                "/api/chat/",
                json={"message": "hello"}
            )

        self.assertEqual(response.status_code, 503)

    def test_unavailable_error_returns_503(self):

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(
                side_effect=LLMUnavailableError(
                    "technical unavailable detail"
                )
            )
        ):

            response = self.client.post(
                "/api/chat/",
                json={"message": "hello"}
            )

        self.assertEqual(response.status_code, 503)

    def test_unexpected_error_does_not_leak_secrets_or_tracebacks(self):

        technical_detail = (
            "SECRET_KEY=super-secret-value provider stack trace"
        )

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(
                side_effect=RuntimeError(technical_detail)
            )
        ):

            response = self.client.post(
                "/api/chat/",
                json={"message": "hello"}
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("SECRET_KEY", response.text)
        self.assertNotIn("super-secret-value", response.text)
        self.assertNotIn("Traceback", response.text)


if __name__ == "__main__":

    unittest.main()
