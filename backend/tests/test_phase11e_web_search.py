"""Phase 11E tests for the web search tool.

Every provider call uses ``httpx.MockTransport``; no DNS lookup, network
request, or real search API is contacted. The suite covers argument
validation, provider enablement, documented request shapes, normalized output,
result limits, timeouts, rate limits, malformed and oversized responses,
unsafe URLs, credential non-disclosure, registry/LLM discovery, and execution
through the central ``ToolExecutor``.
"""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import httpx

import app.tools.executor as executor_module
from app.core.config import settings
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolProviderError,
    ToolSizeLimitError,
    ToolValidationError,
)
from app.tools.executor import tool_executor
from app.tools.implementations import web_search as web_search_module
from app.tools.implementations.web_search import (
    WebSearchInput,
    WebSearchTool,
)
from app.tools.registry import ToolRegistry, tool_registry
from tests.tool_test_support import ToolEngineTestBase


SEARXNG_PAYLOAD = {
    "query": "asta",
    "results": [
        {
            "title": "  ASTA   overview ",
            "url": "https://www.example.com/asta?x=1",
            "content": "A short SearXNG snippet.",
            "engine": "example",
        },
        {
            "title": "Second result",
            "url": "https://docs.example.org/guide",
            "snippet": "Fallback snippet field.",
        },
        {
            "title": "Unsafe scheme",
            "url": "javascript:alert(1)",
            "content": "Must be dropped.",
        },
        {
            "title": "Embedded credentials",
            "url": "https://user:password@example.com/private",
            "content": "Must be dropped.",
        },
        {
            "title": "Control character",
            "url": "https://example.net/bad\npath",
            "content": "Must be dropped.",
        },
        "not-an-object",
    ],
}


TAVILY_PAYLOAD = {
    "query": "asta",
    "results": [
        {
            "title": "Tavily result",
            "url": "https://news.example.com/story",
            "content": "Tavily normalized content.",
            "score": 0.91,
        }
    ],
    "images": [],
    "response_time": 0.42,
    "answer": None,
}


def _response(payload=None, *, status=200, headers=None, content=None):
    if content is not None:
        return httpx.Response(status, content=content, headers=headers)

    return httpx.Response(status, json=payload, headers=headers)


class WebSearchTestBase(unittest.IsolatedAsyncioTestCase):
    """Settings, mocked HTTP transport, and direct tool invocation helpers."""

    def setting(self, stack: ExitStack, **values):
        for name, value in values.items():
            stack.enter_context(patch.object(settings, name, value))

    def configure(self, **overrides):
        values = {
            "WEB_SEARCH_ENABLED": True,
            "WEB_SEARCH_PROVIDER": "searxng",
            "WEB_SEARCH_BASE_URL": "https://search.example.com",
            "WEB_SEARCH_API_KEY": "",
            "WEB_SEARCH_TIMEOUT_SECONDS": 10,
            "WEB_SEARCH_MAX_RESULTS": 5,
            "WEB_SEARCH_MAX_RESPONSE_BYTES": 262_144,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def configure_tavily(
        self,
        api_key="tvly-super-secret-key",
        **overrides,
    ):
        values = {
            "WEB_SEARCH_PROVIDER": "tavily",
            "WEB_SEARCH_API_KEY": api_key,
        }
        values.update(overrides)
        self.configure(**values)

    def mock_http(self, handler):
        requests = []

        def _handle(request):
            requests.append(request)
            return handler(request)

        transport = httpx.MockTransport(_handle)

        def _factory(*, timeout):
            return httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )

        patcher = patch.object(
            web_search_module,
            "_new_client",
            side_effect=_factory,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        return requests

    async def run_search(self, arguments, *, tool=None):
        active_tool = tool or WebSearchTool()
        validated = active_tool.validate_arguments(arguments)

        return await active_tool.execute(None, validated)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class WebSearchValidationTests(unittest.TestCase):

    def validate(self, arguments):
        return WebSearchTool().validate_arguments(arguments)

    def test_query_is_required(self):
        with self.assertRaises(ToolValidationError):
            self.validate({"query": ""})

    def test_blank_query_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            self.validate({"query": "   \t  "})

    def test_control_characters_are_rejected(self):
        for query in ("hello\x00world", "line\nbreak", "tab\tinside"):
            with self.subTest(query=query):
                with self.assertRaises(ToolValidationError):
                    self.validate({"query": query})

    def test_query_length_is_bounded(self):
        with self.assertRaises(ToolValidationError):
            self.validate({"query": "x" * 501})

    def test_max_results_is_bounded(self):
        for value in (0, -1, 11, 100):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    self.validate(
                        {"query": "asta", "max_results": value}
                    )

    def test_unknown_arguments_are_rejected(self):
        with self.assertRaises(ToolValidationError):
            self.validate({"query": "asta", "user_id": 2})

    def test_valid_query_is_stripped(self):
        parsed = self.validate({"query": "  coffee harvest  "})

        self.assertIsInstance(parsed, WebSearchInput)
        self.assertEqual(parsed.query, "coffee harvest")
        self.assertEqual(parsed.max_results, 5)


# ---------------------------------------------------------------------------
# Enablement and configuration
# ---------------------------------------------------------------------------


class WebSearchConfigurationTests(WebSearchTestBase):

    def test_disabled_by_default(self):
        self.configure(WEB_SEARCH_ENABLED=False)

        self.assertFalse(WebSearchTool().enabled)

    def test_searxng_requires_a_base_url(self):
        self.configure(WEB_SEARCH_BASE_URL="")
        self.assertFalse(WebSearchTool().enabled)

        self.configure(WEB_SEARCH_BASE_URL="https://search.example.com")
        self.assertTrue(WebSearchTool().enabled)

    def test_tavily_requires_an_api_key(self):
        self.configure(
            WEB_SEARCH_PROVIDER="tavily",
            WEB_SEARCH_API_KEY="",
        )
        self.assertFalse(WebSearchTool().enabled)

        self.configure(
            WEB_SEARCH_PROVIDER="tavily",
            WEB_SEARCH_API_KEY="tvly-test",
        )
        self.assertTrue(WebSearchTool().enabled)

    def test_unknown_provider_is_disabled(self):
        self.configure(WEB_SEARCH_PROVIDER="unknown")

        self.assertFalse(WebSearchTool().enabled)

    async def test_unknown_provider_raises_configuration_error(self):
        self.configure(WEB_SEARCH_PROVIDER="unknown")
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        with self.assertRaises(ToolConfigurationError):
            await self.run_search({"query": "asta"})

        self.assertEqual(requests, [])

    async def test_missing_tavily_key_raises_configuration_error(self):
        self.configure(
            WEB_SEARCH_PROVIDER="tavily",
            WEB_SEARCH_API_KEY="",
        )
        requests = self.mock_http(
            lambda request: _response(TAVILY_PAYLOAD)
        )

        with self.assertRaises(ToolConfigurationError):
            await self.run_search({"query": "asta"})

        self.assertEqual(requests, [])

    async def test_invalid_searxng_base_url_raises_configuration_error(self):
        for base_url in (
            "not-a-url",
            "ftp://search.example.com",
            "https://user:pass@search.example.com",
        ):
            with self.subTest(base_url=base_url):
                self.configure(WEB_SEARCH_BASE_URL=base_url)
                requests = self.mock_http(
                    lambda request: _response(SEARXNG_PAYLOAD)
                )

                with self.assertRaises(ToolConfigurationError):
                    await self.run_search({"query": "asta"})

                self.assertEqual(requests, [])


# ---------------------------------------------------------------------------
# SearXNG provider
# ---------------------------------------------------------------------------


class SearxngProviderTests(WebSearchTestBase):

    async def test_success_returns_normalized_results(self):
        self.configure()
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        result = await self.run_search({"query": "asta", "max_results": 5})

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url.scheme, "https")
        self.assertEqual(request.url.host, "search.example.com")
        self.assertEqual(request.url.path, "/search")
        self.assertEqual(request.url.params.get("q"), "asta")
        self.assertEqual(request.url.params.get("format"), "json")
        self.assertEqual(request.url.params.get("safesearch"), "1")

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "searxng")
        self.assertTrue(result.metadata["untrusted_content"])

        data = result.data
        self.assertEqual(data["query"], "asta")
        self.assertEqual(data["provider"], "searxng")
        self.assertEqual(data["count"], 2)
        self.assertIs(data["untrusted"], True)

        first = data["results"][0]
        self.assertEqual(first["title"], "ASTA overview")
        self.assertEqual(first["url"], "https://www.example.com/asta?x=1")
        self.assertEqual(first["snippet"], "A short SearXNG snippet.")
        self.assertEqual(first["source"], "example.com")

        second = data["results"][1]
        self.assertEqual(second["snippet"], "Fallback snippet field.")
        self.assertEqual(second["source"], "docs.example.org")

        # Unsafe schemes, embedded credentials, control characters, and
        # non-object entries never reach the model.
        rendered = json.dumps(data)
        self.assertNotIn("javascript:", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("not-an-object", rendered)

    async def test_base_url_path_is_preserved(self):
        self.configure(
            WEB_SEARCH_BASE_URL="https://search.example.com/searxng/",
        )
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        await self.run_search({"query": "asta"})

        self.assertEqual(requests[0].url.path, "/searxng/search")

    async def test_base_url_ending_in_search_is_not_duplicated(self):
        self.configure(
            WEB_SEARCH_BASE_URL="https://search.example.com/search",
        )
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        await self.run_search({"query": "asta"})

        self.assertEqual(requests[0].url.path, "/search")

    async def test_text_fields_are_truncated_and_cleaned(self):
        self.configure()
        payload = {
            "results": [
                {
                    "title": "T" * 400,
                    "url": "https://example.com/long",
                    "content": "S" * 900,
                }
            ]
        }
        self.mock_http(lambda request: _response(payload))

        result = await self.run_search({"query": "asta"})

        item = result.data["results"][0]
        self.assertLessEqual(len(item["title"]), 200)
        self.assertTrue(item["title"].endswith("..."))
        self.assertLessEqual(len(item["snippet"]), 500)
        self.assertTrue(item["snippet"].endswith("..."))

    async def test_configured_max_results_caps_the_request(self):
        self.configure(WEB_SEARCH_MAX_RESULTS=2)
        requests = self.mock_http(
            lambda request: _response(
                {
                    "results": [
                        {
                            "title": f"Result {index}",
                            "url": f"https://example.com/{index}",
                            "content": "snippet",
                        }
                        for index in range(10)
                    ]
                }
            )
        )

        result = await self.run_search(
            {"query": "asta", "max_results": 10}
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(result.data["count"], 2)

    async def test_requested_result_count_is_respected(self):
        self.configure(WEB_SEARCH_MAX_RESULTS=10)
        self.mock_http(
            lambda request: _response(
                {
                    "results": [
                        {
                            "title": f"Result {index}",
                            "url": f"https://example.com/{index}",
                            "content": "snippet",
                        }
                        for index in range(10)
                    ]
                }
            )
        )

        result = await self.run_search(
            {"query": "asta", "max_results": 3}
        )

        self.assertEqual(result.data["count"], 3)

    async def test_empty_results_is_a_success(self):
        self.configure()
        self.mock_http(lambda request: _response({"results": []}))

        result = await self.run_search({"query": "asta"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["results"], [])
        self.assertEqual(result.data["count"], 0)


# ---------------------------------------------------------------------------
# Tavily provider
# ---------------------------------------------------------------------------


class TavilyProviderTests(WebSearchTestBase):

    async def test_success_uses_documented_bearer_auth(self):
        self.configure_tavily()
        requests = self.mock_http(
            lambda request: _response(TAVILY_PAYLOAD)
        )

        result = await self.run_search({"query": "asta", "max_results": 4})

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://api.tavily.com/search")
        self.assertEqual(
            request.headers.get("authorization"),
            "Bearer tvly-super-secret-key",
        )

        body = json.loads(request.content)
        self.assertEqual(body["query"], "asta")
        self.assertEqual(body["max_results"], 4)
        self.assertEqual(body["search_depth"], "basic")
        self.assertIs(body["include_answer"], False)
        self.assertIs(body["include_raw_content"], False)
        self.assertNotIn("api_key", body)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "tavily")
        item = result.data["results"][0]
        self.assertEqual(item["title"], "Tavily result")
        self.assertEqual(item["url"], "https://news.example.com/story")
        self.assertEqual(item["snippet"], "Tavily normalized content.")
        self.assertEqual(item["source"], "news.example.com")

    async def test_configured_max_results_caps_tavily_request(self):
        self.configure_tavily(WEB_SEARCH_MAX_RESULTS=1)
        requests = self.mock_http(
            lambda request: _response(TAVILY_PAYLOAD)
        )

        await self.run_search({"query": "asta", "max_results": 9})

        body = json.loads(requests[0].content)
        self.assertEqual(body["max_results"], 1)

    async def test_api_key_never_appears_in_result_or_metadata(self):
        key = "tvly-do-not-leak-this"
        self.configure_tavily(api_key=key)
        self.mock_http(lambda request: _response(TAVILY_PAYLOAD))

        result = await self.run_search({"query": "asta"})

        rendered = json.dumps(
            {
                "data": result.data,
                "metadata": result.metadata,
                "provider": result.provider,
                "error": result.error_message,
            }
        )
        self.assertNotIn(key, rendered)


# ---------------------------------------------------------------------------
# Provider failures
# ---------------------------------------------------------------------------


class WebSearchFailureTests(WebSearchTestBase):

    async def assert_provider_error(self, handler, message):
        self.configure()
        requests = self.mock_http(handler)

        with self.assertRaises(ToolProviderError) as caught:
            await self.run_search({"query": "asta"})

        self.assertIn(message, str(caught.exception))
        self.assertEqual(len(requests), 1)

        return caught.exception

    async def test_timeout_is_reported_safely(self):
        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        await self.assert_provider_error(handler, "timed out")

    async def test_network_error_is_reported_safely(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        await self.assert_provider_error(handler, "unavailable")

    async def test_rate_limit_is_reported_without_provider_content(self):
        error = await self.assert_provider_error(
            lambda request: _response(
                {"detail": {"error": "rate limit body"}},
                status=429,
                headers={"Retry-After": "60"},
            ),
            "rate limiting",
        )

        self.assertNotIn("rate limit body", str(error))
        self.assertNotIn("60", str(error))

    async def test_rejected_credential_is_reported_safely(self):
        self.configure_tavily()
        requests = self.mock_http(
            lambda request: _response(
                {"detail": {"error": "bad key tvly-super-secret-key"}},
                status=401,
            )
        )

        with self.assertLogs(
            web_search_module.logger,
            level="WARNING",
        ) as captured:
            with self.assertRaises(ToolProviderError) as caught:
                await self.run_search({"query": "asta"})

        message = str(caught.exception)
        self.assertIn("rejected", message)
        self.assertNotIn("tvly-super-secret-key", message)
        self.assertNotIn("bad key", message)
        self.assertIn("HTTP 401", "\n".join(captured.output))
        self.assertNotIn("tvly-super-secret-key", "\n".join(captured.output))
        self.assertEqual(len(requests), 1)

    async def test_forbidden_is_reported_safely(self):
        await self.assert_provider_error(
            lambda request: _response({"error": "forbidden"}, status=403),
            "rejected",
        )

    async def test_redirect_is_not_followed(self):
        await self.assert_provider_error(
            lambda request: _response(
                status=302,
                headers={"Location": "https://internal.example/"},
            ),
            "redirect",
        )

    async def test_server_error_is_reported_safely(self):
        await self.assert_provider_error(
            lambda request: _response(
                {"error": "stack trace detail"},
                status=500,
            ),
            "unavailable",
        )

    async def test_bad_request_is_reported_safely(self):
        await self.assert_provider_error(
            lambda request: _response(
                {"error": "provider detail"},
                status=400,
            ),
            "returned an error",
        )

    async def test_invalid_json_is_reported_safely(self):
        await self.assert_provider_error(
            lambda request: _response(content=b"<html>not json</html>"),
            "invalid response",
        )

    async def test_non_object_payload_is_invalid(self):
        await self.assert_provider_error(
            lambda request: _response([1, 2, 3]),
            "invalid response",
        )

    async def test_missing_results_list_is_invalid(self):
        await self.assert_provider_error(
            lambda request: _response({"query": "asta"}),
            "invalid response",
        )

    async def test_results_must_be_a_list(self):
        await self.assert_provider_error(
            lambda request: _response({"results": {"url": "https://x"}}),
            "invalid response",
        )

    async def test_oversized_response_is_bounded(self):
        self.configure(WEB_SEARCH_MAX_RESPONSE_BYTES=1_024)
        requests = self.mock_http(
            lambda request: _response(content=b"x" * 4_096)
        )

        with self.assertRaises(ToolSizeLimitError) as caught:
            await self.run_search({"query": "asta"})

        self.assertIn("size limit", str(caught.exception))
        self.assertEqual(len(requests), 1)

    async def test_tavily_key_is_not_disclosed_by_failures(self):
        key = "tvly-error-secret"
        self.configure_tavily(api_key=key)

        for handler in (
            lambda request: _response({"error": key}, status=401),
            lambda request: _response({"error": key}, status=429),
            lambda request: _response(content=key.encode(), status=200),
        ):
            with self.subTest(handler=handler):
                self.mock_http(handler)

                with self.assertRaises(
                    (ToolProviderError, ToolSizeLimitError)
                ) as caught:
                    await self.run_search({"query": "asta"})

                rendered = f"{caught.exception!r} {caught.exception.args!r}"
                self.assertNotIn(key, rendered)


# ---------------------------------------------------------------------------
# Registry and LLM discoverability
# ---------------------------------------------------------------------------


class WebSearchRegistryTests(unittest.TestCase):

    def setting(self, stack: ExitStack, **values):
        for name, value in values.items():
            stack.enter_context(patch.object(settings, name, value))

    def test_registry_registers_and_publishes_web_search(self):
        with ExitStack() as stack:
            self.setting(
                stack,
                WEB_SEARCH_ENABLED=True,
                WEB_SEARCH_PROVIDER="searxng",
                WEB_SEARCH_BASE_URL="https://search.example.com",
            )

            registry = ToolRegistry()
            registry.register(WebSearchTool())

            self.assertIn("web_search", registry.names())

            definitions = registry.llm_definitions(private=False)
            by_name = {
                item["function"]["name"]: item
                for item in definitions
            }

            self.assertIn("web_search", by_name)
            function = by_name["web_search"]["function"]
            parameters = function["parameters"]

            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])
            self.assertIn("query", parameters["properties"])
            self.assertIn("max_results", parameters["properties"])
            self.assertEqual(parameters["required"], ["query"])

    def test_disabled_web_search_is_not_published_to_the_llm(self):
        with ExitStack() as stack:
            self.setting(
                stack,
                WEB_SEARCH_ENABLED=False,
                WEB_SEARCH_PROVIDER="searxng",
                WEB_SEARCH_BASE_URL="https://search.example.com",
            )

            registry = ToolRegistry()
            registry.register(WebSearchTool())

            names = {
                item["function"]["name"]
                for item in registry.llm_definitions(private=False)
            }
            self.assertNotIn("web_search", names)

    def test_private_requests_do_not_see_web_search(self):
        with ExitStack() as stack:
            self.setting(
                stack,
                WEB_SEARCH_ENABLED=True,
                WEB_SEARCH_PROVIDER="searxng",
                WEB_SEARCH_BASE_URL="https://search.example.com",
            )

            registry = ToolRegistry()
            registry.register(WebSearchTool())

            public = {
                item["function"]["name"]
                for item in registry.llm_definitions(private=False)
            }
            private = {
                item["function"]["name"]
                for item in registry.llm_definitions(private=True)
            }

            self.assertIn("web_search", public)
            self.assertNotIn("web_search", private)

    def test_tool_metadata_is_conservative(self):
        tool = WebSearchTool()

        self.assertEqual(tool.name, "web_search")
        self.assertEqual(tool.category, ToolCategory.READ_ONLY)
        self.assertFalse(tool.allowed_in_private)
        self.assertFalse(tool.requires_confirmation)
        self.assertEqual(tool.provider_label, "web_search")

    def test_builtin_registration_contains_web_search(self):
        self.assertTrue(
            any(isinstance(tool, WebSearchTool) for tool in BUILTIN_TOOLS)
        )
        self.assertIsInstance(
            tool_registry.maybe_get("web_search"),
            WebSearchTool,
        )

    def test_input_schema_does_not_accept_provider_secrets(self):
        schema = WebSearchTool().definition().parameters
        properties = schema["properties"]

        self.assertEqual(
            set(properties),
            {"query", "max_results"},
        )
        for field_name in ("api_key", "token", "user_id", "private"):
            self.assertNotIn(field_name, properties)


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------


class WebSearchExecutorTests(ToolEngineTestBase):
    """Production execution still goes through ``ToolExecutor``."""

    def use_web_search(self) -> WebSearchTool:
        tool = WebSearchTool()
        registry = ToolRegistry()
        registry.register(tool)
        patcher = patch.object(
            executor_module,
            "tool_registry",
            registry,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return tool

    def configure(self, **overrides):
        values = {
            "WEB_SEARCH_ENABLED": True,
            "WEB_SEARCH_PROVIDER": "searxng",
            "WEB_SEARCH_BASE_URL": "https://search.example.com",
            "WEB_SEARCH_API_KEY": "",
            "WEB_SEARCH_TIMEOUT_SECONDS": 10,
            "WEB_SEARCH_MAX_RESULTS": 5,
            "WEB_SEARCH_MAX_RESPONSE_BYTES": 262_144,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def mock_http(self, handler):
        requests = []

        def _handle(request):
            requests.append(request)
            return handler(request)

        transport = httpx.MockTransport(_handle)

        def _factory(*, timeout):
            return httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )

        patcher = patch.object(
            web_search_module,
            "_new_client",
            side_effect=_factory,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        return requests

    async def run_tool(self, arguments, *, private=False):
        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                private=private,
            )
            return await tool_executor.execute(
                tool_name="web_search",
                arguments=arguments,
                context=context,
            )

    async def test_executor_runs_web_search_without_confirmation(self):
        self.configure()
        self.use_web_search()
        self.mock_http(lambda request: _response(SEARXNG_PAYLOAD))

        result = await self.run_tool({"query": "asta", "max_results": 2})

        self.assertTrue(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertFalse(result.confirmation_required)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.NOT_REQUIRED,
        )
        self.assertEqual(result.provider, "searxng")
        self.assertEqual(result.data["count"], 2)

    async def test_executor_denies_web_search_in_private_mode(self):
        self.configure()
        self.use_web_search()
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        result = await self.run_tool({"query": "asta"}, private=True)

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )
        self.assertEqual(requests, [])

    async def test_executor_denies_disabled_web_search(self):
        self.configure(WEB_SEARCH_ENABLED=False)
        self.use_web_search()
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        result = await self.run_tool({"query": "asta"})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )
        self.assertEqual(requests, [])

    async def test_executor_maps_validation_errors_without_network_io(self):
        self.configure()
        self.use_web_search()
        requests = self.mock_http(
            lambda request: _response(SEARXNG_PAYLOAD)
        )

        result = await self.run_tool({"query": "   "})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.assertEqual(requests, [])

    async def test_executor_maps_rate_limit_to_provider_category(self):
        self.configure()
        self.use_web_search()
        self.mock_http(
            lambda request: _response(
                {"detail": {"error": "rate"}},
                status=429,
            )
        )

        result = await self.run_tool({"query": "asta"})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PROVIDER,
        )
        self.assertIn("rate limiting", result.error_message)


if __name__ == "__main__":
    unittest.main()
