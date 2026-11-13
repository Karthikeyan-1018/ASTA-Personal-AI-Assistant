"""Phase 11D tests for tool permissions and the confirmation system.

Every test uses an isolated in-memory database, a fresh registry, and mocked
tool implementations. No provider, network, file-system, or real tool call is
made.

The suite is organized around the Phase 11D requirements:

* centralized category, enablement, privacy, and confirmation policy;
* owner-, conversation-, and argument-bound confirmations;
* single-use approvals that cannot be replayed or duplicated;
* expired, rejected, missing, and tampered confirmations;
* approval never bypassing validation or authorization;
* authenticated, CSRF-protected API integration.
"""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

import app.models  # noqa: F401  (registers every ORM table)
import app.tools.executor as executor_module
from app.core.config import settings
from app.database.connection import get_db
from app.main import app
from app.models.conversation import Conversation
from app.models.tool_execution import ToolConfirmation
from app.models.user import User
from app.services.tool_confirmation_service import (
    _utcnow as confirmation_utcnow,
    tool_confirmation_service,
)
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolConfirmationStatus,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolDisabledError,
    ToolExecutionError,
    ToolPrivateRequestError,
    ToolValidationError,
)
from app.tools.executor import tool_executor
from app.tools.permissions import permission_policy
from app.tools.registry import ToolRegistry
from app.tools.result import success_result
from app.tools.schemas import StrictModel, arguments_digest
from tests.tool_test_support import EmptyInput, ToolEngineTestBase


# ---------------------------------------------------------------------------
# Tool fixtures
# ---------------------------------------------------------------------------


class ReadOnlyInput(StrictModel):
    target: str = "record"


class ReadOnlyTool(BaseTool):
    name = "test_read_only"
    description = "A normal read-only tool with no confirmation."
    category = ToolCategory.READ_ONLY
    input_model = ReadOnlyInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> object:
        self.calls += 1
        return success_result(
            tool_name=self.name,
            data={"target": arguments.target},
        )


class ReadOnlyConfirmationTool(BaseTool):
    name = "test_read_only_confirm"
    description = "A read-only tool that still demands approval."
    category = ToolCategory.READ_ONLY
    requires_confirmation = True
    input_model = ReadOnlyInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> object:
        self.calls += 1
        return success_result(
            tool_name=self.name,
            data={"target": arguments.target},
        )


class PrivateTool(BaseTool):
    name = "test_private"
    description = "A tool explicitly allowed in private mode."
    category = ToolCategory.READ_ONLY
    allowed_in_private = True
    input_model = EmptyInput

    async def execute(self, context, arguments) -> object:
        return success_result(tool_name=self.name, data={"private": True})


class PrivateDisabledByDefaultTool(BaseTool):
    name = "test_private_denied"
    description = "A tool that is not allowed in private mode."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> object:
        return success_result(tool_name=self.name, data={})


class DisabledTool(BaseTool):
    name = "test_disabled_11d"
    description = "A registered tool disabled by configuration."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    @property
    def enabled(self) -> bool:
        return False

    async def execute(self, context, arguments) -> object:
        return success_result(tool_name=self.name, data={})


class SensitiveTool(BaseTool):
    name = "test_sensitive_11d"
    description = "A sensitive tool controlled by deployment policy."
    category = ToolCategory.SENSITIVE
    input_model = EmptyInput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context, arguments) -> object:
        self.calls += 1
        return success_result(tool_name=self.name, data={"ok": True})


class ReversibleTool(BaseTool):
    name = "test_reversible_11d"
    description = "A reversible action that always needs approval."
    category = ToolCategory.REVERSIBLE
    requires_confirmation = True
    input_model = ReadOnlyInput

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def execute(self, context, arguments) -> object:
        self.calls += 1

        if self.error is not None:
            raise self.error

        return success_result(
            tool_name=self.name,
            data={"target": arguments.target},
        )


# ---------------------------------------------------------------------------
# Policy unit tests
# ---------------------------------------------------------------------------


class PermissionPolicyTests(unittest.TestCase):
    """The policy module is the single authorization decision point."""

    def setting(self, stack: ExitStack, **values):
        for name, value in values.items():
            stack.enter_context(patch.object(settings, name, value))

    def test_read_only_tool_needs_no_confirmation_by_default(self):
        decision = permission_policy.evaluate(
            ReadOnlyTool(),
            private=False,
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_confirmation)
        self.assertIsNone(decision.error_category)

    def test_read_only_tool_with_requires_confirmation_needs_approval(self):
        decision = permission_policy.evaluate(
            ReadOnlyConfirmationTool(),
            private=False,
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_confirmation)
        self.assertEqual(decision.category, ToolCategory.READ_ONLY)

    def test_requires_confirmation_applies_to_every_category(self):
        with ExitStack() as stack:
            self.setting(stack, TOOL_ALLOW_SENSITIVE=True)

            for tool in (
                ReadOnlyConfirmationTool(),
                ReversibleTool(),
                SensitiveTool(),
            ):
                decision = permission_policy.evaluate(tool, private=False)
                self.assertTrue(decision.allowed, tool.category)
                self.assertTrue(
                    decision.requires_confirmation,
                    tool.category,
                )

    def test_disabled_tool_is_denied_with_the_disabled_category(self):
        with self.assertRaises(ToolDisabledError):
            permission_policy.require_allowed(
                DisabledTool(),
                private=False,
            )

        decision = permission_policy.evaluate(
            DisabledTool(),
            private=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.DISABLED,
        )

    def test_private_mode_denies_a_non_private_tool(self):
        with self.assertRaises(ToolPrivateRequestError):
            permission_policy.require_allowed(
                PrivateDisabledByDefaultTool(),
                private=True,
            )

        decision = permission_policy.evaluate(
            PrivateDisabledByDefaultTool(),
            private=True,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.PERMISSION,
        )

    def test_private_mode_allows_an_opted_in_tool(self):
        decision = permission_policy.require_allowed(
            PrivateTool(),
            private=True,
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_confirmation)

    def test_sensitive_tool_is_denied_by_default(self):
        with ExitStack() as stack:
            self.setting(stack, TOOL_ALLOW_SENSITIVE=False)

            decision = permission_policy.evaluate(
                SensitiveTool(),
                private=False,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.PERMISSION,
        )

    def test_sensitive_tool_is_confirmed_when_deployment_allows_it(self):
        with ExitStack() as stack:
            self.setting(stack, TOOL_ALLOW_SENSITIVE=True)

            decision = permission_policy.require_allowed(
                SensitiveTool(),
                private=False,
            )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_confirmation)


class RegistryPolicyTests(ToolEngineTestBase):
    """The LLM must never see a tool the policy would reject."""

    def test_llm_definitions_exclude_disabled_and_private_blocked_tools(self):
        registry = ToolRegistry()
        registry.register_all(
            [
                ReadOnlyTool(),
                DisabledTool(),
                PrivateDisabledByDefaultTool(),
                PrivateTool(),
            ]
        )

        public = {
            item["function"]["name"]
            for item in registry.llm_definitions(private=False)
        }
        private = {
            item["function"]["name"]
            for item in registry.llm_definitions(private=True)
        }

        self.assertIn("test_read_only", public)
        self.assertNotIn("test_disabled_11d", public)
        self.assertIn("test_private_denied", public)

        self.assertNotIn("test_read_only", private)
        self.assertNotIn("test_disabled_11d", private)
        self.assertNotIn("test_private_denied", private)
        self.assertIn("test_private", private)


# ---------------------------------------------------------------------------
# Executor authorization tests
# ---------------------------------------------------------------------------


class ExecutorTestBaseII(ToolEngineTestBase):
    """Install a fresh registry and expose context-aware helpers."""

    def use_registry(self, *tools: BaseTool) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_all(tools)
        patcher = patch.object(
            executor_module,
            "tool_registry",
            registry,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return registry

    async def run_tool(
        self,
        tool_name: str,
        arguments: dict | None = None,
        *,
        user_id: int | None = None,
        conversation_id: int | None = None,
        private: bool = False,
        confirmation_id: int | None = None,
        create_confirmation: bool = True,
    ):
        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=user_id or self.alice_id,
                conversation_id=conversation_id,
                private=private,
            )
            return await tool_executor.execute(
                tool_name=tool_name,
                arguments=arguments or {},
                context=context,
                confirmation_id=confirmation_id,
                create_confirmation=create_confirmation,
            )

    async def create_pending(
        self,
        *,
        tool_name: str = "test_reversible_11d",
        category: ToolCategory = ToolCategory.REVERSIBLE,
        arguments: dict | None = None,
        user_id: int | None = None,
        conversation_id: int | None = None,
        ttl_seconds: int | None = None,
    ) -> ToolConfirmation:
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.create_pending(
                session=session,
                user_id=user_id or self.alice_id,
                conversation_id=conversation_id,
                tool_name=tool_name,
                category=category,
                arguments=arguments or {"target": "alpha"},
                arguments_preview="target=alpha",
                ttl_seconds=ttl_seconds,
            )

    async def stored_confirmation(
        self,
        confirmation_id: int,
        *,
        user_id: int | None = None,
    ) -> ToolConfirmation:
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.get(
                session=session,
                user_id=user_id or self.alice_id,
                confirmation_id=confirmation_id,
            )

    async def create_conversation(self, user_id: int | None = None) -> int:
        async with self.SessionLocal() as session:
            conversation = Conversation(
                user_id=user_id or self.alice_id,
                title="Phase 11D",
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation.id


class ExecutorAuthorizationTests(ExecutorTestBaseII):

    async def test_authorized_read_only_tool_executes(self):
        tool = ReadOnlyTool()
        self.use_registry(tool)

        result = await self.run_tool(
            "test_read_only",
            {"target": "alpha"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.data, {"target": "alpha"})
        self.assertEqual(tool.calls, 1)

    async def test_read_only_confirmation_tool_does_not_execute_unapproved(self):
        tool = ReadOnlyConfirmationTool()
        self.use_registry(tool)

        result = await self.run_tool(
            "test_read_only_confirm",
            {"target": "alpha"},
        )

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
        self.assertEqual(tool.calls, 0)

    async def test_private_mode_denies_tool_without_private_opt_in(self):
        tool = PrivateDisabledByDefaultTool()
        self.use_registry(tool)

        result = await self.run_tool(
            "test_private_denied",
            private=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )

    async def test_private_mode_allows_explicitly_opted_in_tool(self):
        tool = PrivateTool()
        self.use_registry(tool)

        result = await self.run_tool("test_private", private=True)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"private": True})

    async def test_disabled_tool_is_denied_with_disabled_category(self):
        self.use_registry(DisabledTool())

        result = await self.run_tool("test_disabled_11d")

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )
        self.assertEqual(result.error_code, "ToolDisabledError")

    async def test_unauthorized_sensitive_tool_is_denied(self):
        self.settings(TOOL_ALLOW_SENSITIVE=False)
        tool = SensitiveTool()
        self.use_registry(tool)

        result = await self.run_tool("test_sensitive_11d")

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )
        self.assertEqual(tool.calls, 0)

    async def test_allowed_sensitive_tool_still_requires_confirmation(self):
        self.settings(TOOL_ALLOW_SENSITIVE=True)
        tool = SensitiveTool()
        self.use_registry(tool)

        result = await self.run_tool("test_sensitive_11d")

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertEqual(tool.calls, 0)

    async def test_permission_failure_is_recorded_as_denied(self):
        self.settings(TOOL_HISTORY_ENABLED=True)
        self.use_registry(DisabledTool())

        await self.run_tool("test_disabled_11d")

        from app.services.tool_history_service import tool_history_service

        async with self.SessionLocal() as session:
            rows, total = await tool_history_service.list_executions(
                session,
                user_id=self.alice_id,
            )

        self.assertEqual(total, 1)
        self.assertEqual(rows[0].status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            rows[0].error_category,
            ToolErrorCategory.DISABLED,
        )
        self.assertEqual(
            rows[0].confirmation_status,
            ConfirmationState.NOT_REQUIRED,
        )


# ---------------------------------------------------------------------------
# Confirmation binding tests
# ---------------------------------------------------------------------------


class ConfirmationBindingTests(ExecutorTestBaseII):

    async def _claim(
        self,
        confirmation_id: int,
        *,
        user_id: int | None = None,
        conversation_id: int | None = None,
        arguments: dict | None = None,
        tool_name: str = "test_reversible_11d",
    ):
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.claim(
                session=session,
                user_id=user_id or self.alice_id,
                confirmation_id=confirmation_id,
                expected_tool_name=tool_name,
                expected_arguments=(
                    {"target": "alpha"} if arguments is None else arguments
                ),
                expected_conversation_id=conversation_id,
            )

    async def test_same_owner_and_conversation_can_claim(self):
        conversation_id = await self.create_conversation()
        confirmation = await self.create_pending(
            conversation_id=conversation_id,
        )

        claimed = await self._claim(
            confirmation.id,
            conversation_id=conversation_id,
        )

        self.assertEqual(claimed.id, confirmation.id)
        self.assertEqual(claimed.user_id, self.alice_id)
        self.assertEqual(claimed.conversation_id, conversation_id)
        self.assertEqual(claimed.arguments, {"target": "alpha"})

    async def test_confirmation_bound_to_a_different_conversation_is_rejected(
        self,
    ):
        first = await self.create_conversation()
        second = await self.create_conversation()
        confirmation = await self.create_pending(
            conversation_id=first,
        )

        with self.assertRaises(Exception) as raised:
            await self._claim(
                confirmation.id,
                conversation_id=second,
            )

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationMismatchError",
        )

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.PENDING)

    async def test_conversation_bound_confirmation_rejects_missing_context(
        self,
    ):
        conversation_id = await self.create_conversation()
        confirmation = await self.create_pending(
            conversation_id=conversation_id,
        )

        with self.assertRaises(Exception) as raised:
            await self._claim(confirmation.id, conversation_id=None)

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationMismatchError",
        )

    async def test_confirmation_is_owner_scoped(self):
        confirmation = await self.create_pending()

        with self.assertRaises(Exception) as raised:
            await self._claim(confirmation.id, user_id=self.bob_id)

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationNotFoundError",
        )

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.PENDING)

    async def test_confirmation_is_bound_to_the_canonical_arguments(self):
        confirmation = await self.create_pending()

        with self.assertRaises(Exception) as raised:
            await self._claim(
                confirmation.id,
                arguments={"target": "beta"},
            )

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationMismatchError",
        )

    async def test_argument_digest_is_order_independent(self):
        confirmation = await self.create_pending(
            arguments={"target": "alpha", "extra": "value"},
        )

        claimed = await self._claim(
            confirmation.id,
            arguments={"extra": "value", "target": "alpha"},
        )

        self.assertEqual(
            arguments_digest(claimed.arguments),
            claimed.arguments_hash,
        )

    async def test_confirmation_is_bound_to_the_tool_name(self):
        confirmation = await self.create_pending()

        with self.assertRaises(Exception) as raised:
            await self._claim(
                confirmation.id,
                tool_name="test_read_only",
            )

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationMismatchError",
        )

    async def test_replayed_confirmation_is_rejected(self):
        confirmation = await self.create_pending()

        first = await self._claim(confirmation.id)

        with self.assertRaises(Exception) as raised:
            await self._claim(confirmation.id)

        self.assertEqual(first.id, confirmation.id)
        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationResolvedError",
        )

    async def test_claim_clears_the_stored_arguments(self):
        confirmation = await self.create_pending()

        await self._claim(confirmation.id)

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.EXECUTED)
        self.assertEqual(stored.arguments, {})
        self.assertIsNotNone(stored.resolved_at)

    async def test_expired_confirmation_cannot_be_claimed(self):
        confirmation = await self.create_pending(ttl_seconds=30)

        async with self.SessionLocal() as session:
            with patch(
                "app.services.tool_confirmation_service._utcnow",
                return_value=confirmation_utcnow()
                + timedelta(seconds=31),
            ):
                with self.assertRaises(Exception) as raised:
                    await tool_confirmation_service.claim(
                        session=session,
                        user_id=self.alice_id,
                        confirmation_id=confirmation.id,
                    )

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationExpiredError",
        )

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.EXPIRED)
        self.assertEqual(stored.arguments, {})

    async def test_rejected_confirmation_cannot_be_claimed(self):
        confirmation = await self.create_pending()

        async with self.SessionLocal() as session:
            rejected = await tool_confirmation_service.reject(
                session=session,
                user_id=self.alice_id,
                confirmation_id=confirmation.id,
            )

        self.assertEqual(rejected.status, ToolConfirmationStatus.REJECTED)
        self.assertEqual(rejected.arguments, {})

        with self.assertRaises(Exception) as raised:
            await self._claim(confirmation.id)

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationResolvedError",
        )

    async def test_rejecting_an_expired_confirmation_is_safe(self):
        confirmation = await self.create_pending(ttl_seconds=30)

        async with self.SessionLocal() as session:
            with patch(
                "app.services.tool_confirmation_service._utcnow",
                return_value=confirmation_utcnow()
                + timedelta(seconds=31),
            ):
                with self.assertRaises(Exception) as raised:
                    await tool_confirmation_service.reject(
                        session=session,
                        user_id=self.alice_id,
                        confirmation_id=confirmation.id,
                    )

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationExpiredError",
        )

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.EXPIRED)

    async def test_pending_limit_is_still_enforced(self):
        self.settings(TOOL_CONFIRMATION_MAX_PENDING=1)

        await self.create_pending()

        with self.assertRaises(Exception) as raised:
            await self.create_pending()

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfirmationLimitError",
        )


# ---------------------------------------------------------------------------
# Executor confirmation tests
# ---------------------------------------------------------------------------


class ExecutorConfirmationTests(ExecutorTestBaseII):

    async def test_approval_executes_the_stored_arguments_once(self):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self.create_pending()

        first = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )
        replay = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )

        self.assertTrue(first.success)
        self.assertEqual(first.data, {"target": "alpha"})
        self.assertEqual(
            first.confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertEqual(first.confirmation_id, confirmation.id)
        self.assertEqual(tool.calls, 1)

        self.assertFalse(replay.success)
        self.assertEqual(replay.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            replay.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 1)

    async def test_modified_arguments_after_approval_are_rejected(self):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self.create_pending()

        changed = await self.run_tool(
            "test_reversible_11d",
            {"target": "beta"},
            confirmation_id=confirmation.id,
        )

        self.assertFalse(changed.success)
        self.assertEqual(changed.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            changed.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 0)

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.PENDING)

        approved = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(approved.data, {"target": "alpha"})
        self.assertEqual(tool.calls, 1)

    async def test_confirmation_bound_to_another_conversation_is_rejected(
        self,
    ):
        tool = ReversibleTool()
        self.use_registry(tool)
        conversation_id = await self.create_conversation()
        other_conversation_id = await self.create_conversation()
        confirmation = await self.create_pending(
            conversation_id=conversation_id,
        )

        denied = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            conversation_id=other_conversation_id,
            confirmation_id=confirmation.id,
        )

        self.assertFalse(denied.success)
        self.assertEqual(denied.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            denied.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 0)

        allowed = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            conversation_id=conversation_id,
            confirmation_id=confirmation.id,
        )

        self.assertTrue(allowed.success)
        self.assertEqual(tool.calls, 1)

    async def test_confirmation_on_a_tool_without_confirmation_is_rejected(
        self,
    ):
        tool = ReadOnlyTool()
        self.use_registry(tool)
        confirmation = await self.create_pending(
            tool_name="test_read_only",
            category=ToolCategory.READ_ONLY,
        )

        result = await self.run_tool(
            "test_read_only",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(tool.calls, 0)

    async def test_post_claim_validation_failure_marks_confirmation_failed(
        self,
    ):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self.create_pending()

        validated = tool.validate_arguments({"target": "alpha"})

        with patch.object(
            tool,
            "validate_arguments",
            side_effect=[
                validated,
                ToolValidationError("The approved payload is invalid."),
            ],
        ):
            result = await self.run_tool(
                "test_reversible_11d",
                {"target": "alpha"},
                confirmation_id=confirmation.id,
            )

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.ERROR,
        )
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.assertEqual(tool.calls, 0)

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.FAILED)

    async def test_post_claim_permission_failure_marks_confirmation_failed(
        self,
    ):
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self.create_pending()

        decision = permission_policy.evaluate(tool, private=False)

        with patch.object(
            permission_policy,
            "require_allowed",
            side_effect=[
                decision,
                ToolDisabledError("The tool was disabled after approval."),
            ],
        ):
            result = await self.run_tool(
                "test_reversible_11d",
                {"target": "alpha"},
                confirmation_id=confirmation.id,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )
        self.assertEqual(tool.calls, 0)

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.FAILED)

    async def test_tool_failure_after_approval_marks_confirmation_failed(self):
        tool = ReversibleTool(
            error=ToolExecutionError("The operation failed."),
        )
        self.use_registry(tool)
        confirmation = await self.create_pending()

        result = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.ERROR)
        self.assertEqual(result.error_category, ToolErrorCategory.EXECUTION)
        self.assertEqual(tool.calls, 1)

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.FAILED)

    async def test_successful_approval_links_the_execution_history(self):
        self.settings(TOOL_HISTORY_ENABLED=True)
        tool = ReversibleTool()
        self.use_registry(tool)
        confirmation = await self.create_pending()

        result = await self.run_tool(
            "test_reversible_11d",
            {"target": "alpha"},
            confirmation_id=confirmation.id,
        )

        self.assertTrue(result.success)

        stored = await self.stored_confirmation(confirmation.id)
        self.assertEqual(stored.status, ToolConfirmationStatus.EXECUTED)
        self.assertIsNotNone(stored.execution_id)

        from app.services.tool_history_service import tool_history_service

        async with self.SessionLocal() as session:
            rows, total = await tool_history_service.list_executions(
                session,
                user_id=self.alice_id,
            )

        self.assertEqual(total, 1)
        self.assertEqual(rows[0].id, stored.execution_id)
        self.assertEqual(
            rows[0].confirmation_status,
            ConfirmationState.CONFIRMED,
        )


# ---------------------------------------------------------------------------
# Authenticated API integration tests
# ---------------------------------------------------------------------------


OWNER_USERNAME = "phase11d_owner"
OWNER_PASSWORD = "phase11d-correct-horse-battery"
SETUP_TOKEN = "phase11d-setup-token"


class ApiTestBase(ExecutorTestBaseII):
    """Real FastAPI routes, authentication, CSRF, and isolated database."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db

        self._stack = ExitStack()
        self._stack.enter_context(
            patch.object(settings, "AUTH_OWNER_SETUP_TOKEN", SETUP_TOKEN)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_COOKIE_SECURE", True)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_COOKIE_SAMESITE", "lax")
        )
        self._stack.enter_context(
            patch.object(
                settings,
                "AUTH_SESSION_LAST_USED_UPDATE_SECONDS",
                300,
            )
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_SESSION_LIFETIME_HOURS", 168)
        )

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
        )

        setup = await self.client.post(
            "/api/auth/setup",
            json={
                "username": OWNER_USERNAME,
                "password": OWNER_PASSWORD,
            },
            headers={"X-Setup-Token": SETUP_TOKEN},
        )
        self.assertEqual(setup.status_code, 201, setup.text)
        self.owner_id = setup.json()["id"]

        login = await self.client.post(
            "/api/auth/login",
            json={
                "username": OWNER_USERNAME,
                "password": OWNER_PASSWORD,
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.csrf = login.json()["csrf_token"]

    async def asyncTearDown(self):
        await self.client.aclose()
        self._stack.close()
        app.dependency_overrides.clear()
        await super().asyncTearDown()

    @property
    def csrf_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    async def _execute(self, tool: str, arguments: dict | None = None):
        return await self.client.post(
            "/api/tools/execute",
            json={"tool": tool, "arguments": arguments or {}},
            headers=self.csrf_headers,
        )

    async def _confirm(self, confirmation_id: int):
        return await self.client.post(
            f"/api/tools/confirmations/{confirmation_id}/confirm",
            headers=self.csrf_headers,
        )

    async def _reject(self, confirmation_id: int):
        return await self.client.post(
            f"/api/tools/confirmations/{confirmation_id}/reject",
            headers=self.csrf_headers,
        )


class ApiConfirmationTests(ApiTestBase):

    async def test_execution_requires_authentication(self):
        anonymous = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
        )

        try:
            response = await anonymous.post(
                "/api/tools/execute",
                json={"tool": "test_read_only", "arguments": {}},
            )
        finally:
            await anonymous.aclose()

        self.assertEqual(response.status_code, 401)

    async def test_execution_requires_a_valid_csrf_token(self):
        self.use_registry(ReadOnlyTool())

        response = await self.client.post(
            "/api/tools/execute",
            json={"tool": "test_read_only", "arguments": {}},
        )

        self.assertEqual(response.status_code, 403)

    async def test_authorized_tool_executes_over_the_api(self):
        tool = ReadOnlyTool()
        self.use_registry(tool)

        response = await self._execute(
            "test_read_only",
            {"target": "alpha"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["data"], {"target": "alpha"})
        self.assertEqual(tool.calls, 1)

    async def test_api_confirmation_executes_once_and_cannot_be_replayed(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        proposed = await self._execute(
            "test_reversible_11d",
            {"target": "alpha"},
        )

        self.assertEqual(proposed.status_code, 200, proposed.text)
        proposal = proposed.json()
        self.assertTrue(proposal["confirmation_required"])
        self.assertEqual(
            proposal["status"],
            "confirmation_required",
        )
        confirmation_id = proposal["confirmation_id"]
        self.assertEqual(tool.calls, 0)

        approved = await self._confirm(confirmation_id)

        self.assertEqual(approved.status_code, 200, approved.text)
        approved_body = approved.json()
        self.assertTrue(approved_body["result"]["success"])
        self.assertEqual(
            approved_body["result"]["data"],
            {"target": "alpha"},
        )
        self.assertEqual(
            approved_body["confirmation"]["status"],
            "executed",
        )
        self.assertEqual(tool.calls, 1)

        replay = await self._confirm(confirmation_id)

        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(tool.calls, 1)

    async def test_api_rejection_prevents_execution(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        proposed = await self._execute(
            "test_reversible_11d",
            {"target": "alpha"},
        )
        confirmation_id = proposed.json()["confirmation_id"]

        rejected = await self._reject(confirmation_id)

        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(
            rejected.json()["confirmation"]["status"],
            "rejected",
        )
        self.assertIsNone(rejected.json()["result"])
        self.assertEqual(tool.calls, 0)

        confirm = await self._confirm(confirmation_id)

        self.assertEqual(confirm.status_code, 409, confirm.text)
        self.assertEqual(tool.calls, 0)

    async def test_api_unknown_confirmation_returns_not_found(self):
        self.use_registry(ReversibleTool())

        response = await self._confirm(999999)

        self.assertEqual(response.status_code, 404, response.text)

    async def test_api_cannot_confirm_another_owners_confirmation(self):
        self.use_registry(ReversibleTool())

        async with self.SessionLocal() as session:
            intruder = User(
                username=f"phase11d_intruder_{id(self)}",
            )
            session.add(intruder)
            await session.commit()
            await session.refresh(intruder)
            intruder_id = intruder.id

            foreign = await tool_confirmation_service.create_pending(
                session=session,
                user_id=intruder_id,
                conversation_id=None,
                tool_name="test_reversible_11d",
                category=ToolCategory.REVERSIBLE,
                arguments={"target": "alpha"},
                arguments_preview="target=alpha",
            )

        response = await self._confirm(foreign.id)

        self.assertEqual(response.status_code, 404, response.text)

    async def test_api_expired_confirmation_returns_gone(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        proposed = await self._execute(
            "test_reversible_11d",
            {"target": "alpha"},
        )
        confirmation_id = proposed.json()["confirmation_id"]

        with patch(
            "app.services.tool_confirmation_service._utcnow",
            return_value=confirmation_utcnow() + timedelta(hours=2),
        ):
            response = await self._confirm(confirmation_id)

        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(tool.calls, 0)

        stored = await self.stored_confirmation(
            confirmation_id,
            user_id=self.owner_id,
        )
        self.assertEqual(stored.status, ToolConfirmationStatus.EXPIRED)

    async def test_api_rejects_tampered_confirmation_arguments(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        proposed = await self._execute(
            "test_reversible_11d",
            {"target": "alpha"},
        )
        confirmation_id = proposed.json()["confirmation_id"]

        async with self.SessionLocal() as session:
            stored = await session.get(
                ToolConfirmation,
                confirmation_id,
            )
            stored.arguments = {"target": "beta"}
            await session.commit()

        response = await self._confirm(confirmation_id)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(tool.calls, 0)

    async def test_api_cannot_change_arguments_after_proposal(self):
        tool = ReversibleTool()
        self.use_registry(tool)

        first = await self._execute(
            "test_reversible_11d",
            {"target": "alpha"},
        )
        second = await self._execute(
            "test_reversible_11d",
            {"target": "beta"},
        )

        first_id = first.json()["confirmation_id"]
        second_id = second.json()["confirmation_id"]

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(tool.calls, 0)

        approved = await self._confirm(first_id)

        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(
            approved.json()["result"]["data"],
            {"target": "alpha"},
        )
        self.assertEqual(tool.calls, 1)

        second_stored = await self.stored_confirmation(
            second_id,
            user_id=self.owner_id,
        )
        self.assertEqual(
            second_stored.status,
            ToolConfirmationStatus.PENDING,
        )

    async def test_api_confirmation_execution_failure_is_reported(self):
        tool = ReversibleTool(
            error=ToolExecutionError("The operation failed."),
        )
        self.use_registry(tool)

        proposed = await self._execute(
            "test_reversible_11d",
            {"target": "alpha"},
        )
        confirmation_id = proposed.json()["confirmation_id"]

        response = await self._confirm(confirmation_id)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["result"]["success"])
        self.assertEqual(body["result"]["error_category"], "execution")
        self.assertEqual(body["confirmation"]["status"], "failed")
        self.assertEqual(tool.calls, 1)


class ApiPolicyTests(ApiTestBase):
    """API enforcement of the centralized policy."""

    async def test_api_denies_disabled_tool(self):
        self.use_registry(DisabledTool())

        response = await self._execute("test_disabled_11d")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["status"], "denied")
        self.assertEqual(body["error_category"], "disabled")

    async def test_api_denies_sensitive_tool_by_default(self):
        self.settings(TOOL_ALLOW_SENSITIVE=False)
        tool = SensitiveTool()
        self.use_registry(tool)

        response = await self._execute("test_sensitive_11d")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["status"], "denied")
        self.assertEqual(body["error_category"], "permission")
        self.assertEqual(tool.calls, 0)

    async def test_api_never_exposes_private_mode(self):
        # The REST endpoint always builds a non-private context. A tool that
        # is private-only must therefore be evaluated under public policy.
        tool = PrivateTool()
        self.use_registry(tool)

        response = await self._execute("test_private")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])


if __name__ == "__main__":
    unittest.main()
