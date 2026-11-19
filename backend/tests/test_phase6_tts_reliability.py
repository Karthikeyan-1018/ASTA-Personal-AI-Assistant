"""Phase 6 TTS size-limit reliability tests.

These tests use the real local Piper voice for valid audio generation and a
deterministic patched service for failure, limit, and API error behavior. No
audio is written to disk and no private text is logged.
"""

from __future__ import annotations

import asyncio
import threading
import time
import tracemalloc
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all ORM tables)
from app.api.dependencies import get_db  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.exceptions.tts_exceptions import (  # noqa: E402
    TTSAudioTooLargeError,
    TTSInferenceError,
)
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import auth_service  # noqa: E402
from app.services.tts_service import tts_service  # noqa: E402


def _valid_wav(payload: bytes) -> bool:
    return payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"


class Phase6TTSReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """Bounded TTS generation with explicit, non-truncating failures."""

    async def test_01_short_response(self):
        started = time.perf_counter()
        wav = await tts_service.synthesize("Short response works.")
        elapsed_ms = (time.perf_counter() - started) * 1000

        self.assertTrue(_valid_wav(wav))
        self.assertGreater(len(wav), 44)
        self.assertLessEqual(len(wav), settings.VOICE_MAX_TTS_AUDIO_BYTES)
        print(
            "PHASE6_TTS_SHORT "
            f"SYNTHESIS_MS={elapsed_ms:.1f} AUDIO_BYTES={len(wav)}"
        )

    async def test_02_medium_response(self):
        text = "Local speech remains clear and bounded. " * 20
        wav = await tts_service.synthesize(text)

        self.assertTrue(_valid_wav(wav))
        self.assertGreater(len(wav), 44)
        self.assertLessEqual(len(wav), settings.VOICE_MAX_TTS_AUDIO_BYTES)

    async def test_03_long_response_with_memory_measurement(self):
        text = (
            "ASTA can synthesize longer answers through the same local Piper "
            "voice without changing the WAV format. "
        ) * 19
        self.assertLessEqual(len(text), 2000)

        tracemalloc.start()
        try:
            wav = await tts_service.synthesize(text)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertTrue(_valid_wav(wav))
        self.assertGreater(len(wav), 44)
        self.assertLessEqual(len(wav), settings.VOICE_MAX_TTS_AUDIO_BYTES)
        self.assertGreater(peak, 0)

        print(
            "PHASE6_LONG_TTS "
            f"TEXT_CHARS={len(text)} AUDIO_BYTES={len(wav)} "
            f"TRACEMALLOC_PEAK_BYTES={peak}"
        )

    async def test_04_over_limit_fails_without_silent_truncation(self):
        with patch.object(settings, "VOICE_MAX_TTS_AUDIO_BYTES", 1024):
            with self.assertRaises(TTSAudioTooLargeError) as raised:
                await tts_service.synthesize("This answer must not be truncated.")

        self.assertIn("exceeded", str(raised.exception))

    async def test_05_concurrent_requests_are_serialized_and_bounded(self):
        original = tts_service._synthesize_sync
        state = {"active": 0, "max_active": 0}
        lock = threading.Lock()

        def tracked(voice, text, max_bytes):
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                return original(voice, text, max_bytes)
            finally:
                with lock:
                    state["active"] -= 1

        with (
            patch.object(settings, "VOICE_MAX_TTS_AUDIO_BYTES", 8 * 1024 * 1024),
            patch.object(tts_service, "_synthesize_sync", side_effect=tracked),
        ):
            results = await asyncio.gather(
                *[
                    tts_service.synthesize(f"Concurrent request {index} is ready.")
                    for index in range(3)
                ]
            )

        self.assertEqual(state["max_active"], 1)
        self.assertEqual(state["active"], 0)
        self.assertTrue(all(_valid_wav(result) for result in results))
        self.assertTrue(all(len(result) <= 8 * 1024 * 1024 for result in results))

    async def test_06_failure_and_recovery(self):
        original = tts_service._synthesize_sync
        calls = 0

        def flaky(voice, text, max_bytes):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("controlled local failure")
            return original(voice, text, max_bytes)

        with patch.object(tts_service, "_synthesize_sync", side_effect=flaky):
            with self.assertRaises(TTSInferenceError):
                await tts_service.synthesize("This synthesis fails once.")

            recovered = await tts_service.synthesize("Recovery produces valid audio.")

        self.assertEqual(calls, 2)
        self.assertTrue(_valid_wav(recovered))

    async def test_07_api_returns_413_for_oversized_audio(self):
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
            async with session_factory() as db_session:
                yield db_session

        fastapi_app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.addAsyncCleanup(engine.dispose)

        async with session_factory() as db_session:
            owner = User(username="phase6_tts_owner")
            db_session.add(owner)
            await db_session.commit()
            await db_session.refresh(owner)
            raw_session, csrf_token, _ = await auth_service.create_session(
                db_session,
                SimpleNamespace(user_id=owner.id),
            )

        with patch.object(
            tts_service,
            "synthesize",
            new=AsyncMock(
                side_effect=TTSAudioTooLargeError(
                    "The generated speech audio exceeded the configured limit."
                )
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=fastapi_app),
                base_url="https://test",
            ) as client:
                client.cookies.set(settings.AUTH_SESSION_COOKIE_NAME, raw_session)
                response = await client.post(
                    "/api/tts/synthesize",
                    json={"text": "This response is too long to speak."},
                    headers={"X-CSRF-Token": csrf_token},
                )

        self.assertEqual(response.status_code, 413)
        self.assertIn("size limit", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
