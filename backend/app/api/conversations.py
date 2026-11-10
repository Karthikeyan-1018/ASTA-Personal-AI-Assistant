"""REST endpoints for conversation persistence."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.core.config import settings
from app.database.connection import get_db
from app.exceptions.conversation_exceptions import (
    ConversationNotFoundError,
    ConversationValidationError,
)
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageListResponse,
)
from app.services.conversation_service import conversation_service


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/conversations",
    tags=["Conversations"]
)


def _conversation_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="Conversation not found."
    )


@router.post(
    "/",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    payload: ConversationCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await conversation_service.create_conversation(
            session=session,
            user_id=user_id,
            title=payload.title
        )

    except ConversationValidationError as error:
        logger.warning("Invalid conversation creation request: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error

    except Exception as error:
        logger.exception("Unexpected error while creating a conversation.")
        raise HTTPException(
            status_code=500,
            detail="Unable to create conversation."
        ) from error


@router.get("/", response_model=list[ConversationResponse])
async def list_conversations(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await conversation_service.list_conversations(
            session=session,
            user_id=user_id
        )

    except Exception as error:
        logger.exception("Unexpected error while listing conversations.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve conversations."
        ) from error


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await conversation_service.get_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id
        )

    except ConversationNotFoundError as error:
        raise _conversation_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while retrieving a conversation.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve conversation."
        ) from error


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await conversation_service.update_title(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            title=payload.title
        )

    except ConversationNotFoundError as error:
        raise _conversation_not_found() from error

    except ConversationValidationError as error:
        logger.warning("Invalid conversation update request: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error

    except Exception as error:
        logger.exception("Unexpected error while updating a conversation.")
        raise HTTPException(
            status_code=500,
            detail="Unable to update conversation."
        ) from error


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(
    conversation_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        await conversation_service.delete_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id
        )

    except ConversationNotFoundError as error:
        raise _conversation_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while deleting a conversation.")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete conversation."
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessageListResponse
)
async def list_messages(
    conversation_id: int,
    limit: int = Query(
        default=settings.CONVERSATION_MESSAGE_PAGE_SIZE,
        ge=1,
        le=100
    ),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        messages, total = await conversation_service.get_messages(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset
        )

    except ConversationNotFoundError as error:
        raise _conversation_not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while listing messages.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve messages."
        ) from error

    return MessageListResponse(
        messages=messages,
        total=total,
        limit=limit,
        offset=offset
    )
