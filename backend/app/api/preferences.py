"""REST endpoints for structured user preferences and suggestions."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.database.connection import get_db
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.exceptions.memory_exceptions import MemoryValidationError
from app.exceptions.preference_exceptions import (
    PreferenceConflictError,
    PreferenceExtractionError,
    PreferenceNotFoundError,
    PreferenceValidationError,
)
from app.memory.preference_extractor import preference_extractor
from app.schemas.preference import (
    PreferenceCreate,
    PreferenceExtractionRequest,
    PreferenceExtractionResponse,
    PreferenceResponse,
    PreferenceSuggestionListResponse,
    PreferenceUpdate,
)
from app.services.preference_service import preference_service


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/preferences", tags=["Preferences"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Preference not found.")


def _validation_error(error: Exception) -> HTTPException:
    logger.warning("Invalid preference request: %s", error)

    return HTTPException(status_code=400, detail=str(error))


def _extraction_http_error(error: Exception) -> HTTPException:
    if isinstance(error, MemoryValidationError):
        return _validation_error(error)

    if isinstance(error, LLMTimeoutError):
        logger.error(
            "LLM request timed out during preference extraction.",
            exc_info=True
        )
        return HTTPException(
            status_code=504,
            detail="The AI provider timed out. Please try again."
        )

    if isinstance(error, (LLMProviderError, LLMUnavailableError)):
        logger.error(
            "LLM provider failed during preference extraction.",
            exc_info=True
        )
        return HTTPException(
            status_code=503,
            detail=(
                "The AI provider is currently unavailable. "
                "Please try again later."
            )
        )

    if isinstance(error, PreferenceExtractionError):
        # Never include raw model output derived from conversation content.
        logger.error(
            "The AI returned an invalid preference extraction response."
        )
        return HTTPException(
            status_code=502,
            detail=(
                "The AI returned an invalid preference extraction response."
            )
        )

    logger.exception("Unexpected error while extracting preferences.")

    return HTTPException(
        status_code=500,
        detail="Unable to extract preferences."
    )


@router.post(
    "/",
    response_model=PreferenceResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_preference(
    payload: PreferenceCreate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Explicitly create an active, user-owned preference."""

    try:
        return await preference_service.create_preference(
            session=session,
            user_id=user_id,
            key=payload.key,
            value=payload.value,
            category=payload.category
        )

    except PreferenceConflictError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error)
        ) from error

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while creating a preference.")
        raise HTTPException(
            status_code=500,
            detail="Unable to create preference."
        ) from error


@router.get("/", response_model=list[PreferenceResponse])
async def list_preferences(
    category: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """List the user's preferences, optionally filtered by category/status."""

    try:
        return await preference_service.list_preferences(
            session=session,
            user_id=user_id,
            category=category,
            status=status_filter
        )

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while listing preferences.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve preferences."
        ) from error


@router.post(
    "/extract",
    response_model=PreferenceExtractionResponse
)
async def extract_preferences(
    payload: PreferenceExtractionRequest,
    user_id: int = Depends(get_current_user_id)
):
    """Return candidate preferences without saving or approving anything."""

    try:
        candidates = await preference_extractor.extract(
            conversation=payload.conversation,
            private=payload.private
        )

        return PreferenceExtractionResponse(candidates=candidates)

    except Exception as error:
        raise _extraction_http_error(error) from error


@router.get(
    "/suggestions/",
    response_model=PreferenceSuggestionListResponse
)
async def list_suggestions(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """List inferred preferences awaiting user confirmation."""

    try:
        suggestions = await preference_service.list_suggestions(
            session=session,
            user_id=user_id
        )

        return PreferenceSuggestionListResponse(suggestions=suggestions)

    except Exception as error:
        logger.exception(
            "Unexpected error while listing preference suggestions."
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve suggestions."
        ) from error


@router.post(
    "/suggestions/",
    response_model=PreferenceSuggestionListResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_suggestions(
    payload: PreferenceExtractionRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Extract candidate preferences and persist them as pending suggestions.

    Nothing extracted here becomes an active preference until the user calls
    the approve endpoint.
    """

    try:
        candidates = await preference_extractor.extract(
            conversation=payload.conversation,
            private=payload.private
        )

    except Exception as error:
        raise _extraction_http_error(error) from error

    try:
        suggestions = []

        for candidate in candidates:
            suggestion = await preference_service.create_suggestion(
                session=session,
                user_id=user_id,
                key=candidate.key,
                value=candidate.value,
                category=candidate.category
            )

            if suggestion is not None:
                suggestions.append(suggestion)

        return PreferenceSuggestionListResponse(suggestions=suggestions)

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception(
            "Unexpected error while storing preference suggestions."
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to store suggestions."
        ) from error


@router.post(
    "/suggestions/{suggestion_id}/approve",
    response_model=PreferenceResponse
)
async def approve_suggestion(
    suggestion_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Approve an inferred preference so it becomes active."""

    try:
        return await preference_service.approve_suggestion(
            session=session,
            user_id=user_id,
            suggestion_id=suggestion_id
        )

    except PreferenceNotFoundError as error:
        raise _not_found() from error

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while approving a suggestion.")
        raise HTTPException(
            status_code=500,
            detail="Unable to approve suggestion."
        ) from error


@router.post(
    "/suggestions/{suggestion_id}/reject",
    response_model=PreferenceResponse
)
async def reject_suggestion(
    suggestion_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    """Reject an inferred preference; it never becomes active."""

    try:
        return await preference_service.reject_suggestion(
            session=session,
            user_id=user_id,
            suggestion_id=suggestion_id
        )

    except PreferenceNotFoundError as error:
        raise _not_found() from error

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while rejecting a suggestion.")
        raise HTTPException(
            status_code=500,
            detail="Unable to reject suggestion."
        ) from error


@router.get("/{preference_id}", response_model=PreferenceResponse)
async def get_preference(
    preference_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await preference_service.get_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id
        )

    except PreferenceNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while retrieving a preference.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve preference."
        ) from error


@router.patch("/{preference_id}", response_model=PreferenceResponse)
async def update_preference(
    preference_id: int,
    payload: PreferenceUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await preference_service.update_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id,
            key=payload.key,
            value=payload.value,
            category=payload.category,
            status=payload.status
        )

    except PreferenceNotFoundError as error:
        raise _not_found() from error

    except PreferenceConflictError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error)
        ) from error

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while updating a preference.")
        raise HTTPException(
            status_code=500,
            detail="Unable to update preference."
        ) from error


@router.post(
    "/{preference_id}/deactivate",
    response_model=PreferenceResponse
)
async def deactivate_preference(
    preference_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        return await preference_service.deactivate_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id
        )

    except PreferenceNotFoundError as error:
        raise _not_found() from error

    except PreferenceValidationError as error:
        raise _validation_error(error) from error

    except Exception as error:
        logger.exception("Unexpected error while deactivating a preference.")
        raise HTTPException(
            status_code=500,
            detail="Unable to deactivate preference."
        ) from error


@router.delete(
    "/{preference_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_preference(
    preference_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):

    try:
        await preference_service.delete_preference(
            session=session,
            user_id=user_id,
            preference_id=preference_id
        )

    except PreferenceNotFoundError as error:
        raise _not_found() from error

    except Exception as error:
        logger.exception("Unexpected error while deleting a preference.")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete preference."
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)
