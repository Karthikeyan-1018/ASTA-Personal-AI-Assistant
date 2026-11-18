"""Phase 6 VAD session ownership and cross-user isolation tests."""

from __future__ import annotations

import io
import time
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all ORM tables)
from app.api.dependencies import get_db  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.exceptions.vad_exceptions import VADSessionError  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import auth_service  # noqa: E402
from app.services.vad_service import vad_service  # noqa: E402


def _zeros(frames: int = 16) -> np.ndarray:
    return np.zeros(frames * settings.VAD_FRAME_SAMPLES, dtype=np.float32)


def _wav(samples: np.ndarray) -> bytes:
    pcm = np.clip(np.rint(samples * 32767), -32768, 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


class _FakeVADModel:
    """Deterministic stand-in used only to create one completed segment."""

    def __init__(self) -> None:
        self.probabilities = [0.9] * 8 + [0.1] * 20
        self.index = 0

    def __call__(self, tensor, sample_rate):
        probability = self.probabilities[min(self.index, len(self.probabilities) - 1)]
        self.index += 1
        return SimpleNamespace(
            detach=lambda: SimpleNamespace(item=lambda: probability)
        )

    def reset_states(self) -> None:
        return None


class Phase6VADOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """Every VAD session operation is bound to its creating authenticated user."""

    async def asyncSetUp(self) -> None:
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
            async with self.session_factory() as db_session:
                yield db_session

        fastapi_app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.addAsyncCleanup(self.engine.dispose)
        self.addAsyncCleanup(self._clear_registry)

        self.owner = await self._create_identity("phase6_vad_owner")
        self.other = await self._create_identity("phase6_vad_other")

    async def asyncTearDown(self) -> None:
        vad_service._model = None

    async def _clear_registry(self) -> None:
        async with vad_service._registry_lock:
            for session in vad_service._sessions.values():
                session.closed = True
                vad_service._clear_audio(session)
            vad_service._sessions.clear()

    async def _create_identity(self, username: str) -> dict[str, object]:
        async with self.session_factory() as db_session:
            user = User(username=username)
            db_session.add(user)
            await db_session.commit()
            await db_session.refresh(user)
            raw_session, csrf_token, _ = await auth_service.create_session(
                db_session,
                SimpleNamespace(user_id=user.id),
            )
        return {
            "user_id": int(user.id),
            "cookie": raw_session,
            "csrf": csrf_token,
        }

    def _client(self, identity: dict[str, object]) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="https://test",
        )
        client.cookies.set(
            settings.AUTH_SESSION_COOKIE_NAME,
            str(identity["cookie"]),
        )
        return client

    def _csrf(self, identity: dict[str, object]) -> dict[str, str]:
        return {"X-CSRF-Token": str(identity["csrf"])}

    async def _create_session(self, identity: dict[str, object]) -> str:
        async with self._client(identity) as client:
            response = await client.post(
                "/api/vad/sessions",
                headers=self._csrf(identity),
            )
        self.assertEqual(response.status_code, 200)
        return str(response.json()["session_id"])

    async def _completed_segment(self, session_id: str) -> str:
        with (
            patch.object(vad_service, "_model", _FakeVADModel()),
            patch.object(settings, "VAD_MIN_SPEECH_DURATION_SECONDS", 0.1),
        ):
            async with self._client(self.owner) as client:
                response = await client.post(
                    f"/api/vad/sessions/{session_id}/frames",
                    files={"file": ("batch.wav", _wav(_zeros(28)), "audio/wav")},
                    headers=self._csrf(self.owner),
                )
        self.assertEqual(response.status_code, 200)
        segments = response.json()["completed_segments"]
        self.assertTrue(segments)
        return str(segments[0]["segment_id"])

    async def test_01_owner_can_access_status_and_session(self):
        session_id = await self._create_session(self.owner)

        async with self._client(self.owner) as client:
            status = await client.get("/api/vad/status")
            session = await client.get(f"/api/vad/sessions/{session_id}")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["session_id"], session_id)

    async def test_02_owner_can_submit_frames(self):
        session_id = await self._create_session(self.owner)

        async with self._client(self.owner) as client:
            response = await client.post(
                f"/api/vad/sessions/{session_id}/frames",
                files={"file": ("batch.wav", _wav(_zeros(16)), "audio/wav")},
                headers=self._csrf(self.owner),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "listening")

    async def test_03_owner_can_retrieve_wav_segment(self):
        session_id = await self._create_session(self.owner)
        segment_id = await self._completed_segment(session_id)

        async with self._client(self.owner) as client:
            response = await client.get(
                f"/api/vad/sessions/{session_id}/segments/{segment_id}/wav"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.content[:4], b"RIFF")

    async def test_04_owner_can_delete_session(self):
        session_id = await self._create_session(self.owner)

        async with self._client(self.owner) as client:
            deleted = await client.delete(
                f"/api/vad/sessions/{session_id}",
                headers=self._csrf(self.owner),
            )
            after = await client.get(f"/api/vad/sessions/{session_id}")

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True})
        self.assertEqual(after.status_code, 404)

    async def test_05_other_user_cannot_access_session(self):
        session_id = await self._create_session(self.owner)

        async with self._client(self.other) as client:
            response = await client.get(f"/api/vad/sessions/{session_id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "The VAD session is unavailable.")

    async def test_06_other_user_cannot_submit_frames(self):
        session_id = await self._create_session(self.owner)

        async with self._client(self.other) as client:
            response = await client.post(
                f"/api/vad/sessions/{session_id}/frames",
                files={"file": ("batch.wav", _wav(_zeros(16)), "audio/wav")},
                headers=self._csrf(self.other),
            )

        self.assertEqual(response.status_code, 404)

    async def test_07_other_user_cannot_retrieve_audio(self):
        session_id = await self._create_session(self.owner)
        segment_id = await self._completed_segment(session_id)

        async with self._client(self.other) as client:
            response = await client.get(
                f"/api/vad/sessions/{session_id}/segments/{segment_id}/wav"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "The VAD speech segment is unavailable.")

    async def test_08_other_user_cannot_delete_session(self):
        session_id = await self._create_session(self.owner)

        async with self._client(self.other) as client:
            response = await client.delete(
                f"/api/vad/sessions/{session_id}",
                headers=self._csrf(self.other),
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn(session_id, vad_service._sessions)

    async def test_09_expired_session_is_handled_safely(self):
        session_id = await self._create_session(self.owner)
        async with vad_service._registry_lock:
            vad_service._sessions[session_id].last_activity = time.monotonic() - 1000

        async with self._client(self.owner) as client:
            response = await client.get(f"/api/vad/sessions/{session_id}")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(session_id, vad_service._sessions)

    async def test_10_service_methods_reject_foreign_user(self):
        owner_id = int(self.owner["user_id"])
        other_id = int(self.other["user_id"])
        created = await vad_service.create_session(owner_id)
        session_id = str(created["session_id"])

        with self.assertRaises(VADSessionError):
            await vad_service.get_session(session_id, user_id=other_id)
        with self.assertRaises(VADSessionError):
            await vad_service.process_frames(
                session_id,
                b"",
                user_id=other_id,
            )
        with self.assertRaises(VADSessionError):
            await vad_service.export_segment_wav(
                session_id,
                "missing-segment",
                user_id=other_id,
            )
        with self.assertRaises(VADSessionError):
            await vad_service.delete_session(session_id, user_id=other_id)

        self.assertTrue(
            await vad_service.delete_session(session_id, user_id=owner_id)
        )


if __name__ == "__main__":
    unittest.main()
