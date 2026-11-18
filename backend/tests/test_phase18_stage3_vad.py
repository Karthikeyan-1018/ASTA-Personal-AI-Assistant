"""Phase 18 Stage 3 local voice activity detection tests.

Real-model checks use the bundled Silero TorchScript model and locally generated
Piper speech; segmentation edge cases use deterministic fake models so they do
not depend on microphone or room acoustics. All audio stays in memory.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import io
import sys
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from piper import PiperVoice, SynthesisConfig
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all ORM tables)
from app.api.dependencies import get_current_user_id  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.connection import get_db  # noqa: E402
from app.exceptions.vad_exceptions import (  # noqa: E402
    VADBusyError,
    VADModelUnavailableError,
    VADSessionError,
    VADValidationError,
)
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import vad_service as vad_service_module  # noqa: E402
from app.services.auth_service import auth_service  # noqa: E402
from app.services.stt_service import stt_service  # noqa: E402
from app.services.vad_service import VADService, vad_service  # noqa: E402

try:
    import silero_vad  # noqa: E402
except ImportError:  # pragma: no cover - installation check reports this
    silero_vad = None


_SPEECH_PCM: np.ndarray | None = None
_SPEECH_TEXT = "ASTA can detect this locally generated speech segment."
OWNER_USER_ID = 101


def make_wav(
    samples: np.ndarray,
    sample_rate: int = 16000,
    channels: int = 1,
) -> bytes:
    """Encode float PCM as mono or stereo 16-bit WAV bytes in memory."""

    if channels == 2:
        interleaved = np.repeat(samples, 2)
    else:
        interleaved = samples
    pcm = np.clip(
        np.rint(interleaved * 32767),
        -32768,
        32767,
    ).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


def zeros(frames: int) -> np.ndarray:
    return np.zeros(frames * settings.VAD_FRAME_SAMPLES, dtype=np.float32)


def real_speech_pcm() -> np.ndarray:
    """Generate one real speech sample once and resample it to 16 kHz."""

    global _SPEECH_PCM
    if _SPEECH_PCM is not None:
        return _SPEECH_PCM

    model = Path.home() / ".asta" / "tts" / "voices" / "en_US-john-medium.onnx"
    config = model.with_suffix(".onnx.json")
    voice = PiperVoice.load(model, config_path=config, use_cuda=False)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(
            _SPEECH_TEXT,
            wav_file,
            syn_config=SynthesisConfig(length_scale=1.05),
            set_wav_format=True,
        )

    buffer.seek(0)
    with wave.open(buffer, "rb") as wav_file:
        source_rate = wav_file.getframerate()
        pcm = np.frombuffer(
            wav_file.readframes(wav_file.getnframes()),
            dtype="<i2",
        ).astype(np.float32) / 32768.0

    if source_rate == settings.VAD_SAMPLE_RATE:
        _SPEECH_PCM = pcm
    else:
        target = np.arange(
            0,
            pcm.size / source_rate,
            1 / settings.VAD_SAMPLE_RATE,
        ) * source_rate
        indices = np.clip(np.floor(target).astype(int), 0, pcm.size - 1)
        _SPEECH_PCM = pcm[indices]

    return _SPEECH_PCM


class FakeVADModel:
    """Deterministic stand-in with the real model's stateful interface."""

    def __init__(self, probabilities: list[float], delay: float = 0.0) -> None:
        self.probabilities = probabilities
        self.index = 0
        self.reset_calls = 0
        self.delay = delay
        self.active = 0
        self.max_active = 0

    def __call__(self, tensor: torch.Tensor, sample_rate: int) -> SimpleNamespace:
        probability = self.probabilities[min(self.index, len(self.probabilities) - 1)]
        self.index += 1

        if self.delay:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(self.delay)
            finally:
                self.active -= 1

        return SimpleNamespace(
            detach=lambda: SimpleNamespace(item=lambda: probability)
        )

    def reset_states(self) -> None:
        self.reset_calls += 1


class Phase18Stage3VADTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = VADService()

    async def _session(self, service: VADService | None = None) -> str:
        created = await (service or self.service).create_session(OWNER_USER_ID)
        return str(created["session_id"])

    async def test_01_package_and_bundled_model(self):
        self.assertEqual(importlib.metadata.version("silero-vad"), "6.2.3")
        self.assertIsNotNone(silero_vad)
        path = VADService.model_path()
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertGreaterEqual(path.stat().st_size, 1024 * 1024)
        self.assertTrue(self.service.model_available())

    async def test_02_status_contract_and_defaults(self):
        status = self.service.status()
        self.assertEqual(
            set(status),
            {
                "enabled",
                "available",
                "engine",
                "engine_version",
                "model_name",
                "device",
                "sample_rate",
                "frame_samples",
                "speech_threshold",
                "silence_threshold",
                "min_speech_duration_seconds",
                "silence_duration_seconds",
                "max_utterance_seconds",
                "max_session_seconds",
                "max_session_count",
                "max_upload_bytes",
            },
        )
        self.assertTrue(status["enabled"])
        self.assertTrue(status["available"])
        self.assertEqual(status["engine"], "silero-vad")
        self.assertEqual(status["engine_version"], "6.2.3")
        self.assertEqual(status["device"], "cpu")
        self.assertEqual(status["sample_rate"], 16000)
        self.assertEqual(status["frame_samples"], 512)
        self.assertEqual(status["speech_threshold"], 0.5)
        self.assertEqual(status["silence_threshold"], 0.35)
        self.assertEqual(status["min_speech_duration_seconds"], 0.3)
        self.assertEqual(status["silence_duration_seconds"], 0.6)
        self.assertEqual(status["max_utterance_seconds"], 15)
        self.assertEqual(status["max_session_seconds"], 60)

    async def test_03_model_initialization_and_caching(self):
        constructed: list[dict] = []
        fake = FakeVADModel([0.1])

        def fake_loader(**kwargs):
            constructed.append(kwargs)
            return fake

        with patch.object(silero_vad, "load_silero_vad", side_effect=fake_loader):
            first = await self.service._get_model()
            second = await self.service._get_model()

        self.assertIs(first, second)
        self.assertIs(first, fake)
        self.assertEqual(len(constructed), 1)
        self.assertIs(constructed[0]["onnx"], False)
        self.assertEqual(constructed[0]["sampling_rate"], 16000)

    async def test_04_real_silence_detection(self):
        session_id = await self._session()
        result = await self.service.process_frames(
            session_id,
            make_wav(zeros(16)),
            user_id=OWNER_USER_ID,
        )

        self.assertEqual(result["state"], "listening")
        self.assertFalse(result["speech_detected"])
        self.assertLess(result["speech_probability"], 0.5)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["sample_position"], 16 * 512)

    async def test_05_real_speech_detection(self):
        with patch.object(settings, "VAD_MIN_SPEECH_DURATION_SECONDS", 0.2):
            service = VADService()
            session_id = await self._session(service)
            events: list[str] = []
            completed: list[dict] = []
            speech = real_speech_pcm()
            batch_samples = 512 * 16

            for offset in range(0, speech.size, batch_samples):
                batch = speech[offset : offset + batch_samples]
                if batch.size % 512:
                    batch = batch[: batch.size - (batch.size % 512)]
                if batch.size:
                    result = await service.process_frames(
                        session_id,
                        make_wav(batch),
                        user_id=OWNER_USER_ID,
                    )
                    events.extend(result["events"])
                    completed.extend(result["completed_segments"])

            for _ in range(5):
                result = await service.process_frames(
                    session_id,
                    make_wav(zeros(16)),
                    user_id=OWNER_USER_ID,
                )
                events.extend(result["events"])
                completed.extend(result["completed_segments"])
                if completed:
                    break

        self.assertIn("speech_start", events)
        self.assertIn("segment_completed", events)
        self.assertTrue(completed)
        self.assertGreater(completed[0]["peak_probability"], 0.5)
        self.assertGreater(completed[0]["duration_seconds"], 0.2)

    async def test_06_speech_start_and_continuation(self):
        fake = FakeVADModel([0.9])
        self.service._model = fake
        session_id = await self._session()

        first = await self.service.process_frames(
            session_id,
            make_wav(zeros(8)),
            user_id=OWNER_USER_ID,
        )
        second = await self.service.process_frames(
            session_id,
            make_wav(zeros(8)),
            user_id=OWNER_USER_ID,
        )

        self.assertEqual(first["events"], ["speech_start"])
        self.assertEqual(second["events"], [])
        self.assertEqual(first["state"], "speaking")
        self.assertEqual(second["state"], "speaking")
        self.assertEqual(second["sample_position"], 16 * 512)

    async def test_07_short_pause_does_not_end_speech(self):
        probabilities = [0.9] * 4 + [0.2] * 2 + [0.9] * 4
        fake = FakeVADModel(probabilities)
        self.service._model = fake
        session_id = await self._session()

        result = await self.service.process_frames(
            session_id,
            make_wav(zeros(10)),
            user_id=OWNER_USER_ID,
        )

        self.assertIn("speech_start", result["events"])
        self.assertIn("silence_start", result["events"])
        self.assertNotIn("segment_completed", result["events"])
        self.assertEqual(result["state"], "speaking")
        self.assertEqual(result["completed_segments"], [])

    async def test_08_silence_threshold_ends_segment(self):
        probabilities = [0.9] * 8 + [0.1] * 20
        fake = FakeVADModel(probabilities)
        self.service._model = fake

        with patch.object(settings, "VAD_MIN_SPEECH_DURATION_SECONDS", 0.1):
            session_id = await self._session()
            result = await self.service.process_frames(
                session_id,
                make_wav(zeros(28)),
                user_id=OWNER_USER_ID,
            )

        self.assertIn("segment_completed", result["events"])
        self.assertEqual(result["state"], "listening")
        self.assertEqual(len(result["completed_segments"]), 1)
        self.assertEqual(result["completed_segments"][0]["reason"], "silence")

    async def test_09_minimum_speech_duration(self):
        probabilities = [0.9] + [0.1] * 20
        fake = FakeVADModel(probabilities)
        self.service._model = fake
        session_id = await self._session()

        result = await self.service.process_frames(
            session_id,
            make_wav(zeros(21)),
            user_id=OWNER_USER_ID,
        )

        self.assertIn("noise_rejected", result["events"])
        self.assertNotIn("segment_completed", result["events"])
        self.assertEqual(result["completed_segments"], [])

    async def test_10_maximum_utterance_duration(self):
        fake = FakeVADModel([0.9])
        self.service._model = fake

        with (
            patch.object(settings, "VAD_MIN_SPEECH_DURATION_SECONDS", 0.1),
            patch.object(settings, "VAD_MAX_UTTERANCE_SECONDS", 0.2),
        ):
            session_id = await self._session()
            result = await self.service.process_frames(
                session_id,
                make_wav(zeros(12)),
                user_id=OWNER_USER_ID,
            )

        self.assertIn("segment_completed", result["events"])
        self.assertEqual(
            result["completed_segments"][0]["reason"],
            "max_duration",
        )
        self.assertIn("speech_start", result["events"])

    async def test_11_invalid_sample_rate(self):
        session_id = await self._session()
        with self.assertRaises(VADValidationError):
            await self.service.process_frames(
                session_id,
                make_wav(zeros(4), sample_rate=8000),
                user_id=OWNER_USER_ID,
            )

    async def test_12_invalid_frame_size(self):
        session_id = await self._session()
        invalid = np.zeros(500, dtype=np.float32)
        with self.assertRaises(VADValidationError):
            await self.service.process_frames(
                session_id,
                make_wav(invalid),
                user_id=OWNER_USER_ID,
            )

    async def test_13_invalid_audio_input(self):
        session_id = await self._session()

        for payload in (b"", b"this is not audio", make_wav(zeros(4), channels=2)):
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(VADValidationError):
                    await self.service.process_frames(
                        session_id,
                        payload,
                        user_id=OWNER_USER_ID,
                    )

    async def test_14_controlled_model_failure(self):
        service = VADService()
        with patch.object(
            silero_vad,
            "load_silero_vad",
            side_effect=RuntimeError("secret model failure"),
        ):
            session_id = await self._session(service)
            with self.assertRaises(VADModelUnavailableError):
                await service.process_frames(
                    session_id,
                    make_wav(zeros(4)),
                    user_id=OWNER_USER_ID,
                )

    async def test_15_concurrency_is_serialized(self):
        service = VADService()
        fake = FakeVADModel([0.9], delay=0.03)
        service._model = fake
        sessions = [await self._session(service) for _ in range(3)]

        results = await asyncio.gather(
            *[
                service.process_frames(
                    session_id,
                    make_wav(zeros(4)),
                    user_id=OWNER_USER_ID,
                )
                for session_id in sessions
            ]
        )

        self.assertEqual([result["state"] for result in results], ["speaking"] * 3)
        self.assertEqual(fake.max_active, 1)
        self.assertEqual(fake.active, 0)

    async def test_16_no_permanent_audio_storage(self):
        fake = FakeVADModel([0.9] * 8 + [0.1] * 20)
        self.service._model = fake

        with patch.object(settings, "VAD_MIN_SPEECH_DURATION_SECONDS", 0.1):
            session_id = await self._session()
            await self.service.process_frames(
                session_id,
                make_wav(zeros(28)),
                user_id=OWNER_USER_ID,
            )

        state = self.service._sessions[session_id]
        self.assertTrue(state.completed)
        self.assertTrue(all(segment.wav_bytes for segment in state.completed.values()))

        self.assertTrue(
            await self.service.delete_session(session_id, user_id=OWNER_USER_ID)
        )
        self.assertNotIn(session_id, self.service._sessions)

        service_source = Path(vad_service_module.__file__).read_text(encoding="utf-8")
        api_source = Path(
            sys.modules["app.api.vad"].__file__
        ).read_text(encoding="utf-8")
        for source in (service_source, api_source):
            self.assertNotIn("write_text", source)
            self.assertNotIn("write_bytes", source)
            self.assertNotIn("tempfile", source)
            self.assertNotIn("snapshot_download", source)

    async def test_17_cpu_only_inference(self):
        self.assertEqual(settings.VAD_DEVICE, "cpu")
        self.assertFalse(torch.cuda.is_available())

        source = Path(vad_service_module.__file__).read_text(encoding="utf-8")
        self.assertIn("onnx=False", source)
        self.assertIn("sampling_rate=settings.VAD_SAMPLE_RATE", source)
        self.assertNotIn(".cuda(", source)
        self.assertNotIn("cuda:0", source)
        self.assertNotIn("torch.device('cuda'", source)

    async def test_18_stt_input_compatibility(self):
        fake = FakeVADModel([0.9] * 8 + [0.1] * 20)
        self.service._model = fake

        with patch.object(settings, "VAD_MIN_SPEECH_DURATION_SECONDS", 0.1):
            session_id = await self._session()
            result = await self.service.process_frames(
                session_id,
                make_wav(zeros(28)),
                user_id=OWNER_USER_ID,
            )
            segment_id = result["completed_segments"][0]["segment_id"]
            wav_bytes = await self.service.export_segment_wav(
                session_id,
                segment_id,
                user_id=OWNER_USER_ID,
            )

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 16000)

        decoded, duration = stt_service._decode_sync(wav_bytes)
        self.assertEqual(decoded.dtype, np.float32)
        self.assertGreater(duration, 0)
        self.assertAlmostEqual(decoded.size / 16000, duration, places=3)

    async def test_19_authentication_and_csrf(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )

        async def override_get_db():
            async with session_factory() as session:
                yield session

        fastapi_app.dependency_overrides[get_db] = override_get_db

        async with session_factory() as session:
            owner = User(username="phase18_vad_owner")
            session.add(owner)
            await session.commit()
            await session.refresh(owner)
            raw_session, csrf_token, _ = await auth_service.create_session(
                session,
                SimpleNamespace(user_id=owner.id),
            )

        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="https://test",
        ) as anonymous:
            anonymous_status = await anonymous.get("/api/vad/status")
            anonymous_create = await anonymous.post("/api/vad/sessions")

        self.assertEqual(anonymous_status.status_code, 401)
        self.assertEqual(anonymous_create.status_code, 401)

        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="https://test",
        ) as client:
            client.cookies.set(settings.AUTH_SESSION_COOKIE_NAME, raw_session)
            no_csrf = await client.post("/api/vad/sessions")
            status = await client.get(
                "/api/vad/status",
                headers={"X-CSRF-Token": csrf_token},
            )
            created = await client.post(
                "/api/vad/sessions",
                headers={"X-CSRF-Token": csrf_token},
            )

        self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(created.status_code, 200)
        self.assertTrue(created.json()["session_id"])
        self.assertTrue(
            await vad_service.delete_session(
                created.json()["session_id"],
                user_id=owner.id,
            )
        )

        fastapi_app.dependency_overrides.clear()
        await engine.dispose()

    async def test_20_no_llm_or_stage4_coupling(self):
        files = [
            Path(vad_service_module.__file__),
            Path(sys.modules["app.api.vad"].__file__),
            Path(r"C:\Asta\frontend\src\stores\vad.store.ts"),
            Path(r"C:\Asta\frontend\src\services\api\vad.api.ts"),
        ]
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in files
        ).lower()

        for token in (
            "groq",
            "ollama",
            "llm_router",
            "app.api.chat",
            "sendmessage(",
            "ttsspeak(",
            "wake_word",
            "continuous_listening",
            "startcontinuous",
        ):
            self.assertNotIn(token, combined, token)

        response_fields = set(self.service.status().keys())
        self.assertNotIn("text", response_fields)
        self.assertNotIn("transcript", response_fields)
        self.assertIsNotNone(TestClient)


if __name__ == "__main__":
    unittest.main(verbosity=2)
