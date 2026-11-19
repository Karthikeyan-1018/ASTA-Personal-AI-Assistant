"""Focused coverage for speech-only formatting and configurable Piper output."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from httpx import ASGITransport, AsyncClient
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.dependencies import get_db
from app.core.config import settings
from app.database.base import Base
from app.main import app as fastapi_app
from app.models.user import User
from app.personality import build_persona_instructions, load_personality_config
from app.services.auth_service import auth_service
from app.services.speech_normalizer import normalize_speech_text
from app.services import tts_service as tts_service_module
from app.services.tts_service import TTSService


class SpeechNormalizationTests(unittest.TestCase):
    def test_markdown_links_code_and_technical_initialisms_are_spoken_cleanly(self):
        original = (
            "## API overview\nUse the [API guide](https://example.test/docs).\n"
            "`CPU` is used here. A 2 GB limit is 95%.\n```py\nprint('hello')\n```"
        )
        spoken = normalize_speech_text(original)

        self.assertIn("A P I overview", spoken)
        self.assertIn("A P I guide", spoken)
        self.assertNotIn("https://", spoken)
        self.assertIn("C P U", spoken)
        self.assertIn("2 gigabytes", spoken)
        self.assertIn("95 percent", spoken)
        self.assertIn("code example is shown in the written response", spoken)
        self.assertEqual(original.splitlines()[0], "## API overview")

    def test_voice_turn_includes_spoken_style_without_changing_other_modes(self):
        config = load_personality_config()
        written = build_persona_instructions(config, "Explain an API")
        spoken = build_persona_instructions(
            config, "Explain an API", spoken_response=True
        )

        self.assertNotIn("This is a spoken conversation", written)
        self.assertIn("This is a spoken conversation", spoken)


class PiperSettingsTests(unittest.TestCase):
    def test_only_complete_local_voice_pairs_are_offered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "en_US-test-medium.onnx").touch()
            (directory / "en_US-test-medium.onnx.json").touch()
            (directory / "en_US-incomplete-medium.onnx").touch()
            with patch.object(tts_service_module, "VOICE_DIR", directory):
                self.assertEqual(
                    TTSService.available_voices(), ["en_US-test-medium"]
                )

    def test_speed_and_sentence_pause_are_applied_to_real_piper_chunks(self):
        class FakeSynthesisConfig:
            def __init__(self, **values):
                self.values = values

        synthesis_configs = []

        class FakeChunk:
            sample_rate = 16000
            sample_width = 2
            sample_channels = 1
            audio_int16_bytes = b"\x01\x00" * 8

        fake_piper = types.ModuleType("piper")
        fake_piper.SynthesisConfig = FakeSynthesisConfig
        def synthesize(_text, config):
            synthesis_configs.append(config)
            return iter([FakeChunk(), FakeChunk()])

        voice = SimpleNamespace(synthesize=synthesize)
        service = TTSService()
        service._active_speed = 1.2

        with (
            patch.dict(sys.modules, {"piper": fake_piper}),
            patch("app.services.tts_service.settings.TTS_SENTENCE_PAUSE_SECONDS", 0.01),
        ):
            wav_bytes = service._synthesize_sync(voice, "One. Two.", 10000)

        self.assertEqual(wav_bytes[:4], b"RIFF")
        self.assertEqual(len(wav_bytes), 44 + (16 + 320 + 16))
        self.assertAlmostEqual(
            synthesis_configs[0].values["length_scale"], 1.05 / 1.2
        )


class VoiceSettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async def override_get_db():
            async with self.session_factory() as session:
                yield session

        fastapi_app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.addAsyncCleanup(self.engine.dispose)
        self.sessions = {}
        async with self.session_factory() as session:
            for label in ("a", "b"):
                user = User(username=f"tts_settings_user_{label}")
                session.add(user)
                await session.commit()
                await session.refresh(user)
                raw_session, csrf_token, _ = await auth_service.create_session(
                    session, SimpleNamespace(user_id=user.id)
                )
                self.sessions[label] = (raw_session, csrf_token)

    def _client(self, label: str) -> AsyncClient:
        raw_session, _ = self.sessions[label]
        client = AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="https://test",
        )
        client.cookies.set(settings.AUTH_SESSION_COOKIE_NAME, raw_session)
        return client

    def _headers(self, label: str) -> dict[str, str]:
        return {"X-CSRF-Token": self.sessions[label][1]}

    async def test_voice_settings_are_persistent_and_user_scoped(self):
        async with self._client("a") as client_a:
            initial_a = await client_a.get("/api/tts/settings")
            updated_a = await client_a.put(
                "/api/tts/settings",
                json={"voice": "en_US-john-medium", "speed": 1.1},
                headers=self._headers("a"),
            )
            confirmed_a = await client_a.get("/api/tts/settings")

        async with self._client("b") as client_b:
            initial_b = await client_b.get("/api/tts/settings")

        self.assertEqual(initial_a.status_code, 200)
        self.assertEqual(initial_b.status_code, 200)
        self.assertEqual(initial_a.json()["speed"], 1.0)
        self.assertEqual(updated_a.status_code, 200)
        self.assertEqual(confirmed_a.json()["speed"], 1.1)
        self.assertEqual(initial_b.json()["speed"], 1.0)
        self.assertIn("en_US-john-medium", confirmed_a.json()["installed_voices"])


if __name__ == "__main__":
    unittest.main()
