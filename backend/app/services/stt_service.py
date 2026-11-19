"""Local faster-whisper speech-to-text service.

STT is deliberately independent of the LLM router. The service receives bytes
from one explicit microphone recording, decodes them in memory with PyAV, runs
a cached local Whisper model on CPU, and returns only the resulting text. No
audio or transcript is written to disk, persisted, or sent to an external
speech provider.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.exceptions.stt_exceptions import (
    STTBusyError,
    STTDisabledError,
    STTInferenceError,
    STTModelNotFoundError,
    STTServiceError,
    STTValidationError,
)
from app.schemas.stt import (
    STT_OPAQUE_MEDIA_TYPES,
    SUPPORTED_STT_MEDIA_TYPES,
)
from app.services.blocking import run_blocking_to_completion


logger = logging.getLogger(__name__)


REQUIRED_MODEL_FILES = (
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.txt",
)


class STTService:
    """Cache one local Whisper model and serialize CPU inference."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._inference_slots = asyncio.Semaphore(
            max(1, settings.STT_MAX_CONCURRENT)
        )

    def status(self) -> dict[str, Any]:
        """Return public capability information without filesystem paths."""

        return {
            "enabled": settings.STT_ENABLED,
            "available": self.model_available(),
            "model": settings.STT_MODEL_NAME,
            "device": settings.STT_DEVICE,
            "compute_type": settings.STT_COMPUTE_TYPE,
            "max_upload_bytes": settings.STT_MAX_UPLOAD_BYTES,
            "max_duration_seconds": settings.STT_MAX_DURATION_SECONDS,
            "supported_media_types": list(SUPPORTED_STT_MEDIA_TYPES),
        }

    def model_available(self) -> bool:
        """Return whether every downloaded model file is present locally."""

        directory: Path = settings.STT_MODEL_DIR
        return all((directory / name).is_file() for name in REQUIRED_MODEL_FILES)

    def validate_upload(self, content: bytes, content_type: str | None) -> None:
        """Apply cheap limits before any decoding or model work."""

        if not content:
            raise STTValidationError("The audio upload is empty.")

        if len(content) > settings.STT_MAX_UPLOAD_BYTES:
            raise STTValidationError("The audio upload exceeds the size limit.")

        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type and media_type not in (
            SUPPORTED_STT_MEDIA_TYPES + STT_OPAQUE_MEDIA_TYPES
        ):
            raise STTValidationError("The audio content type is not supported.")

    async def transcribe(
        self,
        content: bytes,
        content_type: str | None,
    ) -> dict[str, Any]:
        """Transcribe one in-memory recording with the cached local model."""

        if not settings.STT_ENABLED:
            raise STTDisabledError("Speech-to-text is disabled.")

        self.validate_upload(content, content_type)

        if not self.model_available():
            raise STTModelNotFoundError("The local speech model is unavailable.")

        started = time.perf_counter()
        audio: Any | None = None

        try:
            try:
                audio, duration_seconds = await run_blocking_to_completion(
                    self._decode_sync,
                    content,
                )

            except STTValidationError:
                raise

            except Exception as error:
                logger.warning(
                    "Local audio decoding failed (%s).",
                    type(error).__name__,
                )
                raise STTValidationError(
                    "The uploaded audio could not be decoded."
                ) from error

            if audio.size == 0:
                raise STTValidationError("The uploaded audio contains no samples.")

            if duration_seconds > settings.STT_MAX_DURATION_SECONDS:
                raise STTValidationError(
                    "The recording exceeds the duration limit."
                )

            try:
                await asyncio.wait_for(
                    self._inference_slots.acquire(),
                    timeout=settings.STT_QUEUE_TIMEOUT_SECONDS,
                )

            except TimeoutError:
                raise STTBusyError(
                    "The local speech service is busy. Try again shortly."
                ) from None

            try:
                model = await self._get_model()

                try:
                    result = await run_blocking_to_completion(
                        self._transcribe_sync,
                        model,
                        audio,
                    )

                except STTServiceError:
                    raise

                except Exception as error:
                    logger.warning(
                        "Local speech inference failed (%s).",
                        type(error).__name__,
                    )
                    raise STTInferenceError(
                        "Local speech inference failed."
                    ) from error

            finally:
                self._inference_slots.release()

            processing_ms = (time.perf_counter() - started) * 1000
            return {
                "success": True,
                "text": result["text"],
                "language": result["language"],
                "processing_ms": round(processing_ms, 2),
            }

        finally:
            # Drop the decoded PCM buffer as soon as this request finishes.
            del audio

    async def _get_model(self) -> Any:
        """Load the local model once and share the cached instance."""

        if self._model is not None:
            return self._model

        async with self._load_lock:
            if self._model is None:
                if not self.model_available():
                    raise STTModelNotFoundError(
                        "The local speech model is unavailable."
                    )

                try:
                    from faster_whisper import WhisperModel

                    self._model = await run_blocking_to_completion(
                        WhisperModel,
                        str(settings.STT_MODEL_DIR),
                        device=settings.STT_DEVICE,
                        compute_type=settings.STT_COMPUTE_TYPE,
                        cpu_threads=settings.STT_CPU_THREADS,
                        num_workers=1,
                        local_files_only=True,
                    )

                except Exception as error:
                    logger.warning(
                        "Local speech model could not be loaded (%s).",
                        type(error).__name__,
                    )
                    raise STTModelNotFoundError(
                        "The local speech model is unavailable."
                    ) from error

        return self._model

    @staticmethod
    def _decode_sync(content: bytes) -> tuple[Any, float]:
        """Decode any accepted container to 16 kHz mono PCM in memory."""

        from faster_whisper.audio import decode_audio

        audio = decode_audio(io.BytesIO(content))
        return audio, len(audio) / 16000

    @staticmethod
    def _transcribe_sync(model: Any, audio: Any) -> dict[str, Any]:
        """Run one blocking transcription outside the FastAPI event loop."""

        segments, info = model.transcribe(
            audio,
            beam_size=settings.STT_BEAM_SIZE,
            # Explicitly disabled: Stage 2 has no VAD or continuous listening.
            vad_filter=False,
            condition_on_previous_text=False,
        )

        text = " ".join(
            str(segment.text or "").strip()
            for segment in segments
        ).strip()

        if not text:
            raise STTInferenceError("Local speech inference produced no text.")

        language = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)

        if not isinstance(language, str) or not language.strip():
            language = None
        elif not (
            isinstance(probability, (int, float))
            and float(probability) >= settings.STT_LANGUAGE_CONFIDENCE
        ):
            language = None

        return {"text": text, "language": language}


stt_service = STTService()
