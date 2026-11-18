"""Protected API routes for explicit, local speech-to-text transcription."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import get_current_user_id, require_csrf
from app.core.config import settings
from app.exceptions.stt_exceptions import (
    STTBusyError,
    STTDisabledError,
    STTInferenceError,
    STTModelNotFoundError,
    STTValidationError,
)
from app.schemas.stt import STTStatusResponse, STTTranscribeResponse
from app.services.stt_service import stt_service


router = APIRouter(
    prefix="/api/stt",
    tags=["Speech-to-Text"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/status", response_model=STTStatusResponse)
async def get_stt_status(
    _user_id: int = Depends(get_current_user_id),
) -> STTStatusResponse:
    """Return public STT capability information to an authenticated owner."""

    return STTStatusResponse(**stt_service.status())


@router.post("/transcribe", response_model=STTTranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    _user_id: int = Depends(get_current_user_id),
) -> STTTranscribeResponse:
    """Transcribe one explicit recording using only the local model."""

    content_type = file.content_type

    try:
        content = await file.read(settings.STT_MAX_UPLOAD_BYTES + 1)

    finally:
        await file.close()

    try:
        result = await stt_service.transcribe(content, content_type)

    except STTDisabledError:
        raise HTTPException(
            status_code=503,
            detail="Speech-to-text is disabled.",
        )

    except STTValidationError:
        raise HTTPException(
            status_code=400,
            detail="Invalid audio upload.",
        )

    except STTModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="The local speech model is unavailable.",
        )

    except STTBusyError:
        raise HTTPException(
            status_code=429,
            detail="The local speech service is busy. Try again shortly.",
        )

    except STTInferenceError:
        raise HTTPException(
            status_code=500,
            detail="Speech transcription failed.",
        )

    return STTTranscribeResponse(**result)
