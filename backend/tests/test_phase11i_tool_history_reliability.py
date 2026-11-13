"""Phase 11I tests for tool history, reliability, and recovery.

Coverage:

* lifecycle state mapping and legal/illegal transitions;
* two-phase history persistence, timestamps, duration, retries, truncation;
* success, failure, timeout, tool-raised cancellation, caller cancellation;
* owner isolation and cross-owner API access;
* bounded retries for idempotent read-only tools only;
* no retry for non-idempotent, non-transient, or confirmation-bound tools;
* idempotency replay, duplicate detection, and owner scoping;
* restart recovery of stale in-flight rows;
* confirmation approval/rejection regression and metrics;
* sensitive metadata redaction in history and APIs;
* provider failure metrics through the real chat route;
* authentication and filters on the history APIs.

Every database is an isolated in-memory SQLite instance. Providers and tools are
controlled doubles; no network, filesystem, or model call is made.
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
from app.database.connection import get_db
from app.llm.llm_router import llm_router
from app.main import app
from app.models.tool_execution import ToolConfirmation, ToolExecution
from app.services.tool_history_service import (
    DuplicateExecutionError,
    tool_history_service,
)
from app.services.tool_metrics_service import tool_metrics_service
from app.tools.base import BaseTool
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionLifecycle,
    ToolExecutionStatus,
)
from app.tools.executor import tool_executor
from app.tools.lifecycle import (
    LifecycleTransitionError,
    can_transition,
    is_terminal,
    lifecycle_from_status,
    validate_transition,
)
from app.tools.registry import ToolRegistry
from app.tools.result import error_result, success_result
from app.tools.retry import MAX_RETRIES_CEILING, max_retries
from app.tools.schemas import StrictModel, ToolResult
from tests.tool_test_support import (
    EmptyInput,
    ToolEngineTestBase,
)


class ValueInput(StrictModel):
    value: int = 1


class SecretInput(StrictModel):
    secret_token: str = ""
    visible: str = "ok"


class SensitiveInput(StrictModel):
    target: str = "record"


class IdempotentEchoTool(BaseTool):
    name = "test_idempotent_echo"
    description = "A read-only tool that may be retried."
    category = ToolCategory.READ_ONLY
    input_model = ValueInput
    idempotent = True

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return success_result(
            tool_name=self.name,
            data={"value": arguments.value},
        )


class NonIdempotentTool(BaseTool):
    name = "test_non_idempotent"
    description = "A read-only tool that must never retry."
    category = ToolCategory.READ_ONLY
    input_model = ValueInput
    idempotent = False

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return error_result(
            tool_name=self.name,
            code="ToolTimeout",
            message="transient timeout",
            category=ToolErrorCategory.TIMEOUT,
            status=ToolExecutionStatus.TIMEOUT,
        )


class FlakyIdempotentTool(BaseTool):
    name = "test_flaky_idempotent"
    description = "Fail transiently a bounded number of times."
    category = ToolCategory.READ_ONLY
    input_model = ValueInput
    idempotent = True

    def __init__(self, failures: int = 2) -> None:
        self.failures = failures
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1

        if self.calls <= self.failures:
            return error_result(
                tool_name=self.name,
                code="ToolTimeout",
                message="transient timeout",
                category=ToolErrorCategory.TIMEOUT,
                status=ToolExecutionStatus.TIMEOUT,
            )

        return success_result(
            tool_name=self.name,
            data={"attempts": self.calls},
        )


class UnexpectedFailureIdempotentTool(BaseTool):
    name = "test_unexpected_idempotent"
    description = "Idempotent but fails non-transiently."
    category = ToolCategory.READ_ONLY
    input_model = ValueInput
    idempotent = True

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        raise RuntimeError("boom")


class CancellingTool(BaseTool):
    name = "test_cancelling"
    description = "Raise CancelledError without caller cancellation."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        raise asyncio.CancelledError()


class BlockingTool(BaseTool):
    name = "test_blocking"
    description = "Wait long enough for a caller to cancel."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        await asyncio.sleep(10)
        return success_result(tool_name=self.name, data={})


class TruncatingTool(BaseTool):
    name = "test_truncating"
    description = "Return a payload larger than the result limit."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(
            tool_name=self.name,
            data={"blob": "x" * 5000},
        )


class SecretEchoTool(BaseTool):
    name = "test_secret_echo"
    description = "Echo a caller-supplied value for redaction checks."
    category = ToolCategory.READ_ONLY
    input_model = SecretInput

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(
            tool_name=self.name,
            data={"visible": arguments.visible},
        )


class FlakySensitiveTool(BaseTool):
    name = "test_flaky_sensitive"
    description = "A confirmation-bound tool that never auto-retries."
    category = ToolCategory.SENSITIVE
    requires_confirmation = True
    input_model = SensitiveInput
    idempotent = True

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> ToolResult:
        self.calls += 1
        return error_result(
            tool_name=self.name,
            code="ToolTimeout",
            message="transient timeout",
            category=ToolErrorCategory.TIMEOUT,
            status=ToolExecutionStatus.TIMEOUT,
        )


class SensitiveTool(BaseTool):
    name = "test_sensitive_11i"
    description = "A sensitive tool that needs approval."
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


class SlowTool(BaseTool):
    name = "test_slow_11i"
    description = "Sleep longer than the configured timeout."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        await asyncio.sleep(2)
        return success_result(tool_name=self.name, data={})


class FailingTool(BaseTool):
    name = "test_failing_11i"
    description = "Raise an unexpected error."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Lifecycle unit coverage
# ---------------------------------------------------------------------------


class LifecycleTests(unittest.TestCase):

    def test_expected_transitions_are_legal(self):
        pairs = [
            (
                ToolExecutionLifecycle.PENDING,
                ToolExecutionLifecycle.AWAITING_CONFIRMATION,
            ),
            (
                ToolExecutionLifecycle.PENDING,
                ToolExecutionLifecycle.RUNNING,
            ),
            (
                ToolExecutionLifecycle.RUNNING,
                ToolExecutionLifecycle.SUCCEEDED,
            ),
            (
                ToolExecutionLifecycle.RUNNING,
                ToolExecutionLifecycle.FAILED,
            ),
            (
                ToolExecutionLifecycle.RUNNING,
                ToolExecutionLifecycle.TIMED_OUT,
            ),
            (
                ToolExecutionLifecycle.RUNNING,
                ToolExecutionLifecycle.CANCELLED,
            ),
            (
                ToolExecutionLifecycle.AWAITING_CONFIRMATION,
                ToolExecutionLifecycle.APPROVED,
            ),
            (
                ToolExecutionLifecycle.APPROVED,
                ToolExecutionLifecycle.RUNNING,
            ),
            (
                ToolExecutionLifecycle.APPROVED,
                ToolExecutionLifecycle.REJECTED,
            ),
        ]

        for current, requested in pairs:
            with self.subTest(current=current, requested=requested):
                self.assertTrue(can_transition(current, requested))
                self.assertEqual(
                    validate_transition(current, requested),
                    requested,
                )

    def test_illegal_transitions_are_rejected(self):
        pairs = [
            (
                ToolExecutionLifecycle.SUCCEEDED,
                ToolExecutionLifecycle.FAILED,
            ),
            (
                ToolExecutionLifecycle.FAILED,
                ToolExecutionLifecycle.SUCCEEDED,
            ),
            (
                ToolExecutionLifecycle.RUNNING,
                ToolExecutionLifecycle.PENDING,
            ),
            (
                ToolExecutionLifecycle.AWAITING_CONFIRMATION,
                ToolExecutionLifecycle.RUNNING,
            ),
            (
                ToolExecutionLifecycle.CANCELLED,
                ToolExecutionLifecycle.SUCCEEDED,
            ),
        ]

        for current, requested in pairs:
            with self.subTest(current=current, requested=requested):
                self.assertFalse(can_transition(current, requested))

                with self.assertRaises(LifecycleTransitionError):
                    validate_transition(current, requested)

    def test_same_state_is_idempotent(self):
        self.assertTrue(
            can_transition(
                ToolExecutionLifecycle.SUCCEEDED,
                ToolExecutionLifecycle.SUCCEEDED,
            )
        )
        self.assertFalse(
            can_transition(
                ToolExecutionLifecycle.SUCCEEDED,
                ToolExecutionLifecycle.SUCCEEDED,
                allow_same=False,
            )
        )

    def test_terminal_states_are_reported(self):
        for state in (
            ToolExecutionLifecycle.SUCCEEDED,
            ToolExecutionLifecycle.FAILED,
            ToolExecutionLifecycle.REJECTED,
            ToolExecutionLifecycle.EXPIRED,
            ToolExecutionLifecycle.CANCELLED,
            ToolExecutionLifecycle.TIMED_OUT,
        ):
            self.assertTrue(is_terminal(state))

        for state in (
            ToolExecutionLifecycle.PENDING,
            ToolExecutionLifecycle.AWAITING_CONFIRMATION,
            ToolExecutionLifecycle.APPROVED,
            ToolExecutionLifecycle.RUNNING,
        ):
            self.assertFalse(is_terminal(state))

    def test_status_mapping(self):
        cases = {
            ToolExecutionStatus.SUCCESS: ToolExecutionLifecycle.SUCCEEDED,
            ToolExecutionStatus.TIMEOUT: ToolExecutionLifecycle.TIMED_OUT,
            ToolExecutionStatus.DENIED: ToolExecutionLifecycle.REJECTED,
            ToolExecutionStatus.ERROR: ToolExecutionLifecycle.FAILED,
            ToolExecutionStatus.CANCELLED: ToolExecutionLifecycle.CANCELLED,
            ToolExecutionStatus.CONFIRMATION_REQUIRED: (
                ToolExecutionLifecycle.AWAITING_CONFIRMATION
            ),
        }

        for status, expected in cases.items():
            self.assertEqual(lifecycle_from_status(status), expected)

        self.assertEqual(
            lifecycle_from_status(
                ToolExecutionStatus.DENIED,
                confirmation_state=ConfirmationState.EXPIRED,
            ),
            ToolExecutionLifecycle.EXPIRED,
        )
        self.assertEqual(
            lifecycle_from_status(
                ToolExecutionStatus.ERROR,
                error_code="ToolCancelled",
            ),
            ToolExecutionLifecycle.CANCELLED,
        )


class RetryPolicyUnitTests(unittest.TestCase):

    def test_bounded_retry_count(self):
        with patch.object(
            executor_module.settings,
            "TOOL_RETRY_ENABLED",
            True,
        ):
            with patch.object(
                executor_module.settings,
                "TOOL_MAX_RETRIES",
                99,
            ):
                self.assertEqual(max_retries(), MAX_RETRIES_CEILING)

    def test_retries_disabled_by_default(self):
        with patch.object(
            executor_module.settings,
            "TOOL_RETRY_ENABLED",
            False,
        ):
            self.assertEqual(max_retries(), 0)


# ---------------------------------------------------------------------------
# History and reliability service coverage
# ---------------------------------------------------------------------------


class HistoryReliabilityTestBase(ToolEngineTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        tool_metrics_service.reset()
        self.settings(
            TOOL_HISTORY_ENABLED=True,
            TOOL_RETRY_ENABLED=False,
            TOOL_MAX_RETRIES=1,
            TOOL_RETRY_BACKOFF_MS=0,
            TOOL_EXECUTION_TIMEOUT_SECONDS=2,
        )

        self.registry = ToolRegistry()
        self.registry.register_all(
            [
                IdempotentEchoTool(),
                NonIdempotentTool(),
                FlakyIdempotentTool(),
                UnexpectedFailureIdempotentTool(),
                CancellingTool(),
                BlockingTool(),
                TruncatingTool(),
                SecretEchoTool(),
                FlakySensitiveTool(),
                SensitiveTool(),
                SlowTool(),
                FailingTool(),
            ]
        )

        patcher = patch.object(
            executor_module,
            "tool_registry",
            self.registry,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def rows(self, user_id=None):
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(ToolExecution)
                .where(ToolExecution.user_id == (user_id or self.alice_id))
                .order_by(ToolExecution.id)
            )
            return list(result.scalars().all())


class HistoryPersistenceTests(HistoryReliabilityTestBase):

    async def test_success_persists_full_lifecycle_metadata(self):
        result = await self.execute("test_idempotent_echo", {"value": 7})

        self.assertTrue(result.success)
        self.assertIsNotNone(result.execution_id)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.id, result.execution_id)
        self.assertEqual(row.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(
            row.lifecycle_status,
            ToolExecutionLifecycle.SUCCEEDED,
        )
        self.assertTrue(row.success)
        self.assertIsNotNone(row.started_at)
        self.assertIsNotNone(row.completed_at)
        self.assertIsNotNone(row.duration_ms)
        self.assertEqual(row.retry_count, 0)
        self.assertFalse(row.truncated)
        self.assertEqual(row.argument_keys, ["value"])
        self.assertIsNotNone(row.arguments_hash)

    async def test_failure_persists_failed_lifecycle(self):
        result = await self.execute("test_failing_11i", {})

        self.assertFalse(result.success)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, ToolExecutionStatus.ERROR)
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.FAILED,
        )
        self.assertEqual(rows[0].error_code, "ToolInternalError")

    async def test_timeout_persists_timed_out_lifecycle(self):
        self.settings(TOOL_EXECUTION_TIMEOUT_SECONDS=0.05)

        result = await self.execute("test_slow_11i", {})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.TIMEOUT)

        rows = await self.rows()
        self.assertEqual(rows[0].status, ToolExecutionStatus.TIMEOUT)
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.TIMED_OUT,
        )

    async def test_tool_raised_cancellation_is_cancelled_lifecycle(self):
        result = await self.execute("test_cancelling", {})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "ToolCancelled")

        rows = await self.rows()
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.CANCELLED,
        )

    async def test_caller_cancellation_propagates_and_is_recorded(self):
        async with self.SessionLocal() as session:
            context = await self.context(session)
            task = asyncio.create_task(
                tool_executor.execute(
                    tool_name="test_blocking",
                    arguments={},
                    context=context,
                )
            )

            await asyncio.sleep(0.15)
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, ToolExecutionStatus.CANCELLED)
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.CANCELLED,
        )
        self.assertEqual(
            rows[0].error_code,
            "ToolExecutionCancelled",
        )

    async def test_truncated_result_is_recorded(self):
        self.settings(TOOL_MAX_RESULT_BYTES=100)

        result = await self.execute("test_truncating", {})

        self.assertTrue(result.success)
        self.assertTrue(result.truncated)

        rows = await self.rows()
        self.assertTrue(rows[0].truncated)

    async def test_history_disabled_keeps_execution_result(self):
        self.settings(TOOL_HISTORY_ENABLED=False)

        result = await self.execute("test_idempotent_echo", {"value": 1})

        self.assertTrue(result.success)
        self.assertIsNone(result.execution_id)
        self.assertEqual(await self.rows(), [])

    async def test_owner_isolation_at_the_service_layer(self):
        alice = await self.execute("test_idempotent_echo", {"value": 1})
        await self.execute(
            "test_idempotent_echo",
            {"value": 2},
            user_id=self.bob_id,
        )

        async with self.SessionLocal() as session:
            alice_row = await tool_history_service.get_execution(
                session,
                user_id=self.alice_id,
                execution_id=alice.execution_id,
            )
            bob_view = await tool_history_service.get_execution(
                session,
                user_id=self.bob_id,
                execution_id=alice.execution_id,
            )

        self.assertIsNotNone(alice_row)
        self.assertIsNone(bob_view)

    async def test_terminal_outcome_is_not_overwritten(self):
        result = await self.execute("test_failing_11i", {})
        execution_id = result.execution_id

        async with self.SessionLocal() as session:
            await tool_history_service.record(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="test_failing_11i",
                category=ToolCategory.READ_ONLY,
                status=ToolExecutionStatus.SUCCESS,
                execution_id=execution_id,
            )

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, ToolExecutionStatus.ERROR)
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.FAILED,
        )


class RetryBehaviorTests(HistoryReliabilityTestBase):

    async def test_idempotent_tool_is_retried_within_the_limit(self):
        self.settings(
            TOOL_RETRY_ENABLED=True,
            TOOL_MAX_RETRIES=2,
        )

        result = await self.execute(
            "test_flaky_idempotent",
            {"value": 1},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.retry_count, 2)

        tool = self.registry.get("test_flaky_idempotent")
        self.assertEqual(tool.calls, 3)

        rows = await self.rows()
        self.assertEqual(rows[0].retry_count, 2)

        snapshot = tool_metrics_service.snapshot()
        self.assertEqual(snapshot["retry_total"], 2)

    async def test_non_idempotent_tool_is_never_retried(self):
        self.settings(
            TOOL_RETRY_ENABLED=True,
            TOOL_MAX_RETRIES=3,
        )

        result = await self.execute("test_non_idempotent", {"value": 1})

        self.assertFalse(result.success)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(self.registry.get("test_non_idempotent").calls, 1)

    async def test_non_transient_failure_is_not_retried(self):
        self.settings(
            TOOL_RETRY_ENABLED=True,
            TOOL_MAX_RETRIES=3,
        )

        result = await self.execute(
            "test_unexpected_idempotent",
            {"value": 1},
        )

        self.assertFalse(result.success)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(
            self.registry.get("test_unexpected_idempotent").calls,
            1,
        )

    async def test_retry_ceiling_bounds_total_attempts(self):
        self.settings(
            TOOL_RETRY_ENABLED=True,
            TOOL_MAX_RETRIES=99,
        )

        tool = self.registry.get("test_flaky_idempotent")
        tool.failures = 99

        result = await self.execute(
            "test_flaky_idempotent",
            {"value": 1},
        )

        self.assertFalse(result.success)
        self.assertEqual(tool.calls, 1 + MAX_RETRIES_CEILING)
        self.assertEqual(result.retry_count, MAX_RETRIES_CEILING)

    async def test_confirmation_bound_tool_is_not_retried(self):
        self.settings(
            TOOL_ALLOW_SENSITIVE=True,
            TOOL_RETRY_ENABLED=True,
            TOOL_MAX_RETRIES=3,
        )

        first = await self.execute(
            "test_flaky_sensitive",
            {"target": "alpha"},
        )
        self.assertTrue(first.confirmation_required)

        tool = self.registry.get("test_flaky_sensitive")
        self.assertEqual(tool.calls, 0)

        second = await self.execute(
            "test_flaky_sensitive",
            {"target": "alpha"},
            confirmation_id=first.confirmation_id,
        )

        self.assertFalse(second.success)
        self.assertEqual(second.retry_count, 0)
        self.assertEqual(tool.calls, 1)


class IdempotencyTests(HistoryReliabilityTestBase):

    async def test_duplicate_request_returns_metadata_only_replay(self):
        first = await self.execute(
            "test_idempotent_echo",
            {"value": 11},
            idempotency_key="request-1",
        )
        second = await self.execute(
            "test_idempotent_echo",
            {"value": 11},
            idempotency_key="request-1",
        )

        self.assertTrue(first.success)
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(second.execution_id, first.execution_id)
        self.assertIsNone(second.data)
        self.assertEqual(
            self.registry.get("test_idempotent_echo").calls,
            1,
        )
        self.assertEqual(len(await self.rows()), 1)
        self.assertEqual(
            tool_metrics_service.snapshot()["duplicate_prevented_total"],
            1,
        )

    async def test_in_flight_duplicate_is_not_executed(self):
        async with self.SessionLocal() as session:
            record = await tool_history_service.begin(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="test_idempotent_echo",
                category=ToolCategory.READ_ONLY,
                idempotency_key="in-flight",
            )

        self.assertIsNotNone(record)

        result = await self.execute(
            "test_idempotent_echo",
            {"value": 1},
            idempotency_key="in-flight",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "ToolExecutionInProgress")
        self.assertTrue(result.idempotent_replay)
        self.assertIsNone(result.data)
        self.assertEqual(
            self.registry.get("test_idempotent_echo").calls,
            0,
        )

    async def test_idempotency_is_owner_scoped(self):
        await self.execute(
            "test_idempotent_echo",
            {"value": 1},
            idempotency_key="same-key",
        )
        second = await self.execute(
            "test_idempotent_echo",
            {"value": 2},
            user_id=self.bob_id,
            idempotency_key="same-key",
        )

        self.assertTrue(second.success)
        self.assertFalse(second.idempotent_replay)
        self.assertEqual(
            self.registry.get("test_idempotent_echo").calls,
            2,
        )
        self.assertEqual(len(await self.rows(self.alice_id)), 1)
        self.assertEqual(len(await self.rows(self.bob_id)), 1)

    async def test_duplicate_begin_raises(self):
        async with self.SessionLocal() as session:
            await tool_history_service.begin(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="test_idempotent_echo",
                category=ToolCategory.READ_ONLY,
                idempotency_key="collision",
            )

            with self.assertRaises(DuplicateExecutionError):
                await tool_history_service.begin(
                    session=session,
                    user_id=self.alice_id,
                    conversation_id=None,
                    tool_name="test_idempotent_echo",
                    category=ToolCategory.READ_ONLY,
                    idempotency_key="collision",
                )

    async def test_blank_idempotency_key_is_ignored(self):
        first = await self.execute(
            "test_idempotent_echo",
            {"value": 1},
            idempotency_key="   ",
        )
        second = await self.execute(
            "test_idempotent_echo",
            {"value": 1},
            idempotency_key="   ",
        )

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertFalse(second.idempotent_replay)
        self.assertEqual(
            self.registry.get("test_idempotent_echo").calls,
            2,
        )


class RecoveryTests(HistoryReliabilityTestBase):

    async def test_stale_running_execution_is_recovered(self):
        old = datetime.now(timezone.utc) - timedelta(hours=2)

        async with self.SessionLocal() as session:
            record = await tool_history_service.begin(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="test_idempotent_echo",
                category=ToolCategory.READ_ONLY,
                lifecycle_status=ToolExecutionLifecycle.RUNNING,
                status=ToolExecutionStatus.RUNNING,
                started_at=old,
            )

            recovered = await tool_history_service.recover_interrupted(
                session=session,
                older_than_seconds=60,
            )

        self.assertEqual(recovered, 1)

        rows = await self.rows()
        row = next(item for item in rows if item.id == record.id)
        self.assertEqual(row.lifecycle_status, ToolExecutionLifecycle.CANCELLED)
        self.assertEqual(row.status, ToolExecutionStatus.CANCELLED)
        self.assertEqual(row.error_code, "InterruptedExecution")
        self.assertIsNotNone(row.completed_at)

    async def test_recent_execution_is_not_recovered(self):
        async with self.SessionLocal() as session:
            await tool_history_service.begin(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="test_idempotent_echo",
                category=ToolCategory.READ_ONLY,
                lifecycle_status=ToolExecutionLifecycle.RUNNING,
                status=ToolExecutionStatus.RUNNING,
            )

            recovered = await tool_history_service.recover_interrupted(
                session=session,
                older_than_seconds=3600,
            )

        self.assertEqual(recovered, 0)

    async def test_awaiting_confirmation_is_not_recovered(self):
        old = datetime.now(timezone.utc) - timedelta(hours=2)

        async with self.SessionLocal() as session:
            record = await tool_history_service.begin(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="test_sensitive_11i",
                category=ToolCategory.SENSITIVE,
                lifecycle_status=(
                    ToolExecutionLifecycle.AWAITING_CONFIRMATION
                ),
                status=ToolExecutionStatus.CONFIRMATION_REQUIRED,
                started_at=old,
            )

            recovered = await tool_history_service.recover_interrupted(
                session=session,
                older_than_seconds=0,
            )

        self.assertEqual(recovered, 0)

        rows = await self.rows()
        row = next(item for item in rows if item.id == record.id)
        self.assertEqual(
            row.lifecycle_status,
            ToolExecutionLifecycle.AWAITING_CONFIRMATION,
        )


class SensitiveMetadataTests(HistoryReliabilityTestBase):

    async def test_history_stores_no_raw_arguments_or_payloads(self):
        result = await self.execute(
            "test_secret_echo",
            {"secret_token": "supersecretvalue", "visible": "hello"},
        )

        rows = await self.rows()
        row = rows[0]

        self.assertEqual(sorted(row.argument_keys or []), sorted(
            ["secret_token", "visible"]
        ))
        self.assertNotIn("supersecretvalue", row.arguments_hash or "")

        column_names = set(ToolExecution.__table__.columns.keys())
        for forbidden in (
            "arguments",
            "data",
            "result",
            "raw_output",
            "prompt",
            "source",
        ):
            self.assertNotIn(forbidden, column_names)

        serialized = json.dumps(
            {
                "tool": row.tool_name,
                "error": row.error_code,
                "keys": row.argument_keys,
                "hash": row.arguments_hash,
            }
        )
        self.assertNotIn("supersecretvalue", serialized)
        self.assertTrue(result.success)

    async def test_metrics_contain_no_payload_values(self):
        await self.execute(
            "test_secret_echo",
            {"secret_token": "supersecretvalue", "visible": "hello"},
        )

        serialized = json.dumps(tool_metrics_service.snapshot())
        self.assertNotIn("supersecretvalue", serialized)
        self.assertNotIn("hello", serialized)


# ---------------------------------------------------------------------------
# API coverage
# ---------------------------------------------------------------------------


class ToolHistoryApiTestBase(HistoryReliabilityTestBase):

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
        )

        patcher = patch.object(
            orchestrator_module,
            "tool_registry",
            self.registry,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await super().asyncTearDown()

    def _as_user(self, user_id: int) -> None:
        self._current_user = user_id

    async def _execute_api(self, tool, arguments=None, headers=None):
        return await self.client.post(
            "/api/tools/execute",
            json={"tool": tool, "arguments": arguments or {}},
            headers=headers or {},
        )

    async def _latest_execution_id(self, tool):
        response = await self.client.get(
            "/api/tools/executions",
            params={"tool": tool},
        )
        self.assertEqual(response.status_code, 200, response.text)
        executions = response.json()["executions"]
        self.assertTrue(executions)
        return executions[0]["id"]


class ToolHistoryApiTests(ToolHistoryApiTestBase):

    async def test_history_endpoints_require_authentication(self):
        saved = app.dependency_overrides.pop(get_current_user_id)

        try:
            response = await self.client.get("/api/tools/executions")

        finally:
            app.dependency_overrides[get_current_user_id] = saved

        self.assertEqual(response.status_code, 401)

    async def test_owner_scoped_list_and_detail(self):
        await self.execute("test_idempotent_echo", {"value": 1})
        execution_id = await self._latest_execution_id(
            "test_idempotent_echo"
        )

        detail = await self.client.get(
            f"/api/tools/executions/{execution_id}"
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertEqual(body["id"], execution_id)
        self.assertEqual(body["lifecycle_status"], "succeeded")
        self.assertTrue(body["success"])
        self.assertIsNotNone(body["started_at"])
        self.assertIsNotNone(body["completed_at"])

        self._as_user(self.bob_id)
        cross_owner = await self.client.get(
            f"/api/tools/executions/{execution_id}"
        )
        self.assertEqual(cross_owner.status_code, 404)

        bob_list = await self.client.get("/api/tools/executions")
        self.assertEqual(bob_list.status_code, 200)
        self.assertEqual(bob_list.json()["total"], 0)

    async def test_status_and_summary_are_safe_metadata(self):
        await self.execute(
            "test_secret_echo",
            {"secret_token": "supersecretvalue", "visible": "hello"},
        )
        execution_id = await self._latest_execution_id("test_secret_echo")

        status_response = await self.client.get(
            f"/api/tools/executions/{execution_id}/status"
        )
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()
        self.assertEqual(status_body["status"], "success")
        self.assertEqual(status_body["lifecycle_status"], "succeeded")
        self.assertNotIn("data", status_body)
        self.assertNotIn("arguments", status_body)

        summary_response = await self.client.get(
            f"/api/tools/executions/{execution_id}/summary"
        )
        self.assertEqual(summary_response.status_code, 200)
        summary_body = summary_response.json()
        self.assertEqual(summary_body["id"], execution_id)
        self.assertNotIn("data", summary_body)
        self.assertNotIn("argument_keys", summary_body)

        self.assertNotIn(
            "supersecretvalue",
            status_response.text + summary_response.text,
        )

    async def test_execution_status_and_summary_are_owner_scoped(self):
        await self.execute("test_idempotent_echo", {"value": 1})
        execution_id = await self._latest_execution_id(
            "test_idempotent_echo"
        )

        self._as_user(self.bob_id)

        for suffix in ("status", "summary"):
            response = await self.client.get(
                f"/api/tools/executions/{execution_id}/{suffix}"
            )
            self.assertEqual(response.status_code, 404)

    async def test_lifecycle_and_status_filters(self):
        await self.execute("test_failing_11i", {})
        await self.execute("test_idempotent_echo", {"value": 1})

        response = await self.client.get(
            "/api/tools/executions",
            params={"lifecycle": "failed"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(
            response.json()["executions"][0]["lifecycle_status"],
            "failed",
        )

        response = await self.client.get(
            "/api/tools/executions",
            params={"status": "success"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

        bad = await self.client.get(
            "/api/tools/executions",
            params={"lifecycle": "not-a-state"},
        )
        self.assertEqual(bad.status_code, 400)

        bad_status = await self.client.get(
            "/api/tools/executions",
            params={"status": "not-a-status"},
        )
        self.assertEqual(bad_status.status_code, 400)

    async def test_stats_include_lifecycle_and_retry_metadata(self):
        self.settings(
            TOOL_RETRY_ENABLED=True,
            TOOL_MAX_RETRIES=1,
        )

        # Fail once, then succeed on the single permitted retry.
        self.registry.get("test_flaky_idempotent").failures = 1

        await self.execute("test_flaky_idempotent", {"value": 1})
        await self.execute("test_failing_11i", {})

        response = await self.client.get("/api/tools/executions/stats")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["success_count"], 1)
        self.assertEqual(body["failure_count"], 1)
        self.assertEqual(body["retry_count"], 1)
        self.assertEqual(body["by_lifecycle"]["succeeded"], 1)
        self.assertEqual(body["by_lifecycle"]["failed"], 1)

    async def test_metrics_endpoint_reports_process_counters(self):
        await self.execute("test_idempotent_echo", {"value": 1})
        await self.execute("test_failing_11i", {})

        response = await self.client.get("/api/tools/metrics")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertGreaterEqual(body["executions_total"], 2)
        self.assertGreaterEqual(body["success_total"], 1)
        self.assertGreaterEqual(body["failure_total"], 1)
        self.assertEqual(
            set(body["by_tool"]),
            {"test_idempotent_echo", "test_failing_11i"},
        )
        self.assertNotIn("supersecret", response.text)

    async def test_idempotency_header_replays_metadata_only(self):
        first = await self._execute_api(
            "test_idempotent_echo",
            {"value": 4},
            headers={"Idempotency-Key": "api-key-1"},
        )
        second = await self._execute_api(
            "test_idempotent_echo",
            {"value": 4},
            headers={"Idempotency-Key": "api-key-1"},
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        first_body = first.json()
        second_body = second.json()

        self.assertTrue(first_body["success"])
        self.assertTrue(second_body["idempotent_replay"])
        self.assertIsNone(second_body["data"])
        self.assertEqual(
            second_body["execution_id"],
            first_body["execution_id"],
        )
        self.assertEqual(
            self.registry.get("test_idempotent_echo").calls,
            1,
        )

    async def test_oversized_idempotency_key_is_rejected(self):
        response = await self._execute_api(
            "test_idempotent_echo",
            {"value": 1},
            headers={"Idempotency-Key": "x" * 201},
        )

        self.assertEqual(response.status_code, 400)

    async def test_confirmation_approval_creates_a_second_history_row(self):
        self.settings(TOOL_ALLOW_SENSITIVE=True)

        pending = await self._execute_api(
            "test_sensitive_11i",
            {"target": "alpha"},
        )
        self.assertEqual(pending.status_code, 200, pending.text)
        pending_body = pending.json()
        self.assertTrue(pending_body["confirmation_required"])
        confirmation_id = pending_body["confirmation_id"]

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.AWAITING_CONFIRMATION,
        )

        approved = await self.client.post(
            f"/api/tools/confirmations/{confirmation_id}/confirm"
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(approved.json()["result"]["success"])

        rows = await self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[1].lifecycle_status,
            ToolExecutionLifecycle.SUCCEEDED,
        )
        self.assertEqual(
            rows[1].confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertEqual(
            self.registry.get("test_sensitive_11i").calls,
            1,
        )
        self.assertEqual(
            tool_metrics_service.snapshot()[
                "confirmation_approved_total"
            ],
            1,
        )

    async def test_confirmation_rejection_does_not_execute(self):
        self.settings(TOOL_ALLOW_SENSITIVE=True)

        pending = await self._execute_api(
            "test_sensitive_11i",
            {"target": "beta"},
        )
        confirmation_id = pending.json()["confirmation_id"]

        rejected = await self.client.post(
            f"/api/tools/confirmations/{confirmation_id}/reject"
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(
            self.registry.get("test_sensitive_11i").calls,
            0,
        )

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].lifecycle_status,
            ToolExecutionLifecycle.AWAITING_CONFIRMATION,
        )
        self.assertEqual(
            tool_metrics_service.snapshot()[
                "confirmation_rejected_total"
            ],
            1,
        )


class ProviderFailureMetricsTests(ToolHistoryApiTestBase):

    async def test_provider_failure_after_tool_execution_is_counted(self):
        def turn(calls, content=""):
            return {
                "provider": "groq",
                "model": "test-model",
                "response": content,
                "tool_calls": list(calls),
                "tool_usage": None,
                "usage": None,
                "fallback_used": False,
            }

        from app.llm.tool_calling import NormalizedToolCall

        calls = [
            NormalizedToolCall(
                id="call_1",
                name="test_idempotent_echo",
                arguments={"value": 1},
            )
        ]
        mock = AsyncMock(
            side_effect=[
                turn(calls),
                RuntimeError("provider down"),
            ]
        )
        patcher = patch.object(
            llm_router,
            "generate_with_tools",
            new=mock,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        response = await self.client.post(
            "/api/chat/",
            json={"message": "run the tool"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["provider_failure"])
        self.assertEqual(
            self.registry.get("test_idempotent_echo").calls,
            1,
        )
        self.assertGreaterEqual(
            tool_metrics_service.snapshot()["provider_failure_total"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
