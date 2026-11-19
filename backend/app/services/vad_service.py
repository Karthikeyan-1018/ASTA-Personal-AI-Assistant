"""Local Silero voice activity detection service.

VAD is deliberately independent of speech-to-text and the LLM router. It
receives only explicitly captured 16 kHz mono PCM frames, runs a bundled local
model on CPU, and returns real speech/silence state plus completed segment
metadata. Audio exists only in bounded process memory and is never written to
disk, sent to a provider, or logged.
"""

from __future__ import annotations

import asyncio
import importlib.metadata as metadata
import io
import logging
import secrets
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.exceptions.vad_exceptions import (
    VADBusyError,
    VADDisabledError,
    VADInferenceError,
    VADModelUnavailableError,
    VADSessionError,
    VADServiceError,
    VADValidationError,
)
from app.schemas.vad import VAD_MAX_UPLOAD_BYTES


logger = logging.getLogger(__name__)


ENGINE_NAME = "silero-vad"
ENGINE_VERSION = "6.2.3"
MODEL_NAME = "silero-vad-jit"
MODEL_RESOURCE = "silero_vad/data/silero_vad.jit"
MIN_MODEL_BYTES = 1024 * 1024

STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_SPEAKING = "speaking"
STATE_SILENCE = "silence"


@dataclass
class _Segment:
    """One accepted utterance held only for the lifetime of its session."""

    segment_id: str
    start_sample: int
    end_sample: int
    duration_samples: int
    reason: str
    peak_probability: float
    wav_bytes: bytes


@dataclass
class _SessionState:
    """Bounded per-session segmentation state."""

    session_id: str
    user_id: int
    created_at: float
    last_activity: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: str = STATE_IDLE
    triggered: bool = False
    sample_position: int = 0
    speech_samples: int = 0
    utterance_samples: int = 0
    utterance_start_sample: int = 0
    silence_samples: int = 0
    peak_probability: float = 0.0
    pcm: bytearray = field(default_factory=bytearray)
    completed: dict[str, _Segment] = field(default_factory=dict)
    closed: bool = False


class VADService:
    """Cache one local VAD model and serialize all CPU inference."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        # The TorchScript model carries recurrent state. One inference slot
        # prevents one session's state from leaking into another's results.
        self._inference_slots = asyncio.Semaphore(1)
        self._registry_lock = asyncio.Lock()
        self._sessions: dict[str, _SessionState] = {}

    def status(self) -> dict[str, Any]:
        """Return public capability information without filesystem paths."""

        return {
            "enabled": settings.VAD_ENABLED,
            "available": self.model_available(),
            "engine": ENGINE_NAME,
            "engine_version": self.engine_version(),
            "model_name": MODEL_NAME,
            "device": settings.VAD_DEVICE,
            "sample_rate": settings.VAD_SAMPLE_RATE,
            "frame_samples": settings.VAD_FRAME_SAMPLES,
            "speech_threshold": settings.VAD_SPEECH_THRESHOLD,
            "silence_threshold": settings.VAD_SILENCE_THRESHOLD,
            "min_speech_duration_seconds": settings.VAD_MIN_SPEECH_DURATION_SECONDS,
            "silence_duration_seconds": settings.VAD_SILENCE_DURATION_SECONDS,
            "max_utterance_seconds": settings.VAD_MAX_UTTERANCE_SECONDS,
            "max_session_seconds": settings.VAD_MAX_SESSION_SECONDS,
            "max_session_count": settings.VAD_MAX_SESSIONS,
            "max_upload_bytes": VAD_MAX_UPLOAD_BYTES,
        }

    @staticmethod
    def engine_version() -> str:
        try:
            return metadata.version(ENGINE_NAME)
        except metadata.PackageNotFoundError:
            return "unknown"

    @staticmethod
    def model_path() -> Path | None:
        """Locate the bundled model without importing Torch or using network."""

        try:
            distribution = metadata.distribution(ENGINE_NAME)
        except metadata.PackageNotFoundError:
            return None

        return Path(distribution.locate_file(MODEL_RESOURCE))

    def model_available(self) -> bool:
        path = self.model_path()
        return path is not None and path.is_file() and path.stat().st_size >= MIN_MODEL_BYTES

    @staticmethod
    def validate_wav(content: bytes) -> Any:
        """Validate and decode one small 16 kHz mono PCM WAV batch in memory."""

        import numpy as np

        if not content:
            raise VADValidationError("The VAD audio batch is empty.")

        if len(content) > VAD_MAX_UPLOAD_BYTES:
            raise VADValidationError("The VAD audio batch exceeds the size limit.")

        try:
            with wave.open(io.BytesIO(content), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                compression = wav_file.getcomptype()
                frames = wav_file.readframes(wav_file.getnframes())
        except Exception as error:
            raise VADValidationError("The VAD audio batch is not valid PCM WAV.") from error

        if channels != 1 or sample_width != 2 or compression != "NONE":
            raise VADValidationError("VAD requires mono 16-bit PCM WAV audio.")

        if sample_rate != settings.VAD_SAMPLE_RATE:
            raise VADValidationError("VAD requires 16 kHz audio.")

        if len(frames) == 0:
            raise VADValidationError("The VAD audio batch contains no samples.")

        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        frame_samples = settings.VAD_FRAME_SAMPLES

        if samples.size % frame_samples != 0:
            raise VADValidationError("VAD frame length is invalid.")

        max_samples = int(
            settings.VAD_SAMPLE_RATE * settings.VAD_MAX_BATCH_SECONDS
        )
        if samples.size > max_samples:
            raise VADValidationError("The VAD audio batch is too long.")

        return samples

    async def create_session(self, user_id: int) -> dict[str, Any]:
        """Create a bounded in-memory session owned by one authenticated user."""

        if not settings.VAD_ENABLED:
            raise VADDisabledError("Voice activity detection is disabled.")

        if not self.model_available():
            raise VADModelUnavailableError("The local VAD model is unavailable.")

        await self._prune_expired()

        async with self._registry_lock:
            if len(self._sessions) >= settings.VAD_MAX_SESSIONS:
                raise VADBusyError("Too many local VAD sessions are active.")

            session_id = secrets.token_urlsafe(18)
            now = time.monotonic()
            session = _SessionState(
                session_id=session_id,
                user_id=user_id,
                created_at=now,
                last_activity=now,
            )
            self._sessions[session_id] = session
            return self._session_summary(session)

    async def get_session(self, session_id: str, *, user_id: int) -> dict[str, Any]:
        """Return the current state of one active owned session."""

        session = await self._resolve_session(session_id, user_id)
        async with session.lock:
            return self._session_summary(session)

    async def process_frames(
        self,
        session_id: str,
        content: bytes,
        *,
        user_id: int,
    ) -> dict[str, Any]:
        """Analyze one validated PCM batch and return real detection results."""

        if not settings.VAD_ENABLED:
            raise VADDisabledError("Voice activity detection is disabled.")

        session = await self._resolve_session(session_id, user_id)
        samples = self.validate_wav(content)

        try:
            await asyncio.wait_for(
                self._inference_slots.acquire(),
                timeout=settings.VAD_QUEUE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            raise VADBusyError("The local VAD service is busy.") from None

        try:
            async with session.lock:
                if session.closed:
                    raise VADSessionError("The VAD session is closed.")

                max_session_samples = int(
                    settings.VAD_SAMPLE_RATE * settings.VAD_MAX_SESSION_SECONDS
                )
                if session.sample_position + samples.size > max_session_samples:
                    self._clear_audio(session)
                    raise VADSessionError(
                        "The VAD session duration limit was reached."
                    )

                model = await self._get_model()

                try:
                    started = time.perf_counter()
                    result = await asyncio.to_thread(
                        self._process_sync,
                        model,
                        session,
                        samples,
                    )

                except VADServiceError:
                    raise

                except Exception as error:
                    logger.warning(
                        "Local VAD inference failed (%s).",
                        type(error).__name__,
                    )
                    raise VADInferenceError("Local VAD inference failed.") from error

                result["processing_ms"] = round(
                    (time.perf_counter() - started) * 1000,
                    2,
                )
                session.last_activity = time.monotonic()
                return result

        finally:
            self._inference_slots.release()

    async def delete_session(self, session_id: str, *, user_id: int) -> bool:
        """Close one owned session and drop all buffered audio and segments."""

        session = await self._resolve_session(session_id, user_id)

        async with self._registry_lock:
            current = self._sessions.get(session_id)
            if current is not session:
                return False
            self._sessions.pop(session_id)

        session.closed = True
        self._clear_audio(session)
        return True

    async def export_segment_wav(
        self,
        session_id: str,
        segment_id: str,
        *,
        user_id: int,
    ) -> bytes:
        """Return one owned segment as 16 kHz mono PCM WAV for Stage 2 STT."""

        session = await self._resolve_session(session_id, user_id)
        async with session.lock:
            segment = session.completed.get(segment_id)

        if segment is None:
            raise VADSessionError("The VAD speech segment is unavailable.")

        return segment.wav_bytes

    async def _get_model(self) -> Any:
        """Load the bundled model once; never download or use a GPU."""

        if self._model is not None:
            return self._model

        async with self._load_lock:
            if self._model is None:
                if not self.model_available():
                    raise VADModelUnavailableError("The local VAD model is unavailable.")

                try:
                    import silero_vad

                    self._model = await asyncio.to_thread(
                        silero_vad.load_silero_vad,
                        onnx=False,
                        sampling_rate=settings.VAD_SAMPLE_RATE,
                    )

                except Exception as error:
                    logger.warning(
                        "Local VAD model could not be loaded (%s).",
                        type(error).__name__,
                    )
                    raise VADModelUnavailableError(
                        "The local VAD model is unavailable."
                    ) from error

        return self._model

    async def _resolve_session(self, session_id: str, user_id: int) -> _SessionState:
        await self._prune_expired()

        async with self._registry_lock:
            session = self._sessions.get(session_id)

        if session is None or session.closed or session.user_id != user_id:
            raise VADSessionError("The VAD session is unavailable.")

        session.last_activity = time.monotonic()
        return session

    async def _prune_expired(self) -> None:
        now = time.monotonic()
        timeout = settings.VAD_SESSION_IDLE_TIMEOUT_SECONDS

        async with self._registry_lock:
            expired = [
                session_id
                for session_id, session in self._sessions.items()
                if now - session.last_activity > timeout
            ]
            for session_id in expired:
                session = self._sessions.pop(session_id)
                session.closed = True
                self._clear_audio(session)

    @staticmethod
    def _session_summary(session: _SessionState) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "state": session.state,
            "sample_rate": settings.VAD_SAMPLE_RATE,
            "frame_samples": settings.VAD_FRAME_SAMPLES,
            "sample_position": session.sample_position,
        }

    @staticmethod
    def _clear_audio(session: _SessionState) -> None:
        session.pcm.clear()
        session.completed.clear()
        session.speech_samples = 0
        session.utterance_samples = 0
        session.silence_samples = 0
        session.peak_probability = 0.0
        session.triggered = False
        session.state = STATE_IDLE

    @staticmethod
    def _reset_model_states(model: Any) -> None:
        reset = getattr(model, "reset_states", None)
        if callable(reset):
            reset()

    @staticmethod
    def _pcm_to_wav(pcm: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(settings.VAD_SAMPLE_RATE)
            wav_file.writeframes(pcm)
        return buffer.getvalue()

    @classmethod
    def _process_sync(
        cls,
        model: Any,
        session: _SessionState,
        samples: Any,
    ) -> dict[str, Any]:
        """Run one blocking frame batch outside the FastAPI event loop."""

        import numpy as np
        import torch

        frame_samples = settings.VAD_FRAME_SAMPLES
        sample_rate = settings.VAD_SAMPLE_RATE
        min_speech_samples = int(
            sample_rate * settings.VAD_MIN_SPEECH_DURATION_SECONDS
        )
        min_silence_samples = max(
            frame_samples,
            int(settings.VAD_SILENCE_DURATION_SECONDS * sample_rate),
        )
        max_utterance_samples = int(
            sample_rate * settings.VAD_MAX_UTTERANCE_SECONDS
        )
        events: list[str] = []
        completed: list[dict[str, Any]] = []

        with torch.no_grad():
            for offset in range(0, samples.size, frame_samples):
                frame = samples[offset : offset + frame_samples]
                probability = float(
                    model(
                        torch.from_numpy(np.ascontiguousarray(frame)),
                        sample_rate,
                    ).detach().item()
                )
                session.sample_position += frame_samples

                if (
                    session.triggered
                    and session.utterance_samples + frame_samples
                    > max_utterance_samples
                ):
                    cls._close_utterance(
                        session,
                        model,
                        completed,
                        events,
                        reason="max_duration",
                        min_speech_samples=min_speech_samples,
                    )

                if probability >= settings.VAD_SPEECH_THRESHOLD:
                    if not session.triggered:
                        cls._start_utterance(session, model, probability, events)
                    else:
                        session.silence_samples = 0
                    session.state = STATE_SPEAKING
                    session.speech_samples += frame_samples
                    session.peak_probability = max(
                        session.peak_probability,
                        probability,
                    )

                elif session.triggered:
                    if session.state == STATE_SPEAKING:
                        events.append("silence_start")
                    session.state = STATE_SILENCE
                    session.silence_samples += frame_samples

                else:
                    session.state = STATE_LISTENING

                if session.triggered:
                    int16_frame = np.clip(
                        frame * 32767.0, -32768, 32767
                    ).astype("<i2")
                    session.pcm.extend(int16_frame.tobytes())
                    session.utterance_samples += frame_samples

                if (
                    session.triggered
                    and session.silence_samples >= min_silence_samples
                ):
                    cls._close_utterance(
                        session,
                        model,
                        completed,
                        events,
                        reason="silence",
                        min_speech_samples=min_speech_samples,
                    )

        return {
            "session_id": session.session_id,
            "state": session.state,
            "speech_probability": probability if samples.size else 0.0,
            "speech_detected": session.triggered or session.state == STATE_SPEAKING,
            "speech_duration_seconds": (
                session.speech_samples / sample_rate if session.triggered else 0.0
            ),
            "events": events,
            "completed_segments": completed,
            "sample_position": session.sample_position,
        }

    @staticmethod
    def _start_utterance(
        session: _SessionState,
        model: Any,
        probability: float,
        events: list[str],
    ) -> None:
        session.triggered = True
        session.state = STATE_SPEAKING
        session.speech_samples = 0
        session.utterance_samples = 0
        session.utterance_start_sample = (
            session.sample_position - settings.VAD_FRAME_SAMPLES
        )
        session.silence_samples = 0
        session.peak_probability = probability
        session.pcm.clear()
        events.append("speech_start")
        VADService._reset_model_states(model)

    @staticmethod
    def _close_utterance(
        session: _SessionState,
        model: Any,
        completed: list[dict[str, Any]],
        events: list[str],
        *,
        reason: str,
        min_speech_samples: int,
    ) -> None:
        if session.speech_samples >= min_speech_samples:
            segment_id = secrets.token_urlsafe(9)
            end_sample = session.sample_position
            start_seconds = session.utterance_start_sample / settings.VAD_SAMPLE_RATE
            end_seconds = end_sample / settings.VAD_SAMPLE_RATE
            duration_seconds = session.utterance_samples / settings.VAD_SAMPLE_RATE
            peak_probability = min(1.0, max(0.0, session.peak_probability))
            wav_bytes = VADService._pcm_to_wav(bytes(session.pcm))
            segment = _Segment(
                segment_id=segment_id,
                start_sample=session.utterance_start_sample,
                end_sample=end_sample,
                duration_samples=session.utterance_samples,
                reason=reason,
                peak_probability=peak_probability,
                wav_bytes=wav_bytes,
            )
            session.completed[segment_id] = segment
            completed.append(
                {
                    "segment_id": segment_id,
                    "start_seconds": round(start_seconds, 3),
                    "end_seconds": round(end_seconds, 3),
                    "duration_seconds": round(duration_seconds, 3),
                    "reason": reason,
                    "peak_probability": round(peak_probability, 4),
                }
            )
            events.append("segment_completed")
        else:
            events.append("noise_rejected")

        session.pcm.clear()
        session.triggered = False
        session.state = STATE_LISTENING
        session.speech_samples = 0
        session.utterance_samples = 0
        session.silence_samples = 0
        session.peak_probability = 0.0
        VADService._reset_model_states(model)


vad_service = VADService()
