
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.exceptions.conversation_exceptions import (
    ConversationNotFoundError,
    ConversationValidationError,
)
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.models.message import MessageRole
from app.services.chat_service import chat_service
from app.services.conversation_service import conversation_service
from app.services.tool_confirmation_service import tool_confirmation_service
from app.services.tool_orchestrator import tool_orchestrator
from app.tools.base import ToolContext
from app.tools.exceptions import (
    ToolConfirmationExpiredError,
    ToolConfirmationMismatchError,
    ToolConfirmationNotFoundError,
    ToolConfirmationResolvedError,
)
from app.tools.executor import tool_executor


# Configure logging
logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/chat",
    tags=["Chat"],
    dependencies=[Depends(require_csrf)]
)


class ChatRequest(BaseModel):
    message: str
    private: bool = False

    # Omit to start a new conversation. Supply an owned conversation id to
    # continue an existing conversation.
    conversation_id: int | None = Field(default=None, ge=1)


class ToolExecutionSummary(BaseModel):
    """Safe, data-free summary of one tool execution attempt.

    Raw tool output is never returned here. It may contain untrusted content
    or sensitive values and is deliberately excluded from the public API.
    """

    success: bool = False
    status: str | None = None
    tool: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_category: str | None = None
    confirmation_required: bool = False
    confirmation_id: int | None = None
    confirmation_status: str | None = None
    truncated: bool = False
    duration_ms: float | None = None
    result_bytes: int | None = None
    provider: str | None = None


class PendingConfirmationResponse(BaseModel):
    """A tool action that was proposed but has not executed."""

    confirmation_id: int | None = None
    tool: str | None = None
    category: str | None = None
    preview: str | None = None
    expires_at: datetime | None = None


class ChatResponse(BaseModel):
    provider: str
    model: str
    response: str
    conversation_id: int | None = None

    # Phase 11H fields. They are optional so the historical chat response
    # shape is preserved exactly when tool calling is disabled or no tool was
    # considered.
    tool_calling: bool | None = None
    tool_executions: list[ToolExecutionSummary] | None = None
    pending_confirmations: list[PendingConfirmationResponse] | None = None
    limit_reached: bool | None = None
    provider_failure: bool | None = None
    confirmation_id: int | None = None
    confirmation_status: str | None = None


def _confirmation_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Tool confirmation not found.",
    )


def _confirmation_expired() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="That tool confirmation has expired.",
    )


def _confirmation_resolved() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "That tool confirmation has already been resolved or is no "
            "longer valid."
        ),
    )


async def _get_owned_confirmation(
    *,
    session: AsyncSession,
    user_id: int,
    confirmation_id: int,
):
    """Load a confirmation for its owner, or raise a 404.

    Cross-owner access is indistinguishable from a missing confirmation so
    the API does not confirm the existence of another user's pending action.
    """

    try:
        return await tool_confirmation_service.get(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

    except ToolConfirmationNotFoundError as error:
        raise _confirmation_not_found() from error


def _ensure_pending(confirmation) -> None:
    """Reject an already consumed, failed, or expired confirmation."""

    status_value = getattr(confirmation.status, "value", confirmation.status)

    if status_value == "expired":
        raise _confirmation_expired()

    if status_value != "pending":
        raise _confirmation_resolved()


async def _get_owned_conversation(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int | None,
):
    if conversation_id is None:
        return None

    try:
        return await conversation_service.get_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    except ConversationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        ) from error


async def _persist_tool_turn(
    *,
    session: AsyncSession,
    conversation,
    payload: dict,
    private: bool = False,
) -> dict:
    """Persist the safe tool transcript, then the assistant answer."""

    result = dict(payload)

    if conversation is None:
        return result

    await conversation_service.add_tool_events(
        session=session,
        conversation=conversation,
        events=result.get("tool_events"),
        provider=result.get("provider"),
        model=result.get("model"),
        is_private=private,
    )

    try:
        await conversation_service.add_message(
            session=session,
            conversation=conversation,
            role=MessageRole.ASSISTANT,
            content=result.get("response") or "",
            provider=result.get("provider"),
            model=result.get("model"),
            is_private=private,
        )

    except Exception:
        # A generated answer is still returned even if persistence fails.
        logger.exception(
            "Failed to persist the assistant message for conversation %s.",
            conversation.id,
        )

    result["conversation_id"] = conversation.id

    return result


@router.post(
    "/",
    response_model=ChatResponse,
    response_model_exclude_none=True
)
async def chat(
    request: ChatRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    # Reject empty or whitespace-only prompts.
    if not request.message or not request.message.strip():

        raise HTTPException(
            status_code=400,
            detail="Prompt cannot be empty"
        )

    try:

        result = await chat_service.get_response(
            prompt=request.message,
            private=request.private,
            user_id=user_id,
            session=session,
            conversation_id=request.conversation_id
        )

        return result

    except ConversationNotFoundError as error:

        logger.warning(
            "Conversation not found while processing a chat request."
        )

        raise HTTPException(
            status_code=404,
            detail="Conversation not found."
        ) from error

    except ConversationValidationError as error:

        logger.warning(
            "Invalid conversation data in a chat request: %s",
            error
        )

        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except ValueError as error:

        logger.warning(
            "Invalid chat prompt: %s",
            error
        )

        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except LLMTimeoutError as error:

        # Log the full technical error on the server, but never send
        # provider details or stack traces to the API client.
        logger.error(
            "LLM request timed out.",
            exc_info=True
        )

        raise HTTPException(
            status_code=504,
            detail="The AI provider timed out. Please try again."
        ) from error

    except LLMProviderError as error:

        logger.error(
            "LLM provider request failed.",
            exc_info=True
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "The AI provider is currently unavailable. "
                "Please try again later."
            )
        ) from error

    except LLMUnavailableError as error:

        logger.error(
            "All LLM providers are unavailable.",
            exc_info=True
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "The AI service is currently unavailable. "
                "Please try again later."
            )
        ) from error

    except Exception as error:

        logger.exception(
            "Unexpected error while processing a chat request."
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "An unexpected error occurred while processing "
                "your request."
            )
        ) from error


@router.post(
    "/confirmations/{confirmation_id}/approve",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def approve_chat_confirmation(
    confirmation_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Approve and run one stored confirmation, then answer the user.

    The client never supplies tool arguments. The executor re-validates the
    exact arguments stored at proposal time and atomically claims the
    confirmation, so an approval cannot be replayed, tampered with, or used by
    another owner. The resumed model turn is text-only: the model cannot start
    another tool chain or approve its own action.
    """

    confirmation = await _get_owned_confirmation(
        session=session,
        user_id=user_id,
        confirmation_id=confirmation_id,
    )

    _ensure_pending(confirmation)

    conversation = await _get_owned_conversation(
        session=session,
        user_id=user_id,
        conversation_id=confirmation.conversation_id,
    )

    tool_name = confirmation.tool_name
    stored_arguments = dict(confirmation.arguments or {})
    conversation_id = confirmation.conversation_id

    context = ToolContext(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        private=False,
    )

    result = await tool_executor.execute(
        tool_name=tool_name,
        arguments=stored_arguments,
        context=context,
        confirmation_id=confirmation.id,
        create_confirmation=False,
    )

    if result.error_code == "ToolConfirmationExpiredError":
        raise _confirmation_expired()

    if result.error_code in {
        "ToolConfirmationResolvedError",
        "ToolConfirmationMismatchError",
        "ToolConfirmationNotFoundError",
    }:
        raise _confirmation_resolved()

    payload = await tool_orchestrator.resume_after_confirmation(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id or 0,
        tool_name=tool_name,
        result=result,
        private=False,
    )

    payload = dict(payload)
    payload["confirmation_id"] = confirmation_id
    payload["confirmation_status"] = (
        "executed" if result.success else "failed"
    )

    return await _persist_tool_turn(
        session=session,
        conversation=conversation,
        payload=payload,
        private=False,
    )


@router.post(
    "/confirmations/{confirmation_id}/reject",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def reject_chat_confirmation(
    confirmation_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Reject a pending confirmation without executing anything."""

    confirmation = await _get_owned_confirmation(
        session=session,
        user_id=user_id,
        confirmation_id=confirmation_id,
    )

    conversation = await _get_owned_conversation(
        session=session,
        user_id=user_id,
        conversation_id=confirmation.conversation_id,
    )

    tool_name = confirmation.tool_name

    try:
        await tool_confirmation_service.reject(
            session=session,
            user_id=user_id,
            confirmation_id=confirmation_id,
        )

    except ToolConfirmationNotFoundError as error:
        raise _confirmation_not_found() from error

    except ToolConfirmationExpiredError as error:
        raise _confirmation_expired() from error

    except (
        ToolConfirmationResolvedError,
        ToolConfirmationMismatchError,
    ) as error:
        raise _confirmation_resolved() from error

    payload = {
        "provider": "system",
        "model": "",
        "response": (
            f"The '{tool_name}' action was rejected. Nothing was executed."
        ),
        "routing_policy": "tool_confirmation",
        "cache_hit": False,
        "fallback_used": False,
        "duration_ms": 0.0,
        "usage": None,
        "request_type": "chat",
        "tool_calling": True,
        "tool_executions": [],
        "pending_confirmations": [],
        "tool_events": [
            {
                "kind": "tool_rejected",
                "content": (
                    f"Tool '{tool_name}' was rejected by the user. "
                    "Nothing was executed."
                ),
                "tool": tool_name,
                "status": "rejected",
                "success": False,
                "confirmation_id": confirmation_id,
            }
        ],
        "limit_reached": False,
        "provider_failure": False,
        "confirmation_id": confirmation_id,
        "confirmation_status": "rejected",
    }

    return await _persist_tool_turn(
        session=session,
        conversation=conversation,
        payload=payload,
        private=False,
    )


__all__ = ["router"]
