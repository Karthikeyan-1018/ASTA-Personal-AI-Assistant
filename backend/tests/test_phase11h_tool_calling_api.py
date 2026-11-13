"""Phase 11H tests for the chat/API tool-calling integration.

The suite exercises the real FastAPI chat route, the real orchestrator, the
real executor and confirmation service, and an isolated in-memory database.
Providers and tool implementations are controlled doubles only: no network,
filesystem, or real model call is made.

Coverage is organised around the Phase 11H requirements:

* text-only chat is unchanged when tool calling is disabled;
* one and multiple tool calls, and multiple tool rounds;
* hard round, per-turn, and per-request call limits plus loop prevention;
* bounded tool-result context;
* malformed calls, unknown tools, tool failures, and provider failures;
* disabled tools and sensitive-tool policy;
* private-mode isolation;
* confirmation pending, approval, rejection, expiry, replay, tampering, and
  cross-owner access;
* ordered, metadata-only persistence and prompt-history exclusion;
* caller cancellation;
* Groq and Ollama provider contracts through the real router.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.models  # noqa: F401  (registers every ORM table)
import app.services.tool_orchestrator as orchestrator_module
import app.tools.executor as executor_module
from app.api.dependencies import get_current_user_id
from app.core.config import settings
from app.database.connection import get_db
from app.exceptions.llm_exceptions import LLMProviderError
from app.llm.tool_calling import NormalizedToolCall, ProviderToolTurn
from app.llm.llm_router import llm_router
from app.llm.groq_provider import groq_provider
from app.llm.ollama_provider import ollama_provider
from app.main import app
from app.models.tool_execution import ToolConfirmation
from app.tools.base import BaseTool
from app.tools.enums import ToolCategory, ToolConfirmationStatus
from app.tools.registry import ToolRegistry
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult
from tests.tool_test_support import ToolEngineTestBase


class QueryInput(StrictModel):
    query: str = "default"


class SensitiveInput(StrictModel):
    target: str = "record"


class BigOutputInput(StrictModel):
    size: int = 5000


class QueryTool(BaseTool):
    name = "test_query"
    description = "Search a fixed local index."
    category = ToolCategory.READ_ONLY
    input_model = QueryInput

    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[dict] = []

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        self.seen.append(arguments.model_dump(mode="json"))
        return success_result(
            tool_name=self.name,
            data={"matches": [arguments.query]},
        )


class SensitiveTool(BaseTool):
    name = "test_sensitive"
    description = "A sensitive action that always needs approval."
    category = ToolCategory.SENSITIVE
    requires_confirmation = True
    input_model = SensitiveInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return success_result(
            tool_name=self.name,
            data={"target": arguments.target},
        )


class FailingQueryTool(BaseTool):
    name = "test_failing_query"
    description = "Raise an unexpected error while searching."
    category = ToolCategory.READ_ONLY
    input_model = QueryInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        raise RuntimeError("boom")


class DisabledQueryTool(BaseTool):
    name = "test_disabled_query"
    description = "A registered tool disabled by configuration."
    category = ToolCategory.READ_ONLY
    input_model = QueryInput

    def __init__(self) -> None:
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return False

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return success_result(tool_name=self.name, data={"ok": True})


class BigOutputTool(BaseTool):
    name = "test_big_output"
    description = "Return a large payload for context bounding."
    category = ToolCategory.READ_ONLY
    input_model = BigOutputInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return success_result(
            tool_name=self.name,
            data={"blob": "x" * max(1, arguments.size)},
        )


class ChatToolApiTestBase(ToolEngineTestBase):
    """Authenticated chat API with a fresh registry and mocked providers."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self._current_user = self.alice_id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_owner():
            return self._current_user

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_owner

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

        self.settings(
            TOOL_CALLING_ENABLED=True,
            AI_CACHE_ENABLED=False,
            TOOL_ALLOW_SENSITIVE=False,
            TOOL_MAX_ROUNDS=2,
            TOOL_MAX_CALLS_PER_REQUEST=5,
            TOOL_MAX_CALLS_PER_TURN=8,
            TOOL_MAX_RESULT_CONTEXT_BYTES=50000,
            TOOL_LOOP_PREVENTION_ENABLED=True,
            TOOL_CONFIRMATION_TTL_SECONDS=600,
        )

        self.query_tool = QueryTool()
        self.sensitive_tool = SensitiveTool()
        self.failing_tool = FailingQueryTool()
        self.disabled_tool = DisabledQueryTool()
        self.big_tool = BigOutputTool()

        self.registry = ToolRegistry()
        self.registry.register_all(
            [
                self.query_tool,
                self.sensitive_tool,
                self.failing_tool,
                self.disabled_tool,
                self.big_tool,
            ]
        )

        for module in (orchestrator_module, executor_module):
            patcher = patch.object(module, "tool_registry", self.registry)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await super().asyncTearDown()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _as_user(self, user_id: int) -> None:
        self._current_user = user_id

    @staticmethod
    def _call(name: str, arguments: dict | None = None, call_id: str = "call_1"):
        return NormalizedToolCall(
            id=call_id,
            name=name,
            arguments=arguments or {},
        )

    @staticmethod
    def _turn(
        *,
        content: str = "",
        calls: list[NormalizedToolCall] | None = None,
        provider: str = "groq",
        model: str = "test-model",
    ) -> dict:
        return {
            "provider": provider,
            "model": model,
            "response": content,
            "tool_calls": list(calls or []),
            "tool_usage": None,
            "usage": None,
            "fallback_used": False,
        }

    def _patch_turns(self, *turns):
        mock = AsyncMock(side_effect=list(turns))
        patcher = patch.object(llm_router, "generate_with_tools", new=mock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _patch_text_response(self, response: str = "plain answer"):
        mock = AsyncMock(
            return_value={
                "provider": "groq",
                "model": "test-model",
                "response": response,
                "routing_policy": "groq_first",
                "complexity": "moderate",
                "cache_hit": False,
                "fallback_used": False,
                "duration_ms": 1.0,
                "usage": None,
                "request_type": "chat",
            }
        )
        patcher = patch.object(llm_router, "generate_response", new=mock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _stub_provider(self, provider, *, turns):
        health = AsyncMock(return_value=True)
        generate = AsyncMock(side_effect=list(turns))

        for name, mock in (
            ("health_check", health),
            ("generate_with_tools", generate),
        ):
            patcher = patch.object(provider, name, new=mock)
            patcher.start()
            self.addCleanup(patcher.stop)

        return health, generate

    async def _chat(self, payload: dict):
        return await self.client.post("/api/chat/", json=payload)

    async def _messages(self, conversation_id: int) -> list[dict]:
        response = await self.client.get(
            f"/api/conversations/{conversation_id}/messages"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messages"]

    async def _stored_confirmation(self, confirmation_id: int):
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(ToolConfirmation).where(
                    ToolConfirmation.id == confirmation_id
                )
            )
            return result.scalar_one()

    async def _start_pending_confirmation(self):
        self.settings(TOOL_ALLOW_SENSITIVE=True)
        self._patch_turns(
            self._turn(
                calls=[self._call("test_sensitive", {"target": "alpha"})]
            )
        )

        response = await self._chat({"message": "do the sensitive thing"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["tool_calling"], True)
        self.assertEqual(self.sensitive_tool.calls, 0)
        self.assertEqual(len(body["pending_confirmations"]), 1)

        return body, body["pending_confirmations"][0]["confirmation_id"]


# ---------------------------------------------------------------------------
# Text-only behaviour and response shape
# ---------------------------------------------------------------------------


class ChatTextOnlyTests(ChatToolApiTestBase):

    async def test_response_shape_preserved_when_tool_calling_disabled(self):
        self.settings(TOOL_CALLING_ENABLED=False)
        self._patch_text_response("plain answer")

        response = await self._chat({"message": "Hello"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            set(response.json().keys()),
            {"provider", "model", "response", "conversation_id"},
        )
        self.assertEqual(response.json()["response"], "plain answer")

    async def test_text_only_answer_through_the_tool_loop(self):
        self._patch_turns(self._turn(content="a normal answer"))

        response = await self._chat({"message": "Hello"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["response"], "a normal answer")
        self.assertEqual(body["tool_executions"], [])
        self.assertEqual(body["pending_confirmations"], [])
        self.assertEqual(body["provider"], "groq")
        self.assertFalse(body["limit_reached"])
        self.assertFalse(body["provider_failure"])


# ---------------------------------------------------------------------------
# Tool execution cycles
# ---------------------------------------------------------------------------


class ChatToolExecutionTests(ChatToolApiTestBase):

    async def test_single_tool_call_executes_and_returns_final_answer(self):
        self._patch_turns(
            self._turn(calls=[self._call("test_query", {"query": "needle"})]),
            self._turn(content="found the answer"),
        )

        response = await self._chat({"message": "search"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["response"], "found the answer")
        self.assertEqual(self.query_tool.calls, 1)
        self.assertEqual(self.query_tool.seen, [{"query": "needle"}])
        self.assertEqual(len(body["tool_executions"]), 1)
        self.assertTrue(body["tool_executions"][0]["success"])
        self.assertEqual(body["tool_executions"][0]["tool"], "test_query")
        self.assertIn("provider", body)
        self.assertIn("model", body)

    async def test_multiple_tool_calls_in_one_turn_all_execute(self):
        self._patch_turns(
            self._turn(
                calls=[
                    self._call(
                        "test_query",
                        {"query": "first"},
                        call_id="call_a",
                    ),
                    self._call(
                        "test_query",
                        {"query": "second"},
                        call_id="call_b",
                    ),
                ]
            ),
            self._turn(content="both done"),
        )

        response = await self._chat({"message": "search twice"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query_tool.calls, 2)
        self.assertEqual(
            self.query_tool.seen,
            [{"query": "first"}, {"query": "second"}],
        )
        executions = response.json()["tool_executions"]
        self.assertEqual(len(executions), 2)
        self.assertTrue(all(item["success"] for item in executions))

    async def test_multiple_tool_rounds_are_executed_in_order(self):
        self.settings(TOOL_MAX_ROUNDS=2, TOOL_MAX_CALLS_PER_REQUEST=5)

        self._patch_turns(
            self._turn(
                calls=[self._call("test_query", {"query": "round-one"})]
            ),
            self._turn(
                calls=[
                    self._call(
                        "test_query",
                        {"query": "round-two"},
                        call_id="call_2",
                    )
                ]
            ),
            self._turn(content="finished"),
        )

        response = await self._chat({"message": "two rounds"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query_tool.calls, 2)
        self.assertEqual(
            self.query_tool.seen,
            [{"query": "round-one"}, {"query": "round-two"}],
        )
        self.assertTrue(response.json()["response"].startswith("finished"))
        self.assertTrue(response.json()["limit_reached"])

    async def test_tool_failure_is_reported_and_chat_continues(self):
        self._patch_turns(
            self._turn(calls=[self._call("test_failing_query", {})]),
            self._turn(content="the tool failed but I can still answer"),
        )

        response = await self._chat({"message": "try it"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body["response"],
            "the tool failed but I can still answer",
        )
        self.assertEqual(self.failing_tool.calls, 1)
        execution = body["tool_executions"][0]
        self.assertFalse(execution["success"])
        self.assertEqual(execution["error_code"], "ToolInternalError")
        self.assertNotIn("boom", json.dumps(body))

    async def test_unknown_tool_is_a_structured_error(self):
        self._patch_turns(
            self._turn(calls=[self._call("not_a_tool", {})]),
            self._turn(content="I could not use that tool."),
        )

        response = await self._chat({"message": "use a fake tool"})

        self.assertEqual(response.status_code, 200, response.text)
        execution = response.json()["tool_executions"][0]
        self.assertFalse(execution["success"])
        self.assertEqual(execution["error_code"], "ToolNotFoundError")
        self.assertEqual(execution["error_category"], "not_found")

    async def test_malformed_provider_call_is_reported_not_executed(self):
        malformed = NormalizedToolCall(
            id="call_bad",
            name="",
            arguments={},
            error="The provider returned a tool call without a name.",
        )

        self._patch_turns(
            self._turn(calls=[malformed]),
            self._turn(content="I could not run that."),
        )

        response = await self._chat({"message": "broken call"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query_tool.calls, 0)
        execution = response.json()["tool_executions"][0]
        self.assertFalse(execution["success"])
        self.assertEqual(execution["error_code"], "MalformedToolCall")
        self.assertEqual(execution["error_category"], "malformed_call")


# ---------------------------------------------------------------------------
# Limits, loop prevention, and bounded context
# ---------------------------------------------------------------------------


class ChatLimitTests(ChatToolApiTestBase):

    async def test_max_rounds_is_enforced_with_a_final_text_turn(self):
        self.settings(TOOL_MAX_ROUNDS=1, TOOL_MAX_CALLS_PER_REQUEST=5)
        mock = self._patch_turns(
            self._turn(calls=[self._call("test_query", {"query": "one"})]),
            self._turn(content="final answer"),
        )

        response = await self._chat({"message": "loop"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.query_tool.calls, 1)
        self.assertTrue(body["limit_reached"])
        self.assertIn("tool-call limit", body["response"])
        # One tool turn plus one explicit text-only turn.
        self.assertEqual(mock.await_count, 2)
        self.assertEqual(mock.await_args_list[-1].kwargs["tools"], [])

    async def test_per_request_call_limit_denies_the_extra_call(self):
        self.settings(TOOL_MAX_CALLS_PER_REQUEST=1, TOOL_MAX_ROUNDS=2)
        self._patch_turns(
            self._turn(
                calls=[
                    self._call(
                        "test_query",
                        {"query": "allowed"},
                        call_id="call_a",
                    ),
                    self._call(
                        "test_query",
                        {"query": "denied"},
                        call_id="call_b",
                    ),
                ]
            ),
            self._turn(content="done"),
        )

        response = await self._chat({"message": "too many"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.query_tool.calls, 1)
        self.assertTrue(body["limit_reached"])
        denied = [
            item
            for item in body["tool_executions"]
            if item.get("error_code") == "ToolCallLimitReached"
        ]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["status"], "denied")

    async def test_per_turn_call_limit_denies_the_extra_call(self):
        self.settings(TOOL_MAX_CALLS_PER_TURN=1, TOOL_MAX_ROUNDS=2)
        self._patch_turns(
            self._turn(
                calls=[
                    self._call(
                        "test_query",
                        {"query": "allowed"},
                        call_id="call_a",
                    ),
                    self._call(
                        "test_query",
                        {"query": "denied"},
                        call_id="call_b",
                    ),
                ]
            ),
            self._turn(content="done"),
        )

        response = await self._chat({"message": "too many in one turn"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.query_tool.calls, 1)
        self.assertTrue(body["limit_reached"])
        self.assertEqual(
            body["tool_executions"][-1]["error_code"],
            "ToolCallLimitReached",
        )

    async def test_loop_prevention_rejects_an_identical_call(self):
        self.settings(TOOL_LOOP_PREVENTION_ENABLED=True)
        self._patch_turns(
            self._turn(
                calls=[
                    self._call(
                        "test_query",
                        {"query": "same"},
                        call_id="call_a",
                    ),
                    self._call(
                        "test_query",
                        {"query": "same"},
                        call_id="call_b",
                    ),
                ]
            ),
            self._turn(content="done"),
        )

        response = await self._chat({"message": "repeat"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query_tool.calls, 1)
        repeated = [
            item
            for item in response.json()["tool_executions"]
            if item.get("error_code") == "RepeatedToolCall"
        ]
        self.assertEqual(len(repeated), 1)

    async def test_tool_result_context_is_bounded(self):
        self.settings(TOOL_MAX_RESULT_CONTEXT_BYTES=1000)
        mock = self._patch_turns(
            self._turn(calls=[self._call("test_big_output", {"size": 5000})]),
            self._turn(content="done"),
        )

        response = await self._chat({"message": "big output"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.big_tool.calls, 1)

        final_messages = mock.await_args_list[-1].kwargs["messages"]
        tool_messages = [
            message
            for message in final_messages
            if message.get("role") == "tool"
        ]
        self.assertTrue(tool_messages)

        for message in tool_messages:
            self.assertLessEqual(
                len(message["content"].encode("utf-8")),
                1000,
            )
        self.assertIn("...", tool_messages[0]["content"])


# ---------------------------------------------------------------------------
# Provider failures and provider contracts
# ---------------------------------------------------------------------------


class ChatProviderTests(ChatToolApiTestBase):

    async def test_provider_failure_before_any_tool_returns_503(self):
        self._patch_turns(LLMProviderError("provider down"))

        response = await self._chat({"message": "hello"})

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.query_tool.calls, 0)

    async def test_provider_failure_after_a_tool_returns_a_summary(self):
        self._patch_turns(
            self._turn(calls=[self._call("test_query", {"query": "run"})]),
            LLMProviderError("provider down"),
        )

        response = await self._chat({"message": "run then fail"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.query_tool.calls, 1)
        self.assertEqual(
            body["response"],
            "I completed the requested action.",
        )
        self.assertTrue(body["provider_failure"])
        self.assertEqual(body["provider"], "groq")

    async def test_groq_contract_normalizes_and_executes_a_call(self):
        self.settings(AI_ROUTING_POLICY="groq_first")
        _, generate = self._stub_provider(
            groq_provider,
            turns=[
                ProviderToolTurn(
                    content="",
                    tool_calls=[
                        NormalizedToolCall(
                            id="call_groq",
                            name="test_query",
                            arguments={"query": "groq"},
                        )
                    ],
                ),
                ProviderToolTurn(content="groq final answer"),
            ],
        )

        response = await self._chat({"message": "use groq"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["provider"], "groq")
        self.assertEqual(body["response"], "groq final answer")
        self.assertEqual(self.query_tool.seen, [{"query": "groq"}])
        self.assertEqual(generate.await_count, 2)

    async def test_ollama_contract_normalizes_and_executes_a_call(self):
        self.settings(AI_ROUTING_POLICY="local_first")
        _, generate = self._stub_provider(
            ollama_provider,
            turns=[
                ProviderToolTurn(
                    content="",
                    tool_calls=[
                        NormalizedToolCall(
                            id="call_ollama",
                            name="test_query",
                            arguments={"query": "ollama"},
                        )
                    ],
                ),
                ProviderToolTurn(content="ollama final answer"),
            ],
        )

        response = await self._chat({"message": "use ollama"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["provider"], "ollama")
        self.assertEqual(body["response"], "ollama final answer")
        self.assertEqual(self.query_tool.seen, [{"query": "ollama"}])
        self.assertEqual(generate.await_count, 2)


# ---------------------------------------------------------------------------
# Policy, private mode, and disabled tools
# ---------------------------------------------------------------------------


class ChatPolicyTests(ChatToolApiTestBase):

    async def test_private_chat_never_exposes_or_runs_tools(self):
        mock = self._patch_turns(self._turn(content="unused"))
        text = self._patch_text_response("private local answer")

        response = await self._chat(
            {"message": "private question", "private": True}
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["response"], "private local answer")
        mock.assert_not_awaited()
        text.assert_awaited()
        self.assertEqual(self.query_tool.calls, 0)

    async def test_sensitive_tool_is_not_offered_without_policy(self):
        self.settings(TOOL_ALLOW_SENSITIVE=False)
        definitions = self.registry.llm_definitions(private=False)
        names = {
            item["function"]["name"]
            for item in definitions
        }
        self.assertNotIn("test_sensitive", names)

    async def test_sensitive_tool_request_is_denied_when_policy_is_off(self):
        self.settings(TOOL_ALLOW_SENSITIVE=False)
        self._patch_turns(
            self._turn(
                calls=[self._call("test_sensitive", {"target": "alpha"})]
            ),
            self._turn(content="denied"),
        )

        response = await self._chat({"message": "try sensitive"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.sensitive_tool.calls, 0)
        self.assertEqual(body["tool_executions"][0]["status"], "denied")
        self.assertEqual(
            body["tool_executions"][0]["error_category"],
            "permission",
        )

    async def test_disabled_tool_is_excluded_and_cannot_be_executed_manually(self):
        definitions = self.registry.llm_definitions(private=False)
        names = {item["function"]["name"] for item in definitions}
        self.assertNotIn("test_disabled_query", names)

        response = await self.client.post(
            "/api/tools/execute",
            json={"tool": "test_disabled_query", "arguments": {}},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_category"], "disabled")
        self.assertEqual(self.disabled_tool.calls, 0)


# ---------------------------------------------------------------------------
# Confirmation workflow
# ---------------------------------------------------------------------------


class ChatConfirmationTests(ChatToolApiTestBase):

    async def test_pending_confirmation_is_clear_and_executes_nothing(self):
        body, confirmation_id = await self._start_pending_confirmation()

        self.assertNotIn("executed successfully", body["response"].lower())
        self.assertIn("confirmation", body["response"].lower())

        pending = body["pending_confirmations"][0]
        self.assertEqual(pending["tool"], "test_sensitive")
        self.assertEqual(pending["category"], "sensitive")
        self.assertIsNotNone(pending["expires_at"])

        messages = await self._messages(body["conversation_id"])
        kinds = [
            (item.get("message_metadata") or {}).get("kind")
            for item in messages
        ]
        self.assertEqual(
            kinds,
            [None, "tool_call", "tool_confirmation_required", None],
        )
        self.assertIsNotNone(confirmation_id)

    async def test_approval_executes_exactly_once_and_resumes(self):
        body, confirmation_id = await self._start_pending_confirmation()
        self._patch_turns(self._turn(content="approved and done"))

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )

        self.assertEqual(response.status_code, 200, response.text)
        approved = response.json()
        self.assertEqual(approved["conversation_id"], body["conversation_id"])
        self.assertEqual(approved["response"], "approved and done")
        self.assertEqual(approved["confirmation_status"], "executed")
        self.assertEqual(self.sensitive_tool.calls, 1)
        self.assertEqual(approved["pending_confirmations"], [])

        stored = await self._stored_confirmation(confirmation_id)
        self.assertEqual(stored.status, ToolConfirmationStatus.EXECUTED)
        self.assertEqual(stored.arguments, {})

        messages = await self._messages(body["conversation_id"])
        kinds = [
            (item.get("message_metadata") or {}).get("kind")
            for item in messages
        ]
        self.assertEqual(
            kinds,
            [
                None,
                "tool_call",
                "tool_confirmation_required",
                None,
                "tool_result",
                None,
            ],
        )
        self.assertEqual(messages[-1]["content"], "approved and done")

    async def test_resumed_turn_receives_no_tool_definitions(self):
        _body, confirmation_id = await self._start_pending_confirmation()
        mock = self._patch_turns(self._turn(content="approved"))

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock.await_args.kwargs["tools"], [])

    async def test_rejection_executes_nothing(self):
        body, confirmation_id = await self._start_pending_confirmation()

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/reject"
        )

        self.assertEqual(response.status_code, 200, response.text)
        rejected = response.json()
        self.assertEqual(rejected["confirmation_status"], "rejected")
        self.assertIn("rejected", rejected["response"].lower())
        self.assertEqual(self.sensitive_tool.calls, 0)

        stored = await self._stored_confirmation(confirmation_id)
        self.assertEqual(stored.status, ToolConfirmationStatus.REJECTED)
        self.assertEqual(stored.arguments, {})

        messages = await self._messages(body["conversation_id"])
        kinds = [
            (item.get("message_metadata") or {}).get("kind")
            for item in messages
        ]
        self.assertEqual(
            kinds,
            [None, "tool_call", "tool_confirmation_required", None,
             "tool_rejected", None],
        )

    async def test_replay_returns_409_and_does_not_execute_twice(self):
        _body, confirmation_id = await self._start_pending_confirmation()
        self._patch_turns(self._turn(content="approved"))

        first = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )
        second = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(self.sensitive_tool.calls, 1)

    async def test_reject_after_approval_returns_409(self):
        _body, confirmation_id = await self._start_pending_confirmation()
        self._patch_turns(self._turn(content="approved"))

        first = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )
        second = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/reject"
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 409, second.text)

    async def test_cross_owner_approval_is_not_found(self):
        _body, confirmation_id = await self._start_pending_confirmation()
        self._as_user(self.bob_id)

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.sensitive_tool.calls, 0)

        stored = await self._stored_confirmation(confirmation_id)
        self.assertEqual(stored.status, ToolConfirmationStatus.PENDING)

    async def test_cross_owner_rejection_is_not_found(self):
        _body, confirmation_id = await self._start_pending_confirmation()
        self._as_user(self.bob_id)

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/reject"
        )

        self.assertEqual(response.status_code, 404, response.text)
        stored = await self._stored_confirmation(confirmation_id)
        self.assertEqual(stored.status, ToolConfirmationStatus.PENDING)

    async def test_expired_confirmation_returns_410_and_does_not_execute(self):
        _body, confirmation_id = await self._start_pending_confirmation()

        future = datetime.now(timezone.utc) + timedelta(
            seconds=settings.TOOL_CONFIRMATION_TTL_SECONDS + 60
        )
        clock = patch(
            "app.services.tool_confirmation_service._utcnow",
            return_value=future,
        )
        clock.start()
        self.addCleanup(clock.stop)

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )

        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(self.sensitive_tool.calls, 0)

    async def test_tampered_arguments_are_rejected(self):
        _body, confirmation_id = await self._start_pending_confirmation()

        async with self.SessionLocal() as session:
            result = await session.execute(
                select(ToolConfirmation).where(
                    ToolConfirmation.id == confirmation_id
                )
            )
            stored = result.scalar_one()
            stored.arguments = {"target": "evil"}
            await session.commit()

        response = await self.client.post(
            f"/api/chat/confirmations/{confirmation_id}/approve"
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.sensitive_tool.calls, 0)

    async def test_approving_a_missing_confirmation_is_not_found(self):
        response = await self.client.post(
            "/api/chat/confirmations/999999/approve"
        )

        self.assertEqual(response.status_code, 404, response.text)


# ---------------------------------------------------------------------------
# Persistence, history exclusion, and cancellation
# ---------------------------------------------------------------------------


class ChatPersistenceTests(ChatToolApiTestBase):

    async def test_tool_transcript_is_metadata_only_and_ordered(self):
        self._patch_turns(
            self._turn(
                calls=[
                    self._call("test_query", {"query": "needle-secret"})
                ]
            ),
            self._turn(content="final answer"),
        )

        response = await self._chat({"message": "search"})

        self.assertEqual(response.status_code, 200, response.text)
        conversation_id = response.json()["conversation_id"]
        messages = await self._messages(conversation_id)

        self.assertEqual(
            [item["role"] for item in messages],
            ["user", "assistant", "assistant", "assistant"],
        )
        self.assertEqual(
            [
                (item.get("message_metadata") or {}).get("kind")
                for item in messages
            ],
            [None, "tool_call", "tool_result", None],
        )
        self.assertEqual(messages[-1]["content"], "final answer")

        tool_metadata = json.dumps(
            [item.get("message_metadata") for item in messages]
        )
        self.assertNotIn("needle-secret", tool_metadata)
        self.assertNotIn("matches", tool_metadata)
        self.assertIn("test_query", tool_metadata)

    async def test_tool_transcript_is_excluded_from_later_prompts(self):
        self._patch_turns(
            self._turn(
                calls=[self._call("test_query", {"query": "earlier query"})]
            ),
            self._turn(content="first answer"),
        )

        first = await self._chat({"message": "first question"})
        self.assertEqual(first.status_code, 200, first.text)
        conversation_id = first.json()["conversation_id"]

        self.settings(TOOL_CALLING_ENABLED=False)
        text = self._patch_text_response("second answer")

        second = await self._chat(
            {
                "message": "second question",
                "conversation_id": conversation_id,
            }
        )

        self.assertEqual(second.status_code, 200, second.text)
        prompt = text.await_args.kwargs.get("prompt")

        if prompt is None:
            prompt = text.await_args.args[0]

        self.assertIn("first question", prompt)
        self.assertIn("first answer", prompt)
        self.assertNotIn("Requested tool call(s)", prompt)
        self.assertNotIn("completed successfully", prompt)

    async def test_caller_cancellation_propagates_from_the_orchestrator(self):
        started = asyncio.Event()

        async def hang(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self._patch_turns(
            self._turn(calls=[self._call("test_query", {"query": "hang"})])
        )

        with patch.object(
            orchestrator_module.tool_executor,
            "execute",
            new=hang,
        ):
            async with self.SessionLocal() as session:
                task = asyncio.create_task(
                    orchestrator_module.tool_orchestrator.run(
                        session=session,
                        user_id=self.alice_id,
                        conversation_id=None,
                        private=False,
                        prompt="cancel me",
                        complexity=None,
                    )
                )

                await asyncio.wait_for(started.wait(), timeout=2)
                task.cancel()

                with self.assertRaises(asyncio.CancelledError):
                    await task


if __name__ == "__main__":
    unittest.main()
