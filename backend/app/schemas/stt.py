"""Pydantic schemas for the local speech-to-text API."""

from pydantic import BaseModel, Field


MAX_STT_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_STT_DURATION_SECONDS = 60

# The browser records a narrow set of container/codec combinations. PyAV, used
# by faster-whisper, decodes each of these locally. The declared content type is
# only an early filter; actual decodability is checked from the uploaded bytes.
SUPPORTED_STT_MEDIA_TYPES = (
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/x-m4a",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
)

# Some browsers send a generic binary content type for MediaRecorder output.
# It is accepted only because the service still decodes the real byte stream.
STT_OPAQUE_MEDIA_TYPES = ("application/octet-stream",)


class STTTranscribeResponse(BaseModel):
    """Actual local transcription result for a single explicit recording."""

    success: bool
    text: str = Field(..., max_length=5000)
    language: str | None = None
    processing_ms: float | None = Field(default=None, ge=0)


class STTStatusResponse(BaseModel):
    """Non-sensitive capability information for the authenticated owner."""

    enabled: bool
    available: bool
    model: str
    device: str
    compute_type: str
    max_upload_bytes: int
    max_duration_seconds: int
    supported_media_types: list[str]
