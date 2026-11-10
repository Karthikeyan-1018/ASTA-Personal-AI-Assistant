
import logging

from fastapi import APIRouter, Depends, HTTPException
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
from app.services.chat_service import chat_service


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


class ChatResponse(BaseModel):
    provider: str
    model: str
    response: str
    conversation_id: int | None = None


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
