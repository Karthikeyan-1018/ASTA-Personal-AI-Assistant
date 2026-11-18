"""Protected API routes for explicit, local text-to-speech playback."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.dependencies import get_current_user_id, require_csrf
from app.exceptions.tts_exceptions import (
    TTSAudioTooLargeError,
    TTSInferenceError,
    TTSDisabledError,
    TTSValidationError,
    TTSVoiceNotFoundError,
)
from app.schemas.tts import TTSStatusResponse, TTSynthesizeRequest
from app.services.tts_service import tts_service


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/tts",
    tags=["Text-to-Speech"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/status", response_model=TTSStatusResponse)
async def get_tts_status(
    _user_id: int = Depends(get_current_user_id),
) -> TTSStatusResponse:
    """Return public TTS capability information to an authenticated owner."""

    return TTSStatusResponse(**tts_service.status())


@router.post("/synthesize")
async def synthesize_speech(
    payload: TTSynthesizeRequest,
    _user_id: int = Depends(get_current_user_id),
) -> Response:
    """Generate WAV audio only after an explicit authenticated user action."""

    try:
        wav_bytes = await tts_service.synthesize(payload.text)

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
