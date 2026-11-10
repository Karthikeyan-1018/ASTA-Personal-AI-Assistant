"""Phase 8.1 login-rate-limiting tests.

The HTTP tests use an isolated in-memory SQLite database and the real FastAPI
application. Concurrency and restart-persistence tests use a temporary
file-backed SQLite database so independent sessions can contend for writes in
the same way separate workers would. PostgreSQL-specific behaviour is verified
separately by ``scripts/verify_phase81_rate_limit.py``.
"""

import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.core.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.auth import LoginAttempt
from app.services.login_rate_limit_service import (
    login_rate_limit_service,
    normalize_account_key,
    resolve_client_ip,
    setup_account_key,
)


OWNER_USERNAME = "rate_limit_owner"
OWNER_PASSWORD = "correct-horse-battery-staple"
SETUP_TOKEN = "phase81-test-setup-token"
CLIENT_IP = "203.0.113.10"
OTHER_IP = "203.0.113.20"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RateLimitHTTPTestCase(unittest.IsolatedAsyncioTestCase):
    """Shared in-memory database, real app, and patched rate-limit settings."""

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
            patch.object(settings, "AUTH_LOGIN_RATE_LIMIT_ENABLED", True)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_MAX_FAILED_ATTEMPTS", 5)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES", 15)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_COOLDOWN_MULTIPLIER", 2)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_MAX_COOLDOWN_MINUTES", 1440)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_ATTEMPT_RETENTION_HOURS", 48)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_TRUSTED_PROXIES", "")
        )

        self._clients: list[AsyncClient] = []
        self.client = self._make_client(CLIENT_IP)

    async def asyncTearDown(self):

        for client in self._clients:
            await client.aclose()

        self._stack.close()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_client(self, ip_address: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(
                app=app,
                client=(ip_address, 12345),
            ),
            base_url="https://test",
        )
        self._clients.append(client)
        return client

    async def _setup_owner(self, client: AsyncClient | None = None):
        client = client or self.client

        return await client.post(
            "/api/auth/setup",
            json={
                "username": OWNER_USERNAME,
                "password": OWNER_PASSWORD,
            },
            headers={"X-Setup-Token": SETUP_TOKEN},
        )

    async def _login(
        self,
        *,
        username: str = OWNER_USERNAME,
        password: str = OWNER_PASSWORD,
        client: AsyncClient | None = None,
        headers: dict | None = None,
    ):
        client = client or self.client

        return await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=headers or {},
        )

    async def _attempt_rows(self) -> list[LoginAttempt]:
        async with self.SessionLocal() as session:
            result = await session.execute(select(LoginAttempt))
            return list(result.scalars().all())

    async def _attempt_for(self, ip_address: str, account_key: str):
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(LoginAttempt).where(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.account_key == account_key,
                )
            )
            return result.scalar_one_or_none()

    async def _expire_lock(self, ip_address: str, account_key: str):
        now = _now()

        async with self.SessionLocal() as session:
            await session.execute(
                update(LoginAttempt)
                .where(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.account_key == account_key,
                )
                .values(
                    locked_until=now - timedelta(seconds=1),
                    last_failed_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Threshold, HTTP 429, and generic errors
    # ------------------------------------------------------------------

    async def test_first_five_failures_are_401_and_sixth_is_429(self):

        setup = await self._setup_owner()
        self.assertEqual(setup.status_code, 201, setup.text)

        for attempt in range(5):
            response = await self._login(password="wrong-password")
            self.assertEqual(
                response.status_code,
                401,
                f"attempt {attempt + 1}: {response.text}",
            )
            self.assertEqual(
                response.json()["detail"],
                "Invalid username or password.",
            )

        account_key = normalize_account_key(OWNER_USERNAME)
        row = await self._attempt_for(CLIENT_IP, account_key)

        self.assertIsNotNone(row)
        self.assertEqual(row.failure_count, 5)
        self.assertIsNotNone(row.locked_until)

        cooldown = (
            _aware(row.locked_until) - _aware(row.last_failed_at)
        ).total_seconds()
        self.assertAlmostEqual(cooldown, 15 * 60, delta=5)

        blocked = await self._login(password="wrong-password")
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("Retry-After", blocked.headers)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)
        self.assertEqual(
            blocked.json()["detail"],
            "Too many failed authentication attempts. Try again later.",
        )

        # Even the correct password is blocked while the cooldown is active.
        correct_but_blocked = await self._login()
        self.assertEqual(correct_but_blocked.status_code, 429)
        self.assertIsNone(
            self.client.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
        )

    async def test_unknown_account_and_wrong_password_are_indistinguishable(self):

        setup = await self._setup_owner()
        self.assertEqual(setup.status_code, 201, setup.text)

        wrong_password = await self._login(password="wrong-password")
        unknown_account = await self._login(
            username="does-not-exist",
            password="wrong-password",
        )

        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_account.status_code, 401)
        self.assertEqual(
            wrong_password.json(),
            unknown_account.json(),
        )
        self.assertNotIn(OWNER_PASSWORD, wrong_password.text)
        self.assertNotIn("owner", wrong_password.text.lower())

    async def test_unknown_accounts_are_also_rate_limited(self):

        setup = await self._setup_owner()
        self.assertEqual(setup.status_code, 201, setup.text)

        username = "no-such-account"
        account_key = normalize_account_key(username)

        for _ in range(5):
            response = await self._login(
                username=username,
                password="wrong-password",
            )
            self.assertEqual(response.status_code, 401)

        row = await self._attempt_for(CLIENT_IP, account_key)
        self.assertEqual(row.failure_count, 5)
        self.assertIsNotNone(row.locked_until)

        blocked = await self._login(
            username=username,
            password="wrong-password",
        )
        self.assertEqual(blocked.status_code, 429)

    # ------------------------------------------------------------------
    # Cooldown behaviour and reset
    # ------------------------------------------------------------------

    async def test_cooldown_expiry_allows_login_and_clears_state(self):

        await self._setup_owner()

        for _ in range(5):
            await self._login(password="wrong-password")

        account_key = normalize_account_key(OWNER_USERNAME)
        self.assertIsNotNone(await self._attempt_for(CLIENT_IP, account_key))

        blocked = await self._login(password="wrong-password")
        self.assertEqual(blocked.status_code, 429)

        await self._expire_lock(CLIENT_IP, account_key)

        allowed = await self._login()
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertIsNone(await self._attempt_for(CLIENT_IP, account_key))

    async def test_successful_login_resets_failure_state(self):

        await self._setup_owner()

        for _ in range(4):
            response = await self._login(password="wrong-password")
            self.assertEqual(response.status_code, 401)

        account_key = normalize_account_key(OWNER_USERNAME)
        row = await self._attempt_for(CLIENT_IP, account_key)
        self.assertEqual(row.failure_count, 4)

        success = await self._login()
        self.assertEqual(success.status_code, 200, success.text)
        self.assertIsNone(await self._attempt_for(CLIENT_IP, account_key))

        # The counter starts again from zero rather than remaining at four.
        for _ in range(4):
            response = await self._login(password="wrong-password")
            self.assertEqual(response.status_code, 401)

        row = await self._attempt_for(CLIENT_IP, account_key)
        self.assertEqual(row.failure_count, 4)

    async def test_repeated_abuse_escalates_the_cooldown(self):

        await self._setup_owner()
        account_key = normalize_account_key(OWNER_USERNAME)

        for _ in range(5):
            await self._login(password="wrong-password")

        first = await self._attempt_for(CLIENT_IP, account_key)
        first_cooldown = (
            _aware(first.locked_until) - _aware(first.last_failed_at)
        ).total_seconds()
        self.assertAlmostEqual(first_cooldown, 15 * 60, delta=5)

        await self._expire_lock(CLIENT_IP, account_key)

        # The first post-cooldown failure starts a new count but keeps the
        # cooldown level, so the next cooldown is longer.
        for _ in range(5):
            response = await self._login(password="wrong-password")
            self.assertEqual(response.status_code, 401, response.text)

        second = await self._attempt_for(CLIENT_IP, account_key)
        second_cooldown = (
            _aware(second.locked_until) - _aware(second.last_failed_at)
        ).total_seconds()
        self.assertAlmostEqual(second_cooldown, 30 * 60, delta=5)
        self.assertGreater(second_cooldown, first_cooldown)

        blocked = await self._login(password="wrong-password")
        self.assertEqual(blocked.status_code, 429)

    async def test_cooldown_is_capped(self):

        with patch.object(
            settings,
            "AUTH_LOGIN_MAX_COOLDOWN_MINUTES",
            60,
        ):
            self.assertEqual(
                login_rate_limit_service.cooldown_seconds(0),
                15 * 60,
            )
            self.assertEqual(
                login_rate_limit_service.cooldown_seconds(2),
                60 * 60,
            )
            self.assertEqual(
                login_rate_limit_service.cooldown_seconds(50),
                60 * 60,
            )

    # ------------------------------------------------------------------
    # IP / proxy handling
    # ------------------------------------------------------------------

    async def test_different_source_ips_have_independent_counters(self):

        await self._setup_owner()
        other_client = self._make_client(OTHER_IP)

        for _ in range(5):
            response = await self._login(password="wrong-password")
            self.assertEqual(response.status_code, 401)

        blocked = await self._login(password="wrong-password")
        self.assertEqual(blocked.status_code, 429)

        # The other source IP is unaffected.
        other_failure = await self._login(
            password="wrong-password",
            client=other_client,
        )
        self.assertEqual(other_failure.status_code, 401, other_failure.text)

        other_login = await self._login(client=other_client)
        self.assertEqual(other_login.status_code, 200, other_login.text)

        own_row = await self._attempt_for(
            CLIENT_IP,
            normalize_account_key(OWNER_USERNAME),
        )
        other_row = await self._attempt_for(
            OTHER_IP,
            normalize_account_key(OWNER_USERNAME),
        )

        self.assertIsNotNone(own_row)
        self.assertIsNone(other_row)

    async def test_spoofed_forwarded_for_is_ignored_without_trusted_proxy(self):

        await self._setup_owner()

        forged_headers = {"X-Forwarded-For": "198.51.100.99"}

        for _ in range(5):
            response = await self._login(
                password="wrong-password",
                headers=forged_headers,
            )
            self.assertEqual(response.status_code, 401)

        # The counter is keyed by the real peer, not the forged header.
        row = await self._attempt_for(
            CLIENT_IP,
            normalize_account_key(OWNER_USERNAME),
        )
        self.assertIsNotNone(row)

        # Changing the forged header does not bypass the cooldown.
        blocked = await self._login(
            password="wrong-password",
            headers={"X-Forwarded-For": "198.51.100.100"},
        )
        self.assertEqual(blocked.status_code, 429)

    async def test_forwarded_for_is_used_only_from_a_trusted_proxy(self):

        with patch.object(settings, "AUTH_TRUSTED_PROXIES", CLIENT_IP):
            await self._setup_owner()

            response = await self._login(
                password="wrong-password",
                headers={"X-Forwarded-For": "198.51.100.7"},
            )
            self.assertEqual(response.status_code, 401, response.text)

            forwarded_row = await self._attempt_for(
                "198.51.100.7",
                normalize_account_key(OWNER_USERNAME),
            )
            self.assertIsNotNone(forwarded_row)

            peer_row = await self._attempt_for(
                CLIENT_IP,
                normalize_account_key(OWNER_USERNAME),
            )
            self.assertIsNone(peer_row)

    async def test_malformed_forwarded_for_falls_back_to_peer(self):

        with patch.object(settings, "AUTH_TRUSTED_PROXIES", CLIENT_IP):
            await self._setup_owner()

            response = await self._login(
                password="wrong-password",
                headers={"X-Forwarded-For": "not-an-ip"},
            )
            self.assertEqual(response.status_code, 401)

            row = await self._attempt_for(
                CLIENT_IP,
                normalize_account_key(OWNER_USERNAME),
            )
            self.assertIsNotNone(row)

    def test_resolve_client_ip_ignores_forwarded_header_by_default(self):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "client": (CLIENT_IP, 12345),
            "headers": [(b"x-forwarded-for", b"198.51.100.1")],
            "method": "POST",
            "path": "/api/auth/login",
            "query_string": b"",
            "server": ("test", 443),
            "scheme": "https",
        }

        self.assertEqual(resolve_client_ip(Request(scope)), CLIENT_IP)

    # ------------------------------------------------------------------
    # Setup endpoint and configuration
    # ------------------------------------------------------------------

    async def test_setup_failures_are_rate_limited(self):

        for _ in range(5):
            response = await self.client.post(
                "/api/auth/setup",
                json={
                    "username": OWNER_USERNAME,
                    "password": OWNER_PASSWORD,
                },
                headers={"X-Setup-Token": "wrong-token"},
            )
            self.assertEqual(response.status_code, 403)

        account_key = setup_account_key(OWNER_USERNAME)
        row = await self._attempt_for(CLIENT_IP, account_key)
        self.assertIsNotNone(row)
        self.assertEqual(row.failure_count, 5)

        blocked = await self.client.post(
            "/api/auth/setup",
            json={
                "username": OWNER_USERNAME,
                "password": OWNER_PASSWORD,
            },
            headers={"X-Setup-Token": SETUP_TOKEN},
        )
        self.assertEqual(blocked.status_code, 429, blocked.text)

        await self._expire_lock(CLIENT_IP, account_key)

        created = await self._setup_owner()
        self.assertEqual(created.status_code, 201, created.text)
        self.assertIsNone(await self._attempt_for(CLIENT_IP, account_key))

    async def test_disabled_rate_limiting_never_blocks(self):

        with patch.object(settings, "AUTH_LOGIN_RATE_LIMIT_ENABLED", False):
            await self._setup_owner()

            for _ in range(10):
                response = await self._login(password="wrong-password")
                self.assertEqual(response.status_code, 401)

            success = await self._login()
            self.assertEqual(success.status_code, 200, success.text)

        self.assertEqual(await self._attempt_rows(), [])

    async def test_attempt_table_never_stores_credentials(self):

        await self._setup_owner()
        await self._login(password="wrong-password")

        rows = await self._attempt_rows()
        self.assertEqual(len(rows), 1)

        columns = set(LoginAttempt.__table__.columns.keys())
        self.assertEqual(
            columns,
            {
                "id",
                "ip_address",
                "account_key",
                "failure_count",
                "cooldown_level",
                "first_failed_at",
                "last_failed_at",
                "locked_until",
                "created_at",
                "updated_at",
            },
        )

        for column in ("password", "password_hash", "token", "token_hash"):
            self.assertNotIn(column, columns)

        stored_values = " ".join(
            str(getattr(rows[0], column))
            for column in columns
        )
        self.assertNotIn(OWNER_PASSWORD, stored_values)
        self.assertNotIn("wrong-password", stored_values)

    async def test_cleanup_removes_expired_attempt_rows(self):

        await self._setup_owner()

        now = _now()
        old = LoginAttempt(
            ip_address="198.51.100.1",
            account_key="old-account",
            failure_count=2,
            cooldown_level=0,
            first_failed_at=now - timedelta(hours=100),
            last_failed_at=now - timedelta(hours=100),
        )
        fresh = LoginAttempt(
            ip_address="198.51.100.2",
            account_key="fresh-account",
            failure_count=1,
            cooldown_level=0,
            first_failed_at=now,
            last_failed_at=now,
        )

        async with self.SessionLocal() as session:
            session.add_all([old, fresh])
            await session.commit()

            deleted = await login_rate_limit_service.cleanup_expired(
                session,
                now=now,
            )

        self.assertEqual(deleted, 1)

        remaining = await self._attempt_rows()
        self.assertEqual(
            [(row.ip_address, row.account_key) for row in remaining],
            [("198.51.100.2", "fresh-account")],
        )


class RateLimitPersistenceTestCase(unittest.IsolatedAsyncioTestCase):
    """Restart persistence and concurrent atomic increments."""

    async def asyncSetUp(self):

        self._tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self._tempdir.name) / "rate-limit.db"
        self.engine = self._create_engine()
        self._stack = ExitStack()
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_RATE_LIMIT_ENABLED", True)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_MAX_FAILED_ATTEMPTS", 5)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES", 15)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_COOLDOWN_MULTIPLIER", 2)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_MAX_COOLDOWN_MINUTES", 1440)
        )
        self._stack.enter_context(
            patch.object(settings, "AUTH_LOGIN_ATTEMPT_RETENTION_HOURS", 48)
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):

        self._stack.close()
        await self.engine.dispose()
        self._tempdir.cleanup()

    def _create_engine(self):
        return create_async_engine(
            f"sqlite+aiosqlite:///{self.database_path}",
            connect_args={"timeout": 30},
            poolclass=NullPool,
        )

    async def test_rate_limit_state_persists_across_engine_restart(self):

        account_key = normalize_account_key("restart_owner")

        for _ in range(5):
            async with self.SessionLocal() as session:
                await login_rate_limit_service.record_failure(
                    session,
                    ip_address="192.0.2.10",
                    account_key=account_key,
                )

        await self.engine.dispose()

        # Simulate a backend restart with a brand-new engine and pool.
        self.engine = self._create_engine()
        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async with self.SessionLocal() as session:
            decision = await login_rate_limit_service.check_allowed(
                session,
                ip_address="192.0.2.10",
                account_key=account_key,
            )

        self.assertFalse(decision.allowed)
        self.assertGreater(decision.retry_after_seconds, 0)

    async def test_concurrent_failures_are_counted_atomically(self):

        account_key = normalize_account_key("concurrent_owner")

        async def fail_once():
            async with self.SessionLocal() as session:
                return await login_rate_limit_service.record_failure(
                    session,
                    ip_address="192.0.2.20",
                    account_key=account_key,
                )

        results = await asyncio.gather(
            *(fail_once() for _ in range(6)),
            return_exceptions=True,
        )

        errors = [
            result
            for result in results
            if isinstance(result, Exception)
        ]
        self.assertEqual(errors, [])

        async with self.SessionLocal() as session:
            row = (
                await session.execute(
                    select(LoginAttempt).where(
                        LoginAttempt.ip_address == "192.0.2.20",
                        LoginAttempt.account_key == account_key,
                    )
                )
            ).scalar_one()

            decision = await login_rate_limit_service.check_allowed(
                session,
                ip_address="192.0.2.20",
                account_key=account_key,
            )

        self.assertGreaterEqual(row.failure_count, 5)
        self.assertIsNotNone(row.locked_until)
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
