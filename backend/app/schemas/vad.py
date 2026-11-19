"""Pydantic schemas for the local voice activity detection API."""

from pydantic import BaseModel, Field


# One request carries at most one second of 16 kHz mono 16-bit PCM in a WAV
# container. A 0.5-second batch is used by the frontend, leaving headroom.
VAD_MAX_UPLOAD_BYTES = 1024 * 1024


class VADStatusResponse(BaseModel):
    """Public capability information for local VAD."""

    enabled: bool
    available: bool
    engine: str
    engine_version: str
    model_name: str
    device: str
    sample_rate: int
    frame_samples: int
    speech_threshold: float
    silence_threshold: float
    min_speech_duration_seconds: float
    silence_duration_seconds: float
    max_utterance_seconds: float
    max_session_seconds: float
    max_session_count: int
    max_upload_bytes: int


class VADSessionResponse(BaseModel):
    """An opaque, in-memory VAD analysis session."""

    session_id: str
    state: str
    sample_rate: int
    frame_samples: int
    sample_position: int = Field(ge=0)


class VADSegmentSummary(BaseModel):
    """Metadata for one completed speech segment. No audio bytes are returned."""

    segment_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    reason: str
    peak_probability: float = Field(ge=0, le=1)


class VADFrameResponse(BaseModel):
    """Actual detection result for one PCM frame batch."""

    session_id: str
    state: str
    speech_probability: float = Field(ge=0, le=1)
    speech_detected: bool
    speech_duration_seconds: float = Field(default=0, ge=0)
    events: list[str]
    completed_segments: list[VADSegmentSummary]
    sample_position: int = Field(ge=0)
    processing_ms: float = Field(ge=0)
