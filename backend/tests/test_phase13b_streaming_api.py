"""Phase 13B tests: the SSE chat endpoint.

The suite exercises the real FastAPI route, the real chat service, and an
isolated database. The LLM router is replaced with a deterministic async
generator, so no provider call is made.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.api.dependencies import get_current_user_id
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.llm_router import llm_router
from app.main import app
from app.services.conversation_service import conversation_service
from app.services.usage_tracking_service import usage_tracking_service
from app.task_intent.chat_handler import task_intent_chat_handler
from tests.streaming_test_support import (
    StreamingApiTestBase,
    combined_text,
    event_names,
    parse_sse,
)


class StreamingEndpointTests(StreamingApiTestBase):

    async def test_stream_returns_sse_and_ordered_events(self):
        self._patch_router_stream(["Hello", " world"])

        response = await self._stream({"message": "Hi"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")

        events = parse_sse(response.text)

        self.assertEqual(
            event_names(events),
            ["start", "delta", "delta", "done"],
        )
        self.assertEqual(combined_text(events), "Hello world")

        start = events[0][1]
        done = events[-1][1]

        self.assertIsInstance(start["conversation_id"], int)
        self.assertEqual(done["conversation_id"], start["conversation_id"])
        self.assertEqual(done["provider"], "groq")
        self.assertEqual(done["model"], "test-model")
        self.assertEqual(done["finish_reason"], "stop")
        self.assertFalse(done["fallback_used"])

    async def test_conversation_is_reused_when_supplied(self):
        self._patch_router_stream(["first"])
        first = await self._stream({"message": "one"})
        conversation_id = parse_sse(first.text)[0][1]["conversation_id"]

        self._patch_router_stream(["second"])
        second = await self._stream(
            {"message": "two", "conversation_id": conversation_id}
        )

        events = parse_sse(second.text)
        self.assertEqual(events[0][1]["conversation_id"], conversation_id)

        messages = await self._messages(conversation_id)
        self.assertEqual(
            [item["role"] for item in messages],
            ["user", "assistant", "user", "assistant"],
        )

    async def test_empty_prompt_is_rejected_before_streaming(self):
        response = await self._stream({"message": "   "})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(
            response.headers.get("content-type", "").startswith(
                "text/event-stream"
            )
        )

    async def test_streaming_disabled_returns_503(self):
        self.settings(STREAMING_ENABLED=False)

        response = await self._stream({"message": "Hi"})

        self.assertEqual(response.status_code, 503, response.text)

    async def test_unknown_conversation_returns_404_before_streaming(self):
        async with self.SessionLocal() as session:
            bob_conversation = await conversation_service.create_conversation(
                session=session,
                user_id=self.bob_id,
                title="Bob's conversation",
            )
            bob_id = bob_conversation.id

        response = await self._stream(
            {"message": "Hi", "conversation_id": bob_id}
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn("event:", response.text)

    async def test_authentication_is_required(self):
        app.dependency_overrides.pop(get_current_user_id, None)

        try:
            response = await self._stream({"message": "Hi"})
        finally:
            async def override_owner():
                return self.alice_id

            app.dependency_overrides[get_current_user_id] = override_owner

        self.assertEqual(response.status_code, 401, response.text)

    async def test_private_flag_is_passed_to_the_router(self):
        seen: dict = {}

        async def generator(
            prompt=None,
            private=False,
            complexity=None,
            request_type=None,
            metadata=None,
            **kwargs,
        ):
            seen["private"] = private
            seen["prompt"] = prompt

            if metadata is not None:
                metadata.update(
                    {
                        "provider": "ollama",
                        "model": "local-model",
                        "usage": None,
                        "duration_ms": 1.0,
                        "fallback_used": False,
                    }
                )

            yield "local answer"

        with patch.object(llm_router, "stream_response", new=generator):
            response = await self._stream(
                {"message": "private question", "private": True}
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(seen["private"])
        events = parse_sse(response.text)
        self.assertEqual(combined_text(events), "local answer")

    async def test_provider_failure_becomes_a_safe_error_event(self):
        self._patch_router_stream(
            [],
            error=LLMProviderError("secret-provider-detail"),
        )

        response = await self._stream({"message": "Hi"})

        self.assertEqual(response.status_code, 200, response.text)
        events = parse_sse(response.text)
        self.assertEqual(event_names(events), ["start", "error"])

        code, data = events[-1]
        self.assertEqual(code, "error")
        self.assertEqual(data["code"], "provider_error")
        self.assertNotIn("secret-provider-detail", response.text)
        self.assertNotIn("Traceback", response.text)

    async def test_timeout_becomes_a_safe_error_event(self):
        self._patch_router_stream(
            [],
            error=LLMTimeoutError("secret-internal-timeout"),
        )

        response = await self._stream({"message": "Hi"})

        events = parse_sse(response.text)
        self.assertEqual(events[-1][1]["code"], "timeout")
        self.assertNotIn("secret-internal-timeout", response.text)

    async def test_unavailable_becomes_a_safe_error_event(self):
        self._patch_router_stream(
            [],
            error=LLMUnavailableError("both providers down"),
        )

        response = await self._stream({"message": "Hi"})

        events = parse_sse(response.text)
        self.assertEqual(events[-1][1]["code"], "unavailable")
        self.assertNotIn("both providers down", response.text)

    async def test_task_intent_reply_is_a_single_delta(self):
        async def task_reply(**kwargs):
            return {
                "provider": "system",
                "model": "",
                "response": "Your reminder was created.",
                "duration_ms": 0.0,
                "usage": None,
                "fallback_used": False,
            }

        with patch.object(
            task_intent_chat_handler,
            "handle",
            new=task_reply,
        ):
            response = await self._stream({"message": "remind me at 9"})

        events = parse_sse(response.text)
        self.assertEqual(
            event_names(events),
            ["start", "delta", "done"],
        )
        self.assertEqual(
            combined_text(events),
            "Your reminder was created.",
        )
        self.assertEqual(events[-1][1]["provider"], "system")

    async def test_non_streaming_chat_route_is_unchanged(self):
        mock = AsyncMock(
            return_value={
                "provider": "groq",
                "model": "test-model",
                "response": "plain answer",
                "routing_policy": "groq_first",
                "complexity": "moderate",
                "cache_hit": False,
                "fallback_used": False,
                "duration_ms": 1.0,
                "usage": None,
                "request_type": "chat",
            }
        )

        with patch.object(llm_router, "generate_response", new=mock):
            response = await self.client.post(
                "/api/chat/",
                json={"message": "Hello"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["response"], "plain answer")
        self.assertEqual(
            set(body.keys()),
            {"provider", "model", "response", "conversation_id"},
        )
        self.assertFalse(
            response.headers["content-type"].startswith("text/event-stream")
        )

    async def test_usage_failure_does_not_break_the_stream(self):
        self._patch_router_stream(["complete"])

        with patch.object(
            usage_tracking_service,
            "record",
            new=AsyncMock(side_effect=RuntimeError("analytics down")),
        ):
            response = await self._stream({"message": "Hi"})

        events = parse_sse(response.text)
        self.assertEqual(
            event_names(events),
            ["start", "delta", "done"],
        )
        self.assertEqual(combined_text(events), "complete")

    async def test_start_event_reports_the_primary_route(self):
        self._patch_router_stream(["x"])

        response = await self._stream({"message": "Hi"})
        start = parse_sse(response.text)[0][1]

        self.assertEqual(start["provider"], "groq")
        self.assertTrue(start["model"])


if __name__ == "__main__":
    unittest.main()
