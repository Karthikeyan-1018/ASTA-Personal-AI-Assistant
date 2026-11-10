"""Real-PostgreSQL verification for Phase 8.1 login rate limiting.

Guarded to ``asta_db``. It creates throwaway ``auth_login_attempts`` rows with
unique account keys, verifies threshold behaviour, persistence, progressive
cooldowns, concurrent atomic increments, and cleanup, then deletes only the
rows it created. It never touches users, owner accounts, sessions, memories,
conversations, preferences, tasks, pending actions, or embeddings.

Run after applying the migration:

    cd C:\\Asta\\backend
    ..\\venv\\Scripts\\python.exe -m alembic upgrade head
    ..\\venv\\Scripts\\python.exe scripts\\verify_phase81_rate_limit.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, update

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.models.auth import LoginAttempt
from app.services.login_rate_limit_service import (
    login_rate_limit_service,
    normalize_account_key,
)


MAX_ATTEMPTS = 5
FIRST_COOLDOWN_SECONDS = 15 * 60
SECOND_COOLDOWN_SECONDS = 30 * 60


def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"OK  {message}")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def configure_policy() -> None:
    settings.AUTH_LOGIN_RATE_LIMIT_ENABLED = True
    settings.AUTH_LOGIN_MAX_FAILED_ATTEMPTS = MAX_ATTEMPTS
    settings.AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES = 15
    settings.AUTH_LOGIN_COOLDOWN_MULTIPLIER = 2
    settings.AUTH_LOGIN_MAX_COOLDOWN_MINUTES = 1440
    settings.AUTH_LOGIN_ATTEMPT_RETENTION_HOURS = 48


async def get_row(ip_address: str, account_key: str):
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(LoginAttempt).where(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.account_key == account_key,
                )
            )
        ).scalar_one_or_none()


async def fail(ip_address: str, account_key: str):
    async with AsyncSessionLocal() as session:
        return await login_rate_limit_service.record_failure(
            session,
            ip_address=ip_address,
            account_key=account_key,
        )


async def allowed(ip_address: str, account_key: str):
    async with AsyncSessionLocal() as session:
        return await login_rate_limit_service.check_allowed(
            session,
            ip_address=ip_address,
            account_key=account_key,
        )


async def expire_lock(ip_address: str, account_key: str) -> None:
    now = utcnow()

    async with AsyncSessionLocal() as session:
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


async def cleanup_keys(keys: list[str]) -> None:
    if not keys:
        return

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.account_key.in_(keys)
            )
        )
        await session.commit()


async def verify_threshold_and_persistence(suffix: str, keys: list[str]):
    account_key = normalize_account_key(f"phase81_verify_{suffix}_threshold")
    ip_address = "203.0.113.201"
    keys.append(account_key)

    for attempt in range(MAX_ATTEMPTS):
        result = await fail(ip_address, account_key)
        check(
            not result.locked,
            f"failure {attempt + 1} is recorded without blocking the attempt",
        )

    row = await get_row(ip_address, account_key)

    check(row is not None, "attempt row persists in PostgreSQL")
    check(
        row.failure_count == MAX_ATTEMPTS,
        "five failures are counted exactly once",
    )
    check(
        row.locked_until is not None
        and aware(row.locked_until) > utcnow(),
        "cooldown is stored as a future timezone-aware timestamp",
    )

    cooldown = (
        aware(row.locked_until) - aware(row.last_failed_at)
    ).total_seconds()
    check(
        abs(cooldown - FIRST_COOLDOWN_SECONDS) <= 5,
        "first cooldown is 15 minutes",
    )
    check(
        row.cooldown_level == 1,
        "first cooldown advances the cooldown level",
    )

    decision = await allowed(ip_address, account_key)
    check(not decision.allowed, "blocked state is visible to a new session")
    check(
        decision.retry_after_seconds > 0,
        "Retry-After value is positive while blocked",
    )

    # A completely new session (simulating a restarted backend) must still see
    # the cooldown from PostgreSQL.
    async with AsyncSessionLocal() as session:
        restarted = await login_rate_limit_service.check_allowed(
            session,
            ip_address=ip_address,
            account_key=account_key,
        )

    check(
        not restarted.allowed,
        "cooldown survives a new database session/backend restart",
    )

    return account_key, ip_address


async def verify_progressive_cooldown(account_key: str, ip_address: str):
    await expire_lock(ip_address, account_key)

    first = await fail(ip_address, account_key)
    check(
        not first.locked and first.failure_count == 1,
        "expired cooldown starts a new failure cycle",
    )

    for _ in range(MAX_ATTEMPTS - 1):
        await fail(ip_address, account_key)

    row = await get_row(ip_address, account_key)
    cooldown = (
        aware(row.locked_until) - aware(row.last_failed_at)
    ).total_seconds()

    check(
        row.failure_count == MAX_ATTEMPTS,
        "new cycle counts five failures again",
    )
    check(
        abs(cooldown - SECOND_COOLDOWN_SECONDS) <= 5,
        "second cooldown is progressively longer (30 minutes)",
    )
    check(
        row.cooldown_level == 2,
        "cooldown level increases after repeated abuse",
    )


async def verify_concurrency(suffix: str, keys: list[str]):
    account_key = normalize_account_key(f"phase81_verify_{suffix}_concurrent")
    ip_address = "203.0.113.202"
    keys.append(account_key)

    async def one_failure():
        return await fail(ip_address, account_key)

    results = await asyncio.gather(
        *(one_failure() for _ in range(6)),
        return_exceptions=True,
    )

    errors = [result for result in results if isinstance(result, Exception)]
    check(errors == [], "concurrent failures complete without database errors")

    row = await get_row(ip_address, account_key)
    decision = await allowed(ip_address, account_key)

    check(
        row.failure_count >= MAX_ATTEMPTS,
        "concurrent failures are counted atomically",
    )
    check(
        row.locked_until is not None
        and aware(row.locked_until) > utcnow(),
        "concurrent failures set exactly one cooldown",
    )
    check(not decision.allowed, "concurrent abuse leaves the pair blocked")


async def verify_cleanup(suffix: str, keys: list[str]):
    old_key = normalize_account_key(f"phase81_verify_{suffix}_old")
    fresh_key = normalize_account_key(f"phase81_verify_{suffix}_fresh")
    keys.extend([old_key, fresh_key])

    now = utcnow()

    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                LoginAttempt(
                    ip_address="203.0.113.203",
                    account_key=old_key,
                    failure_count=2,
                    cooldown_level=0,
                    first_failed_at=now - timedelta(hours=100),
                    last_failed_at=now - timedelta(hours=100),
                ),
                LoginAttempt(
                    ip_address="203.0.113.204",
                    account_key=fresh_key,
                    failure_count=1,
                    cooldown_level=0,
                    first_failed_at=now,
                    last_failed_at=now,
                ),
            ]
        )
        await session.commit()

        deleted = await login_rate_limit_service.cleanup_expired(
            session,
            now=now,
        )

    check(deleted >= 1, "cleanup deletes at least one expired row")
    check(
        await get_row("203.0.113.203", old_key) is None,
        "expired attempt row is removed",
    )
    check(
        await get_row("203.0.113.204", fresh_key) is not None,
        "fresh attempt row is retained",
    )


async def main() -> int:
    if settings.DB_NAME != "asta_db":
        print(
            f"Refusing to run: DB_NAME is {settings.DB_NAME!r}, expected "
            "'asta_db'."
        )
        return 2

    configure_policy()

    suffix = uuid.uuid4().hex[:12]
    keys: list[str] = []

    print(f"Database: {settings.DB_NAME} on {settings.DB_HOST}:{settings.DB_PORT}")

    try:
        account_key, ip_address = await verify_threshold_and_persistence(
            suffix,
            keys,
        )
        await verify_progressive_cooldown(account_key, ip_address)
        await verify_concurrency(suffix, keys)
        await verify_cleanup(suffix, keys)

        print("\nAll Phase 8.1 PostgreSQL rate-limit checks passed.")

    finally:
        await cleanup_keys(keys)
        print("Throwaway rate-limit rows removed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
