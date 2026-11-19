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
import gc
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
from app.services.speech_normalizer import normalize_speech_text
from app.services.blocking import run_blocking_to_completion


logger = logging.getLogger(__name__)


VOICE_DIR = Path.home() / ".asta" / "tts" / "voices"
VOICE_NAME = settings.TTS_DEFAULT_VOICE

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
        self._loaded_voice_name: str | None = None
        self._load_lock = asyncio.Lock()
        self._inference_slots = asyncio.Semaphore(1)
        self.default_voice = settings.TTS_DEFAULT_VOICE
        self.default_speed = settings.TTS_DEFAULT_SPEED
        self._active_speed = self.default_speed

    @staticmethod
    def available_voices() -> list[str]:
        """Return only complete voice models already installed locally."""

        return sorted(
            model.stem
            for model in VOICE_DIR.glob("*.onnx")
            if model.with_name(f"{model.name}.json").is_file()
        )

    def status(self) -> dict[str, Any]:
        """Return a public status summary without exposing filesystem paths."""

        return {
            "enabled": settings.TTS_ENABLED,
            "available": self.voice_available(self.default_voice),
            "voice": self.default_voice,
            "voices": self.available_voices(),
            "speed": self.default_speed,
            "max_text_chars": MAX_TTS_TEXT_CHARS,
            "max_audio_bytes": settings.VOICE_MAX_TTS_AUDIO_BYTES,
        }

    def voice_available(self, voice_name: str | None = None) -> bool:
        name = voice_name or self.default_voice
        return name in self.available_voices()

    async def synthesize(
        self,
        text: str,
        voice_name: str | None = None,
        speed: float | None = None,
    ) -> bytes:
        """Synthesize user-requested text into an in-memory WAV payload."""

        if not settings.TTS_ENABLED:
            raise TTSDisabledError("Text-to-speech is disabled.")

        original = text.strip()
        if not original:
            raise TTSValidationError("Speech text must not be empty.")
        if len(original) > MAX_TTS_TEXT_CHARS:
            raise TTSValidationError("Speech text exceeds the configured limit.")
        selected_voice = voice_name or self.default_voice
        selected_speed = self.default_speed if speed is None else float(speed)
        if not 0.8 <= selected_speed <= 1.2:
            raise TTSValidationError("Speech speed must be between 0.8 and 1.2.")
        if not self.voice_available(selected_voice):
            raise TTSVoiceNotFoundError("The local speech voice is unavailable.")
        cleaned = normalize_speech_text(original)
        if not cleaned:
            raise TTSValidationError("No speakable text remains after formatting cleanup.")
        if len(cleaned) > MAX_TTS_TEXT_CHARS * 2:
            raise TTSValidationError("Normalized speech text exceeds the configured limit.")

        max_audio_bytes = settings.VOICE_MAX_TTS_AUDIO_BYTES
        if max_audio_bytes <= 0:
            raise TTSValidationError("The speech audio size limit is invalid.")

        async with self._inference_slots:
            voice = await self._get_voice(selected_voice)
            self._active_speed = selected_speed

            try:
                wav_bytes = await run_blocking_to_completion(
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

    async def _get_voice(self, voice_name: str) -> Any:
        if self._voice is not None and self._loaded_voice_name == voice_name:
            return self._voice

        async with self._load_lock:
            if self._voice is None or self._loaded_voice_name != voice_name:
                try:
                    from piper import PiperVoice

                    self._voice = None
                    gc.collect()
                    model_path = VOICE_DIR / f"{voice_name}.onnx"
                    config_path = VOICE_DIR / f"{voice_name}.onnx.json"

                    self._voice = await run_blocking_to_completion(
                        PiperVoice.load,
                        model_path,
                        config_path=config_path,
                        use_cuda=False,
                    )
                    self._loaded_voice_name = voice_name

                except Exception as error:
                    logger.warning(
                        "Local speech voice could not be loaded (%s).",
                        type(error).__name__,
                    )
                    raise TTSVoiceNotFoundError(
                        "The local speech voice is unavailable."
                    ) from error

        return self._voice

    def _synthesize_sync(self, voice: Any, text: str, max_bytes: int) -> bytes:
        from piper import SynthesisConfig

        buffer = _BoundedBytesIO(max_bytes)
        with wave.open(buffer, "wb") as wav_file:
            syn_config = SynthesisConfig(
                length_scale=CALM_LENGTH_SCALE / self._active_speed
            )
            pause_samples = 0
            sample_width = 2
            channels = 1
            for chunk_index, chunk in enumerate(voice.synthesize(text, syn_config)):
                if chunk_index == 0:
                    wav_file.setframerate(chunk.sample_rate)
                    wav_file.setsampwidth(chunk.sample_width)
                    wav_file.setnchannels(chunk.sample_channels)
                    sample_width = chunk.sample_width
                    channels = chunk.sample_channels
                    pause_samples = max(
                        0,
                        round(
                            chunk.sample_rate
                            * max(0.0, settings.TTS_SENTENCE_PAUSE_SECONDS)
                        ),
                    )
                elif pause_samples:
                    wav_file.writeframes(bytes(pause_samples * sample_width * channels))
                wav_file.writeframes(chunk.audio_int16_bytes)

        return buffer.getvalue()


tts_service = TTSService()
