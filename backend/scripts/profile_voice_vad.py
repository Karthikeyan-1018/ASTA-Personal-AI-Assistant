"""Profile local Silero VAD with both current and lower-latency batch sizes.

Uses the three local audition WAVs as synthetic input. No microphone data is
captured and no network service is contacted.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from faster_whisper.audio import decode_audio  # noqa: E402
import silero_vad  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.vad_service import _SessionState, vad_service  # noqa: E402


def run_batches(model, audio, frames_per_batch: int) -> dict[str, float | int]:
    import numpy as np

    vad_service._reset_model_states(model)
    session = _SessionState(
        session_id=f"profile-{frames_per_batch}",
        user_id=0,
        created_at=time.monotonic(),
        last_activity=time.monotonic(),
    )
    samples_per_batch = settings.VAD_FRAME_SAMPLES * frames_per_batch
    usable_samples = audio.size - (audio.size % settings.VAD_FRAME_SAMPLES)
    elapsed_ms: list[float] = []
    speech_starts = 0
    completed_segments = 0
    for offset in range(0, usable_samples, samples_per_batch):
        batch = np.ascontiguousarray(
            audio[offset : min(offset + samples_per_batch, usable_samples)]
        )
        started = time.perf_counter()
        result = vad_service._process_sync(model, session, batch)
        elapsed_ms.append((time.perf_counter() - started) * 1000)
        speech_starts += result["events"].count("speech_start")
        completed_segments += len(result["completed_segments"])
    return {
        "http_batches_equivalent": len(elapsed_ms),
        "mean_model_processing_ms_per_batch": round(sum(elapsed_ms) / max(1, len(elapsed_ms)), 2),
        "total_model_processing_ms": round(sum(elapsed_ms), 1),
        "speech_start_events": speech_starts,
        "completed_segments": completed_segments,
    }


def main() -> None:
    files = sorted(
        (ROOT / "voice_comparison_samples").glob(
            "asta_standard_comparison_*.wav"
        )
    )
    if not files:
        raise SystemExit("Generate the standard voice comparison WAVs first.")
    model = silero_vad.load_silero_vad(
        onnx=False,
        sampling_rate=settings.VAD_SAMPLE_RATE,
    )
    output = {}
    for file in files:
        audio = decode_audio(str(file))
        output[file.stem] = {
            "duration_seconds": round(audio.size / settings.VAD_SAMPLE_RATE, 2),
            "batch_16_frames": run_batches(model, audio, 16),
            "batch_8_frames": run_batches(model, audio, 8),
        }
    import numpy as np

    sample_rate = settings.VAD_SAMPLE_RATE
    silence = np.zeros(sample_rate * 3, dtype=np.float32)
    low_noise = np.random.default_rng(42).normal(
        0.0, 0.003, sample_rate * 3
    ).astype(np.float32)
    output["synthetic_controls"] = {
        "silence_batch_8_frames": run_batches(model, silence, 8),
        "low_noise_batch_8_frames": run_batches(model, low_noise, 8),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
