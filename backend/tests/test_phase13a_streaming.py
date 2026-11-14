"""Phase 13A tests: provider streaming and the streaming router contract.

Every provider call is a controlled double. No Groq or Ollama request is made.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from groq import GroqError

from app.ai.query_classifier import QueryComplexity
from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.groq_provider import groq_provider
from app.llm.llm_router import llm_router
from app.llm.ollama_provider import ollama_provider
from app.llm.streaming import close_async_stream, iterate_with_timeouts
from tests.streaming_test_support import (
    BlockingStream,
    FakeAsyncStream,
    FakeSyncCloseStream,
    groq_chunk,
    groq_usage_chunk,
    ollama_chunk,
)


def provider_stream(chunks, *, error=None, delay: float = 0.0):
    async def generator(prompt=None, model=None, **kwargs):
        for chunk in chunks:
            if delay:
                await asyncio.sleep(delay)
            yield chunk

        if error is not None:
            raise error

    return generator


class TimeoutHelperTests(unittest.IsolatedAsyncioTestCase):

    async def test_yields_every_item_and_closes_the_source(self):
        stream = FakeAsyncStream([1, 2, 3])

        seen = [
            item
            async for item in iterate_with_timeouts(
                stream,
                idle_timeout=1.0,
                overall_timeout=5.0,
            )
        ]

        self.assertEqual(seen, [1, 2, 3])
        self.assertTrue(stream.closed)

    async def test_idle_timeout_raises_and_closes(self):
        stream = BlockingStream()

        with self.assertRaises(asyncio.TimeoutError):
            async for _item in iterate_with_timeouts(
                stream,
                idle_timeout=0.05,
                overall_timeout=5.0,
            ):
                pass

        self.assertTrue(stream.closed)

    async def test_overall_timeout_raises_and_closes(self):
        stream = FakeAsyncStream([1, 2, 3, 4], delay=0.03)

        with self.assertRaises(asyncio.TimeoutError):
            async for _item in iterate_with_timeouts(
                stream,
                idle_timeout=1.0,
                overall_timeout=0.05,
            ):
                pass

        self.assertTrue(stream.closed)

    async def test_cancellation_closes_the_source(self):
        stream = BlockingStream()

        async def consume():
            async for _item in iterate_with_timeouts(
                stream,
                idle_timeout=None,
                overall_timeout=None,
            ):
                pass

        task = asyncio.create_task(consume())
        await stream.started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(stream.closed)

    async def test_close_supports_a_synchronous_close(self):
        stream = FakeSyncCloseStream([])

        await close_async_stream(stream)

        self.assertTrue(stream.close_called)
        self.assertTrue(stream.closed)

    async def test_close_is_safe_for_none(self):
        await close_async_stream(None)


class GroqStreamingTests(unittest.IsolatedAsyncioTestCase):

    async def test_stream_yields_text_and_captures_usage(self):
        stream = FakeSyncCloseStream(
            [
                groq_chunk("Hel"),
                groq_chunk("lo"),
                groq_usage_chunk(),
            ]
        )
        create = AsyncMock(return_value=stream)

        with patch.object(
            groq_provider.client.chat.completions,
            "create",
            new=create,
        ):
            chunks = [
                chunk
                async for chunk in groq_provider.stream_response(
                    "prompt",
                    model="test-groq",
                )
            ]

        self.assertEqual(chunks, ["Hel", "lo"])
        self.assertTrue(stream.close_called)
        self.assertEqual(groq_provider.last_usage["input_tokens"], 10)
        self.assertEqual(groq_provider.last_usage["output_tokens"], 5)
        self.assertEqual(groq_provider.last_usage["total_tokens"], 15)

        kwargs = create.await_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["model"], "test-groq")
        self.assertEqual(
            kwargs["messages"],
            [{"role": "user", "content": "prompt"}],
        )

    async def test_timeout_is_mapped_and_stream_closed(self):
        stream = FakeSyncCloseStream([], error=asyncio.TimeoutError())
        create = AsyncMock(return_value=stream)

        with patch.object(
            groq_provider.client.chat.completions,
            "create",
            new=create,
        ):
            with self.assertRaises(LLMTimeoutError):
                async for _chunk in groq_provider.stream_response("prompt"):
                    pass

        self.assertTrue(stream.close_called)

    async def test_provider_error_is_mapped_and_stream_closed(self):
        stream = FakeSyncCloseStream([], error=GroqError("provider down"))
        create = AsyncMock(return_value=stream)

        with patch.object(
            groq_provider.client.chat.completions,
            "create",
            new=create,
        ):
            with self.assertRaises(LLMProviderError):
                async for _chunk in groq_provider.stream_response("prompt"):
                    pass

        self.assertTrue(stream.close_called)

    async def test_unexpected_error_is_mapped(self):
        stream = FakeSyncCloseStream([], error=RuntimeError("boom"))
        create = AsyncMock(return_value=stream)

        with patch.object(
            groq_provider.client.chat.completions,
            "create",
            new=create,
        ):
            with self.assertRaises(LLMProviderError):
                async for _chunk in groq_provider.stream_response("prompt"):
                    pass

        self.assertTrue(stream.close_called)

    async def test_cancellation_propagates_and_stream_closed(self):
        stream = BlockingStream()
        create = AsyncMock(return_value=stream)

        async def consume():
            async for _chunk in groq_provider.stream_response("prompt"):
                pass

        with patch.object(
            groq_provider.client.chat.completions,
            "create",
            new=create,
        ):
            task = asyncio.create_task(consume())
            await stream.started.wait()
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(stream.closed)


class OllamaStreamingTests(unittest.IsolatedAsyncioTestCase):

    async def test_stream_yields_text_and_captures_usage(self):
        stream = FakeAsyncStream(
            [
                ollama_chunk("Hel"),
                ollama_chunk("lo"),
                ollama_chunk(
                    None,
                    done=True,
                    prompt_eval_count=7,
                    eval_count=3,
                ),
            ]
        )
        chat = AsyncMock(return_value=stream)

        with patch.object(ollama_provider.client, "chat", new=chat):
            chunks = [
                chunk
                async for chunk in ollama_provider.stream_response(
                    "prompt",
                    model="test-ollama",
                )
            ]

        self.assertEqual(chunks, ["Hel", "lo"])
        self.assertTrue(stream.closed)
        self.assertEqual(ollama_provider.last_usage["input_tokens"], 7)
        self.assertEqual(ollama_provider.last_usage["output_tokens"], 3)
        self.assertEqual(ollama_provider.last_usage["total_tokens"], 10)

        kwargs = chat.await_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["model"], "test-ollama")

    async def test_timeout_is_mapped_and_stream_closed(self):
        stream = FakeAsyncStream([], error=asyncio.TimeoutError())
        chat = AsyncMock(return_value=stream)

        with patch.object(ollama_provider.client, "chat", new=chat):
            with self.assertRaises(LLMTimeoutError):
                async for _chunk in ollama_provider.stream_response("prompt"):
                    pass

        self.assertTrue(stream.closed)

    async def test_provider_error_is_mapped(self):
        import ollama

        stream = FakeAsyncStream(
            [],
            error=ollama.ResponseError("model missing"),
        )
        chat = AsyncMock(return_value=stream)

        with patch.object(ollama_provider.client, "chat", new=chat):
            with self.assertRaises(LLMProviderError):
                async for _chunk in ollama_provider.stream_response("prompt"):
                    pass

        self.assertTrue(stream.closed)

    async def test_cancellation_propagates_and_stream_closed(self):
        stream = BlockingStream()
        chat = AsyncMock(return_value=stream)

        async def consume():
            async for _chunk in ollama_provider.stream_response("prompt"):
                pass

        with patch.object(ollama_provider.client, "chat", new=chat):
            task = asyncio.create_task(consume())
            await stream.started.wait()
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(stream.closed)


class RouterStreamingTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.settings_patchers = []

        for name, value in (
            ("AI_ROUTING_POLICY", "groq_first"),
            ("STREAM_MAX_RESPONSE_CHARS", 50000),
        ):
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.settings_patchers.append(patcher)

        self.addCleanup(self._stop_settings)

        self._original_groq_health = groq_provider.health_check
        self._original_ollama_health = ollama_provider.health_check
        self._original_groq_stream = groq_provider.stream_response
        self._original_ollama_stream = ollama_provider.stream_response
        self._original_groq_usage = groq_provider.last_usage
        self._original_ollama_usage = ollama_provider.last_usage

        self.addCleanup(self._restore_providers)

    def _stop_settings(self):
        for patcher in self.settings_patchers:
            patcher.stop()

    def _restore_providers(self):
        groq_provider.health_check = self._original_groq_health
        ollama_provider.health_check = self._original_ollama_health
        groq_provider.stream_response = self._original_groq_stream
        ollama_provider.stream_response = self._original_ollama_stream
        groq_provider.last_usage = self._original_groq_usage
        ollama_provider.last_usage = self._original_ollama_usage

    def _configure(self, *, groq_ok=True, ollama_ok=True, groq_stream=None, ollama_stream=None):
        groq_provider.health_check = AsyncMock(return_value=groq_ok)
        ollama_provider.health_check = AsyncMock(return_value=ollama_ok)
        groq_provider.stream_response = groq_stream or provider_stream([])
        ollama_provider.stream_response = ollama_stream or provider_stream([])
        groq_provider.last_usage = None
        ollama_provider.last_usage = None

    async def _collect(self, **kwargs):
        metadata: dict = {}
        chunks = [
            chunk
            async for chunk in llm_router.stream_response(
                prompt="question",
                complexity=QueryComplexity.MODERATE,
                metadata=metadata,
                **kwargs,
            )
        ]
        return chunks, metadata

    async def test_primary_provider_streams_and_metadata_is_populated(self):
        self._configure(
            groq_stream=provider_stream(["a", "b"]),
            ollama_stream=AsyncMock(),
        )

        chunks, metadata = await self._collect()

        self.assertEqual(chunks, ["a", "b"])
        self.assertEqual(metadata["provider"], "groq")
        self.assertEqual(metadata["model"], groq_provider.model)
        self.assertFalse(metadata["fallback_used"])
        self.assertEqual(metadata["routing_policy"], "groq_first")
        self.assertIsNotNone(metadata["duration_ms"])
        ollama_provider.stream_response.assert_not_called()

    async def test_fallback_is_used_before_the_first_token(self):
        self._configure(
            groq_stream=provider_stream(
                [],
                error=LLMProviderError("groq down"),
            ),
            ollama_stream=provider_stream(["local answer"]),
        )

        chunks, metadata = await self._collect()

        self.assertEqual(chunks, ["local answer"])
        self.assertEqual(metadata["provider"], "ollama")
        self.assertTrue(metadata["fallback_used"])

    async def test_no_fallback_after_partial_output(self):
        self._configure(
            groq_stream=provider_stream(
                ["partial"],
                error=LLMProviderError("groq died"),
            ),
            ollama_stream=AsyncMock(),
        )

        received = []

        with self.assertRaises(LLMUnavailableError):
            async for chunk in llm_router.stream_response(
                prompt="question",
                complexity=QueryComplexity.MODERATE,
                metadata={},
            ):
                received.append(chunk)

        self.assertEqual(received, ["partial"])
        ollama_provider.stream_response.assert_not_called()

    async def test_private_request_never_consults_groq(self):
        self._configure(
            groq_stream=AsyncMock(),
            ollama_stream=provider_stream(["secret local"]),
        )

        chunks, metadata = await self._collect(private=True)

        self.assertEqual(chunks, ["secret local"])
        self.assertEqual(metadata["provider"], "ollama")
        groq_provider.stream_response.assert_not_called()
        groq_provider.health_check.assert_not_called()

    async def test_private_request_fails_when_ollama_is_unavailable(self):
        self._configure(
            groq_ok=True,
            ollama_ok=False,
            groq_stream=AsyncMock(),
        )

        with self.assertRaises(LLMUnavailableError):
            await self._collect(private=True)

        groq_provider.stream_response.assert_not_called()

    async def test_empty_streams_raise_unavailable(self):
        self._configure(
            groq_stream=provider_stream([]),
            ollama_stream=provider_stream([]),
        )

        with self.assertRaises(LLMUnavailableError):
            await self._collect()

    async def test_cancellation_propagates_from_the_router(self):
        stream = BlockingStream()

        async def blocking(prompt=None, model=None, **kwargs):
            try:
                async for item in stream:
                    yield item
            finally:
                await stream.aclose()

        self._configure(groq_stream=blocking)

        async def consume():
            async for _chunk in llm_router.stream_response(
                prompt="question",
                complexity=QueryComplexity.MODERATE,
                metadata={},
            ):
                pass

        task = asyncio.create_task(consume())
        await stream.started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
