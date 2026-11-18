"""Protected API routes for the explicit local voice conversation manager.

Stage 4 owns conversation state on the backend only. The browser submits real
VAD segment audio, observes backend state, and reports actual playback results;
it never asserts its own conversation state or calls an LLM directly.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.dependencies import get_current_user_id, require_csrf
from app.core.config import settings
from app.database.connection import get_db
from app.exceptions.voice_conversation_exceptions import (
    VoiceComponentUnavailableError,
    VoiceConversationDisabledError,
    VoiceConversationError,
    VoiceDuplicateSessionError,
    VoiceEmptyTranscriptionError,
    VoiceInvalidStateError,
    VoiceSessionError,
    VoiceTurnError,
)
from app.schemas.voice_conversation import (
    VoiceEventRequest,
    VoiceSessionResponse,
    VoiceStatusResponse,
    VoiceStopResponse,
    VoiceTurnResponse,
)
from app.services.voice_conversation_manager import voice_conversation_manager


router = APIRouter(
    prefix="/api/voice-conversations",
    tags=["Voice Conversation"],
    dependencies=[Depends(require_csrf)],
)


@router.get("/status", response_model=VoiceStatusResponse)
async def get_voice_status(
    user_id: int = Depends(get_current_user_id),
) -> VoiceStatusResponse:
    """Return backend capability information and the caller's active state."""

    return VoiceStatusResponse(**voice_conversation_manager.status(user_id))


@router.post("/sessions", response_model=VoiceSessionResponse)
async def create_voice_session(
    user_id: int = Depends(get_current_user_id),
) -> VoiceSessionResponse:
    """Explicitly start one half-duplex voice conversation."""

    try:
        session = await voice_conversation_manager.create_session(user_id)

    except VoiceConversationDisabledError:
        raise HTTPException(
            status_code=503,
            detail="Voice conversations are disabled.",
        )

    except VoiceComponentUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error))

    except VoiceDuplicateSessionError:
        raise HTTPException(
            status_code=409,
            detail="A voice conversation is already active.",
        )

    except VoiceConversationError as error:
        raise HTTPException(status_code=500, detail=str(error))

    return VoiceSessionResponse(**session)


@router.get("/sessions/{session_id}", response_model=VoiceSessionResponse)
async def get_voice_session(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
) -> VoiceSessionResponse:
    """Return the authoritative backend state for an owned session."""

    try:
        session = await voice_conversation_manager.get_session(
            session_id, user_id
        )

    except VoiceSessionError:
        raise HTTPException(
            status_code=404,
            detail="The voice session is unavailable.",
        )

    return VoiceSessionResponse(**session)


@router.post(
    "/sessions/{session_id}/events",
    response_model=VoiceSessionResponse,
)
async def apply_voice_event(
    session_id: str,
    payload: VoiceEventRequest,
    user_id: int = Depends(get_current_user_id),
) -> VoiceSessionResponse:
    """Apply a real VAD speech event or actual playback completion event."""

    try:
        session = await voice_conversation_manager.apply_event(
            session_id,
            user_id,
            payload.event,
            vad_session_id=payload.vad_session_id,
        )

    except VoiceSessionError:
        raise HTTPException(
            status_code=404,
            detail="The voice session is unavailable.",
        )

    except VoiceInvalidStateError as error:
        raise HTTPException(status_code=409, detail=str(error))

    return VoiceSessionResponse(**session)


@router.post(
    "/sessions/{session_id}/turns",
    response_model=VoiceTurnResponse,
)
async def submit_voice_turn(
    session_id: str,
    file: UploadFile = File(...),
    segment_id: str | None = Form(default=None, max_length=128),
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
) -> VoiceTurnResponse:
    """Transcribe one real completed VAD segment and continue the conversation.

    The uploaded bytes come from the Stage 3 VAD segment WAV endpoint. Stage 4
    never fabricates speech, never accepts client-supplied transcript text, and
    delegates all chat behavior to the existing chat service.
    """

    try:
        audio = await file.read(settings.VOICE_MAX_TURN_AUDIO_BYTES + 1)

    finally:
        await file.close()

    try:
        result = await voice_conversation_manager.submit_turn(
            session_id=session_id,
            user_id=user_id,
            audio=audio,
            db=db,
            segment_id=segment_id,
        )

    except VoiceSessionError:
        raise HTTPException(
            status_code=404,
            detail="The voice session is unavailable.",
        )

    except VoiceInvalidStateError as error:
        raise HTTPException(status_code=409, detail=str(error))

    except VoiceEmptyTranscriptionError as error:
        raise HTTPException(status_code=400, detail=str(error))

    except VoiceTurnError as error:
        raise HTTPException(status_code=500, detail=str(error))

    return VoiceTurnResponse(**result)


@router.post(
    "/sessions/{session_id}/stop",
    response_model=VoiceStopResponse,
)
async def stop_voice_session(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
) -> VoiceStopResponse:
    """Explicitly stop a conversation and release its in-memory resources."""

    try:
        result = await voice_conversation_manager.stop_session(
            session_id, user_id
        )

    except VoiceSessionError:
        raise HTTPException(
            status_code=404,
            detail="The voice session is unavailable.",
        )

    return VoiceStopResponse(**result)
