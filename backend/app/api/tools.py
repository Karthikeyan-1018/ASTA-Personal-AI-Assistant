"""Authenticated REST endpoints for the tool engine.

Identity is always derived from the server-side session. The client can never
supply an owner id, a permission decision, or an approved argument payload.
Confirmation approval consumes the stored, validated arguments; a changed
payload is rejected by the executor.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.schemas.tool import (
    EmergencyStopResponse,
    ToolConfirmationActionResponse,
    ToolConfirmationListResponse,
    ToolConfirmationResponse,
    ToolDefinitionResponse,
    ToolExecuteRequest,
    ToolExecutionListResponse,
    ToolExecutionResponse,
    ToolExecutionStatusResponse,
    ToolExecutionSummaryResponse,
    ToolListResponse,
    ToolMetricsResponse,
    ToolResultResponse,
    ToolStatsResponse,
)
from app.services.tool_confirmation_service import tool_confirmation_service
from app.services.tool_history_service import (
    MAX_IDEMPOTENCY_KEY_CHARS,
    tool_history_service,
)
from app.services.tool_metrics_service import tool_metrics_service
from app.tools.base import ToolContext
from app.tools.enums import ToolExecutionLifecycle, ToolExecutionStatus
from app.tools.exceptions import (
    ToolConfirmationExpiredError,
    ToolConfirmationMismatchError,
    ToolConfirmationNotFoundError,
    ToolConfirmationResolvedError,
)
from app.tools.executor import tool_executor
from app.tools import os_audit, os_security
from app.tools.registry import tool_registry


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/tools",
    tags=["Tools"],
    dependencies=[Depends(require_csrf)],
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _confirmation_response(confirmation) -> ToolConfirmationResponse:
    expires_at = _aware(confirmation.expires_at)
    is_expired = bool(
        expires_at is not None
        and confirmation.status.value == "pending"
        and expires_at <= datetime.now(timezone.utc)
    )

    return ToolConfirmationResponse(
        id=confirmation.id,
        tool_name=confirmation.tool_name,
        category=confirmation.category,
        status=confirmation.status,
        arguments_preview=confirmation.arguments_preview,
        created_at=confirmation.created_at,
        expires_at=confirmation.expires_at,
        resolved_at=confirmation.resolved_at,
        conversation_id=confirmation.conversation_id,
        execution_id=confirmation.execution_id,
        is_expired=is_expired,
    )


@router.get("/", response_model=ToolListResponse)
async def list_tools(
    user_id: int = Depends(get_current_user_id),
):
    """List every registered tool, including currently disabled ones."""

    definitions = tool_registry.definitions(include_disabled=True)

    return ToolListResponse(
        tools=[
            ToolDefinitionResponse(
                name=definition.name,
                description=definition.description,
                category=definition.category.value,
                enabled=definition.enabled,
                requires_confirmation=definition.requires_confirmation,
                allowed_in_private=definition.allowed_in_private,
                parameters=definition.parameters,
                output_schema=definition.output_schema,
            )
            for definition in definitions
        ]
    )


@router.post("/execute", response_model=ToolResultResponse)
async def execute_tool(
    request: ToolExecuteRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Execute a tool under the full server-side policy.

    Permission failures, validation errors, and timeouts are returned as a
    structured result instead of raising, so a caller cannot distinguish a
    missing tool from a denied one by status code alone.

    An optional ``Idempotency-Key`` header prevents a retried HTTP request from
    executing the same action twice. A replay returns stored metadata only and
    never the original payload.
    """

    if idempotency_key is not None and (
        len(idempotency_key) > MAX_IDEMPOTENCY_KEY_CHARS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key is too long.",
        )

    context = ToolContext(
        session=session,
        user_id=user_id,
        private=False,
    )

    result = await tool_executor.execute(
        tool_name=request.tool,
        arguments=request.arguments,
        context=context,
        idempotency_key=idempotency_key,
    )

    return result.as_dict()


@router.get("/executions", response_model=ToolExecutionListResponse)
async def list_executions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tool: str | None = Query(default=None, max_length=100),
    status_filter: str | None = Query(default=None, alias="status"),
    lifecycle_filter: str | None = Query(default=None, alias="lifecycle"),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """List metadata-only execution history for the authenticated owner."""

    parsed_status = None
    parsed_lifecycle = None

    if status_filter:
        try:
            parsed_status = ToolExecutionStatus(status_filter)

        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown execution status filter.",
            ) from error

    if lifecycle_filter:
        try:
            parsed_lifecycle = ToolExecutionLifecycle(lifecycle_filter)

        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown execution lifecycle filter.",
            ) from error

    executions, total = await tool_history_service.list_executions(
        session=session,
        user_id=user_id,
        limit=limit,
        offset=offset,
        tool_name=tool,
        status=parsed_status,
        lifecycle_status=parsed_lifecycle,
    )

    return ToolExecutionListResponse(
        executions=[
            ToolExecutionResponse.model_validate(execution)
            for execution in executions
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/executions/stats", response_model=ToolStatsResponse)
async def execution_stats(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    stats = await tool_history_service.stats(
        session=session,
        user_id=user_id,
    )

    return ToolStatsResponse(**stats)


async def _owned_execution(session: AsyncSession, user_id: int, execution_id: int):
    execution = await tool_history_service.get_execution(
        session=session,
        user_id=user_id,
        execution_id=execution_id,
    )

    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool execution not found.",
        )

    return execution


@router.get(
    "/executions/{execution_id}",
    response_model=ToolExecutionResponse,
)
async def get_execution(
    execution_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return metadata for one owner-scoped execution attempt."""

    execution = await _owned_execution(session, user_id, execution_id)

    return ToolExecutionResponse.model_validate(execution)


@router.get(
    "/executions/{execution_id}/status",
    response_model=ToolExecutionStatusResponse,
)
async def get_execution_status(
    execution_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return the current lifecycle and outcome of one execution attempt."""

    execution = await _owned_execution(session, user_id, execution_id)

    return ToolExecutionStatusResponse.model_validate(execution)


@router.get(
    "/executions/{execution_id}/summary",
    response_model=ToolExecutionSummaryResponse,
)
async def get_execution_summary(
    execution_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Return a compact, safe summary with no argument or result payload."""

    execution = await _owned_execution(session, user_id, execution_id)

    return ToolExecutionSummaryResponse.model_validate(execution)


@router.get("/metrics", response_model=ToolMetricsResponse)
async def tool_metrics(
    user_id: int = Depends(get_current_user_id),
):
    """Return process-local, aggregate tool metrics.

    Metrics reset when the process restarts. Durable per-owner history remains
    available through the execution endpoints. No payload data is included.
    """

    return ToolMetricsResponse(**tool_metrics_service.snapshot())


@router.get(
    "/confirmations",
    response_model=ToolConfirmationListResponse,
)
async def list_confirmations(
    conversation_id: int | None = Query(default=None, ge=1),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    confirmations = await tool_confirmation_service.list_pending(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    return ToolConfirmationListResponse(
        confirmations=[
            _confirmation_response(item) for item in confirmations
        ]
    )


@router.post(
    "/confirmations/{confirmation_id}/confirm",
    response_model=ToolConfirmationActionResponse,
)
async def confirm_tool_action(
    confirmation_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Approve and execute one exact, unexpired, single-use tool call."""

    try:
        confirmation = await tool_confirmation_service.get(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

    except ToolConfirmationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool confirmation not found.",
        ) from error

    context = ToolContext(
        session=session,
        user_id=user_id,
        conversation_id=confirmation.conversation_id,
        private=False,
    )

    result = await tool_executor.execute(
        tool_name=confirmation.tool_name,
        arguments=dict(confirmation.arguments or {}),
        context=context,
        confirmation_id=confirmation.id,
        create_confirmation=False,
    )

    if result.error_code == "ToolConfirmationExpiredError":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That tool confirmation has expired.",
        )

    if result.error_code in {
        "ToolConfirmationResolvedError",
        "ToolConfirmationMismatchError",
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That tool confirmation has already been resolved.",
        )

    if result.error_code == "ToolEmergencyStopError":
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="OS automation is blocked by the emergency stop.",
        )

    try:
        fresh = await tool_confirmation_service.get(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

    except ToolConfirmationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool confirmation not found.",
        ) from error

    return ToolConfirmationActionResponse(
        confirmation=_confirmation_response(fresh),
        result=result.as_dict(),
    )


@router.post(
    "/confirmations/{confirmation_id}/reject",
    response_model=ToolConfirmationActionResponse,
)
async def reject_tool_action(
    confirmation_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Reject a pending tool confirmation without executing anything."""

    try:
        await tool_confirmation_service.reject(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

    except ToolConfirmationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool confirmation not found.",
        ) from error

    except ToolConfirmationExpiredError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That tool confirmation has expired.",
        ) from error

    except (
        ToolConfirmationResolvedError,
        ToolConfirmationMismatchError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That tool confirmation has already been resolved.",
        ) from error

    fresh = await tool_confirmation_service.get(
        session=session,
        user_id=user_id,
        confirmation_id=confirmation_id,
    )

    return ToolConfirmationActionResponse(
        confirmation=_confirmation_response(fresh),
        result=None,
    )


def _emergency_stop_response(state) -> EmergencyStopResponse:
    return EmergencyStopResponse(
        engaged=state.engaged,
        engaged_at=state.engaged_at,
        engaged_by_user_id=state.engaged_by_user_id,
        recovery_configured=state.recovery_configured,
    )


@router.get("/emergency-stop", response_model=EmergencyStopResponse)
async def emergency_stop_status(
    user_id: int = Depends(get_current_user_id),
):
    """Return the current OS automation emergency-stop state.

    This endpoint never exposes the recovery token or any secret.
    """

    return _emergency_stop_response(os_security.emergency_stop_state())


@router.post("/emergency-stop", response_model=EmergencyStopResponse)
async def engage_emergency_stop(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Engage the OS automation emergency stop.

    This is a trusted control endpoint, not a registered LLM tool. It only
    flips a process-local flag and invalidates pending confirmations. It never
    kills a process, touches ASTA or PostgreSQL, deletes a file, or changes a
    setting.
    """

    state = os_security.engage_emergency_stop(user_id=user_id)

    try:
        rejected = await tool_confirmation_service.reject_all_pending(
            session=session
        )

        if rejected:
            logger.warning(
                "Emergency stop invalidated %s pending tool confirmation(s).",
                rejected,
            )

    except Exception:
        # The in-memory stop still blocks execution even if the database
        # update fails. Pending confirmations are also re-checked by the
        # executor before they can run.
        logger.exception(
            "Failed to invalidate pending confirmations after the emergency "
            "stop."
        )

    os_audit.log_emergency_stop(
        action="engaged",
        user_id=user_id,
        engaged_at=state.engaged_at,
        engaged_by_user_id=state.engaged_by_user_id,
    )

    return _emergency_stop_response(state)


@router.post("/emergency-stop/recover", response_model=EmergencyStopResponse)
async def recover_emergency_stop(
    recovery_token: str | None = Header(
        default=None,
        alias="X-Emergency-Recovery-Token",
    ),
    user_id: int = Depends(get_current_user_id),
):
    """Clear the emergency stop with the trusted server-side token.

    Authentication alone is never sufficient. A missing configured token, a
    missing header, or a mismatch fails closed.
    """

    if not os_security.recover_emergency_stop(
        user_id=user_id,
        token=recovery_token,
    ):
        os_audit.log_emergency_stop(
            action="recovery_denied",
            user_id=user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Emergency stop recovery is not authorized.",
        )

    return _emergency_stop_response(os_security.emergency_stop_state())


__all__ = ["router"]
