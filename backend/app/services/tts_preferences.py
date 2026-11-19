"""User-scoped local voice-output preferences."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.preference_exceptions import PreferenceConflictError
from app.models.preference import PreferenceCategory, PreferenceStatus
from app.services.preference_service import preference_service
from app.services.tts_service import tts_service


PREFERENCE_KEY = "voice_output_settings"
logger = logging.getLogger(__name__)


async def get_tts_preferences(
    session: AsyncSession | None,
    user_id: int | None,
) -> dict[str, str | float]:
    defaults: dict[str, str | float] = {
        "voice": tts_service.default_voice,
        "speed": tts_service.default_speed,
    }
    if session is None or user_id is None:
        return defaults

    try:
        rows = await preference_service.list_preferences(session, user_id)
    except Exception:
        # Speech preference lookup must not prevent an otherwise valid chat
        # turn from being answered; use the validated local defaults.
        logger.warning("Could not read local voice preferences; using defaults.")
        return defaults
    row = next((item for item in rows if item.key == PREFERENCE_KEY), None)
    if row is None or row.status is not PreferenceStatus.ACTIVE:
        return defaults
    try:
        stored = json.loads(row.value)
        voice = stored.get("voice")
        speed = float(stored.get("speed"))
        if isinstance(voice, str) and tts_service.voice_available(voice):
            defaults["voice"] = voice
        if 0.8 <= speed <= 1.2:
            defaults["speed"] = speed
    except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
        pass
    return defaults


async def save_tts_preferences(
    session: AsyncSession,
    user_id: int,
    voice: str,
    speed: float,
) -> None:
    value = json.dumps({"voice": voice, "speed": speed}, separators=(",", ":"))
    rows = await preference_service.list_preferences(session, user_id)
    row = next((item for item in rows if item.key == PREFERENCE_KEY), None)
    if row is None:
        try:
            await preference_service.create_preference(
                session=session,
                user_id=user_id,
                key=PREFERENCE_KEY,
                value=value,
                category=PreferenceCategory.GENERAL,
                status=PreferenceStatus.ACTIVE,
            )
        except PreferenceConflictError:
            rows = await preference_service.list_preferences(session, user_id)
            row = next((item for item in rows if item.key == PREFERENCE_KEY), None)
            if row is None:
                raise
    if row is not None:
        await preference_service.update_preference(
            session=session,
            user_id=user_id,
            preference_id=row.id,
            value=value,
            status=PreferenceStatus.ACTIVE,
        )


async def reset_tts_preferences(
    session: AsyncSession,
    user_id: int,
) -> None:
    rows = await preference_service.list_preferences(session, user_id)
    row = next((item for item in rows if item.key == PREFERENCE_KEY), None)
    if row is not None and row.status is not PreferenceStatus.INACTIVE:
        await preference_service.update_preference(
            session=session,
            user_id=user_id,
            preference_id=row.id,
            status=PreferenceStatus.INACTIVE,
        )
