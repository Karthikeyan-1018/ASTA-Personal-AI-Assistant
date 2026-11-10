"""Phase 8 single-owner authentication, session, CSRF, and protection tests.

These tests exercise the real FastAPI application and the real auth service
against an isolated SQLite database. PostgreSQL-specific constraints are
verified separately by ``scripts/verify_phase8_auth.py``.

Cookies are issued with ``Secure`` enabled, so the HTTP client uses an
``https://`` base URL. The TrustedHost allowlist includes ``test``.
"""

import hashlib
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.exceptions.auth_exceptions import InvalidOwnerPasswordError
from app.main import app
from app.models.auth import AuthSession, OwnerAccount
from app.models.task_action import PendingActionStatus, PendingActionType
from app.models.user import User
from app.services.auth_service import auth_service, hash_token
from app.services.chat_service import chat_service
from app.services.conversation_service import conversation_service
from app.task_intent.pending_service import pending_action_service


OWNER_USERNAME = "owner_test"
OWNER_PASSWORD = "correct-horse-battery-staple"
SETUP_TOKEN = "phase8-test-setup-token"


class Phase8AuthTestCase(unittest.IsolatedAsyncioTestCase):
    """Shared isolated database, settings, and HTTP client."""

    async def asyncSetUp(self):

        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db

        self._stack = ExitStack()
        self._stack.enter_context(
            patch.object(settings, "AUTH_OWNER_SETUP_TOKEN", SETUP_TOKEN)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_COOKIE_SECURE", True)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_COOKIE_SAMESITE", "lax")
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_SESSION_LAST_USED_UPDATE_SECONDS", 300)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_SESSION_LIFETIME_HOURS", 168)
        )

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
        )

    async def asyncTearDown(self):

        await self.client.aclose()
        self._stack.close()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _setup_owner(
        self,
        username: str = OWNER_USERNAME,
        password: str = OWNER_PASSWORD,
        setup_token: str | None = SETUP_TOKEN,
    ):
        headers = {}

        if setup_token is not None:
            headers["X-Setup-Token"] = setup_token

        return await self.client.post(
            "/api/auth/setup",
            json={"username": username, "password": password},
            headers=headers,
        )

    async def _login(
        self,
        username: str = OWNER_USERNAME,
        password: str = OWNER_PASSWORD,
    ):
        return await self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )

    async def _create_owner_and_login(self) -> tuple[int, str]:
        setup = await self._setup_owner()
        self.assertEqual(setup.status_code, 201, setup.text)

        login = await self._login()
        self.assertEqual(login.status_code, 200, login.text)

        csrf = login.json()["csrf_token"]
        return setup.json()["id"], csrf

    def _session_token(self) -> str | None:
        return self.client.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)

    async def _get_auth_session(self, raw_token: str) -> AuthSession | None:
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(AuthSession).where(
                    AuthSession.token_hash == hash_token(raw_token)
                )
            )
            return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 1. Owner setup
    # ------------------------------------------------------------------

    async def test_owner_setup_creates_single_hashed_account(self):

        response = await self._setup_owner()

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()

        self.assertEqual(body["username"], OWNER_USERNAME)
        self.assertNotIn("password", body)
        self.assertNotIn("password_hash", body)
        self.assertNotIn(OWNER_PASSWORD, response.text)

        async with self.SessionLocal() as session:
            owners = (
                await session.execute(select(OwnerAccount))
            ).scalars().all()
            users = (
                await session.execute(select(User))
            ).scalars().all()

        self.assertEqual(len(owners), 1)
        self.assertEqual(len(users), 1)

        owner = owners[0]

        self.assertEqual(owner.id, 1)
        self.assertTrue(owner.password_hash.startswith("$argon2id$"))
        self.assertNotIn(OWNER_PASSWORD, owner.password_hash)
        self.assertTrue(
            auth_service.verify_password(owner.password_hash, OWNER_PASSWORD)
        )

    async def test_duplicate_owner_setup_is_rejected(self):

        first = await self._setup_owner()
        self.assertEqual(first.status_code, 201, first.text)

        second = await self._setup_owner(
            username="another_owner",
            password="another-strong-password",
        )

        self.assertEqual(second.status_code, 409, second.text)

        async with self.SessionLocal() as session:
            owners = (
                await session.execute(select(OwnerAccount))
            ).scalars().all()

        self.assertEqual(len(owners), 1)

    async def test_setup_disabled_when_token_not_configured(self):

        with patch.object(settings, "AUTH_OWNER_SETUP_TOKEN", ""):
            response = await self._setup_owner(setup_token=None)

        self.assertEqual(response.status_code, 503, response.text)

        async with self.SessionLocal() as session:
            owners = (
                await session.execute(select(OwnerAccount))
            ).scalars().all()

        self.assertEqual(owners, [])

    async def test_setup_rejects_wrong_token(self):

        response = await self._setup_owner(setup_token="wrong-token")

        self.assertEqual(response.status_code, 403, response.text)

        async with self.SessionLocal() as session:
            owners = (
                await session.execute(select(OwnerAccount))
            ).scalars().all()

        self.assertEqual(owners, [])

    async def test_setup_rejects_weak_password(self):

        response = await self._setup_owner(password="short")

        self.assertEqual(response.status_code, 400, response.text)

    async def test_setup_does_not_reuse_existing_users(self):

        async with self.SessionLocal() as session:
            session.add(User(username="existing_user"))
            await session.commit()

        response = await self._setup_owner()

        self.assertEqual(response.status_code, 201, response.text)

        async with self.SessionLocal() as session:
            users = (
                await session.execute(select(User).order_by(User.id))
            ).scalars().all()

        usernames = [user.username for user in users]
        self.assertIn("existing_user", usernames)
        self.assertIn(OWNER_USERNAME, usernames)
        self.assertEqual(len(usernames), 2)

    # ------------------------------------------------------------------
    # 2. Password hashing
    # ------------------------------------------------------------------

    async def test_password_hashes_are_argon2id_and_verifiable(self):

        password_hash = auth_service.hash_password(OWNER_PASSWORD)

        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertNotIn(OWNER_PASSWORD, password_hash)
        self.assertTrue(
            auth_service.verify_password(password_hash, OWNER_PASSWORD)
        )
        self.assertFalse(
            auth_service.verify_password(password_hash, "wrong-password")
        )

        with self.assertRaises(InvalidOwnerPasswordError):
            auth_service.hash_password("short")

    # ------------------------------------------------------------------
    # 3. Login and /api/auth/me
    # ------------------------------------------------------------------

    async def test_successful_login_sets_session_and_csrf(self):

        await self._setup_owner()

        response = await self._login()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        self.assertEqual(body["owner"]["username"], OWNER_USERNAME)
        self.assertNotIn("password", body)
        self.assertNotIn("password_hash", body)
        self.assertTrue(body["csrf_token"])

        raw_token = self._session_token()
        self.assertIsNotNone(raw_token)
        self.assertNotIn(OWNER_PASSWORD, response.text)

        stored = await self._get_auth_session(raw_token)
        self.assertIsNotNone(stored)

        # Only the hash of the raw session token is persisted.
        self.assertNotEqual(stored.token_hash, raw_token)
        self.assertEqual(
            stored.token_hash,
            hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(stored.csrf_token_hash, body["csrf_token"])
        self.assertEqual(
            stored.csrf_token_hash,
            hashlib.sha256(body["csrf_token"].encode("utf-8")).hexdigest(),
        )

    async def test_invalid_login_is_rejected(self):

        await self._setup_owner()

        wrong_password = await self._login(password="definitely-not-right")
        self.assertEqual(wrong_password.status_code, 401, wrong_password.text)

        unknown_user = await self._login(username="not_the_owner")
        self.assertEqual(unknown_user.status_code, 401, unknown_user.text)

        self.assertIsNone(self._session_token())

    async def test_me_returns_authenticated_owner(self):

        owner_id, _csrf = await self._create_owner_and_login()

        response = await self.client.get("/api/auth/me")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        self.assertEqual(body["owner"]["id"], owner_id)
        self.assertEqual(body["owner"]["username"], OWNER_USERNAME)
        self.assertNotIn("password", body)
        self.assertIn("session_expires_at", body)

    async def test_me_without_session_returns_401(self):

        response = await self.client.get("/api/auth/me")

        self.assertEqual(response.status_code, 401, response.text)

    async def test_invalid_session_token_returns_401(self):

        response = await self.client.get(
            "/api/auth/me",
            headers={"Cookie": f"{settings.AUTH_SESSION_COOKIE_NAME}=garbage"},
        )

        self.assertEqual(response.status_code, 401, response.text)

    async def test_expired_session_is_rejected(self):

        _owner_id, _csrf = await self._create_owner_and_login()
        raw_token = self._session_token()

        async with self.SessionLocal() as session:
            stored = (
                await session.execute(
                    select(AuthSession).where(
                        AuthSession.token_hash == hash_token(raw_token)
                    )
                )
            ).scalar_one()
            stored.created_at = datetime.now(timezone.utc) - timedelta(days=2)
            stored.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()

        response = await self.client.get("/api/auth/me")

        self.assertEqual(response.status_code, 401, response.text)

    async def test_revoked_session_is_rejected(self):

        _owner_id, csrf = await self._create_owner_and_login()
        raw_token = self._session_token()

        logout = await self.client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(logout.status_code, 204, logout.text)

        stored = await self._get_auth_session(raw_token)
        self.assertIsNotNone(stored.revoked_at)

        # The raw cookie must no longer authenticate, even if replayed.
        replay = await self.client.get(
            "/api/auth/me",
            headers={
                "Cookie": f"{settings.AUTH_SESSION_COOKIE_NAME}={raw_token}"
            },
        )
        self.assertEqual(replay.status_code, 401, replay.text)

    async def test_logout_clears_cookies(self):

        _owner_id, csrf = await self._create_owner_and_login()

        logout = await self.client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(logout.status_code, 204, logout.text)
        self.assertIsNone(self._session_token())

        me = await self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 401, me.text)

    async def test_session_lifetime_is_configurable(self):

        await self._setup_owner()

        with patch.object(settings, "AUTH_SESSION_LIFETIME_HOURS", 2):
            response = await self._login()

        self.assertEqual(response.status_code, 200, response.text)

        raw_token = self._session_token()
        stored = await self._get_auth_session(raw_token)

        created = stored.created_at
        expires = stored.expires_at

        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        self.assertAlmostEqual(
            (expires - created).total_seconds(),
            2 * 3600,
            delta=5,
        )

    async def test_last_used_is_recorded_and_throttled(self):

        _owner_id, _csrf = await self._create_owner_and_login()
        raw_token = self._session_token()

        me = await self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)

        first = await self._get_auth_session(raw_token)
        self.assertIsNotNone(first.last_used_at)
        first_last_used = first.last_used_at

        # With a 300s throttle a second immediate request must not rewrite it.
        me = await self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)

        second = await self._get_auth_session(raw_token)

        self.assertEqual(second.last_used_at, first_last_used)

    # ------------------------------------------------------------------
    # 4. Cookie flags
    # ------------------------------------------------------------------

    async def test_session_cookie_flags(self):

        await self._setup_owner()
        response = await self._login()

        self.assertEqual(response.status_code, 200, response.text)

        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(
            c for c in cookies
            if c.startswith(settings.AUTH_SESSION_COOKIE_NAME + "=")
        )
        csrf_cookie = next(
            c for c in cookies
            if c.startswith(settings.AUTH_CSRF_COOKIE_NAME + "=")
        )

        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("Secure", session_cookie)
        self.assertIn("SameSite=lax", session_cookie)
        self.assertIn(f"Path={settings.AUTH_COOKIE_PATH}", session_cookie)
        self.assertNotIn("Domain=", session_cookie)

        # The CSRF cookie must be readable by frontend JavaScript.
        self.assertNotIn("HttpOnly", csrf_cookie)
        self.assertIn("Secure", csrf_cookie)
        self.assertIn("SameSite=lax", csrf_cookie)
        self.assertIn(
            f"Path={settings.AUTH_CSRF_COOKIE_PATH}",
            csrf_cookie,
        )

    # ------------------------------------------------------------------
    # 5. CSRF
    # ------------------------------------------------------------------

    async def test_state_changing_request_without_csrf_is_rejected(self):

        await self._create_owner_and_login()

        response = await self.client.post(
            "/api/conversations/",
            json={"title": "No CSRF"},
        )

        self.assertEqual(response.status_code, 403, response.text)

    async def test_state_changing_request_with_wrong_csrf_is_rejected(self):

        await self._create_owner_and_login()

        response = await self.client.post(
            "/api/conversations/",
            json={"title": "Wrong CSRF"},
            headers={"X-CSRF-Token": "not-the-right-token"},
        )

        self.assertEqual(response.status_code, 403, response.text)

    async def test_state_changing_request_with_valid_csrf_succeeds(self):

        _owner_id, csrf = await self._create_owner_and_login()

        response = await self.client.post(
            "/api/conversations/",
            json={"title": "Valid CSRF"},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["title"], "Valid CSRF")

    async def test_state_changing_task_request_without_csrf_is_rejected(self):

        await self._create_owner_and_login()

        response = await self.client.post(
            "/api/tasks/",
            json={"title": "No CSRF task"},
        )

        self.assertEqual(response.status_code, 403, response.text)

    async def test_safe_methods_do_not_require_csrf(self):

        await self._create_owner_and_login()

        response = await self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200, response.text)

    # ------------------------------------------------------------------
    # 6. Protected endpoints
    # ------------------------------------------------------------------

    async def test_protected_endpoints_require_authentication(self):

        endpoints = [
            ("GET", "/api/conversations/"),
            ("GET", "/api/memories/"),
            ("GET", "/api/preferences/"),
            ("GET", "/api/tasks/"),
            ("GET", "/api/task-actions/pending"),
        ]

        for method, path in endpoints:
            response = await self.client.request(method, path)
            self.assertEqual(
                response.status_code,
                401,
                f"{method} {path} returned {response.status_code}",
            )

        chat = await self.client.post(
            "/api/chat/",
            json={"message": "hello"},
        )
        self.assertEqual(chat.status_code, 401, chat.text)

    async def test_protected_endpoints_work_with_authentication(self):

        _owner_id, csrf = await self._create_owner_and_login()

        endpoints = [
            "/api/conversations/",
            "/api/memories/",
            "/api/preferences/",
            "/api/tasks/",
            "/api/task-actions/pending",
        ]

        for path in endpoints:
            response = await self.client.get(path)
            self.assertEqual(
                response.status_code,
                200,
                f"GET {path} returned {response.status_code}: {response.text}",
            )

        chat_result = {
            "provider": "groq",
            "model": "test-model",
            "response": "authenticated hello",
            "conversation_id": 1,
        }

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(return_value=chat_result),
        ):
            chat = await self.client.post(
                "/api/chat/",
                json={"message": "hello"},
                headers={"X-CSRF-Token": csrf},
            )

        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertEqual(chat.json()["response"], "authenticated hello")

    async def test_user_id_spoofing_is_ignored(self):

        owner_id, csrf = await self._create_owner_and_login()

        async with self.SessionLocal() as session:
            other = User(username="other_user")
            session.add(other)
            await session.commit()
            await session.refresh(other)
            other_id = other.id

        async with self.SessionLocal() as session:
            await conversation_service.create_conversation(
                session=session,
                user_id=other_id,
                title="Other user's private conversation",
            )

        # Extra body fields are rejected rather than trusted.
        body_spoof = await self.client.post(
            "/api/conversations/",
            json={"title": "Spoofed", "user_id": other_id},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(body_spoof.status_code, 422, body_spoof.text)

        # Query-string and header spoofing are ignored.
        created = await self.client.post(
            f"/api/conversations/?user_id={other_id}",
            json={"title": "Owner conversation"},
            headers={
                "X-CSRF-Token": csrf,
                "X-User-Id": str(other_id),
            },
        )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["user_id"], owner_id)
        self.assertNotEqual(created.json()["user_id"], other_id)

        listing = await self.client.get(
            f"/api/conversations/?user_id={other_id}"
        )

        self.assertEqual(listing.status_code, 200, listing.text)
        titles = [item["title"] for item in listing.json()]

        self.assertIn("Owner conversation", titles)
        self.assertNotIn("Other user's private conversation", titles)

    # ------------------------------------------------------------------
    # 7. Access to existing domain data
    # ------------------------------------------------------------------

    async def test_owner_can_access_existing_conversations_memories_preferences_tasks(
        self
    ):

        owner_id, csrf = await self._create_owner_and_login()
        headers = {"X-CSRF-Token": csrf}

        conversation = await self.client.post(
            "/api/conversations/",
            json={"title": "Phase 8 conversation"},
            headers=headers,
        )
        self.assertEqual(conversation.status_code, 201, conversation.text)
        conversation_id = conversation.json()["id"]

        fetched_conversation = await self.client.get(
            f"/api/conversations/{conversation_id}"
        )
        self.assertEqual(
            fetched_conversation.status_code,
            200,
            fetched_conversation.text,
        )

        memory = await self.client.post(
            "/api/memories/",
            json={"content": "Owner memory", "category": "general"},
            headers=headers,
        )
        self.assertEqual(memory.status_code, 201, memory.text)
        memory_id = memory.json()["id"]

        fetched_memory = await self.client.get(f"/api/memories/{memory_id}")
        self.assertEqual(fetched_memory.status_code, 200, fetched_memory.text)
        self.assertEqual(fetched_memory.json()["user_id"], owner_id)

        preference = await self.client.post(
            "/api/preferences/",
            json={
                "key": "favourite_language",
                "value": "Python",
                "category": "programming",
            },
            headers=headers,
        )
        self.assertEqual(preference.status_code, 201, preference.text)
        preference_id = preference.json()["id"]

        fetched_preference = await self.client.get(
            f"/api/preferences/{preference_id}"
        )
        self.assertEqual(
            fetched_preference.status_code,
            200,
            fetched_preference.text,
        )

        task = await self.client.post(
            "/api/tasks/",
            json={"title": "Phase 8 task"},
            headers=headers,
        )
        self.assertEqual(task.status_code, 201, task.text)
        task_id = task.json()["id"]

        fetched_task = await self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(fetched_task.status_code, 200, fetched_task.text)
        self.assertEqual(fetched_task.json()["user_id"], owner_id)

    # ------------------------------------------------------------------
    # 8. Pending-action authorization
    # ------------------------------------------------------------------

    async def test_pending_actions_are_scoped_to_the_owner(self):

        owner_id, csrf = await self._create_owner_and_login()
        headers = {"X-CSRF-Token": csrf}

        async with self.SessionLocal() as session:
            other = User(username="pending_other")
            session.add(other)
            await session.commit()
            await session.refresh(other)

            other_conversation = await conversation_service.create_conversation(
                session=session,
                user_id=other.id,
                title="Other pending conversation",
            )

            other_action = await pending_action_service.create_pending(
                session=session,
                user_id=other.id,
                conversation_id=other_conversation.id,
                action_type=PendingActionType.DELETE,
                payload={},
                summary="Delete another user's task",
            )

        listing = await self.client.get("/api/task-actions/pending")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json(), [])

        cross_owner = await self.client.post(
            f"/api/task-actions/{other_action.id}/reject",
            headers=headers,
        )
        self.assertEqual(cross_owner.status_code, 404, cross_owner.text)

        owner_conversation = await self.client.post(
            "/api/conversations/",
            json={"title": "Owner pending conversation"},
            headers=headers,
        )
        self.assertEqual(owner_conversation.status_code, 201)

        async with self.SessionLocal() as session:
            owner_action = await pending_action_service.create_pending(
                session=session,
                user_id=owner_id,
                conversation_id=owner_conversation.json()["id"],
                action_type=PendingActionType.DELETE,
                payload={},
                summary="Delete the owner's task",
            )
            owner_action_id = owner_action.id

        owner_listing = await self.client.get("/api/task-actions/pending")
        self.assertEqual(owner_listing.status_code, 200, owner_listing.text)
        self.assertEqual(
            [item["id"] for item in owner_listing.json()],
            [owner_action_id],
        )

        rejected = await self.client.post(
            f"/api/task-actions/{owner_action_id}/reject",
            headers=headers,
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["status"], "rejected")

        async with self.SessionLocal() as session:
            stored = await pending_action_service.get_action(
                session=session,
                user_id=owner_id,
                action_id=owner_action_id,
            )
            self.assertEqual(stored.status, PendingActionStatus.REJECTED)

    # ------------------------------------------------------------------
    # 9. Existing feature regression
    # ------------------------------------------------------------------

    async def test_public_health_endpoints_remain_open(self):

        root = await self.client.get("/")
        health = await self.client.get("/health")

        self.assertEqual(root.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "Healthy")

    async def test_untrusted_host_header_is_rejected(self):

        response = await self.client.get(
            "/health",
            headers={"Host": "attacker.example"},
        )

        self.assertEqual(response.status_code, 400, response.text)

    async def test_authenticated_chat_preserves_response_contract(self):

        _owner_id, csrf = await self._create_owner_and_login()

        result = {
            "provider": "ollama",
            "model": "qwen3:8b",
            "response": "regression answer",
            "conversation_id": 7,
        }

        with patch.object(
            chat_service,
            "get_response",
            new=AsyncMock(return_value=result),
        ) as get_response:
            response = await self.client.post(
                "/api/chat/",
                json={"message": "Hello ASTA", "private": True},
                headers={"X-CSRF-Token": csrf},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            set(response.json().keys()),
            {"provider", "model", "response", "conversation_id"},
        )
        self.assertEqual(response.json()["response"], "regression answer")

        kwargs = get_response.await_args.kwargs
        self.assertTrue(kwargs["private"])
        self.assertIsInstance(kwargs["user_id"], int)


if __name__ == "__main__":
    unittest.main()
