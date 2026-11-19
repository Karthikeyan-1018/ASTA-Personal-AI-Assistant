"""Benchmark the installed faster-whisper model on ASTA's local audition WAV.

This compares beam sizes 5 (the current setting), 3, and 1 using the same
model and CPU configuration. It reads only the three local standard voice
samples and does not download models or call a network service.

    python backend/scripts/profile_voice_stt.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
import wave
import ctypes
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402


SAMPLE_DIR = ROOT / "voice_comparison_samples"
REFERENCE = ROOT / "voice_comparison_samples" / "asta_standard_comparison_normalized.txt"


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def word_error_rate(reference: list[str], hypothesis: list[str]) -> float:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, start=1):
        current = [row]
        for col, actual in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[col] + 1,
                    previous[col - 1] + (expected != actual),
                )
            )
        previous = current
    return previous[-1] / max(1, len(reference))


def current_rss_mib() -> float | None:
    if os.name != "nt":
        return None
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    psapi = ctypes.WinDLL("Psapi.dll")
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    ):
        return None
    return counters.WorkingSetSize / (1024 * 1024)


def main() -> None:
    samples = sorted(SAMPLE_DIR.glob("asta_standard_comparison_*.wav"))
    if not samples or not REFERENCE.is_file():
        raise SystemExit("Generate the standard voice comparison WAVs first.")
    if not settings.STT_MODEL_DIR.exists():
        raise SystemExit("The configured local STT model is unavailable.")

    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    reference = words(REFERENCE.read_text(encoding="utf-8"))

    load_started = time.perf_counter()
    model = WhisperModel(
        str(settings.STT_MODEL_DIR),
        device=settings.STT_DEVICE,
        compute_type=settings.STT_COMPUTE_TYPE,
        cpu_threads=settings.STT_CPU_THREADS,
        num_workers=1,
        local_files_only=True,
    )
    load_ms = (time.perf_counter() - load_started) * 1000
    results: dict[str, object] = {
        "model": settings.STT_MODEL_NAME,
        "device": settings.STT_DEVICE,
        "compute_type": settings.STT_COMPUTE_TYPE,
        "cpu_threads": settings.STT_CPU_THREADS,
        "model_load_ms": round(load_ms, 1),
    }
    rss_after_load = current_rss_mib()
    if rss_after_load is not None:
        results["rss_after_load_mib"] = round(rss_after_load, 1)

    voice_results: dict[str, object] = {}
    for sample in samples:
        with wave.open(str(sample), "rb") as audio_file:
            duration_seconds = audio_file.getnframes() / audio_file.getframerate()
        audio = decode_audio(str(sample))
        beam_results: dict[str, object] = {}
        for beam_size in dict.fromkeys((settings.STT_BEAM_SIZE, 3, 1)):
            elapsed_ms: list[float] = []
            cpu_ms: list[float] = []
            transcript: list[str] = []
            for _ in range(2):
                wall_started = time.perf_counter()
                cpu_started = time.process_time()
                segments, _ = model.transcribe(
                    audio,
                    language="en",
                    beam_size=beam_size,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                transcript = [word for segment in segments for word in words(segment.text)]
                elapsed_ms.append((time.perf_counter() - wall_started) * 1000)
                cpu_ms.append((time.process_time() - cpu_started) * 1000)

            beam_results[f"beam_{beam_size}"] = {
                "runs": len(elapsed_ms),
                "mean_wall_ms": round(statistics.mean(elapsed_ms), 1),
                "median_wall_ms": round(statistics.median(elapsed_ms), 1),
                "mean_process_cpu_ms": round(statistics.mean(cpu_ms), 1),
                "word_error_rate": round(word_error_rate(reference, transcript), 4),
            }
        voice_results[sample.stem.replace("asta_standard_comparison_", "")] = {
            "sample_duration_seconds": round(duration_seconds, 2),
            "beams": beam_results,
        }
    results["voices"] = voice_results
    rss_after_inference = current_rss_mib()
    if rss_after_inference is not None:
        results["rss_after_inference_mib"] = round(rss_after_inference, 1)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
