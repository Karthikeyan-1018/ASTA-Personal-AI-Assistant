"""Local Piper text-to-speech service.

TTS is deliberately independent of the LLM router. The service receives text
that ASTA has already produced, synthesizes it on CPU through a locally cached
Piper voice, and returns in-memory WAV bytes. No audio is written to disk and no
text is sent to an external speech provider.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.exceptions.tts_exceptions import (
    TTSAudioTooLargeError,
    TTSInferenceError,
    TTSDisabledError,
    TTSValidationError,
    TTSVoiceNotFoundError,
)
from app.schemas.tts import MAX_TTS_TEXT_CHARS


logger = logging.getLogger(__name__)


VOICE_NAME = "en_US-john-medium"
VOICE_DIR = Path.home() / ".asta" / "tts" / "voices"
MODEL_PATH = VOICE_DIR / f"{VOICE_NAME}.onnx"
CONFIG_PATH = VOICE_DIR / f"{VOICE_NAME}.onnx.json"

# A slightly longer phoneme duration produces a calm, unhurried delivery
# without adding a user-facing speed control that Piper does not need here.
CALM_LENGTH_SCALE = 1.05


class _BoundedBytesIO(io.BytesIO):
    """In-memory WAV buffer that fails before an oversized PCM copy exists."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self._max_bytes = max_bytes

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self.tell() + len(data) > self._max_bytes:
            raise TTSAudioTooLargeError(
                "The generated speech audio exceeded the configured limit."
            )
        return super().write(data)


class TTSService:
    """Cache one Piper voice and serialize local CPU inference."""

    def __init__(self) -> None:
        self._voice: Any | None = None
        self._load_lock = asyncio.Lock()
        self._inference_slots = asyncio.Semaphore(1)

    def status(self) -> dict[str, Any]:
        """Return a public status summary without exposing filesystem paths."""

        return {
            "enabled": settings.TTS_ENABLED,
            "available": self.voice_available(),
            "voice": VOICE_NAME,
            "max_text_chars": MAX_TTS_TEXT_CHARS,
            "max_audio_bytes": settings.VOICE_MAX_TTS_AUDIO_BYTES,
        }

    def voice_available(self) -> bool:
        return MODEL_PATH.is_file() and CONFIG_PATH.is_file()

    async def synthesize(self, text: str) -> bytes:
        """Synthesize user-requested text into an in-memory WAV payload."""

        if not settings.TTS_ENABLED:
            raise TTSDisabledError("Text-to-speech is disabled.")

        cleaned = text.strip()
        if not cleaned:
            raise TTSValidationError("Speech text must not be empty.")
        if len(cleaned) > MAX_TTS_TEXT_CHARS:
            raise TTSValidationError("Speech text exceeds the configured limit.")
        if not self.voice_available():
            raise TTSVoiceNotFoundError("The local speech voice is unavailable.")

        max_audio_bytes = settings.VOICE_MAX_TTS_AUDIO_BYTES
        if max_audio_bytes <= 0:
            raise TTSValidationError("The speech audio size limit is invalid.")

        async with self._inference_slots:
            voice = await self._get_voice()

            try:
                wav_bytes = await asyncio.to_thread(
                    self._synthesize_sync,
                    voice,
                    cleaned,
                    max_audio_bytes,
                )

            except TTSAudioTooLargeError:
                logger.warning(
                    "Local speech audio exceeded the configured size limit."
                )
                raise

            except Exception as error:
                # Never log the private text or a full model/filesystem path.
                logger.warning(
                    "Local speech inference failed (%s).",
                    type(error).__name__,
                )
                raise TTSInferenceError("Local speech inference failed.") from error

        if not wav_bytes:
            raise TTSInferenceError("Local speech inference produced no audio.")
        if len(wav_bytes) > max_audio_bytes:
            raise TTSAudioTooLargeError(
                "The generated speech audio exceeded the configured limit."
            )

        return wav_bytes

    async def _get_voice(self) -> Any:
        if self._voice is not None:
            return self._voice

        async with self._load_lock:
            if self._voice is None:
                try:
                    from piper import PiperVoice

                    self._voice = await asyncio.to_thread(
                        PiperVoice.load,
                        MODEL_PATH,
                        config_path=CONFIG_PATH,
                        use_cuda=False,
                    )

                except Exception as error:
                    logger.warning(
                        "Local speech voice could not be loaded (%s).",
                        type(error).__name__,
                    )
                    raise TTSVoiceNotFoundError(
                        "The local speech voice is unavailable."
                    ) from error

        return self._voice

    @staticmethod
    def _synthesize_sync(voice: Any, text: str, max_bytes: int) -> bytes:
        from piper import SynthesisConfig

        buffer = _BoundedBytesIO(max_bytes)
        with wave.open(buffer, "wb") as wav_file:
            voice.synthesize_wav(
                text,
                wav_file,
                syn_config=SynthesisConfig(length_scale=CALM_LENGTH_SCALE),
                set_wav_format=True,
            )

        return buffer.getvalue()


tts_service = TTSService()
