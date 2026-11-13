"""Central tool execution engine.

Every tool call, whether from chat or the REST API, passes through this
executor. It resolves the registered tool, re-checks the server-side permission
policy, validates arguments, enforces confirmation, applies a timeout, bounds
and sanitizes the result, records metadata-only history, and converts every
failure into a normalized ``ToolResult``.

The executor is provider-independent. It accepts plain tool names and argument
mappings; the Phase 11B translation layer and the chat orchestrator are
responsible for turning provider output into that shape. ``execute_call`` is a
convenience adapter for a normalized Phase 11B call object.

Execution is deliberately fail-closed:

* identity, privacy, and permissions come only from ``ToolContext``;
* a missing context never falls back to an anonymous or default owner;
* argument and result values are never written to history;
* tool errors are bounded and sanitized before they reach a caller;
* a tool failure, timeout, cancellation, or history failure never escapes as an
  unhandled exception.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.services.tool_confirmation_service import (
    ClaimedConfirmation,
    tool_confirmation_service,
)
from app.services.tool_history_service import (
    DuplicateExecutionError,
    tool_history_service,
)
from app.services.tool_metrics_service import tool_metrics_service
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import (
    ConfirmationState,
    ToolErrorCategory,
    ToolExecutionLifecycle,
    ToolExecutionStatus,
)
from app.tools.lifecycle import lifecycle_from_status
from app.tools.retry import (
    backoff_seconds,
    max_retries,
    retry_eligible,
)
from app.tools.exceptions import (
    ToolConfirmationExpiredError,
    ToolConfirmationLimitError,
    ToolConfirmationMismatchError,
    ToolConfirmationNotFoundError,
    ToolConfirmationRequiredError,
    ToolConfirmationResolvedError,
    ToolConfigurationError,
    ToolDisabledError,
    ToolError,
    ToolExecutionError,
    ToolMalformedCallError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolProviderError,
    ToolSizeLimitError,
    ToolTimeoutError,
    ToolValidationError,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from app.tools.result import (
    enforce_result_limit,
    error_result,
    redact_secrets,
    sanitize_error_message,
)
from app.tools.schemas import (
    ToolResult,
    arguments_digest,
    json_size,
)


logger = logging.getLogger(__name__)


DEFAULT_EXECUTION_TIMEOUT_SECONDS = 15.0
MIN_EXECUTION_TIMEOUT_SECONDS = 0.01
DEFAULT_ARGUMENT_LIMIT_BYTES = 16384
MAX_TOOL_NAME_CHARS = 100


class ToolExecutor:

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        confirmation_id: int | None = None,
        create_confirmation: bool = True,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """Execute one tool call under the full server-side policy."""

        started = time.perf_counter()
        safe_name = _tool_name(tool_name)

        try:
            user_id, session, private, conversation_id = _trusted_context(
                context
            )

            # Never inherit a history identifier from a previous attempt that
            # reused this context object.
            context.execution_id = None

        except ToolConfigurationError as error:
            return _failure(
                tool_name=safe_name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.CONFIGURATION,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        if safe_name is None:
            return _failure(
                tool_name=None,
                code="ToolNotFound",
                message="A tool name is required.",
                category=ToolErrorCategory.NOT_FOUND,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        # Idempotency is checked before any side effect. A replay returns only
        # the stored metadata; it never returns the original tool payload.
        if idempotency_key:
            try:
                existing = await tool_history_service.find_by_idempotency_key(
                    session=session,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                )

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Failed to check tool idempotency for user %s.",
                    user_id,
                )
                existing = None

            if existing is not None:
                tool_metrics_service.record_duplicate_prevented()
                return _replay_result(existing)

        try:
            tool = tool_registry.get(safe_name)

        except ToolNotFoundError as error:
            return _failure(
                tool_name=safe_name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.NOT_FOUND,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        except ToolError as error:
            # Registry loading or validation failed. That is a deployment
            # problem, not a permission decision.
            logger.exception("Tool registry resolution failed for '%s'.", safe_name)
            return _failure(
                tool_name=safe_name,
                code=type(error).__name__,
                message="The tool registry is unavailable.",
                category=ToolErrorCategory.CONFIGURATION,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        except Exception:
            logger.exception("Unexpected tool resolution failure for '%s'.", safe_name)
            return _failure(
                tool_name=safe_name,
                code="ToolInternalError",
                message="The tool could not be resolved.",
                category=ToolErrorCategory.INTERNAL,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        # Create the in-flight history row before any policy decision or side
        # effect. If the process dies during execution, startup recovery can
        # find this row and mark it cancelled.
        try:
            history_record = await tool_history_service.begin(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
                tool_name=tool.name,
                category=tool.category,
                provider=tool.provider_label,
                idempotency_key=idempotency_key,
            )

            if history_record is not None:
                context.execution_id = history_record.id

        except asyncio.CancelledError:
            raise

        except DuplicateExecutionError:
            try:
                existing = await tool_history_service.find_by_idempotency_key(
                    session=session,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                )

            except Exception:
                logger.exception(
                    "Failed to load a duplicate tool request for user %s.",
                    user_id,
                )
                existing = None

            if existing is not None:
                tool_metrics_service.record_duplicate_prevented()
                return _replay_result(existing)

            logger.warning(
                "A duplicate tool request was detected but no stored row was "
                "found for user %s.",
                user_id,
            )

        except Exception:
            # History is audit information, never a precondition for running a
            # tool. A storage failure must not change the execution outcome.
            logger.exception(
                "Failed to create in-flight tool history for tool '%s'.",
                tool.name,
            )

        # Permission and privacy policy is evaluated before argument validation.
        # A disabled or private-only tool therefore cannot disclose schema
        # details through validation messages.
        try:
            decision = permission_policy.require_allowed(
                tool,
                private=private,
            )

        except ToolDisabledError as error:
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=ToolErrorCategory.DISABLED,
                status=ToolExecutionStatus.DENIED,
            )

        except ToolPermissionError as error:
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=ToolErrorCategory.PERMISSION,
                status=ToolExecutionStatus.DENIED,
            )

        except ToolError as error:
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=ToolErrorCategory.PERMISSION,
                status=ToolExecutionStatus.DENIED,
            )

        except Exception:
            logger.exception(
                "Unexpected permission failure for tool '%s'.",
                tool.name,
            )
            return await self._reject(
                context=context,
                tool=tool,
                error=ToolError("The tool permission check failed."),
                started=started,
                category=ToolErrorCategory.INTERNAL,
                status=ToolExecutionStatus.ERROR,
            )

        if not isinstance(arguments, dict):
            return await self._reject(
                context=context,
                tool=tool,
                error=ToolValidationError(
                    "Tool arguments must be an object."
                ),
                started=started,
                category=ToolErrorCategory.VALIDATION,
                status=ToolExecutionStatus.ERROR,
            )

        try:
            argument_size = json_size(arguments)

        except Exception:
            argument_size = 0

        if argument_size > _argument_limit():
            return await self._reject(
                context=context,
                tool=tool,
                error=ToolSizeLimitError(
                    "Tool arguments exceed the configured size limit."
                ),
                started=started,
                category=ToolErrorCategory.SIZE_LIMIT,
                status=ToolExecutionStatus.ERROR,
            )

        try:
            validated = tool.validate_arguments(arguments)

        except ToolValidationError as error:
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=ToolErrorCategory.VALIDATION,
                status=ToolExecutionStatus.ERROR,
            )

        except ToolError as error:
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=ToolErrorCategory.VALIDATION,
                status=ToolExecutionStatus.ERROR,
            )

        except Exception:
            logger.exception(
                "Unexpected argument validation failure for tool '%s'.",
                tool.name,
            )
            return await self._reject(
                context=context,
                tool=tool,
                error=ToolValidationError(
                    "The tool arguments could not be validated."
                ),
                started=started,
                category=ToolErrorCategory.INTERNAL,
                status=ToolExecutionStatus.ERROR,
            )

        validated_arguments = validated.model_dump(mode="json")

        claimed: ClaimedConfirmation | None = None

        if decision.requires_confirmation and confirmation_id is None:
            if not create_confirmation:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=ToolConfirmationRequiredError(
                        "This tool requires explicit confirmation."
                    ),
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.CONFIRMATION_REQUIRED,
                    confirmation_state=ConfirmationState.PENDING,
                )

            return await self._create_confirmation(
                context=context,
                tool=tool,
                validated=validated,
                validated_arguments=validated_arguments,
                started=started,
            )

        if confirmation_id is not None:
            if not decision.requires_confirmation:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=ToolValidationError(
                        "This tool does not use confirmations."
                    ),
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.ERROR,
                )

            try:
                claimed = await tool_confirmation_service.claim(
                    session=session,
                    user_id=user_id,
                    confirmation_id=confirmation_id,
                    expected_tool_name=tool.name,
                    expected_arguments=validated_arguments,
                    expected_conversation_id=conversation_id,
                )

            except ToolConfirmationExpiredError as error:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.EXPIRED,
                )

            except ToolConfirmationMismatchError as error:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.REJECTED,
                )

            except ToolConfirmationResolvedError as error:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.REJECTED,
                )

            except ToolConfirmationNotFoundError as error:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.REJECTED,
                )

            except Exception:
                logger.exception(
                    "Unexpected confirmation claim failure for tool '%s'.",
                    tool.name,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=ToolError(
                        "The tool confirmation could not be claimed."
                    ),
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.ERROR,
                )

            tool_metrics_service.record_confirmation_approval()
            await self._transition(
                session=session,
                user_id=user_id,
                context=context,
                lifecycle=ToolExecutionLifecycle.APPROVED,
                status=ToolExecutionStatus.RUNNING,
            )

            # The confirmation stores the exact approved payload. Re-validate
            # it rather than trusting the caller to resend the same values.
            # Approval must never bypass argument validation.
            try:
                validated = tool.validate_arguments(claimed.arguments)
                validated_arguments = validated.model_dump(mode="json")

            except ToolValidationError as error:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.VALIDATION,
                    status=ToolExecutionStatus.ERROR,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            except ToolError as error:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.VALIDATION,
                    status=ToolExecutionStatus.ERROR,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            except Exception:
                logger.exception(
                    "Unexpected post-confirmation validation failure for tool "
                    "'%s'.",
                    tool.name,
                )
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=ToolValidationError(
                        "The approved tool arguments are no longer valid."
                    ),
                    started=started,
                    category=ToolErrorCategory.INTERNAL,
                    status=ToolExecutionStatus.ERROR,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            # Re-evaluate policy after approval. Configuration can change
            # between proposal and execution, so an approval never bypasses
            # the current authorization decision.
            try:
                permission_policy.require_allowed(
                    tool,
                    private=private,
                )

            except ToolDisabledError as error:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.DISABLED,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            except ToolPermissionError as error:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.PERMISSION,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            except ToolError as error:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.PERMISSION,
                    status=ToolExecutionStatus.DENIED,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            except Exception:
                logger.exception(
                    "Unexpected post-confirmation permission failure for tool "
                    "'%s'.",
                    tool.name,
                )
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=ToolError(
                        "The tool permission check failed."
                    ),
                    started=started,
                    category=ToolErrorCategory.INTERNAL,
                    status=ToolExecutionStatus.ERROR,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

        await self._transition(
            session=session,
            user_id=user_id,
            context=context,
            lifecycle=ToolExecutionLifecycle.RUNNING,
            status=ToolExecutionStatus.RUNNING,
        )

        try:
            result = await self._run(
                context=context,
                tool=tool,
                validated=validated,
                started=started,
                allow_retry=(
                    claimed is None and not decision.requires_confirmation
                ),
            )

        except asyncio.CancelledError:
            # Caller cancellation must propagate, but the in-flight history row
            # should not be left as running forever. Shield the final write so
            # the cancellation cannot interrupt it in turn.
            confirmation_state = (
                ConfirmationState.CONFIRMED
                if claimed is not None
                else ConfirmationState.NOT_REQUIRED
            )

            if claimed is not None:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )

            cancelled = _failure(
                tool_name=tool.name,
                code="ToolExecutionCancelled",
                message="The tool execution was cancelled by the caller.",
                category=ToolErrorCategory.EXECUTION,
                status=ToolExecutionStatus.CANCELLED,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
                confirmation_status=confirmation_state,
            )

            try:
                await asyncio.shield(
                    self._record(
                        context=context,
                        tool=tool,
                        result=cancelled,
                        validated_arguments=validated_arguments,
                        started=started,
                        confirmation_state=confirmation_state,
                    )
                )

            except Exception:
                logger.exception(
                    "Failed to record a cancelled tool execution for '%s'.",
                    tool.name,
                )

            raise

        if claimed is not None:
            # Report the confirmation that authorized this attempt. The tool
            # result remains the source of truth for success or failure.
            result.confirmation_id = claimed.id
            result.confirmation_status = ConfirmationState.CONFIRMED

            if result.success:
                await self._attach_and_record(
                    context=context,
                    tool=tool,
                    result=result,
                    validated_arguments=validated_arguments,
                    started=started,
                    confirmation_state=ConfirmationState.CONFIRMED,
                    confirmation_id=claimed.id,
                )
            else:
                await self._mark_failed(
                    session=session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )

                await self._record(
                    context=context,
                    tool=tool,
                    result=result,
                    validated_arguments=validated_arguments,
                    started=started,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            return result

        await self._record(
            context=context,
            tool=tool,
            result=result,
            validated_arguments=validated_arguments,
            started=started,
            confirmation_state=ConfirmationState.NOT_REQUIRED,
        )

        return result

    async def execute_call(
        self,
        call: Any,
        *,
        context: ToolContext,
        confirmation_id: int | None = None,
        create_confirmation: bool = True,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """Execute a normalized Phase 11B call object.

        Accepts either ``NormalizedToolCall`` or the public ``ToolCall`` schema.
        A call carrying a parser error is never executed: it becomes a
        structured ``malformed_call`` result so the assistant can recover.
        """

        started = time.perf_counter()
        name = _tool_name(getattr(call, "name", None))
        error = getattr(call, "error", None)
        arguments = getattr(call, "arguments", None)

        if error or name is None:
            return _failure(
                tool_name=name,
                code="MalformedToolCall",
                message=(
                    error
                    or "The provider returned an invalid tool call."
                ),
                category=ToolErrorCategory.MALFORMED_CALL,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        if not isinstance(arguments, dict):
            # A normalized call must carry an object argument payload. Treat
            # anything else as a malformed call instead of silently executing a
            # tool with defaults, which could make a destructive action succeed
            # with an empty request.
            return _failure(
                tool_name=name,
                code="MalformedToolCall",
                message=(
                    "The provider returned a non-object argument payload."
                ),
                category=ToolErrorCategory.MALFORMED_CALL,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        return await self.execute(
            tool_name=name,
            arguments=arguments,
            context=context,
            confirmation_id=confirmation_id,
            create_confirmation=create_confirmation,
            idempotency_key=idempotency_key,
        )

    async def _run(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        validated,
        started: float,
        allow_retry: bool = True,
    ) -> ToolResult:
        """Run one tool with bounded, policy-limited retries.

        Confirmation-bound attempts and non-idempotent tools are never retried.
        Cancellation always propagates immediately.
        """

        retries = 0
        limit = max_retries() if allow_retry else 0

        while True:
            result = await self._run_once(
                context=context,
                tool=tool,
                validated=validated,
                started=started,
            )
            result.retry_count = retries

            if retries >= limit:
                return result

            if not retry_eligible(
                tool,
                result,
                allow_retry=allow_retry,
            ):
                return result

            retries += 1

            try:
                tool_metrics_service.record_retry()

            except Exception:
                logger.debug(
                    "Failed to record a tool retry metric.",
                    exc_info=True,
                )

            delay = backoff_seconds(retries)

            if delay > 0:
                await asyncio.sleep(delay)

    async def _run_once(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        validated,
        started: float,
    ) -> ToolResult:
        timeout = _execution_timeout()

        try:
            result = await asyncio.wait_for(
                tool.execute(context, validated),
                timeout=timeout,
            )

        except asyncio.CancelledError:
            # Distinguish an actual caller cancellation from a tool that raised
            # CancelledError on its own. Caller cancellation must propagate so
            # request shutdown and timeouts remain cooperative.
            task = asyncio.current_task()
            cancelling = getattr(task, "cancelling", None)

            if cancelling is not None and cancelling() > 0:
                logger.info(
                    "Tool '%s' was cancelled by the caller.",
                    tool.name,
                )
                raise

            logger.warning(
                "Tool '%s' raised CancelledError without caller cancellation.",
                tool.name,
            )
            return _failure(
                tool_name=tool.name,
                code="ToolCancelled",
                message="The tool execution was cancelled.",
                category=ToolErrorCategory.EXECUTION,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except (asyncio.TimeoutError, ToolTimeoutError):
            return _failure(
                tool_name=tool.name,
                code="ToolTimeout",
                message=(
                    f"Tool execution exceeded the {timeout:g}-second timeout."
                ),
                category=ToolErrorCategory.TIMEOUT,
                status=ToolExecutionStatus.TIMEOUT,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolError as error:
            failure = _failure_for_tool_error(
                error=error,
                tool=tool,
                started=started,
            )
            return failure

        except Exception:
            logger.exception(
                "Unexpected failure in tool '%s' for user %s.",
                tool.name,
                getattr(context, "user_id", None),
            )
            return _failure(
                tool_name=tool.name,
                code="ToolInternalError",
                message="The tool failed unexpectedly.",
                category=ToolErrorCategory.INTERNAL,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        if not isinstance(result, ToolResult):
            logger.error(
                "Tool '%s' returned %s instead of ToolResult.",
                tool.name,
                type(result).__name__,
            )
            return _failure(
                tool_name=tool.name,
                code="ToolContractViolation",
                message="The tool returned an invalid result.",
                category=ToolErrorCategory.INTERNAL,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        return _finalize_result(
            result=result,
            tool=tool,
            started=started,
        )

    async def _create_confirmation(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        validated,
        validated_arguments: dict[str, Any],
        started: float,
    ) -> ToolResult:
        try:
            confirmation = await tool_confirmation_service.create_pending(
                session=context.session,
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                tool_name=tool.name,
                category=tool.category,
                arguments=validated_arguments,
                arguments_preview=tool.summarize(validated),
            )

        except ToolConfirmationLimitError as error:
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=ToolErrorCategory.CONFIRMATION,
                status=ToolExecutionStatus.DENIED,
            )

        except Exception:
            logger.exception(
                "Failed to create a confirmation for tool '%s'.",
                tool.name,
            )
            return await self._reject(
                context=context,
                tool=tool,
                error=ToolError(
                    "Unable to create a confirmation for this action."
                ),
                started=started,
                category=ToolErrorCategory.INTERNAL,
                status=ToolExecutionStatus.ERROR,
            )

        result = _failure(
            tool_name=tool.name,
            code="ConfirmationRequired",
            message=(
                "This action requires explicit confirmation before it can "
                "run."
            ),
            category=ToolErrorCategory.CONFIRMATION,
            status=ToolExecutionStatus.CONFIRMATION_REQUIRED,
            duration_ms=_elapsed_ms(started),
            provider=tool.provider_label,
            confirmation_required=True,
            confirmation_id=confirmation.id,
            confirmation_status=ConfirmationState.PENDING,
            metadata={
                "preview": confirmation.arguments_preview,
                "expires_at": confirmation.expires_at,
                "category": tool.category.value,
            },
        )

        await self._record(
            context=context,
            tool=tool,
            result=result,
            validated_arguments=validated_arguments,
            started=started,
            confirmation_state=ConfirmationState.PENDING,
        )

        return result

    async def _reject(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        error: ToolError,
        started: float,
        category: ToolErrorCategory,
        status: ToolExecutionStatus,
        confirmation_state: ConfirmationState = (
            ConfirmationState.NOT_REQUIRED
        ),
    ) -> ToolResult:
        result = _failure(
            tool_name=tool.name,
            code=type(error).__name__,
            message=str(error),
            category=category,
            status=status,
            duration_ms=_elapsed_ms(started),
            provider=tool.provider_label,
            confirmation_required=(
                status == ToolExecutionStatus.CONFIRMATION_REQUIRED
            ),
            confirmation_status=confirmation_state,
        )

        await self._record(
            context=context,
            tool=tool,
            result=result,
            validated_arguments={},
            started=started,
            confirmation_state=confirmation_state,
        )

        return result

    async def _attach_and_record(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        result: ToolResult,
        validated_arguments: dict[str, Any],
        started: float,
        confirmation_state: ConfirmationState,
        confirmation_id: int,
    ) -> None:
        record = await self._record(
            context=context,
            tool=tool,
            result=result,
            validated_arguments=validated_arguments,
            started=started,
            confirmation_state=confirmation_state,
        )

        try:
            await tool_confirmation_service.attach_execution(
                session=context.session,
                user_id=context.user_id,
                confirmation_id=confirmation_id,
                execution_id=record.id if record is not None else None,
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to attach execution history to confirmation %s.",
                confirmation_id,
            )

    async def _mark_failed(
        self,
        *,
        session,
        user_id: int,
        confirmation_id: int,
    ) -> None:
        try:
            await tool_confirmation_service.mark_failed(
                session=session,
                user_id=user_id,
                confirmation_id=confirmation_id,
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to mark confirmation %s as failed.",
                confirmation_id,
            )

    async def _transition(
        self,
        *,
        session,
        user_id: int,
        context: ToolContext,
        lifecycle: ToolExecutionLifecycle,
        status: ToolExecutionStatus | None = None,
    ) -> None:
        """Best-effort lifecycle transition that never affects execution."""

        execution_id = getattr(context, "execution_id", None)

        if execution_id is None:
            return

        try:
            await tool_history_service.transition(
                session=session,
                user_id=user_id,
                execution_id=execution_id,
                lifecycle_status=lifecycle,
                status=status,
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to update tool execution %s lifecycle.",
                execution_id,
            )

    async def _record(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        result: ToolResult,
        validated_arguments: dict[str, Any],
        started: float,
        confirmation_state: ConfirmationState,
    ):
        """Record metadata-only history without affecting the result.

        History and metrics are audit information, not part of tool execution.
        A database problem or a disabled history setting must never turn a
        successful action into a failed assistant response.
        """

        execution_id = getattr(context, "execution_id", None)
        lifecycle = lifecycle_from_status(
            result.status,
            confirmation_state=confirmation_state,
            error_code=result.error_code,
        )

        record = None

        try:
            record = await tool_history_service.record(
                session=context.session,
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                tool_name=tool.name,
                category=tool.category,
                status=result.status,
                confirmation_status=confirmation_state,
                duration_ms=result.duration_ms,
                error_category=result.error_category,
                error_code=result.error_code,
                provider=result.provider or tool.provider_label,
                arguments_hash=(
                    arguments_digest(validated_arguments)
                    if validated_arguments
                    else None
                ),
                argument_keys=(
                    list(validated_arguments.keys())
                    if validated_arguments
                    else None
                ),
                result_bytes=result.result_bytes,
                execution_id=execution_id,
                lifecycle_status=lifecycle,
                retry_count=result.retry_count,
                truncated=result.truncated,
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to record tool execution history for tool '%s' "
                "and user %s.",
                tool.name,
                getattr(context, "user_id", None),
            )
            record = None

        if record is not None:
            result.execution_id = record.id
            context.execution_id = record.id

        try:
            tool_metrics_service.record_execution(
                tool_name=tool.name,
                status=result.status,
                success=bool(result.success),
                duration_ms=result.duration_ms,
                lifecycle=lifecycle,
            )

        except Exception:
            logger.debug(
                "Failed to record tool execution metrics.",
                exc_info=True,
            )

        # Metadata-only structured log. Argument values, results, prompts, and
        # credentials are never included.
        logger.info(
            "tool_execution tool=%s status=%s lifecycle=%s duration_ms=%s "
            "retry_count=%s confirmation=%s error_code=%s",
            tool.name,
            _value(result.status),
            lifecycle.value,
            result.duration_ms,
            int(result.retry_count or 0),
            _value(confirmation_state),
            result.error_code,
        )

        return record


def _trusted_context(context: Any) -> tuple[int, Any, bool, int | None]:
    """Extract the trusted fields from a ``ToolContext``.

    Only the backend-created context is accepted. The LLM never supplies this
    object, so user identity, privacy, and the database session cannot be
    overridden through tool arguments.
    """

    if context is None:
        raise ToolConfigurationError(
            "A trusted execution context is required to run tools."
        )

    try:
        user_id = context.require_user_id()

    except ToolConfigurationError:
        raise

    except (AttributeError, TypeError):
        raise ToolConfigurationError(
            "A trusted execution context is required to run tools."
        )

    except Exception as error:
        raise ToolConfigurationError(
            "The trusted execution context is invalid."
        ) from error

    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
    ):
        raise ToolConfigurationError(
            "A trusted owner identity is required to execute tools."
        )

    session = getattr(context, "session", None)

    if session is None:
        raise ToolConfigurationError(
            "A trusted database session is required to execute tools."
        )

    private = bool(getattr(context, "private", False))
    conversation_id = getattr(context, "conversation_id", None)

    if not isinstance(conversation_id, int) or isinstance(
        conversation_id,
        bool,
    ):
        conversation_id = None

    return user_id, session, private, conversation_id


def _execution_timeout() -> float:
    try:
        value = float(settings.TOOL_EXECUTION_TIMEOUT_SECONDS)

    except (TypeError, ValueError):
        return DEFAULT_EXECUTION_TIMEOUT_SECONDS

    return max(MIN_EXECUTION_TIMEOUT_SECONDS, value)


def _argument_limit() -> int:
    try:
        value = int(settings.TOOL_MAX_ARGUMENT_BYTES)

    except (TypeError, ValueError):
        return DEFAULT_ARGUMENT_LIMIT_BYTES

    return max(1, value)


def _tool_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    return cleaned[:MAX_TOOL_NAME_CHARS]


def _failure_for_tool_error(
    *,
    error: ToolError,
    tool: BaseTool,
    started: float,
) -> ToolResult:
    """Map one structured tool exception to a stable status/category pair."""

    status = ToolExecutionStatus.ERROR
    confirmation_required = False

    if isinstance(error, ToolValidationError):
        category = ToolErrorCategory.VALIDATION

    elif isinstance(error, ToolDisabledError):
        category = ToolErrorCategory.DISABLED
        status = ToolExecutionStatus.DENIED

    elif isinstance(error, ToolPermissionError):
        category = ToolErrorCategory.PERMISSION
        status = ToolExecutionStatus.DENIED

    elif isinstance(error, ToolConfirmationRequiredError):
        category = ToolErrorCategory.CONFIRMATION
        status = ToolExecutionStatus.CONFIRMATION_REQUIRED
        confirmation_required = True

    elif isinstance(
        error,
        (
            ToolConfirmationLimitError,
            ToolConfirmationNotFoundError,
            ToolConfirmationExpiredError,
            ToolConfirmationResolvedError,
        ),
    ):
        category = ToolErrorCategory.CONFIRMATION
        status = ToolExecutionStatus.DENIED

    elif isinstance(error, ToolTimeoutError):
        category = ToolErrorCategory.TIMEOUT
        status = ToolExecutionStatus.TIMEOUT

    elif isinstance(error, ToolProviderError):
        category = ToolErrorCategory.PROVIDER

    elif isinstance(error, ToolConfigurationError):
        category = ToolErrorCategory.CONFIGURATION

    elif isinstance(error, ToolSizeLimitError):
        category = ToolErrorCategory.SIZE_LIMIT

    elif isinstance(error, ToolMalformedCallError):
        category = ToolErrorCategory.MALFORMED_CALL

    elif isinstance(error, ToolExecutionError):
        category = ToolErrorCategory.EXECUTION

    else:
        category = ToolErrorCategory.EXECUTION

    return _failure(
        tool_name=tool.name,
        code=type(error).__name__,
        message=str(error),
        category=category,
        status=status,
        duration_ms=_elapsed_ms(started),
        provider=tool.provider_label,
        confirmation_required=confirmation_required,
    )


def _finalize_result(
    *,
    result: ToolResult,
    tool: BaseTool,
    started: float,
) -> ToolResult:
    """Normalize status, sanitize content, and bound the serialized size."""

    result.tool_name = tool.name
    result.duration_ms = _elapsed_ms(started)
    result.provider = result.provider or tool.provider_label
    result.metadata = redact_secrets(result.metadata or {})

    if result.error_message is not None:
        result.error_message = sanitize_error_message(
            result.error_message,
            fallback="The tool reported an error.",
        )

    limited, truncated, _size = enforce_result_limit(result.data)
    safe_data = redact_secrets(limited)
    limited, redaction_truncated, size = enforce_result_limit(safe_data)

    result.data = limited
    result.truncated = bool(
        result.truncated or truncated or redaction_truncated
    )
    result.result_bytes = size

    if result.success:
        result.status = ToolExecutionStatus.SUCCESS
        result.error_code = None
        result.error_message = None
        result.error_category = None
        return result

    result.status = _terminal_status(result.status)
    result.error_category = _error_category(result.error_category)

    if not result.error_code:
        result.error_code = "ToolExecutionError"

    if not result.error_message:
        result.error_message = "The tool reported an error."

    return result


def _terminal_status(value: Any) -> ToolExecutionStatus:
    if isinstance(value, ToolExecutionStatus):
        return (
            ToolExecutionStatus.ERROR
            if value == ToolExecutionStatus.SUCCESS
            else value
        )

    try:
        status = ToolExecutionStatus(value)

    except (TypeError, ValueError):
        return ToolExecutionStatus.ERROR

    if status == ToolExecutionStatus.SUCCESS:
        return ToolExecutionStatus.ERROR

    return status


def _error_category(value: Any) -> ToolErrorCategory:
    if isinstance(value, ToolErrorCategory):
        return value

    try:
        return ToolErrorCategory(value)

    except (TypeError, ValueError):
        return ToolErrorCategory.EXECUTION


def _failure(
    *,
    tool_name: str | None,
    code: str,
    message: Any,
    category: ToolErrorCategory,
    status: ToolExecutionStatus = ToolExecutionStatus.ERROR,
    duration_ms: float | None = None,
    provider: str | None = None,
    confirmation_required: bool = False,
    confirmation_id: int | None = None,
    confirmation_status: ConfirmationState = (
        ConfirmationState.NOT_REQUIRED
    ),
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    if status == ToolExecutionStatus.SUCCESS:
        status = ToolExecutionStatus.ERROR

    return error_result(
        tool_name=tool_name,
        code=code,
        message=sanitize_error_message(message),
        category=category,
        status=status,
        duration_ms=duration_ms,
        provider=provider,
        confirmation_required=confirmation_required,
        confirmation_id=confirmation_id,
        confirmation_status=confirmation_status,
        metadata=metadata,
    )


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (time.perf_counter() - started) * 1000.0), 2)


def _value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


def _replay_result(record: Any) -> ToolResult:
    """Build a metadata-only result for an idempotent replay.

    The original result payload is deliberately not retained, so a replay can
    only report the stored outcome fields. An in-flight duplicate is reported as
    running instead of executing a second time.
    """

    try:
        status = (
            record.status
            if isinstance(record.status, ToolExecutionStatus)
            else ToolExecutionStatus(record.status)
        )

    except (TypeError, ValueError):
        status = ToolExecutionStatus.ERROR

    try:
        lifecycle = (
            record.lifecycle_status
            if isinstance(
                record.lifecycle_status,
                ToolExecutionLifecycle,
            )
            else ToolExecutionLifecycle(record.lifecycle_status)
        )

    except (TypeError, ValueError):
        lifecycle = ToolExecutionLifecycle.FAILED

    common = {
        "tool_name": record.tool_name,
        "confirmation_status": record.confirmation_status,
        "truncated": bool(record.truncated),
        "duration_ms": (
            None
            if record.duration_ms is None
            else float(record.duration_ms)
        ),
        "result_bytes": record.result_bytes,
        "provider": record.provider,
        "retry_count": int(record.retry_count or 0),
        "execution_id": record.id,
        "idempotent_replay": True,
        "metadata": {
            "idempotent_replay": True,
            "execution_id": record.id,
            "lifecycle": lifecycle.value,
        },
    }

    if lifecycle in {
        ToolExecutionLifecycle.PENDING,
        ToolExecutionLifecycle.APPROVED,
        ToolExecutionLifecycle.RUNNING,
    }:
        return ToolResult(
            success=False,
            data=None,
            status=ToolExecutionStatus.RUNNING,
            error_code="ToolExecutionInProgress",
            error_message=(
                "The original tool request is still in progress."
            ),
            error_category=ToolErrorCategory.EXECUTION,
            confirmation_required=False,
            **common,
        )

    success = status == ToolExecutionStatus.SUCCESS

    return ToolResult(
        success=success,
        data=None,
        status=status,
        error_code=record.error_code,
        error_message=(
            None
            if success
            else "The original tool request did not succeed."
        ),
        error_category=record.error_category,
        confirmation_required=(
            status == ToolExecutionStatus.CONFIRMATION_REQUIRED
        ),
        **common,
    )


tool_executor = ToolExecutor()


__all__ = ["ToolExecutor", "tool_executor"]
