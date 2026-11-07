
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.llm.groq_provider import GroqProvider, groq_provider
from app.llm.llm_router import llm_router
from app.llm.ollama_provider import OllamaProvider, ollama_provider
from app.llm.router import app as legacy_app
from app.main import app
from app.services.chat_service import chat_service


def make_groq_health_provider(list_mock):
    provider = GroqProvider.__new__(GroqProvider)
    provider.client = SimpleNamespace(
        models=SimpleNamespace(list=list_mock)
    )

    return provider


def make_ollama_health_provider(list_mock):
    provider = OllamaProvider.__new__(OllamaProvider)
    provider.client = SimpleNamespace(list=list_mock)

    return provider


class GroqHealthCheckTests(unittest.IsolatedAsyncioTestCase):

    async def test_health_check_returns_true_when_available(self):

        list_mock = AsyncMock(return_value=[])

        provider = make_groq_health_provider(list_mock)

        self.assertTrue(await provider.health_check())
        list_mock.assert_awaited_once()

    async def test_health_check_returns_false_when_unavailable(self):

        list_mock = AsyncMock(
            side_effect=RuntimeError("Groq unavailable")
        )

        provider = make_groq_health_provider(list_mock)

        self.assertFalse(await provider.health_check())
        list_mock.assert_awaited_once()


class OllamaHealthCheckTests(unittest.IsolatedAsyncioTestCase):

    async def test_health_check_returns_true_when_available(self):

        list_mock = AsyncMock(return_value=[])

        provider = make_ollama_health_provider(list_mock)

        self.assertTrue(await provider.health_check())
        list_mock.assert_awaited_once()

    async def test_health_check_returns_false_when_unavailable(self):

        list_mock = AsyncMock(
            side_effect=ConnectionError("Ollama unavailable")
        )

        provider = make_ollama_health_provider(list_mock)

        self.assertFalse(await provider.health_check())
        list_mock.assert_awaited_once()


class RouterProviderCheckTests(unittest.IsolatedAsyncioTestCase):

    async def test_check_providers_returns_both_statuses(self):

        groq_health = AsyncMock(return_value=True)
        ollama_health = AsyncMock(return_value=False)

        with (
            patch.object(
                groq_provider,
                "health_check",
                new=groq_health
            ),
            patch.object(
                ollama_provider,
                "health_check",
                new=ollama_health
            ),
        ):

            status = await llm_router.check_providers()

        self.assertEqual(
            status,
            {
                "groq": True,
                "ollama": False
            }
        )
        groq_health.assert_awaited_once()
        ollama_health.assert_awaited_once()


class HealthAPITests(unittest.TestCase):

    def setUp(self):

        self.client = TestClient(app)

    def test_both_providers_available_returns_healthy(self):

        status = {
            "groq": True,
            "ollama": True
        }

        with patch.object(
            llm_router,
            "check_providers",
            new=AsyncMock(return_value=status)
        ):

            response = self.client.get("/api/health/providers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "groq": "available",
                "ollama": "available",
                "overall_status": "healthy"
            }
        )

    def test_one_provider_unavailable_returns_degraded(self):

        status = {
            "groq": False,
            "ollama": True
        }

        with patch.object(
            llm_router,
            "check_providers",
            new=AsyncMock(return_value=status)
        ):

            response = self.client.get("/api/health/providers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "groq": "unavailable",
                "ollama": "available",
                "overall_status": "degraded"
            }
        )


class BasicEndpointTests(unittest.TestCase):

    def setUp(self):

        self.client = TestClient(app)

    def test_home_endpoint(self):

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Running")

    def test_liveness_endpoint(self):

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Healthy")


class ChatSuccessAPITests(unittest.TestCase):

    def setUp(self):

        self.client = TestClient(app)

    def test_successful_chat_returns_200_and_expected_format(self):

        expected = {
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "response": "Hello from Groq"
        }

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(return_value=expected)
        ):

            response = self.client.post(
                "/api/chat/",
                json={
                    "message": "Hello",
                    "private": False
                }
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)


class LegacyRouterCompatibilityTests(unittest.TestCase):

    def test_legacy_router_exports_the_main_application(self):

        self.assertIs(legacy_app, app)


if __name__ == "__main__":

    unittest.main()
