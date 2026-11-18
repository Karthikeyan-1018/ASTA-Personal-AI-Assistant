"""Protected API for the user's reusable ASTA personality settings."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.database.connection import get_db
from app.exceptions.preference_exceptions import (
    PreferenceConflictError,
    PreferenceValidationError,
)
from app.personality.service import PersonalityState, personality_service
from app.schemas.personality import (
    PersonalitySettingsResponse,
    PersonalitySettingsUpdate,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/settings/personality",
    tags=["Personality"],
    dependencies=[Depends(require_csrf)],
)


def _response(state: PersonalityState) -> PersonalitySettingsResponse:
    return PersonalitySettingsResponse(
        **state.config.model_dump(),
        has_overrides=state.has_overrides,
    )


@router.get("", response_model=PersonalitySettingsResponse)
async def get_personality_settings(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> PersonalitySettingsResponse:
    """Return effective settings, including configured defaults."""

    try:
        state = await personality_service.get_state(
            session=session,
            user_id=user_id,
        )

    except Exception:
        logger.exception("Unable to load personality settings.")
        raise HTTPException(
            status_code=500,
            detail="Unable to load personality settings.",
        )

    return _response(state)


@router.patch("", response_model=PersonalitySettingsResponse)
async def update_personality_settings(
    payload: PersonalitySettingsUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> PersonalitySettingsResponse:
    """Persist validated personality changes in existing user preferences."""

    updates = payload.model_dump(exclude_none=True)

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one personality setting to update.",
        )

    try:
        state = await personality_service.update(
            session=session,
            user_id=user_id,
            updates=updates,
        )

    except (PreferenceValidationError, PreferenceConflictError) as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception("Unable to save personality settings.")
        raise HTTPException(
            status_code=500,
            detail="Unable to save personality settings.",
        )

    return _response(state)


@router.post("/reset", response_model=PersonalitySettingsResponse)
async def reset_personality_settings(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> PersonalitySettingsResponse:
    """Remove user overrides and restore the configured defaults."""

    try:
        state = await personality_service.reset(
            session=session,
            user_id=user_id,
        )

    except Exception:
        logger.exception("Unable to reset personality settings.")
        raise HTTPException(
            status_code=500,
            detail="Unable to reset personality settings.",
        )

    return _response(state)
