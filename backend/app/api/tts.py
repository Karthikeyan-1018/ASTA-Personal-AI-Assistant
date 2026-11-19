"""Protected API routes for explicit, local text-to-speech playback."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_csrf
from app.core.config import settings
from app.database.connection import get_db
from app.exceptions.tts_exceptions import (
    TTSAudioTooLargeError,
    TTSInferenceError,
    TTSDisabledError,
    TTSValidationError,
    TTSVoiceNotFoundError,
)
from app.schemas.tts import (
    TTSSettingsResponse,
    TTSSettingsUpdate,
    TTSStatusResponse,
    TTSynthesizeRequest,
    MAX_TTS_TEXT_CHARS,
)
from app.services.tts_service import tts_service
from app.services.tts_preferences import (
    get_tts_preferences,
    reset_tts_preferences,
    save_tts_preferences,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/tts",
    tags=["Text-to-Speech"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/status", response_model=TTSStatusResponse)
async def get_tts_status(
    _user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> TTSStatusResponse:
    """Return public TTS capability information to an authenticated owner."""

    status = tts_service.status()
    preferences = await get_tts_preferences(session, _user_id)
    selected_voice = str(preferences["voice"])
    return TTSStatusResponse(
        **{
            **status,
            "available": bool(
                status["enabled"] and tts_service.voice_available(selected_voice)
            ),
            "voice": selected_voice,
            "speed": preferences["speed"],
        }
    )


def _settings_response(preferences: dict[str, str | float]) -> TTSSettingsResponse:
    return TTSSettingsResponse(
        voice=str(preferences["voice"]),
        speed=float(preferences["speed"]),
        default_voice=tts_service.default_voice,
        installed_voices=tts_service.available_voices(),
        enabled=settings.TTS_ENABLED,
        available=bool(
            settings.TTS_ENABLED and tts_service.available_voices()
        ),
        max_text_chars=MAX_TTS_TEXT_CHARS,
        max_audio_bytes=settings.VOICE_MAX_TTS_AUDIO_BYTES,
    )


@router.get("/settings", response_model=TTSSettingsResponse)
async def get_tts_settings(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> TTSSettingsResponse:
    return _settings_response(await get_tts_preferences(session, user_id))


@router.put("/settings", response_model=TTSSettingsResponse)
async def update_tts_settings(
    payload: TTSSettingsUpdate,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> TTSSettingsResponse:
    if payload.reset:
        await reset_tts_preferences(session, user_id)
        return _settings_response(await get_tts_preferences(session, user_id))

    if payload.voice is None and payload.speed is None:
        raise HTTPException(status_code=400, detail="Provide a voice or speed setting.")

    current = await get_tts_preferences(session, user_id)
    voice = payload.voice if payload.voice is not None else str(current["voice"])
    speed = payload.speed if payload.speed is not None else float(current["speed"])
    if not tts_service.voice_available(voice):
        raise HTTPException(status_code=400, detail="That Piper voice is not installed locally.")
    await save_tts_preferences(session, user_id, voice, speed)
    return _settings_response(await get_tts_preferences(session, user_id))


@router.post("/synthesize")
async def synthesize_speech(
    payload: TTSynthesizeRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Generate WAV audio only after an explicit authenticated user action."""

    try:
        preferences = await get_tts_preferences(session, user_id)
        wav_bytes = await tts_service.synthesize(
            payload.text,
            voice_name=str(preferences["voice"]),
            speed=float(preferences["speed"]),
        )

    except TTSDisabledError:
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech is disabled.",
        )

    except TTSValidationError:
        raise HTTPException(
            status_code=400,
            detail="Invalid text for speech synthesis.",
        )

    except TTSVoiceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="The local speech voice is unavailable.",
        )

    except TTSAudioTooLargeError:
        raise HTTPException(
            status_code=413,
            detail="Generated speech audio exceeds the configured size limit.",
        )

    except TTSInferenceError:
        raise HTTPException(
            status_code=500,
            detail="Speech generation failed.",
        )

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
