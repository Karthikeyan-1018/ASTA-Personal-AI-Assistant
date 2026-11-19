"""Generate the same five-sentence local Piper audition for installed voices.

Run from any directory with the ASTA backend virtual environment:
    python C:/Asta/voice_comparison_samples/generate_standard_comparison.py

The script uses ASTA's existing normalizer and Piper service, synthesizes one
voice at a time, and writes all audio locally. It does not call a cloud service.
"""

from __future__ import annotations

import sys
import subprocess
import time
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.speech_normalizer import normalize_speech_text  # noqa: E402


SENTENCES = (
    "Hello! I'm ASTA. What are we working on today?",
    "Don't worry. Let's solve this problem step by step.",
    "Your project is progressing well. Let's move to the next milestone.",
    "An API allows two applications to communicate with each other.",
    "We encountered an error. Let's identify the root cause.",
)
SAMPLE_TEXT = " ".join(SENTENCES)
OUTPUT_DIR = Path(__file__).resolve().parent


def main() -> None:
    model_dir = Path.home() / ".asta" / "tts" / "voices"
    voices = sorted(
        model.stem
        for model in model_dir.glob("*.onnx")
        if model.with_name(f"{model.name}.json").is_file()
    )
    if not voices:
        raise SystemExit("No complete local Piper voices are installed.")

    piper_cli = Path(sys.executable).with_name("piper.exe")
    if not piper_cli.is_file():
        raise SystemExit(f"Piper CLI was not found beside {sys.executable}.")
    normalized_text = normalize_speech_text(SAMPLE_TEXT)
    sample_text_path = OUTPUT_DIR / "asta_standard_comparison_sample.txt"
    sample_text_path.write_text("\n".join(SENTENCES) + "\n", encoding="utf-8")
    normalized_path = OUTPUT_DIR / "asta_standard_comparison_normalized.txt"
    normalized_path.write_text(normalized_text, encoding="utf-8")
    for voice_name in voices:
        started = time.perf_counter()
        output_path = OUTPUT_DIR / f"asta_standard_comparison_{voice_name}.wav"
        subprocess.run(
            [
                str(piper_cli),
                "--model", str(model_dir / f"{voice_name}.onnx"),
                "--config", str(model_dir / f"{voice_name}.onnx.json"),
                "--input-file", str(normalized_path),
                "--output-file", str(output_path),
                "--length-scale", "1.05",
                "--sentence-silence", "0.18",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        with wave.open(str(output_path), "rb") as wav_file:
            duration = wav_file.getnframes() / wav_file.getframerate()
        print(
            f"{voice_name}: {output_path.name}, {output_path.stat().st_size} bytes, "
            f"{duration:.2f} seconds, {elapsed_ms:.1f} ms"
        )


if __name__ == "__main__":
    main()
