import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.exceptions.memory_exceptions import (
    EmbeddingServiceError,
    MemoryExtractionError,
    MemoryNotFoundError,
    MemoryValidationError,
    SemanticRetrievalUnavailable,
)
from app.memory.memory_extractor import memory_extractor
from app.memory.memory_retriever import memory_retriever
from app.schemas.memory import (
    MemoryCreate,
    MemoryResponse,
    MemoryUpdate,
)
from app.schemas.memory_extraction import (
    MemoryExtractionRequest,
    MemoryExtractionResponse,
)
from app.schemas.memory_retrieval import (
    MemoryRetrievalRequest,
    MemoryRetrievalResponse,
    RetrievedMemoryItem,
)
from app.services.memory_service import memory_service


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/memories",
    tags=["Memories"],
    dependencies=[Depends(require_csrf)]
)


def _memory_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="Memory not found."
    )


@router.post(
    "/",
    response_model=MemoryResponse,
    status_code=201
)
async def create_memory(
    payload: MemoryCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await memory_service.create_memory(
            session=session,
            user_id=user_id,
            content=payload.content,
            category=payload.category
        )

    except MemoryValidationError as error:
        logger.warning(
            "Invalid memory creation request: %s",
            error
        )

        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while creating a memory."
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to create memory."
        ) from error


@router.get("/", response_model=list[MemoryResponse])
async def list_memories(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await memory_service.get_user_memories(
            session=session,
            user_id=user_id
        )

    except Exception as error:
        logger.exception(
            "Unexpected error while listing memories."
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve memories."
        ) from error


@router.post("/extract", response_model=MemoryExtractionResponse)
async def extract_memories(
    payload: MemoryExtractionRequest,
    user_id: int = Depends(get_current_user_id)
):
    """Suggest candidate memories without saving or approving anything."""

    try:
        candidates = await memory_extractor.extract(
            conversation=payload.conversation,
            private=payload.private
        )

        return MemoryExtractionResponse(candidates=candidates)

    except MemoryValidationError as error:
        logger.warning(
            "Invalid memory extraction request: %s",
            error
        )

        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except LLMTimeoutError as error:
        logger.error(
            "LLM request timed out during memory extraction.",
            exc_info=True
        )

        raise HTTPException(
            status_code=504,
            detail="The AI provider timed out. Please try again."
        ) from error

    except (LLMProviderError, LLMUnavailableError) as error:
        logger.error(
            "LLM provider failed during memory extraction.",
            exc_info=True
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "The AI provider is currently unavailable. "
                "Please try again later."
            )
        ) from error

    except MemoryExtractionError as error:
        # Never include the raw model output: it derives from the private
        # conversation and may contain sensitive text.
        logger.error(
            "The AI returned an invalid memory extraction response."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "The AI returned an invalid memory extraction response."
            )
        ) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while extracting memories."
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to extract memories."
        ) from error


@router.post("/retrieve", response_model=MemoryRetrievalResponse)
async def retrieve_memories(
    payload: MemoryRetrievalRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Return approved memories ranked by relevance to the query."""

    try:
        results = await memory_retriever.retrieve(
            session=session,
            user_id=user_id,
            query=payload.query,
            category=payload.category,
            limit=payload.limit,
            mode=payload.mode
        )

        memories = [
            RetrievedMemoryItem(
                content=result.content,
                category=result.category,
                relevance_score=result.relevance_score
            )
            for result in results
        ]

        return MemoryRetrievalResponse(
            memories=memories,
            total=len(memories)
        )

    except MemoryValidationError as error:
        logger.warning(
            "Invalid memory retrieval request: %s",
            error
        )

        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except SemanticRetrievalUnavailable as error:
        logger.warning(
            "Semantic memory retrieval is unavailable."
        )

        raise HTTPException(
            status_code=503,
            detail="Semantic memory retrieval is currently unavailable."
        ) from error

    except EmbeddingServiceError as error:
        logger.warning(
            "Memory embedding generation failed during retrieval."
        )

        raise HTTPException(
            status_code=503,
            detail="Semantic memory retrieval is currently unavailable."
        ) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while retrieving memories."
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve memories."
        ) from error


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await memory_service.get_memory_by_id(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

    except MemoryNotFoundError as error:
        raise _memory_not_found() from error

    except Exception as error:
        logger.exception(
            "Unexpected error while retrieving memory %s.",
            memory_id
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve memory."
        ) from error


@router.put("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: int,
    payload: MemoryUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await memory_service.update_memory(
            session=session,
            user_id=user_id,
            memory_id=memory_id,
            content=payload.content,
            category=payload.category
        )

    except MemoryNotFoundError as error:
        raise _memory_not_found() from error

    except MemoryValidationError as error:
        logger.warning(
            "Invalid memory update request for memory %s: %s",
            memory_id,
            error
        )

        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while updating memory %s.",
            memory_id
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to update memory."
        ) from error


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        await memory_service.delete_memory(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

    except MemoryNotFoundError as error:
        raise _memory_not_found() from error

    except Exception as error:
        logger.exception(
            "Unexpected error while deleting memory %s.",
            memory_id
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to delete memory."
        ) from error

    return None


@router.patch(
    "/{memory_id}/approve",
    response_model=MemoryResponse
)
async def approve_memory(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await memory_service.approve_memory(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

    except MemoryNotFoundError as error:
        raise _memory_not_found() from error

    except Exception as error:
        logger.exception(
            "Unexpected error while approving memory %s.",
            memory_id
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to approve memory."
        ) from error


@router.patch(
    "/{memory_id}/reject",
    response_model=MemoryResponse
)
async def reject_memory(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await memory_service.reject_memory(
            session=session,
            user_id=user_id,
            memory_id=memory_id
        )

    except MemoryNotFoundError as error:
        raise _memory_not_found() from error

    except Exception as error:
        logger.exception(
            "Unexpected error while rejecting memory %s.",
            memory_id
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to reject memory."
        ) from error
