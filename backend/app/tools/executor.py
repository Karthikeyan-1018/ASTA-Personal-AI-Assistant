"""Central tool execution engine.

Every tool call, whether from chat or the REST API, passes through this
executor. It resolves the registered tool, validates arguments, re-checks the
server-side permission policy, enforces confirmation, applies a timeout, bounds
the result size, records metadata-only history, and converts failures into a
normalized result.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.config import settings
from app.services.tool_confirmation_service import (
    ClaimedConfirmation,
    tool_confirmation_service,
)
from app.services.tool_history_service import tool_history_service
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolConfirmationExpiredError,
    ToolConfirmationLimitError,
    ToolConfirmationMismatchError,
    ToolConfirmationNotFoundError,
    ToolConfirmationResolvedError,
    ToolConfigurationError,
    ToolDisabledError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolProviderError,
    ToolSizeLimitError,
    ToolTimeoutError,
    ToolValidationError,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from app.tools.result import enforce_result_limit, error_result
from app.tools.schemas import (
    ToolResult,
    arguments_digest,
    json_size,
)


logger = logging.getLogger(__name__)


class ToolExecutor:

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        confirmation_id: int | None = None,
        create_confirmation: bool = True,
    ) -> ToolResult:
        """Execute one tool call under the full server-side policy."""

        started = time.perf_counter()

        try:
            user_id = context.require_user_id()
            tool = tool_registry.get(tool_name)

        except ToolNotFoundError as error:
            return _failure(
                tool_name=tool_name,
                code="ToolNotFound",
                message=str(error),
                category=ToolErrorCategory.NOT_FOUND,
                status=ToolExecutionStatus.ERROR,
                duration_ms=_elapsed_ms(started),
            )

        except ToolError as error:
            return _failure(
                tool_name=tool_name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.PERMISSION,
                status=ToolExecutionStatus.DENIED,
                duration_ms=_elapsed_ms(started),
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

        if argument_size > max(1, settings.TOOL_MAX_ARGUMENT_BYTES):
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

        validated_arguments = validated.model_dump(mode="json")

        try:
            decision = permission_policy.require_allowed(
                tool,
                private=context.private,
            )

        except ToolError as error:
            category = (
                ToolErrorCategory.DISABLED
                if isinstance(error, ToolDisabledError)
                else ToolErrorCategory.PERMISSION
            )
            return await self._reject(
                context=context,
                tool=tool,
                error=error,
                started=started,
                category=category,
                status=ToolExecutionStatus.DENIED,
            )

        claimed: ClaimedConfirmation | None = None

        if decision.requires_confirmation and confirmation_id is None:
            if not create_confirmation:
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=ToolError(
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
                    session=context.session,
                    user_id=user_id,
                    confirmation_id=confirmation_id,
                    expected_tool_name=tool.name,
                    expected_arguments=validated_arguments,
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

            # The confirmation stores the exact approved payload. Re-validate
            # it rather than trusting the caller to resend the same values.
            try:
                validated = tool.validate_arguments(claimed.arguments)
                validated_arguments = validated.model_dump(mode="json")

            except ToolValidationError as error:
                await tool_confirmation_service.mark_failed(
                    session=context.session,
                    user_id=user_id,
                    confirmation_id=claimed.id,
                )
                return await self._reject(
                    context=context,
                    tool=tool,
                    error=error,
                    started=started,
                    category=ToolErrorCategory.CONFIRMATION,
                    status=ToolExecutionStatus.ERROR,
                    confirmation_state=ConfirmationState.CONFIRMED,
                )

            # Re-evaluate policy after approval. Configuration can change
            # between proposal and execution.
            try:
                permission_policy.require_allowed(
                    tool,
                    private=context.private,
                )

            except ToolError as error:
                await tool_confirmation_service.mark_failed(
                    session=context.session,
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

        result = await self._run(
            context=context,
            tool=tool,
            validated=validated,
            started=started,
        )

        if claimed is not None:

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
                await tool_confirmation_service.mark_failed(
                    session=context.session,
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

    async def _run(
        self,
        *,
        context: ToolContext,
        tool: BaseTool,
        validated,
        started: float,
    ) -> ToolResult:
        timeout = max(1, int(settings.TOOL_EXECUTION_TIMEOUT_SECONDS))

        try:
            result = await asyncio.wait_for(
                tool.execute(context, validated),
                timeout=timeout,
            )

        except (asyncio.TimeoutError, ToolTimeoutError):
            return _failure(
                tool_name=tool.name,
                code="ToolTimeout",
                message=(
                    f"Tool execution exceeded the {timeout}-second timeout."
                ),
                category=ToolErrorCategory.TIMEOUT,
                status=ToolExecutionStatus.TIMEOUT,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolValidationError as error:
            return _failure(
                tool_name=tool.name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.VALIDATION,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolProviderError as error:
            return _failure(
                tool_name=tool.name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.PROVIDER,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolConfigurationError as error:
            return _failure(
                tool_name=tool.name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.CONFIGURATION,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolSizeLimitError as error:
            return _failure(
                tool_name=tool.name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.SIZE_LIMIT,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolExecutionError as error:
            return _failure(
                tool_name=tool.name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.EXECUTION,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except ToolError as error:
            return _failure(
                tool_name=tool.name,
                code=type(error).__name__,
                message=str(error),
                category=ToolErrorCategory.EXECUTION,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        except Exception:
            logger.exception(
                "Unexpected failure in tool '%s' for user %s.",
                tool.name,
                context.user_id,
            )
            return _failure(
                tool_name=tool.name,
                code="ToolInternalError",
                message="The tool failed unexpectedly.",
                category=ToolErrorCategory.INTERNAL,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        if not isinstance(result, ToolResult):
            return _failure(
                tool_name=tool.name,
                code="ToolContractViolation",
                message="The tool returned an invalid result.",
                category=ToolErrorCategory.INTERNAL,
                duration_ms=_elapsed_ms(started),
                provider=tool.provider_label,
            )

        limited, truncated, size = enforce_result_limit(result.data)

        result.tool_name = tool.name
        result.data = limited
        result.truncated = bool(result.truncated or truncated)
        result.result_bytes = size
        result.duration_ms = _elapsed_ms(started)
        result.provider = result.provider or tool.provider_label

        return result

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

        await tool_confirmation_service.attach_execution(
            session=context.session,
            user_id=context.user_id,
            confirmation_id=confirmation_id,
            execution_id=record.id if record is not None else None,
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
        return await tool_history_service.record(
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
        )


def _failure(
    *,
    tool_name: str,
    code: str,
    message: str,
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
    return error_result(
        tool_name=tool_name,
        code=code,
        message=message,
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


tool_executor = ToolExecutor()


__all__ = ["ToolExecutor", "tool_executor"]
