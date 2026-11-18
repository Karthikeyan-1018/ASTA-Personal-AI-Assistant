"""Backend-owned orchestration for explicit local voice conversations.

This service is a coordinator, not a second chatbot or speech engine. It owns
only in-memory session state and delegates audio work to the cached Stage 1-3
services and conversational work to the existing chat service. Completed turns
are persisted exclusively by the existing chat conversation store; this module
never writes audio, transcripts, or messages to disk.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.tts_exceptions import TTSServiceError
from app.exceptions.tts_exceptions import TTSAudioTooLargeError
from app.exceptions.voice_conversation_exceptions import (
    VoiceComponentUnavailableError,
    VoiceConversationDisabledError,
    VoiceConversationError,
    VoiceDuplicateSessionError,
    VoiceEmptyTranscriptionError,
    VoiceInvalidStateError,
    VoiceSessionError,
    VoiceTTSError,
    VoiceTurnError,
)
from app.schemas.voice_conversation import (
    VoiceConversationEvent,
    VoiceConversationState,
)
from app.services.chat_service import chat_service
from app.services.stt_service import stt_service
from app.services.tts_service import tts_service
from app.services.vad_service import vad_service


logger = logging.getLogger(__name__)


State = VoiceConversationState

VALID_TRANSITIONS: dict[State, frozenset[State]] = {
    State.IDLE: frozenset({State.LISTENING}),
    State.LISTENING: frozenset(
        {State.SPEECH_DETECTED, State.STOPPING, State.ERROR}
    ),
    State.SPEECH_DETECTED: frozenset(
        {State.TRANSCRIBING, State.STOPPING, State.ERROR}
    ),
    State.TRANSCRIBING: frozenset(
        {State.PROCESSING, State.STOPPING, State.ERROR}
    ),
    State.PROCESSING: frozenset({State.SPEAKING, State.STOPPING, State.ERROR}),
    State.SPEAKING: frozenset(
        {State.FOLLOW_UP_LISTENING, State.STOPPING, State.ERROR}
    ),
    State.FOLLOW_UP_LISTENING: frozenset(
        {State.SPEECH_DETECTED, State.IDLE, State.STOPPING, State.ERROR}
    ),
    State.STOPPING: frozenset({State.IDLE}),
    State.ERROR: frozenset({State.IDLE, State.STOPPING}),
}

ACTIVE_STATES = frozenset(
    {
        State.LISTENING,
        State.SPEECH_DETECTED,
        State.TRANSCRIBING,
        State.PROCESSING,
        State.SPEAKING,
        State.FOLLOW_UP_LISTENING,
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _VoiceSession:
    """Mutable, process-local coordination state for one explicit session."""

    session_id: str
    user_id: int
    created_at: datetime
    created_monotonic: float
    last_activity: datetime
    last_activity_monotonic: float
    state: State = State.IDLE
    conversation_id: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    vad_session_id: str | None = None
    follow_up_expires_at: datetime | None = None
    follow_up_deadline: float | None = None
    timeout_task: asyncio.Task | None = None
    timeout_token: int = 0
    turn_task: asyncio.Task | None = None
    stopped: bool = False
    stop_reason: str | None = None
    error: str | None = None


class VoiceConversationManager:
    """Create, transition, and reclaim bounded voice conversation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, _VoiceSession] = {}
        self._registry_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def status(self, user_id: int) -> dict[str, Any]:
        """Return capability information and the caller's active session."""

        components = {
            "vad": bool(settings.VAD_ENABLED and vad_service.model_available()),
            "stt": bool(settings.STT_ENABLED and stt_service.model_available()),
            "tts": bool(settings.TTS_ENABLED and tts_service.voice_available()),
            # Chat availability is determined per turn by the existing router.
            "chat": True,
        }
        active = next(
            (
                session
                for session in self._sessions.values()
                if session.user_id == user_id and session.state in ACTIVE_STATES
            ),
            None,
        )

        return {
            "enabled": settings.VOICE_CONVERSATION_ENABLED,
            "available": all(components.values()),
            "components": components,
            "states": [state.value for state in State],
            "active_session_id": active.session_id if active else None,
            "state": active.state if active else State.IDLE,
            "follow_up_timeout_seconds": (
                settings.VOICE_FOLLOW_UP_TIMEOUT_SECONDS
            ),
            "max_sessions": settings.VOICE_MAX_SESSIONS,
            "max_turn_audio_bytes": settings.VOICE_MAX_TURN_AUDIO_BYTES,
        }

    async def create_session(self, user_id: int) -> dict[str, Any]:
        """Create one authenticated session and move it to ``listening``."""

        self._ensure_enabled()
        self._ensure_components()
        await self._prune_expired()

        async with self._registry_lock:
            user_sessions = [
                session
                for session in self._sessions.values()
                if session.user_id == user_id and session.state in ACTIVE_STATES
            ]
            if user_sessions:
                raise VoiceDuplicateSessionError(
                    "A voice conversation is already active for this user."
                )
            if len(self._sessions) >= settings.VOICE_MAX_SESSIONS:
                raise VoiceConversationError(
                    "Too many active voice conversations. Try again shortly."
                )

            now = _utcnow()
            monotonic = time.monotonic()
            session = _VoiceSession(
                session_id=secrets.token_urlsafe(18),
                user_id=user_id,
                created_at=now,
                created_monotonic=monotonic,
                last_activity=now,
                last_activity_monotonic=monotonic,
            )
            self._transition(session, State.LISTENING)
            self._sessions[session.session_id] = session
            return self._summary(session)

    async def get_session(self, session_id: str, user_id: int) -> dict[str, Any]:
        """Return authoritative state for one owned session."""

        session = await self._owned_session(session_id, user_id)
        async with session.lock:
            return self._summary(session)

    async def apply_event(
        self,
        session_id: str,
        user_id: int,
        event: VoiceConversationEvent,
        vad_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply a real VAD/playback observation using validated transitions."""

        session = await self._owned_session(session_id, user_id)

        async with session.lock:
            if vad_session_id:
                session.vad_session_id = vad_session_id

            if event is VoiceConversationEvent.SPEECH_START:
                if session.state not in (
                    State.LISTENING,
                    State.FOLLOW_UP_LISTENING,
                ):
                    raise VoiceInvalidStateError(
                        "Speech cannot start in the current conversation state."
                    )
                self._cancel_timeout(session)
                session.follow_up_expires_at = None
                session.follow_up_deadline = None
                session.stop_reason = None
                session.error = None
                self._transition(session, State.SPEECH_DETECTED)

            elif event is VoiceConversationEvent.PLAYBACK_COMPLETE:
                if session.state is not State.SPEAKING:
                    raise VoiceInvalidStateError(
                        "Playback completion is not valid in this state."
                    )
                self._transition(session, State.FOLLOW_UP_LISTENING)
                self._start_follow_up_timeout(session)

            elif event is VoiceConversationEvent.PLAYBACK_FAILED:
                if session.state is not State.SPEAKING:
                    raise VoiceInvalidStateError(
                        "Playback failure is not valid in this state."
                    )
                self._cancel_timeout(session)
                session.error = (
                    "Assistant audio playback failed. The text response "
                    "remains available in the conversation."
                )
                self._transition(session, State.ERROR)

            return self._summary(session)

    async def submit_turn(
        self,
        session_id: str,
        user_id: int,
        audio: bytes,
        db: AsyncSession,
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one completed VAD segment through STT, chat, and TTS."""

        session = await self._owned_session(session_id, user_id)

        async with session.lock:
            self._ensure_live(session)
            if session.state is not State.SPEECH_DETECTED:
                raise VoiceInvalidStateError(
                    "A speech segment was submitted outside a speech turn."
                )
            if len(audio) > settings.VOICE_MAX_TURN_AUDIO_BYTES:
                raise VoiceTurnError("The speech segment exceeds the size limit.")
            self._transition(session, State.TRANSCRIBING)
            session.turn_task = asyncio.current_task()

        try:
            stt_result = await stt_service.transcribe(audio, "audio/wav")
            transcript = str(stt_result.get("text") or "").strip()

            if not stt_result.get("success") or len(transcript) < max(
                1, settings.VOICE_MIN_TRANSCRIPT_CHARS
            ):
                raise VoiceEmptyTranscriptionError(
                    "No usable speech was recognized. Please try the utterance "
                    "again."
                )

            async with session.lock:
                self._ensure_live(session)
                self._transition(session, State.PROCESSING)

            chat_result = await chat_service.get_response(
                prompt=transcript,
                private=False,
                user_id=user_id,
                session=db,
                conversation_id=session.conversation_id,
            )

            assistant_response = str(chat_result.get("response") or "").strip()
            if not assistant_response:
                raise VoiceTurnError(
                    "The assistant response was empty. No speech was generated."
                )

            pending = self._pending_confirmations(chat_result)

            async with session.lock:
                self._ensure_live(session)
                conversation_id = chat_result.get("conversation_id")
                if isinstance(conversation_id, int) and conversation_id > 0:
                    session.conversation_id = conversation_id
                self._transition(session, State.SPEAKING)
                response_base = self._summary(session)

            tts_available = False
            audio_base64: str | None = None
            tts_error: str | None = None

            try:
                wav = await tts_service.synthesize(assistant_response)
                if len(wav) > settings.VOICE_MAX_TTS_AUDIO_BYTES:
                    raise VoiceTTSError("The generated speech audio was too large.")
                audio_base64 = base64.b64encode(wav).decode("ascii")
                tts_available = True

            except (TTSServiceError, VoiceTTSError) as error:
                tts_available = False
                if isinstance(error, (TTSAudioTooLargeError, VoiceTTSError)):
                    tts_error = (
                        "The assistant response is too long to speak. The full "
                        "text response is still available in the conversation."
                    )
                else:
                    tts_error = (
                        "Speech generation failed. The assistant response is still "
                        "available in the conversation."
                    )
                logger.warning(
                    "Voice conversation TTS failed (%s).",
                    type(error).__name__,
                )
                async with session.lock:
                    if not session.stopped and session.state is not State.ERROR:
                        session.error = tts_error
                        self._transition(session, State.ERROR)

            async with session.lock:
                self._ensure_live(session)
                self._touch(session)
                response_base = self._summary(session)

            return {
                **response_base,
                "transcript": transcript,
                "assistant_response": assistant_response,
                "provider": chat_result.get("provider"),
                "model": chat_result.get("model"),
                "tts_available": tts_available,
                "audio_base64": audio_base64,
                "audio_media_type": "audio/wav" if audio_base64 else None,
                "pending_confirmations": pending,
                "tts_error": tts_error,
            }

        except asyncio.CancelledError:
            # An explicit Stop finalizes the session and cancels this task.
            raise

        except Exception as error:
            if isinstance(error, (VoiceEmptyTranscriptionError, VoiceTurnError)):
                public_error = str(error)
            else:
                public_error = (
                    "The voice turn could not be completed. "
                    "The conversation remains available."
                )
            async with session.lock:
                if not session.stopped and session.state is not State.ERROR:
                    session.error = public_error
                    self._transition(session, State.ERROR)
            logger.warning(
                "Voice conversation turn failed (%s).",
                type(error).__name__,
            )
            if isinstance(error, VoiceConversationError):
                raise
            raise VoiceTurnError(public_error) from error

        finally:
            async with session.lock:
                if session.turn_task is asyncio.current_task():
                    session.turn_task = None

    async def stop_session(
        self, session_id: str, user_id: int
    ) -> dict[str, Any]:
        """Stop an owned session immediately and release its resources."""

        session = await self._owned_session(session_id, user_id)
        await self._finalize(session, reason="user_stop")
        return {
            "session_id": session.session_id,
            "stopped": True,
            "state": State.IDLE,
        }

    async def shutdown(self) -> None:
        """Cancel and reclaim all sessions during application shutdown."""

        async with self._registry_lock:
            sessions = list(self._sessions.values())

        for session in sessions:
            await self._finalize(session, reason="shutdown")

    # ------------------------------------------------------------------
    # Internal state management
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_enabled() -> None:
        if not settings.VOICE_CONVERSATION_ENABLED:
            raise VoiceConversationDisabledError(
                "Voice conversations are disabled."
            )

    @staticmethod
    def _ensure_live(session: _VoiceSession) -> None:
        """Reject further work once a session has been stopped or reclaimed."""

        if session.stopped or session.state in (State.IDLE, State.STOPPING):
            raise VoiceSessionError("The voice session is unavailable.")

    @staticmethod
    def _ensure_components() -> None:
        missing: list[str] = []
        if not settings.VAD_ENABLED or not vad_service.model_available():
            missing.append("voice activity detection")
        if not settings.STT_ENABLED or not stt_service.model_available():
            missing.append("speech-to-text")
        if not settings.TTS_ENABLED or not tts_service.voice_available():
            missing.append("text-to-speech")
        if missing:
            raise VoiceComponentUnavailableError(
                "Unavailable local component: " + ", ".join(missing) + "."
            )

    @staticmethod
    def _transition(session: _VoiceSession, target: State) -> None:
        """Apply a centrally validated transition while the session is locked."""

        allowed = VALID_TRANSITIONS.get(session.state, frozenset())
        if target not in allowed:
            raise VoiceInvalidStateError(
                f"Invalid voice state transition: {session.state.value}->"
                f"{target.value}."
            )

        session.state = target
        session.last_activity = _utcnow()
        session.last_activity_monotonic = time.monotonic()

    @staticmethod
    def _touch(session: _VoiceSession) -> None:
        session.last_activity = _utcnow()
        session.last_activity_monotonic = time.monotonic()

    @staticmethod
    def _cancel_timeout(session: _VoiceSession) -> None:
        session.timeout_token += 1
        task = session.timeout_task
        session.timeout_task = None
        if task is not None and not task.done():
            task.cancel()

    def _start_follow_up_timeout(self, session: _VoiceSession) -> None:
        self._cancel_timeout(session)
        timeout = max(0.1, settings.VOICE_FOLLOW_UP_TIMEOUT_SECONDS)
        session.follow_up_deadline = time.monotonic() + timeout
        session.follow_up_expires_at = _utcnow() + timedelta(seconds=timeout)
        token = session.timeout_token
        session.timeout_task = asyncio.create_task(
            self._follow_up_timeout(session.session_id, token),
            name=f"voice-follow-up-{session.session_id[:8]}",
        )

    async def _follow_up_timeout(self, session_id: str, token: int) -> None:
        try:
            await asyncio.sleep(max(0.1, settings.VOICE_FOLLOW_UP_TIMEOUT_SECONDS))

            async with self._registry_lock:
                session = self._sessions.get(session_id)

            if session is None:
                return

            async with session.lock:
                if token != session.timeout_token:
                    return
                if session.state is not State.FOLLOW_UP_LISTENING:
                    return

            await self._finalize(session, reason="follow_up_timeout")

        except asyncio.CancelledError:
            raise

    async def _finalize(
        self, session: _VoiceSession, *, reason: str
    ) -> None:
        turn_task: asyncio.Task | None = None

        async with session.lock:
            self._cancel_timeout(session)
            turn_task = session.turn_task
            session.turn_task = None
            session.stopped = True
            session.follow_up_expires_at = None
            session.follow_up_deadline = None
            session.stop_reason = reason
            if session.state is not State.IDLE:
                if session.state is not State.STOPPING:
                    self._transition(session, State.STOPPING)
                self._transition(session, State.IDLE)

        if turn_task is not None and turn_task is not asyncio.current_task():
            turn_task.cancel()

        async with self._registry_lock:
            if self._sessions.get(session.session_id) is session:
                self._sessions.pop(session.session_id, None)

    async def _owned_session(
        self, session_id: str, user_id: int
    ) -> _VoiceSession:
        await self._prune_expired()

        async with self._registry_lock:
            session = self._sessions.get(session_id)

        if session is None or session.user_id != user_id:
            raise VoiceSessionError("The voice session is unavailable.")

        return session

    async def _prune_expired(self) -> None:
        now = time.monotonic()
        idle_timeout = settings.VOICE_SESSION_IDLE_TIMEOUT_SECONDS
        max_duration = settings.VOICE_SESSION_MAX_SECONDS

        async with self._registry_lock:
            expired = [
                session
                for session in self._sessions.values()
                if now - session.created_monotonic > max_duration
                or (
                    now - session.last_activity_monotonic > idle_timeout
                    and session.state
                    in (State.LISTENING, State.FOLLOW_UP_LISTENING, State.ERROR)
                )
            ]

        for session in expired:
            await self._finalize(session, reason="expired")

    @staticmethod
    def _summary(session: _VoiceSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "state": session.state,
            "conversation_id": session.conversation_id,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
            "follow_up_timeout_seconds": (
                settings.VOICE_FOLLOW_UP_TIMEOUT_SECONDS
            ),
            "follow_up_expires_at": session.follow_up_expires_at,
            "stop_reason": session.stop_reason,
            "error": session.error,
        }

    @staticmethod
    def _pending_confirmations(chat_result: dict[str, Any]) -> list[dict[str, Any]]:
        items = chat_result.get("pending_confirmations") or []
        if not isinstance(items, (list, tuple)):
            return []

        cleaned: list[dict[str, Any]] = []
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            cleaned.append(
                {
                    "confirmation_id": item.get("confirmation_id"),
                    "tool": item.get("tool"),
                    "category": item.get("category"),
                    "expires_at": item.get("expires_at"),
                }
            )
        return cleaned


voice_conversation_manager = VoiceConversationManager()
