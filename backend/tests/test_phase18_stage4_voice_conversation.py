"""Phase 18 Stage 4 backend-owned local voice conversation tests.

Stage 1-3 services and the existing chat service are mocked at their real
contracts. These checks verify orchestration, state ownership, cleanup, and
protected API behavior without microphones, model inference, or LLM calls.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all ORM tables)
from app.api import voice_conversation as voice_api_module  # noqa: E402
from app.api.dependencies import get_db  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.exceptions.tts_exceptions import TTSServiceError  # noqa: E402
from app.exceptions.voice_conversation_exceptions import (  # noqa: E402
    VoiceConversationError,
    VoiceDuplicateSessionError,
    VoiceEmptyTranscriptionError,
    VoiceInvalidStateError,
    VoiceSessionError,
    VoiceTurnError,
)
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.voice_conversation import (  # noqa: E402
    VoiceConversationEvent,
    VoiceConversationState,
)
from app.services import chat_service as chat_service_module  # noqa: E402
from app.services import stt_service as stt_service_module  # noqa: E402
from app.services import tts_service as tts_service_module  # noqa: E402
from app.services import vad_service as vad_service_module  # noqa: E402
from app.services.auth_service import auth_service  # noqa: E402
from app.services.chat_service import chat_service  # noqa: E402
from app.services.stt_service import stt_service  # noqa: E402
from app.services.tts_service import tts_service  # noqa: E402
from app.services.vad_service import vad_service  # noqa: E402
from app.services.voice_conversation_manager import (  # noqa: E402
    VALID_TRANSITIONS,
    VoiceConversationManager,
)


STT_RESULT = {
    "success": True,
    "text": "What tasks are due today",
    "language": "en",
    "processing_ms": 12.5,
}

CHAT_RESULT = {
    "success": True,
    "response": "You have two tasks due today.",
    "conversation_id": 77,
    "provider": "ollama",
    "model": "qwen3:8b",
    "pending_confirmations": [],
}


class Phase18Stage4VoiceConversationTests(unittest.IsolatedAsyncioTestCase):
    """Twenty-two focused checks for the Stage 4 coordinator and API."""

    def setUp(self) -> None:
        self.manager = VoiceConversationManager()
        self.db = object()

        self._patch_value(settings, "VOICE_CONVERSATION_ENABLED", True)
        self._patch_value(settings, "VOICE_FOLLOW_UP_TIMEOUT_SECONDS", 10.0)
        self._patch_value(settings, "VOICE_SESSION_IDLE_TIMEOUT_SECONDS", 120.0)
        self._patch_value(settings, "VOICE_SESSION_MAX_SECONDS", 300.0)
        self._patch_value(settings, "VOICE_MAX_SESSIONS", 8)
        self._patch_value(settings, "VOICE_MAX_SESSIONS_PER_USER", 1)
        self._patch_value(settings, "VOICE_MIN_TRANSCRIPT_CHARS", 2)
        self._patch_value(settings, "VOICE_MAX_TURN_AUDIO_BYTES", 10 * 1024 * 1024)
        self._patch_value(settings, "VOICE_MAX_TTS_AUDIO_BYTES", 4 * 1024 * 1024)
        self._patch_value(settings, "VAD_ENABLED", True)
        self._patch_value(settings, "STT_ENABLED", True)
        self._patch_value(settings, "TTS_ENABLED", True)

        self._patch_return(vad_service, "model_available", True)
        self._patch_return(stt_service, "model_available", True)
        self._patch_return(tts_service, "voice_available", True)

        self.stt_mock = self._patch_async(
            stt_service, "transcribe", dict(STT_RESULT)
        )
        self.chat_mock = self._patch_async(
            chat_service, "get_response", dict(CHAT_RESULT)
        )
        self.tts_mock = self._patch_async(
            tts_service, "synthesize", b"RIFF-stage4-voice"
        )

    def _start_patch(self, patcher):
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _patch_value(self, target, attribute: str, value) -> None:
        self._start_patch(patch.object(target, attribute, value))

    def _patch_return(self, target, attribute: str, value) -> None:
        self._start_patch(patch.object(target, attribute, return_value=value))

    def _patch_async(self, target, attribute: str, return_value):
        return self._start_patch(
            patch.object(
                target,
                attribute,
                new=AsyncMock(return_value=return_value),
            )
        )

    async def _session(self, user_id: int = 1) -> dict:
        return await self.manager.create_session(user_id)

    async def _speech(self, session: dict, user_id: int = 1) -> dict:
        return await self.manager.apply_event(
            session["session_id"],
            user_id,
            VoiceConversationEvent.SPEECH_START,
            "vad-session-1",
        )

    async def _turn(self, session: dict, user_id: int = 1) -> dict:
        return await self.manager.submit_turn(
            session["session_id"],
            user_id,
            b"RIFF-real-vad-segment",
            self.db,
            "segment-1",
        )

    # 01 ------------------------------------------------------------------
    async def test_01_configuration_defaults_are_stage4_specific(self):
        env = Path(r"C:\Asta\.env.example").read_text(encoding="utf-8")
        config_text = Path(
            sys.modules[settings.__class__.__module__].__file__
        ).read_text(encoding="utf-8")

        for name in (
            "VOICE_CONVERSATION_ENABLED",
            "VOICE_FOLLOW_UP_TIMEOUT_SECONDS",
            "VOICE_SESSION_IDLE_TIMEOUT_SECONDS",
            "VOICE_SESSION_MAX_SECONDS",
            "VOICE_MAX_SESSIONS",
            "VOICE_MAX_SESSIONS_PER_USER",
            "VOICE_MIN_TRANSCRIPT_CHARS",
            "VOICE_MAX_TURN_AUDIO_BYTES",
            "VOICE_MAX_TTS_AUDIO_BYTES",
        ):
            self.assertIn(name, config_text, name)
            self.assertIn(name, env, name)
        self.assertIn("VOICE_FOLLOW_UP_TIMEOUT_SECONDS=10", env)

    # 02 ------------------------------------------------------------------
    async def test_02_all_required_states_and_validated_transitions(self):
        expected = {
            "idle",
            "listening",
            "speech_detected",
            "transcribing",
            "processing",
            "speaking",
            "follow_up_listening",
            "stopping",
            "error",
        }
        self.assertEqual(
            {state.value for state in VoiceConversationState}, expected
        )
        self.assertEqual(set(VALID_TRANSITIONS), set(VoiceConversationState))

        session = await self._session()
        with self.assertRaises(VoiceInvalidStateError):
            await self.manager.apply_event(
                session["session_id"],
                1,
                VoiceConversationEvent.PLAYBACK_COMPLETE,
            )
        self.assertEqual(
            (await self.manager.get_session(session["session_id"], 1))["state"],
            VoiceConversationState.LISTENING,
        )

    # 03 ------------------------------------------------------------------
    async def test_03_create_session_is_backend_owned_and_listening(self):
        session = await self._session(11)
        self.assertEqual(session["state"], VoiceConversationState.LISTENING)
        self.assertTrue(session["session_id"])
        self.assertIsNone(session["conversation_id"])
        self.assertIsNone(session["audio_base64"] if "audio_base64" in session else None)
        self.assertEqual(
            session["follow_up_timeout_seconds"],
            settings.VOICE_FOLLOW_UP_TIMEOUT_SECONDS,
        )

    # 04 ------------------------------------------------------------------
    async def test_04_one_active_session_per_user(self):
        await self._session(21)
        with self.assertRaises(VoiceDuplicateSessionError):
            await self._session(21)

    # 05 ------------------------------------------------------------------
    async def test_05_global_session_cap_is_enforced(self):
        self._patch_value(settings, "VOICE_MAX_SESSIONS", 2)
        await self._session(31)
        await self._session(32)
        with self.assertRaises(VoiceConversationError):
            await self._session(33)

    # 06 ------------------------------------------------------------------
    async def test_06_owner_isolation_for_get_event_and_stop(self):
        session = await self._session(41)
        with self.assertRaises(VoiceSessionError):
            await self.manager.get_session(session["session_id"], 42)
        with self.assertRaises(VoiceSessionError):
            await self.manager.apply_event(
                session["session_id"],
                42,
                VoiceConversationEvent.SPEECH_START,
            )
        with self.assertRaises(VoiceSessionError):
            await self.manager.stop_session(session["session_id"], 42)

    # 07 ------------------------------------------------------------------
    async def test_07_real_speech_event_advances_state(self):
        session = await self._session()
        speech = await self._speech(session)
        self.assertEqual(
            speech["state"], VoiceConversationState.SPEECH_DETECTED
        )
        self.assertIsNone(speech["follow_up_expires_at"])

    # 08 ------------------------------------------------------------------
    async def test_08_turn_runs_stt_chat_tts_in_order(self):
        session = await self._session()
        await self._speech(session)
        result = await self._turn(session)

        self.assertEqual(
            result["transcript"], STT_RESULT["text"]
        )
        self.assertEqual(
            result["assistant_response"], CHAT_RESULT["response"]
        )
        self.assertEqual(result["conversation_id"], 77)
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertTrue(result["tts_available"])
        self.assertEqual(result["audio_media_type"], "audio/wav")
        self.assertIsNotNone(result["audio_base64"])

        self.assertEqual(self.stt_mock.await_count, 1)
        self.assertEqual(self.chat_mock.await_count, 1)
        self.assertEqual(self.tts_mock.await_count, 1)
        self.assertEqual(self.stt_mock.await_args.args[1], "audio/wav")
        self.assertEqual(self.tts_mock.await_args.args[0], CHAT_RESULT["response"])

    # 09 ------------------------------------------------------------------
    async def test_09_chat_trusted_boundary_has_no_http_self_call(self):
        session = await self._session(51)
        await self._speech(session, 51)
        await self._turn(session, 51)

        kwargs = self.chat_mock.await_args.kwargs
        self.assertEqual(kwargs["prompt"], STT_RESULT["text"])
        self.assertFalse(kwargs["private"])
        self.assertEqual(kwargs["user_id"], 51)
        self.assertIs(kwargs["session"], self.db)
        self.assertIsNone(kwargs["conversation_id"])

        source = Path(
            sys.modules[
                "app.services.voice_conversation_manager"
            ].__file__
        ).read_text(encoding="utf-8")
        for forbidden in (
            "/api/chat",
            "httpx",
            "requests.",
            "llm_router",
            "generate_response",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    # 10 ------------------------------------------------------------------
    async def test_10_central_transition_sequence_is_observable(self):
        session = await self._session()
        await self._speech(session)

        original = VoiceConversationManager._transition
        observed: list[str] = []

        def recorder(instance, target):
            observed.append(target.value)
            return original(instance, target)

        with patch.object(
            VoiceConversationManager, "_transition", side_effect=recorder
        ):
            result = await self._turn(session)
            await self.manager.apply_event(
                session["session_id"],
                1,
                VoiceConversationEvent.PLAYBACK_COMPLETE,
            )

        self.assertEqual(
            observed[:4],
            [
                "transcribing",
                "processing",
                "speaking",
                "follow_up_listening",
            ],
        )
        self.assertEqual(result["state"], VoiceConversationState.SPEAKING)

    # 11 ------------------------------------------------------------------
    async def test_11_playback_complete_opens_bounded_follow_up_window(self):
        session = await self._session()
        await self._speech(session)
        await self._turn(session)

        follow_up = await self.manager.apply_event(
            session["session_id"],
            1,
            VoiceConversationEvent.PLAYBACK_COMPLETE,
        )
        self.assertEqual(
            follow_up["state"], VoiceConversationState.FOLLOW_UP_LISTENING
        )
        self.assertIsNotNone(follow_up["follow_up_expires_at"])

    # 12 ------------------------------------------------------------------
    async def test_12_follow_up_timeout_finalizes_session(self):
        self._patch_value(settings, "VOICE_FOLLOW_UP_TIMEOUT_SECONDS", 0.05)
        session = await self._session(61)
        await self._speech(session, 61)
        await self._turn(session, 61)
        await self.manager.apply_event(
            session["session_id"],
            61,
            VoiceConversationEvent.PLAYBACK_COMPLETE,
        )

        await asyncio.sleep(0.15)
        with self.assertRaises(VoiceSessionError):
            await self.manager.get_session(session["session_id"], 61)

    # 13 ------------------------------------------------------------------
    async def test_13_new_speech_cancels_follow_up_timeout(self):
        self._patch_value(settings, "VOICE_FOLLOW_UP_TIMEOUT_SECONDS", 0.05)
        session = await self._session(62)
        await self._speech(session, 62)
        await self._turn(session, 62)
        await self.manager.apply_event(
            session["session_id"],
            62,
            VoiceConversationEvent.PLAYBACK_COMPLETE,
        )
        speech = await self._speech(session, 62)

        await asyncio.sleep(0.15)
        current = await self.manager.get_session(session["session_id"], 62)
        self.assertEqual(speech["state"], VoiceConversationState.SPEECH_DETECTED)
        self.assertEqual(current["state"], VoiceConversationState.SPEECH_DETECTED)
        self.assertIsNone(current["follow_up_expires_at"])

    # 14 ------------------------------------------------------------------
    async def test_14_stop_releases_timeout_task_and_session(self):
        self._patch_value(settings, "VOICE_FOLLOW_UP_TIMEOUT_SECONDS", 0.05)
        session = await self._session(63)
        await self._speech(session, 63)
        await self._turn(session, 63)
        await self.manager.apply_event(
            session["session_id"],
            63,
            VoiceConversationEvent.PLAYBACK_COMPLETE,
        )

        stopped = await self.manager.stop_session(session["session_id"], 63)
        await asyncio.sleep(0.02)
        with self.assertRaises(VoiceSessionError):
            await self.manager.get_session(session["session_id"], 63)

        self.assertTrue(stopped["stopped"])
        self.assertEqual(stopped["state"], VoiceConversationState.IDLE)
        self.assertFalse(
            [
                task
                for task in asyncio.all_tasks()
                if task.get_name().startswith("voice-follow-up")
            ]
        )

    # 15 ------------------------------------------------------------------
    async def test_15_tts_failure_preserves_chat_response(self):
        tts_mock = self._start_patch(
            patch.object(
                tts_service,
                "synthesize",
                new=AsyncMock(side_effect=TTSServiceError("voice failed")),
            )
        )
        session = await self._session(71)
        await self._speech(session, 71)
        result = await self._turn(session, 71)

        self.assertEqual(result["assistant_response"], CHAT_RESULT["response"])
        self.assertFalse(result["tts_available"])
        self.assertIsNone(result["audio_base64"])
        self.assertIsNotNone(result["tts_error"])
        self.assertEqual(result["state"], VoiceConversationState.ERROR)
        self.assertEqual(tts_mock.await_count, 1)

    # 16 ------------------------------------------------------------------
    async def test_16_empty_transcription_is_rejected_before_chat(self):
        self.stt_mock.return_value = {"success": False, "text": ""}
        session = await self._session(72)
        await self._speech(session, 72)

        with self.assertRaises(VoiceEmptyTranscriptionError):
            await self._turn(session, 72)

        self.assertEqual(self.chat_mock.await_count, 0)
        current = await self.manager.get_session(session["session_id"], 72)
        self.assertEqual(current["state"], VoiceConversationState.ERROR)

    # 17 ------------------------------------------------------------------
    async def test_17_pending_confirmations_are_metadata_only(self):
        self.chat_mock.return_value = {
            **CHAT_RESULT,
            "pending_confirmations": [
                {
                    "confirmation_id": 9,
                    "tool": "os_command",
                    "category": "os",
                    "preview": "do not expose",
                    "expires_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        session = await self._session(73)
        await self._speech(session, 73)
        result = await self._turn(session, 73)

        self.assertEqual(len(result["pending_confirmations"]), 1)
        pending = result["pending_confirmations"][0]
        self.assertEqual(pending["confirmation_id"], 9)
        self.assertEqual(pending["tool"], "os_command")
        self.assertNotIn("preview", pending)

    # 18 ------------------------------------------------------------------
    async def test_18_oversized_turn_audio_is_rejected(self):
        self._patch_value(settings, "VOICE_MAX_TURN_AUDIO_BYTES", 4)
        session = await self._session(74)
        await self._speech(session, 74)

        with self.assertRaises(VoiceTurnError):
            await self.manager.submit_turn(
                session["session_id"],
                74,
                b"12345",
                self.db,
                "segment-large",
            )
        self.assertEqual(self.stt_mock.await_count, 0)

    # 19 ------------------------------------------------------------------
    async def test_19_idle_sessions_are_pruned(self):
        self._patch_value(settings, "VOICE_SESSION_IDLE_TIMEOUT_SECONDS", 0.01)
        session = await self._session(75)
        await asyncio.sleep(0.03)

        with self.assertRaises(VoiceSessionError):
            await self.manager.get_session(session["session_id"], 75)

    # 20 ------------------------------------------------------------------
    async def test_20_shutdown_cancels_all_sessions(self):
        session = await self._session(76)
        await self._speech(session, 76)
        await self._turn(session, 76)
        await self.manager.apply_event(
            session["session_id"],
            76,
            VoiceConversationEvent.PLAYBACK_COMPLETE,
        )

        await self.manager.shutdown()
        await asyncio.sleep(0.02)
        with self.assertRaises(VoiceSessionError):
            await self.manager.get_session(session["session_id"], 76)
        self.assertFalse(
            [
                task
                for task in asyncio.all_tasks()
                if task.get_name().startswith("voice-follow-up")
            ]
        )

    # 21 ------------------------------------------------------------------
    async def test_21_api_auth_csrf_and_real_turn_endpoint(self):
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
        api_patcher = patch.object(
            voice_api_module, "voice_conversation_manager", self.manager
        )
        api_patcher.start()
        self.addCleanup(api_patcher.stop)
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.addCleanup(engine.dispose)

        async with session_factory() as db_session:
            owner = User(username="phase18_stage4_owner")
            db_session.add(owner)
            await db_session.commit()
            await db_session.refresh(owner)
            raw_session, csrf_token, _ = await auth_service.create_session(
                db_session,
                SimpleNamespace(user_id=owner.id),
            )

        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="https://test",
        ) as anonymous:
            anonymous_status = await anonymous.get(
                "/api/voice-conversations/status"
            )
            anonymous_create = await anonymous.post(
                "/api/voice-conversations/sessions"
            )

        self.assertEqual(anonymous_status.status_code, 401)
        self.assertEqual(anonymous_create.status_code, 401)

        headers = {"X-CSRF-Token": csrf_token}
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="https://test",
        ) as client:
            client.cookies.set(settings.AUTH_SESSION_COOKIE_NAME, raw_session)
            no_csrf = await client.post("/api/voice-conversations/sessions")
            status = await client.get(
                "/api/voice-conversations/status", headers=headers
            )
            created = await client.post(
                "/api/voice-conversations/sessions", headers=headers
            )
            session_id = created.json()["session_id"]
            event = await client.post(
                f"/api/voice-conversations/sessions/{session_id}/events",
                json={"event": "speech_start", "vad_session_id": "vad-api"},
                headers=headers,
            )
            turn = await client.post(
                f"/api/voice-conversations/sessions/{session_id}/turns",
                data={"segment_id": "segment-api"},
                files={
                    "file": (
                        "segment.wav",
                        b"RIFF-real-segment",
                        "audio/wav",
                    )
                },
                headers=headers,
            )
            playback = await client.post(
                f"/api/voice-conversations/sessions/{session_id}/events",
                json={"event": "playback_complete"},
                headers=headers,
            )
            stopped = await client.post(
                f"/api/voice-conversations/sessions/{session_id}/stop",
                headers=headers,
            )

        self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(event.status_code, 200)
        self.assertEqual(turn.status_code, 200)
        self.assertEqual(playback.status_code, 200)
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(turn.json()["transcript"], STT_RESULT["text"])
        self.assertTrue(turn.json()["audio_base64"])
        self.assertEqual(
            playback.json()["state"],
            VoiceConversationState.FOLLOW_UP_LISTENING.value,
        )

    # 22 ------------------------------------------------------------------
    async def test_22_no_forbidden_features_or_new_persistence(self):
        paths = [
            Path(sys.modules["app.services.voice_conversation_manager"].__file__),
            Path(voice_api_module.__file__),
            Path(r"C:\Asta\frontend\src\stores\voiceConversation.store.ts"),
            Path(
                r"C:\Asta\frontend\src\services\api\voiceConversation.api.ts"
            ),
        ]
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in paths
        ).lower()

        for forbidden in (
            "wake_word",
            "hey asta",
            "continuous_listening",
            "startcontinuous",
            "barge_in",
            "full_duplex",
            "groq",
            "ollama",
            "openai",
            "elevenlabs",
            "agriculture_db",
            "alembic",
            "create_all",
            "add_message(",
            "approve_confirmation",
            "/api/chat",
        ):
            self.assertNotIn(forbidden, combined, forbidden)

        for module in (
            chat_service_module,
            stt_service_module,
            tts_service_module,
            vad_service_module,
        ):
            self.assertTrue(module.__name__.startswith("app.services."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
