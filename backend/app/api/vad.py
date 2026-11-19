"""Protected API routes for explicit, local voice activity detection."""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from app.api.dependencies import get_current_user_id, require_csrf
from app.exceptions.vad_exceptions import (
    VADBusyError,
    VADDisabledError,
    VADInferenceError,
    VADModelUnavailableError,
    VADSessionError,
    VADValidationError,
)
from app.schemas.vad import (
    VAD_MAX_UPLOAD_BYTES,
    VADFrameResponse,
    VADSegmentSummary,
    VADSessionResponse,
    VADStatusResponse,
)
from app.services.vad_service import vad_service


router = APIRouter(
    prefix="/api/vad",
    tags=["Voice Activity Detection"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/status", response_model=VADStatusResponse)
async def get_vad_status(
    _user_id: int = Depends(get_current_user_id),
) -> VADStatusResponse:
    """Return public local VAD capability information."""

    return VADStatusResponse(**vad_service.status())


@router.post("/sessions", response_model=VADSessionResponse)
async def create_vad_session(
    user_id: int = Depends(get_current_user_id),
) -> VADSessionResponse:
    """Create an in-memory session for one explicitly started recording."""

    try:
        session = await vad_service.create_session(user_id)

    except VADDisabledError:
        raise HTTPException(
            status_code=503,
            detail="Voice activity detection is disabled.",
        )

    except VADModelUnavailableError:
        raise HTTPException(
            status_code=404,
            detail="The local VAD model is unavailable.",
        )

    except VADBusyError:
        raise HTTPException(
            status_code=429,
            detail="Too many local VAD sessions are active.",
        )

    return VADSessionResponse(**session)


@router.get("/sessions/{session_id}", response_model=VADSessionResponse)
async def get_vad_session(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
) -> VADSessionResponse:
    """Return the current state of an active owned VAD session."""

    try:
        session = await vad_service.get_session(session_id, user_id=user_id)

    except VADSessionError:
        raise HTTPException(
            status_code=404,
            detail="The VAD session is unavailable.",
        )

    return VADSessionResponse(**session)


@router.post(
    "/sessions/{session_id}/frames",
    response_model=VADFrameResponse,
)
async def process_vad_frames(
    session_id: str,
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
) -> VADFrameResponse:
    """Analyze one 16 kHz mono PCM WAV batch with the cached local model."""

    try:
        content = await file.read(VAD_MAX_UPLOAD_BYTES + 1)

    finally:
        await file.close()

    try:
        result = await vad_service.process_frames(session_id, content, user_id=user_id)

    except VADDisabledError:
        raise HTTPException(
            status_code=503,
            detail="Voice activity detection is disabled.",
        )

    except VADValidationError:
        raise HTTPException(
            status_code=400,
            detail="Invalid VAD audio.",
        )

    except VADSessionError:
        raise HTTPException(
            status_code=404,
            detail="The VAD session is unavailable.",
        )

    except VADModelUnavailableError:
        raise HTTPException(
            status_code=404,
            detail="The local VAD model is unavailable.",
        )

    except VADBusyError:
        raise HTTPException(
            status_code=429,
            detail="The local VAD service is busy. Try again shortly.",
        )

    except VADInferenceError:
        raise HTTPException(
            status_code=500,
            detail="Local VAD inference failed.",
        )

    # Pydantic validation here is internal: the service constructs the exact
    # documented shape, but the response model guards against accidental audio
    # or transcript fields leaking into the client contract.
    return VADFrameResponse(
        session_id=result["session_id"],
        state=result["state"],
        speech_probability=result["speech_probability"],
        speech_detected=result["speech_detected"],
        speech_duration_seconds=result.get("speech_duration_seconds", 0.0),
        events=result["events"],
        completed_segments=[
            VADSegmentSummary(**segment)
            for segment in result["completed_segments"]
        ],
        sample_position=result["sample_position"],
        processing_ms=result["processing_ms"],
    )


@router.delete("/sessions/{session_id}")
async def delete_vad_session(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, bool]:
    """Close an owned session and immediately release its in-memory audio."""

    try:
        deleted = await vad_service.delete_session(session_id, user_id=user_id)
    except VADSessionError:
        raise HTTPException(
            status_code=404,
            detail="The VAD session is unavailable.",
        )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="The VAD session is unavailable.",
        )
    return {"deleted": True}


@router.get(
    "/sessions/{session_id}/segments/{segment_id}/wav",
    response_class=Response,
)
async def get_vad_segment_wav(
    session_id: str,
    segment_id: str,
    user_id: int = Depends(get_current_user_id),
) -> Response:
    """Export one completed segment as 16 kHz PCM WAV for Stage 2 STT.

    This route is an explicit integration boundary. VAD itself never calls STT
    and never transcribes, stores, or automatically submits the returned audio.
    """

    try:
        wav_bytes = await vad_service.export_segment_wav(
            session_id,
            segment_id,
            user_id=user_id,
        )

    except VADSessionError:
        raise HTTPException(
            status_code=404,
            detail="The VAD speech segment is unavailable.",
        )

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )
