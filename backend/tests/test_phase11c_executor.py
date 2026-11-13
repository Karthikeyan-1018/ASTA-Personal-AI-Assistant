"""Phase 11C tests for the central tool execution engine.

Every test uses an isolated in-memory database and a fresh registry, so the
built-in tools and any other test module cannot affect the outcome. Tools are
mocked implementations only; no provider, network, or file-system call is made.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import app.tools.executor as executor_module
from app.llm.tool_calling import NormalizedToolCall
from app.services.tool_confirmation_service import tool_confirmation_service
from app.services.tool_history_service import tool_history_service
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import ToolExecutionError
from app.tools.executor import tool_executor
from app.tools.registry import ToolRegistry
from app.tools.result import REDACTION, success_result
from app.tools.schemas import StrictModel, ToolCall, ToolResult
from tests.tool_test_support import (
    EchoTool,
    EmptyInput,
    FailingTool,
    SlowTool,
    ToolEngineTestBase,
)


class ReversibleInput(StrictModel):
    target: str = "record"


class BlobInput(StrictModel):
    payload: str = ""


class ReversibleTool(BaseTool):
    name = "test_reversible"
    description = "A reversible action that always needs confirmation."
    category = ToolCategory.REVERSIBLE
    requires_confirmation = True
    input_model = ReversibleInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return success_result(
            tool_name=self.name,
            data={"target": arguments.target},
        )


class SensitiveTool(BaseTool):
    name = "test_sensitive"
    description = "A sensitive action that always needs confirmation."
    category = ToolCategory.SENSITIVE
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(tool_name=self.name, data={"ok": True})


class DisabledTool(BaseTool):
    name = "test_disabled"
    description = "A registered tool disabled by configuration."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    @property
    def enabled(self) -> bool:
        return False

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(tool_name=self.name, data={"ok": True})


class BlobTool(BaseTool):
    name = "test_blob"
    description = "Echo a caller-supplied string payload."
    category = ToolCategory.READ_ONLY
    input_model = BlobInput

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(
            tool_name=self.name,
            data={"payload": arguments.payload},
        )


class OversizedTool(BaseTool):
    name = "test_oversized"
    description = "Return a result larger than the configured limit."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        # Deliberately construct the result directly so the executor, not the
        # result helper, is the component under test.
        return ToolResult(
            success=True,
            data="x" * 5000,
            tool_name=self.name,
            status=ToolExecutionStatus.SUCCESS,
        )


class ContractViolationTool(BaseTool):
    name = "test_bad_contract"
    description = "Return an object that is not a ToolResult."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments):
        return {"not": "a ToolResult"}


class StructuredErrorTool(BaseTool):
    name = "test_structured_error"
    description = "Raise a structured tool exception."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        raise ToolExecutionError(
            "The provider failed with api_key=sk-live-abcdef1234567890"
        )


class SecretDataTool(BaseTool):
    name = "test_secret_data"
    description = "Return credential-shaped data."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(
            tool_name=self.name,
            data={
                "api_key": "sk-live-abcdef1234567890",
                "nested": {"password": "hunter2"},
                "items": [{"token": "abc123"}],
                "safe": "visible value",
            },
        )


class CancelTool(BaseTool):
    name = "test_cancel"
    description = "Raise CancelledError without caller cancellation."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        raise asyncio.CancelledError()


class ExecutorTestBase(ToolEngineTestBase):
    """Install a fresh registry and expose a few direct-call helpers."""

    def use_registry(self, *tools: BaseTool) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_all(tools)
        patcher = patch.object(executor_module, "tool_registry", registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        return registry

    async def direct(self, *, session, private: bool = False, **kwargs):
        context = ToolContext(
            session=session,
            user_id=kwargs.pop("user_id", self.alice_id),
            conversation_id=None,
            private=private,
        )

        return await tool_executor.execute(context=context, **kwargs)


class ExecutorCoreTests(ExecutorTestBase):

    async def test_successful_execution_returns_normalized_result(self):
        self.use_registry(EchoTool())

        result = await self.execute("test_echo", {"value": 7})

        self.assertTrue(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.tool_name, "test_echo")
        self.assertEqual(result.data, {"ok": True, "value": 7})
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error_message)
        self.assertIsNone(result.error_category)
        self.assertFalse(result.confirmation_required)
        self.assertIsNotNone(result.duration_ms)
        self.assertGreaterEqual(result.duration_ms, 0)
        self.assertGreater(result.result_bytes, 0)
        self.assertFalse(result.truncated)

    async def test_unknown_tool_is_a_structured_not_found_error(self):
        self.use_registry(EchoTool())

        result = await self.execute("missing_tool", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.NOT_FOUND)
        self.assertEqual(result.error_code, "ToolNotFoundError")
        self.assertEqual(result.tool_name, "missing_tool")

    async def test_disabled_tool_is_denied_before_validation(self):
        self.use_registry(DisabledTool())

        result = await self.execute("test_disabled", {"unexpected": True})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(result.error_category, ToolErrorCategory.DISABLED)
        self.assertNotIn("unexpected", result.error_message or "")

    async def test_invalid_arguments_are_rejected_without_echoing_values(self):
        self.use_registry(EchoTool())

        result = await self.execute("test_echo", {"value": "not-an-int"})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.VALIDATION)
        self.assertIn("value", result.error_message or "")
        self.assertNotIn("not-an-int", result.error_message or "")

    async def test_oversized_arguments_are_rejected(self):
        self.settings(TOOL_MAX_ARGUMENT_BYTES=50)
        self.use_registry(BlobTool())

        result = await self.execute("test_blob", {"payload": "x" * 500})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.SIZE_LIMIT)

    async def test_tool_exception_is_caught_and_not_leaked(self):
        self.use_registry(FailingTool())

        result = await self.execute("test_failing", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.INTERNAL)
        self.assertEqual(result.error_code, "ToolInternalError")
        self.assertNotIn("boom", result.error_message or "")
        self.assertNotIn("Traceback", result.error_message or "")

    async def test_structured_tool_error_is_mapped(self):
        self.use_registry(StructuredErrorTool())

        result = await self.execute("test_structured_error", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.EXECUTION)
        self.assertEqual(result.error_code, "ToolExecutionError")

    async def test_wrong_return_type_is_a_contract_violation(self):
        self.use_registry(ContractViolationTool())

        result = await self.execute("test_bad_contract", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.INTERNAL)
        self.assertEqual(result.error_code, "ToolContractViolation")

    async def test_timeout_returns_a_timeout_result(self):
        self.settings(TOOL_EXECUTION_TIMEOUT_SECONDS=0.05)
        self.use_registry(SlowTool())

        result = await self.execute("test_slow", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.TIMEOUT)
        self.assertEqual(result.error_category, ToolErrorCategory.TIMEOUT)
        self.assertEqual(result.error_code, "ToolTimeout")
        self.assertLess(result.duration_ms, 1000)

    async def test_oversized_result_is_truncated_and_bounded(self):
        self.settings(TOOL_MAX_RESULT_BYTES=200)
        self.use_registry(OversizedTool())

        result = await self.execute("test_oversized", {})

        self.assertTrue(result.success)
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.result_bytes, 200)
        self.assertIsInstance(result.data, dict)
        self.assertTrue(result.data.get("truncated"))

    async def test_tool_raised_cancellation_is_structured(self):
        self.use_registry(CancelTool())

        result = await self.execute("test_cancel", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.EXECUTION)
        self.assertEqual(result.error_code, "ToolCancelled")

    async def test_caller_cancellation_propagates(self):
        self.use_registry(SlowTool())

        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                private=False,
            )

            task = asyncio.create_task(
                tool_executor.execute(
                    tool_name="test_slow",
                    arguments={},
                    context=context,
                )
            )

            await asyncio.sleep(0.05)
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_multiple_calls_are_independent(self):
        self.use_registry(EchoTool())

        results = [
            await self.execute("test_echo", {"value": 1}),
            await self.execute("test_echo", {"value": 2}),
            await self.execute("test_echo", {"value": 3}),
        ]

        self.assertEqual(
            [result.data["value"] for result in results],
            [1, 2, 3],
        )
        self.assertTrue(all(result.success for result in results))

    async def test_mixed_success_and_failure_does_not_raise(self):
        self.use_registry(EchoTool(), FailingTool())

        results = [
            await self.execute("test_echo", {"value": 9}),
            await self.execute("unknown_tool", {}),
            await self.execute("test_failing", {}),
            await self.execute("test_echo", {"value": 10}),
        ]

        self.assertEqual(
            [result.status for result in results],
            [
                ToolExecutionStatus.SUCCESS,
                ToolExecutionStatus.ERROR,
                ToolExecutionStatus.ERROR,
                ToolExecutionStatus.SUCCESS,
            ],
        )
        self.assertTrue(results[0].success)
        self.assertTrue(results[3].success)
        self.assertEqual(results[0].data["value"], 9)
        self.assertEqual(results[3].data["value"], 10)

    async def test_history_failure_does_not_change_the_result(self):
        self.use_registry(EchoTool())

        with patch.object(
            tool_history_service,
            "record",
            new=AsyncMock(side_effect=RuntimeError("history unavailable")),
        ):
            result = await self.execute("test_echo", {"value": 5})

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"ok": True, "value": 5})


class ExecutorSecurityTests(ExecutorTestBase):

    async def test_missing_trusted_context_is_rejected(self):
        self.use_registry(EchoTool())

        result = await tool_executor.execute(
            tool_name="test_echo",
            arguments={},
            context=None,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.CONFIGURATION)
        self.assertEqual(result.error_code, "ToolConfigurationError")

    async def test_invalid_owner_identity_is_rejected(self):
        self.use_registry(EchoTool())

        async with self.SessionLocal() as session:
            result = await tool_executor.execute(
                tool_name="test_echo",
                arguments={},
                context=ToolContext(session=session, user_id=0),
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, ToolErrorCategory.CONFIGURATION)
        self.assertIn("owner identity", result.error_message or "")

    async def test_missing_session_is_rejected(self):
        self.use_registry(EchoTool())

        result = await tool_executor.execute(
            tool_name="test_echo",
            arguments={},
            context=ToolContext(session=None, user_id=self.alice_id),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, ToolErrorCategory.CONFIGURATION)
        self.assertIn("database session", result.error_message or "")

    async def test_private_request_blocks_non_private_tool(self):
        self.use_registry(EchoTool())

        async with self.SessionLocal() as session:
            result = await self.direct(
                session=session,
                private=True,
                tool_name="test_echo",
                arguments={},
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(result.error_category, ToolErrorCategory.PERMISSION)

    async def test_sensitive_tool_is_denied_by_default(self):
        self.settings(TOOL_ALLOW_SENSITIVE=False)
        self.use_registry(SensitiveTool())

        result = await self.execute("test_sensitive", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(result.error_category, ToolErrorCategory.PERMISSION)

    async def test_sensitive_tool_requires_confirmation_when_allowed(self):
        self.settings(TOOL_ALLOW_SENSITIVE=True)
        self.use_registry(SensitiveTool())

        result = await self.execute("test_sensitive", {})

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertIsNotNone(result.confirmation_id)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.PENDING,
        )

    async def test_credentials_in_tool_errors_are_redacted(self):
        self.use_registry(StructuredErrorTool())

        result = await self.execute("test_structured_error", {})

        message = result.error_message or ""
        self.assertNotIn("sk-live", message)
        self.assertIn(REDACTION, message)

    async def test_credentials_in_tool_data_are_redacted(self):
        self.use_registry(SecretDataTool())

        result = await self.execute("test_secret_data", {})

        self.assertTrue(result.success)
        self.assertEqual(result.data["api_key"], REDACTION)
        self.assertEqual(result.data["nested"]["password"], REDACTION)
        self.assertEqual(result.data["items"][0]["token"], REDACTION)
        self.assertEqual(result.data["safe"], "visible value")

        rendered = json.dumps(result.as_dict())
        self.assertNotIn("sk-live", rendered)
        self.assertNotIn("hunter2", rendered)
        self.assertNotIn("abc123", rendered)

    async def test_tool_cannot_supply_an_owner_identity(self):
        self.use_registry(EchoTool())

        with self.assertRaises(ValueError):
            ToolCall(
                call_id="call_1",
                name="test_echo",
                arguments={"value": 1},
                user_id=self.bob_id,
            )


class ExecutorConfirmationTests(ExecutorTestBase):

    async def _pending(self, arguments: dict, *, user_id=None):
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.create_pending(
                session=session,
                user_id=user_id or self.alice_id,
                conversation_id=None,
                tool_name="test_reversible",
                category=ToolCategory.REVERSIBLE,
                arguments=arguments,
                arguments_preview="target preview",
            )

    async def test_confirmation_is_created_without_executing(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        result = await self.execute("test_reversible", {"target": "alpha"})

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertIsNotNone(result.confirmation_id)
        self.assertEqual(tool.calls, 0)

        async with self.SessionLocal() as session:
            stored = await tool_confirmation_service.get(
                session=session,
                user_id=self.alice_id,
                confirmation_id=result.confirmation_id,
            )

        self.assertEqual(stored.arguments, {"target": "alpha"})
        self.assertIn("alpha", stored.arguments_preview)

    async def test_approved_confirmation_executes_once(self):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self._pending({"target": "alpha"})

        first = await self.execute(
            "test_reversible",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )
        second = await self.execute(
            "test_reversible",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )

        self.assertTrue(first.success)
        self.assertEqual(first.data, {"target": "alpha"})
        self.assertEqual(
            first.confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertEqual(tool.calls, 1)

        self.assertFalse(second.success)
        self.assertEqual(second.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            second.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 1)

    async def test_confirmation_rejects_changed_arguments(self):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self._pending({"target": "alpha"})

        result = await self.execute(
            "test_reversible",
            {"target": "beta"},
            confirmation_id=confirmation.id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 0)

    async def test_confirmation_is_owner_scoped(self):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self._pending({"target": "alpha"})

        result = await self.execute(
            "test_reversible",
            {"target": "alpha"},
            user_id=self.bob_id,
            confirmation_id=confirmation.id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 0)

    async def test_confirmation_id_on_an_unconfirmed_tool_is_rejected(self):
        self.use_registry(EchoTool())

        result = await self.execute(
            "test_echo",
            {"value": 1},
            confirmation_id=1,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, ToolErrorCategory.CONFIRMATION)

    async def test_disabled_confirmation_creation_is_reported(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        result = await self.execute(
            "test_reversible",
            {"target": "alpha"},
            create_confirmation=False,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertIsNone(result.confirmation_id)
        self.assertEqual(result.error_code, "ToolConfirmationRequiredError")
        self.assertEqual(tool.calls, 0)


class ExecutorCompatibilityTests(ExecutorTestBase):

    async def test_valid_normalized_call_executes(self):
        self.use_registry(EchoTool())
        call = NormalizedToolCall(
            id="call_1",
            name="test_echo",
            arguments={"value": 4},
        )

        async with self.SessionLocal() as session:
            context = await self.context(session)
            result = await tool_executor.execute_call(call, context=context)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"ok": True, "value": 4})

    async def test_public_tool_call_executes(self):
        self.use_registry(EchoTool())
        call = ToolCall(
            call_id="call_2",
            name="test_echo",
            arguments={"value": 6},
        )

        async with self.SessionLocal() as session:
            context = await self.context(session)
            result = await tool_executor.execute_call(call, context=context)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"ok": True, "value": 6})

    async def test_malformed_call_never_reaches_the_registry(self):
        self.use_registry(EchoTool())
        call = NormalizedToolCall(
            id="call_bad",
            name="",
            arguments={"value": 1},
            error="The provider returned invalid JSON arguments.",
        )

        async with self.SessionLocal() as session:
            context = await self.context(session)
            result = await tool_executor.execute_call(call, context=context)

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.MALFORMED_CALL,
        )
        self.assertEqual(result.error_code, "MalformedToolCall")

    async def test_non_mapping_arguments_are_not_trusted(self):
        self.use_registry(BlobTool())
        call = NormalizedToolCall(
            id="call_3",
            name="test_blob",
            arguments={"payload": "safe"},
        )
        call.arguments = ["not", "a", "mapping"]

        async with self.SessionLocal() as session:
            context = await self.context(session)
            result = await tool_executor.execute_call(call, context=context)

        # A non-mapping payload is a malformed call and must never execute.
        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.MALFORMED_CALL,
        )
        self.assertEqual(result.error_code, "MalformedToolCall")

    async def test_tool_call_rejects_extra_fields(self):
        with self.assertRaises(ValueError):
            ToolCall(
                name="test_echo",
                arguments={},
                private=True,
            )

    async def test_execute_call_does_not_mutate_the_provider_call(self):
        self.use_registry(EchoTool())
        call = NormalizedToolCall(
            id="call_4",
            name="test_echo",
            arguments={"value": 8},
        )

        async with self.SessionLocal() as session:
            context = await self.context(session)
            await tool_executor.execute_call(call, context=context)

        self.assertEqual(call.name, "test_echo")
        self.assertEqual(call.arguments, {"value": 8})
        self.assertIsNone(call.error)
        self.assertTrue(call.is_valid)
        self.assertIsInstance(call.to_tool_call(), ToolCall)


class ExecutorHistoryTests(ExecutorTestBase):

    async def test_history_records_metadata_only(self):
        self.settings(TOOL_HISTORY_ENABLED=True)
        self.use_registry(EchoTool())

        result = await self.execute("test_echo", {"value": 42})
        self.assertTrue(result.success)

        async with self.SessionLocal() as session:
            rows, total = await tool_history_service.list_executions(
                session,
                user_id=self.alice_id,
            )

        self.assertEqual(total, 1)
        row = rows[0]
        self.assertEqual(row.tool_name, "test_echo")
        self.assertEqual(row.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(row.confirmation_status, ConfirmationState.NOT_REQUIRED)
        self.assertEqual(row.argument_keys, ["value"])
        self.assertIsNotNone(row.arguments_hash)
        self.assertNotIn("42", row.arguments_hash or "")

    async def test_history_is_owner_scoped(self):
        self.settings(TOOL_HISTORY_ENABLED=True)
        self.use_registry(EchoTool())

        await self.execute("test_echo", {"value": 1})
        await self.execute("test_echo", {"value": 2}, user_id=self.bob_id)

        async with self.SessionLocal() as session:
            alice_rows, alice_total = await tool_history_service.list_executions(
                session,
                user_id=self.alice_id,
            )
            bob_rows, bob_total = await tool_history_service.list_executions(
                session,
                user_id=self.bob_id,
            )

        self.assertEqual(alice_total, 1)
        self.assertEqual(bob_total, 1)
        self.assertEqual(alice_rows[0].user_id, self.alice_id)
        self.assertEqual(bob_rows[0].user_id, self.bob_id)


if __name__ == "__main__":
    unittest.main()
