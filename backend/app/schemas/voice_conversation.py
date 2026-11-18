"""Pydantic schemas for the local voice conversation manager API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class VoiceConversationState(str, Enum):
    """Backend-owned states for one explicit voice conversation."""

    IDLE = "idle"
    LISTENING = "listening"
    SPEECH_DETECTED = "speech_detected"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    FOLLOW_UP_LISTENING = "follow_up_listening"
    STOPPING = "stopping"
    ERROR = "error"


class VoiceConversationEvent(str, Enum):
    """Client observations that the backend may apply to its own state."""

    SPEECH_START = "speech_start"
    PLAYBACK_COMPLETE = "playback_complete"
    PLAYBACK_FAILED = "playback_failed"


class VoiceEventRequest(BaseModel):
    """A real VAD or audio-playback observation, never a state assertion."""

    model_config = ConfigDict(extra="forbid")

    event: VoiceConversationEvent
    vad_session_id: str | None = Field(default=None, max_length=128)


class VoiceSessionResponse(BaseModel):
    """Authoritative state for one backend-owned voice session.

    Audio, transcripts, and provider details are deliberately absent. Chat
    messages remain only in the existing authenticated conversation store.
    """

    session_id: str
    state: VoiceConversationState
    conversation_id: int | None = None
    created_at: datetime
    last_activity: datetime
    follow_up_timeout_seconds: float = Field(gt=0)
    follow_up_expires_at: datetime | None = None
    stop_reason: str | None = None
    error: str | None = None


class VoicePendingConfirmation(BaseModel):
    """Metadata-only view of an existing chat tool confirmation."""

    confirmation_id: int | None = None
    tool: str | None = None
    category: str | None = None
    expires_at: datetime | None = None


class VoiceTurnResponse(VoiceSessionResponse):
    """Result of one real VAD segment passing through STT, chat, and TTS."""

    transcript: str | None = None
    assistant_response: str | None = None
    provider: str | None = None
    model: str | None = None
    tts_available: bool = False
    audio_base64: str | None = None
    audio_media_type: str | None = None
    pending_confirmations: list[VoicePendingConfirmation] = Field(
        default_factory=list
    )
    tts_error: str | None = None


class VoiceStatusResponse(BaseModel):
    """Non-sensitive capability summary for an authenticated owner."""

    enabled: bool
    available: bool
    components: dict[str, bool]
    states: list[str]
    active_session_id: str | None = None
    state: VoiceConversationState = VoiceConversationState.IDLE
    follow_up_timeout_seconds: float = Field(gt=0)
    max_sessions: int = Field(ge=1)
    max_turn_audio_bytes: int = Field(ge=1)


class VoiceStopResponse(BaseModel):
    """Result of an explicit Stop Conversation action."""

    session_id: str
    stopped: bool
    state: VoiceConversationState = VoiceConversationState.IDLE
