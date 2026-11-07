
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_optional_user_id
from app.database.connection import get_db
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.chat_service import chat_service


# Configure logging
logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    message: str
    private: bool = False


class ChatResponse(BaseModel):
    provider: str
    model: str
    response: str


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user_id: int | None = Depends(get_optional_user_id),
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
            session=session
        )

        return result

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
