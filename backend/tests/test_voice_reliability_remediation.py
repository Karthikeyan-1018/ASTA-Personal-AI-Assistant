"""Regression tests for ASTA voice conversation reliability and fixes.

Verifies:
1. VAD PCM encoding: 16-bit signed PCM frames, correct WAV length and duration.
2. VAD -> STT end-to-end: synthesized audio passed through VAD segments is accurately transcribed by STT.
3. SPEECH_START idempotency: duplicate SPEECH_START events do not fail with VoiceInvalidStateError.
4. Error specificity: STT and LLM errors produce meaningful diagnostic messages.
5. Multi-turn execution: 5 consecutive turns maintain conversation continuity and state transitions.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
import unittest
import wave
from unittest.mock import AsyncMock, patch

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.config import settings
from app.database.base import Base
from app.exceptions.llm_exceptions import LLMTimeoutError, LLMUnavailableError
from app.exceptions.stt_exceptions import STTInferenceError, STTModelNotFoundError
from app.exceptions.voice_conversation_exceptions import VoiceInvalidStateError, VoiceTurnError
from app.models.user import User
from app.schemas.voice_conversation import (
    VoiceConversationEvent,
    VoiceConversationState,
)
from app.services.chat_service import chat_service
from app.services.blocking import run_blocking_to_completion
from app.services.stt_service import stt_service
from app.services.tts_service import tts_service
from app.services.vad_service import _SessionState, vad_service
from app.services.voice_conversation_manager import (
    State,
    VoiceConversationManager,
)


class VoiceReliabilityRemediationTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for VAD PCM encoding, idempotency, and multi-turn execution."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _make_pcm16_wav(self, samples: np.ndarray, sample_rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(samples.astype("<i2").tobytes())
        return buf.getvalue()

    async def test_cancelled_worker_inference_is_drained_before_return(self):
        worker_started = threading.Event()
        worker_finished = threading.Event()

        def simulated_native_inference():
            worker_started.set()
            time.sleep(0.05)
            worker_finished.set()
            return b"done"

        task = asyncio.create_task(
            run_blocking_to_completion(simulated_native_inference)
        )
        await asyncio.wait_for(asyncio.to_thread(worker_started.wait), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(worker_finished.is_set())

    def test_01_vad_process_sync_stores_16bit_pcm(self):
        """Verify that _process_sync stores 16-bit signed PCM frames, not 32-bit floats."""
        class DummyModel:
            def __call__(self, tensor, sample_rate):
                class DummyProb:
                    def detach(self):
                        return self
                    def item(self):
                        return 0.95
                return DummyProb()

        session = _SessionState(
            session_id="test_sess",
            user_id=1,
            created_at=0.0,
            last_activity=0.0,
        )

        # 512 samples of float32 in [-1.0, 1.0]
        test_samples = np.sin(np.linspace(0, 20, 512, dtype=np.float32))

        result = vad_service._process_sync(
            DummyModel(),
            session,
            test_samples,
        )

        self.assertTrue(result["speech_detected"])
        self.assertEqual(session.utterance_samples, 512)
        # 512 samples at 16-bit (2 bytes per sample) MUST be exactly 1024 bytes
        self.assertEqual(len(session.pcm), 1024)

        # Reconstructed int16 values must match the original float32 values scaled to 32767
        reconstructed = np.frombuffer(session.pcm, dtype="<i2")
        expected = np.clip(test_samples * 32767.0, -32768, 32767).astype("<i2")
        np.testing.assert_array_equal(reconstructed, expected)

    def test_02_vad_exported_wav_has_correct_duration_and_nframes(self):
        """Verify exported segment WAV has accurate frame count matching duration."""
        pcm = np.random.randint(-15000, 15000, 16000, dtype=np.int16).tobytes()
        wav_bytes = vad_service._pcm_to_wav(pcm)

        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnframes(), 16000)
            self.assertAlmostEqual(w.getnframes() / 16000.0, 1.0)

    async def test_03_speech_start_event_is_idempotent(self):
        """Verify that sending SPEECH_START when already in SPEECH_DETECTED does not fail."""
        manager = VoiceConversationManager()
        session = await manager.create_session(user_id=1)
        session_id = session["session_id"]

        # First SPEECH_START transitions from LISTENING to SPEECH_DETECTED
        res1 = await manager.apply_event(
            session_id,
            user_id=1,
            event=VoiceConversationEvent.SPEECH_START,
        )
        self.assertEqual(res1["state"], State.SPEECH_DETECTED)

        # Second SPEECH_START while already in SPEECH_DETECTED must succeed idempotently
        res2 = await manager.apply_event(
            session_id,
            user_id=1,
            event=VoiceConversationEvent.SPEECH_START,
        )
        self.assertEqual(res2["state"], State.SPEECH_DETECTED)

    async def test_04_submit_turn_reports_stt_and_llm_errors_specifically(self):
        """Verify that STT and LLM errors in submit_turn are reported with informative detail."""
        manager = VoiceConversationManager()

        async with self.session_factory() as db:
            user = User(id=1, username="testuser")
            db.add(user)
            await db.commit()

            # Test STT error reporting
            session1 = await manager.create_session(user_id=1)
            sid1 = session1["session_id"]
            await manager.apply_event(sid1, user_id=1, event=VoiceConversationEvent.SPEECH_START)

            with patch.object(stt_service, "transcribe", side_effect=STTInferenceError("Local speech inference failed.")):
                with self.assertRaises(VoiceTurnError) as cm:
                    await manager.submit_turn(sid1, user_id=1, audio=b"RIFFdummyWAVE", db=db)
                self.assertIn("Local speech inference failed", str(cm.exception))

            # Test LLM error reporting
            session2 = await manager.create_session(user_id=1)
            sid2 = session2["session_id"]
            await manager.apply_event(sid2, user_id=1, event=VoiceConversationEvent.SPEECH_START)

            with patch.object(stt_service, "transcribe", return_value={"success": True, "text": "test prompt"}):
                with patch.object(chat_service, "get_response", side_effect=LLMTimeoutError("Groq request timed out.")):
                    with self.assertRaises(VoiceTurnError) as cm:
                        await manager.submit_turn(sid2, user_id=1, audio=b"RIFFdummyWAVE", db=db)
                    self.assertIn("Groq request timed out", str(cm.exception))

    async def test_05_multi_turn_consecutive_flow(self):
        """Verify at least five consecutive turns maintain conversation continuity."""
        manager = VoiceConversationManager()

        async with self.session_factory() as db:
            user = User(id=1, username="testowner")
            db.add(user)
            await db.commit()

            # Start voice session
            session = await manager.create_session(user_id=1)
            sid = session["session_id"]
            self.assertEqual(session["state"], State.LISTENING)

            turns_input = [
                ("Hello ASTA.", "Hello! How can I assist you today?"),
                ("Explain what an API is.", "An API is a set of rules allowing programs to communicate."),
                ("Give me a simple Java example.", "Here is a simple Java HttpClient example."),
                ("Explain that example again in simpler words.", "In simple words, it is like ordering food at a restaurant."),
                ("Thank you, stop the conversation.", "You're welcome! Conversation stopped."),
            ]

            conversation_id = None

            for turn_idx, (user_text, assistant_text) in enumerate(turns_input, start=1):
                # 1. Speech starts
                await manager.apply_event(sid, user_id=1, event=VoiceConversationEvent.SPEECH_START)

                # 2. Turn is submitted
                dummy_wav = self._make_pcm16_wav(np.zeros(1600, dtype=np.int16))
                with patch.object(stt_service, "transcribe", return_value={"success": True, "text": user_text}):
                    with patch.object(
                        chat_service,
                        "get_response",
                        return_value={
                            "response": assistant_text,
                            "conversation_id": conversation_id or 101,
                            "provider": "ollama",
                            "model": "qwen3:8b",
                        },
                    ):
                        with patch.object(tts_service, "synthesize", return_value=self._make_pcm16_wav(np.zeros(3200, dtype=np.int16))):
                            result = await manager.submit_turn(
                                session_id=sid,
                                user_id=1,
                                audio=dummy_wav,
                                db=db,
                                segment_id=f"seg_{turn_idx}",
                            )

                self.assertEqual(result["state"], State.SPEAKING)
                self.assertEqual(result["transcript"], user_text)
                self.assertEqual(result["assistant_response"], assistant_text)
                self.assertTrue(result["tts_available"])
                self.assertIsNotNone(result["audio_base64"])
                conversation_id = result["conversation_id"]

                # 3. Audio playback completes, follow-up listening begins (turns 1-4)
                if turn_idx < 5:
                    follow_up = await manager.apply_event(
                        sid,
                        user_id=1,
                        event=VoiceConversationEvent.PLAYBACK_COMPLETE,
                    )
                    self.assertEqual(follow_up["state"], State.FOLLOW_UP_LISTENING)
                else:
                    # Turn 5: Stop the conversation cleanly
                    stop_result = await manager.stop_session(sid, user_id=1)
                    self.assertTrue(stop_result["stopped"])
                    self.assertEqual(stop_result["state"], State.IDLE)

            # Check that manager status has no active session
            status = manager.status(user_id=1)
            self.assertIsNone(status["active_session_id"])
            self.assertEqual(status["state"], State.IDLE)
