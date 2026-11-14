"""Phase 13C tests: persistence semantics for streamed chat.

A streamed turn persists the user message before the provider is called and the
assistant message exactly once, only after a normal, non-empty completion.
Partial, failed, oversized, empty, and cancelled answers are never stored.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.context.builder import unified_context_builder
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
)
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.services.conversation_service import conversation_service
from tests.streaming_test_support import (
    StreamingApiTestBase,
    call_app_with_disconnect,
    combined_text,
    event_names,
    parse_sse,
)


class StreamingPersistenceTests(StreamingApiTestBase):

    async def _all_messages(self, user_id=None) -> list[Message]:
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Conversation.user_id == (user_id or self.alice_id))
                .order_by(Message.id)
            )
            return list(result.scalars().all())

    async def test_completed_stream_persists_user_then_one_assistant(self):
        self._patch_router_stream(["Hello", " there"])

        response = await self._stream({"message": "Hi"})
        conversation_id = parse_sse(response.text)[0][1]["conversation_id"]

        rows = await self._message_rows(conversation_id)

        self.assertEqual(
            [row.role for row in rows],
            [MessageRole.USER, MessageRole.ASSISTANT],
        )
        self.assertEqual(rows[0].content, "Hi")
        self.assertEqual(rows[1].content, "Hello there")
        self.assertEqual(rows[1].provider, "groq")
        self.assertEqual(rows[1].model, "test-model")
        self.assertFalse(rows[1].is_private)

    async def test_user_message_exists_before_the_provider_streams(self):
        observed: dict = {}

        async def generator(
            prompt=None,
            private=False,
            complexity=None,
            request_type=None,
            metadata=None,
            **kwargs,
        ):
            async with self.SessionLocal() as session:
                result = await session.execute(
                    select(Message).order_by(Message.id)
                )
                observed["roles"] = [
                    row.role for row in result.scalars().all()
                ]

            if metadata is not None:
                metadata.update(
                    {
                        "provider": "groq",
                        "model": "test-model",
                        "usage": None,
                        "duration_ms": 1.0,
                        "fallback_used": False,
                    }
                )

            yield "answer"

        with patch(
            "app.llm.llm_router.llm_router.stream_response",
            new=generator,
        ):
            await self._stream({"message": "Hi"})

        self.assertEqual(observed["roles"], [MessageRole.USER])

    async def test_provider_failure_does_not_persist_an_assistant(self):
        self._patch_router_stream(
            ["partial"],
            error=LLMProviderError("provider failed"),
        )

        response = await self._stream({"message": "Hi"})
        events = parse_sse(response.text)

        self.assertEqual(events[-1][0], "error")
        self.assertEqual(combined_text(events), "partial")

        rows = await self._all_messages()
        self.assertEqual([row.role for row in rows], [MessageRole.USER])

    async def test_timeout_does_not_persist_an_assistant(self):
        self._patch_router_stream([], error=LLMTimeoutError("timeout"))

        response = await self._stream({"message": "Hi"})
        self.assertEqual(parse_sse(response.text)[-1][1]["code"], "timeout")

        rows = await self._all_messages()
        self.assertEqual([row.role for row in rows], [MessageRole.USER])

    async def test_empty_response_does_not_persist_an_assistant(self):
        self._patch_router_stream([])

        response = await self._stream({"message": "Hi"})
        events = parse_sse(response.text)

        self.assertEqual(events[-1][1]["code"], "empty_response")

        rows = await self._all_messages()
        self.assertEqual([row.role for row in rows], [MessageRole.USER])

    async def test_oversized_response_is_not_persisted(self):
        self.settings(STREAM_MAX_RESPONSE_CHARS=5)
        self._patch_router_stream(["123", "456"])

        response = await self._stream({"message": "Hi"})
        events = parse_sse(response.text)

        self.assertEqual(events[-1][1]["code"], "response_too_large")
        self.assertEqual(combined_text(events), "123")

        rows = await self._all_messages()
        self.assertEqual([row.role for row in rows], [MessageRole.USER])

    async def test_client_disconnect_does_not_persist_an_assistant(self):
        self._patch_router_stream(["first", "second"], delay=0.05)

        await call_app_with_disconnect(
            path="/api/chat/stream",
            payload={"message": "cancel me"},
            disconnect_after_chunks=1,
            timeout=10.0,
        )

        # Allow cancellation cleanup to finish.
        await asyncio.sleep(0.05)

        rows = await self._all_messages()

        self.assertEqual([row.role for row in rows], [MessageRole.USER])
        self.assertEqual(rows[0].content, "cancel me")

    async def test_existing_history_is_preserved(self):
        async with self.SessionLocal() as session:
            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=self.alice_id,
                title="Existing",
            )

            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content="old question",
            )
            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.ASSISTANT,
                content="old answer",
                provider="groq",
                model="old-model",
            )
            conversation_id = conversation.id

        self._patch_router_stream(["new answer"])

        response = await self._stream(
            {"message": "new question", "conversation_id": conversation_id}
        )
        self.assertEqual(response.status_code, 200, response.text)

        rows = await self._message_rows(conversation_id)

        self.assertEqual(
            [row.content for row in rows],
            ["old question", "old answer", "new question", "new answer"],
        )
        self.assertEqual(rows[1].provider, "groq")
        self.assertEqual(rows[1].model, "old-model")

    async def test_private_history_is_excluded_from_a_cloud_prompt(self):
        captured: dict = {}

        async def inspect(**kwargs):
            captured["history"] = list(kwargs["history"])
            return type(
                "Inspection",
                (),
                {"prompt": f"context::{kwargs['user_message']}"},
            )()

        async with self.SessionLocal() as session:
            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=self.alice_id,
                title="Mixed",
            )

            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content="TOP-SECRET-PRIVATE",
                is_private=True,
            )
            await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content="public history",
                is_private=False,
            )
            conversation_id = conversation.id

        self._patch_router_stream(["answer"])

        with patch.object(unified_context_builder, "inspect", new=inspect):
            await self._stream(
                {"message": "public question", "conversation_id": conversation_id}
            )

        contents = [row.content for row in captured["history"]]

        self.assertNotIn("TOP-SECRET-PRIVATE", contents)
        self.assertIn("public history", contents)

    async def test_private_stream_marks_the_assistant_message_private(self):
        self._patch_router_stream(
            ["private answer"],
            provider="ollama",
            model="local-model",
        )

        response = await self._stream(
            {"message": "private question", "private": True}
        )
        conversation_id = parse_sse(response.text)[0][1]["conversation_id"]

        rows = await self._message_rows(conversation_id)

        self.assertTrue(all(row.is_private for row in rows))
        self.assertEqual(rows[-1].provider, "ollama")
        self.assertEqual(rows[-1].model, "local-model")

    async def test_second_stream_appends_without_duplicating_prior_turns(self):
        self._patch_router_stream(["first answer"])
        first = await self._stream({"message": "first question"})
        conversation_id = parse_sse(first.text)[0][1]["conversation_id"]

        self._patch_router_stream(["second answer"])
        await self._stream(
            {"message": "second question", "conversation_id": conversation_id}
        )

        rows = await self._message_rows(conversation_id)

        self.assertEqual(
            [row.content for row in rows],
            [
                "first question",
                "first answer",
                "second question",
                "second answer",
            ],
        )


if __name__ == "__main__":
    unittest.main()
