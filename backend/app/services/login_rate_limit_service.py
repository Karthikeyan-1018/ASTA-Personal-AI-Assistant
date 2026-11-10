"""Database-backed login rate limiting.

State is stored in ``auth_login_attempts`` so a backend restart does not reset
an active cooldown and so every worker shares the same view. Counters are
updated with atomic SQL statements; there is no read-modify-write race between
concurrent failed logins.

The limiter only stores a normalised source IP and a normalised account
identifier. Passwords, password hashes, session tokens, and CSRF tokens are
never written to this table or to logs.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import and_, case, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import LoginAttempt


logger = logging.getLogger(__name__)


MAX_ACCOUNT_KEY_LENGTH = 255
MAX_IP_LENGTH = 64
UNKNOWN_IP = "unknown"
UNKNOWN_ACCOUNT = "unknown"
SETUP_ACCOUNT_PREFIX = "setup:"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def normalize_account_key(value: str | None) -> str:
    """Return a stable, bounded account identifier for rate-limit state.

    Unicode is normalised so visually identical usernames share one counter.
    This is not an authentication decision; the auth service still performs the
    authoritative username comparison.
    """

    if not isinstance(value, str):
        return UNKNOWN_ACCOUNT

    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        character if character.isprintable() else " "
        for character in normalized
    )
    normalized = " ".join(normalized.split()).casefold()

    if not normalized:
        return UNKNOWN_ACCOUNT

    return normalized[:MAX_ACCOUNT_KEY_LENGTH]


def setup_account_key(username: str | None) -> str:
    """Namespace owner-setup counters separately from login counters."""

    return f"{SETUP_ACCOUNT_PREFIX}{normalize_account_key(username)}"[
        :MAX_ACCOUNT_KEY_LENGTH
    ]


def normalize_ip_address(value: str | None) -> str:
    """Return a canonical IP string, or a safe placeholder."""

    if not isinstance(value, str):
        return UNKNOWN_IP

    candidate = value.strip()

    if not candidate or len(candidate) > MAX_IP_LENGTH:
        return UNKNOWN_IP

    try:
        return ipaddress.ip_address(candidate).compressed

    except ValueError:
        # ``request.client.host`` is normally an IP, but a test client or an
        # unusual ASGI server may provide something else. Bound the value so it
        # can never overflow the database column.
        return candidate[:MAX_IP_LENGTH]


def resolve_client_ip(request: Request) -> str:
    """Return the client IP, honouring forwarded headers only from proxies.

    ``X-Forwarded-For`` is ignored unless the direct peer is explicitly listed
    in ``AUTH_TRUSTED_PROXIES``. When it is trusted, the left-most address is
    the original client. A malformed forwarded value falls back to the peer
    address rather than creating attacker-controlled counter keys.
    """

    peer = UNKNOWN_IP

    if request.client is not None and request.client.host:
        peer = normalize_ip_address(request.client.host)

    trusted_proxies = {
        normalize_ip_address(proxy)
        for proxy in settings.auth_trusted_proxies
    } - {UNKNOWN_IP}

    if peer not in trusted_proxies:
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")

    if not forwarded:
        return peer

    first_hop = forwarded.split(",", 1)[0].strip()

    if not first_hop:
        return peer

    try:
        return ipaddress.ip_address(first_hop).compressed

    except ValueError:
        return peer


@dataclass(frozen=True)
class RateLimitDecision:
    """Whether an authentication attempt may proceed."""

    allowed: bool
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class FailureResult:
    """Outcome of recording one failed authentication attempt."""

    locked: bool
    retry_after_seconds: int = 0
    failure_count: int = 0


class LoginRateLimitService:
    """Persistent, progressive login/setup rate limiting."""

    def max_attempts(self) -> int:
        return max(1, settings.AUTH_LOGIN_MAX_FAILED_ATTEMPTS)

    def cooldown_seconds(self, cooldown_level: int) -> int:
        """Return the cooldown for the zero-based ``cooldown_level``."""

        base_seconds = max(
            1,
            settings.AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES,
        ) * 60
        multiplier = max(1, settings.AUTH_LOGIN_COOLDOWN_MULTIPLIER)
        cap_seconds = max(
            base_seconds,
            settings.AUTH_LOGIN_MAX_COOLDOWN_MINUTES * 60,
        )

        # Bound the exponent so a tampered cooldown_level cannot overflow.
        exponent = min(max(0, cooldown_level), 30)

        return min(cap_seconds, base_seconds * (multiplier ** exponent))

    def retention(self) -> timedelta:
        hours = max(
            settings.AUTH_LOGIN_ATTEMPT_RETENTION_HOURS,
            settings.AUTH_LOGIN_MAX_COOLDOWN_MINUTES // 60 + 1,
            1,
        )
        return timedelta(hours=hours)

    @staticmethod
    def _retry_after(locked_until: datetime | None, now: datetime) -> int:
        aware_until = _aware(locked_until)

        if aware_until is None:
            return 0

        return max(
            1,
            math.ceil((aware_until - now).total_seconds()),
        )

    async def check_allowed(
        self,
        session: AsyncSession,
        *,
        ip_address: str,
        account_key: str,
        now: datetime | None = None,
    ) -> RateLimitDecision:
        """Return whether an attempt may proceed for this IP/account pair."""

        if not settings.AUTH_LOGIN_RATE_LIMIT_ENABLED:
            return RateLimitDecision(True)

        now = now or _utcnow()

        result = await session.execute(
            select(LoginAttempt.locked_until).where(
                LoginAttempt.ip_address == ip_address,
                LoginAttempt.account_key == account_key,
            )
        )

        locked_until = result.scalar_one_or_none()
        aware_until = _aware(locked_until)

        if aware_until is not None and aware_until > now:
            return RateLimitDecision(
                False,
                self._retry_after(aware_until, now),
            )

        return RateLimitDecision(True)

    async def _delete_expired(
        self,
        session: AsyncSession,
        *,
        now: datetime,
    ) -> int:
        cutoff = now - self.retention()

        result = await session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.last_failed_at < cutoff,
            )
        )

        return int(result.rowcount or 0)

    async def cleanup_expired(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> int:
        """Delete attempt rows past the retention window and commit."""

        now = now or _utcnow()
        deleted = await self._delete_expired(session, now=now)

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            logger.warning("Failed to clean up expired login attempts.")
            raise

        return deleted

    async def record_failure(
        self,
        session: AsyncSession,
        *,
        ip_address: str,
        account_key: str,
        now: datetime | None = None,
    ) -> FailureResult:
        """Atomically increment the failure counter and apply a cooldown.

        The counter is reset to one after a cooldown expires, while the
        cooldown level is kept so repeated abuse receives progressively longer
        cooldowns. A successful login deletes the row entirely.
        """

        if not settings.AUTH_LOGIN_RATE_LIMIT_ENABLED:
            return FailureResult(False)

        now = now or _utcnow()
        max_attempts = self.max_attempts()

        try:
            await self._delete_expired(session, now=now)
        except Exception:
            # Cleanup is best-effort; never let it prevent counting a failure.
            await session.rollback()
            logger.warning("Login-attempt cleanup failed; continuing.")

        for _attempt in range(3):
            expired_lock = and_(
                LoginAttempt.locked_until.is_not(None),
                LoginAttempt.locked_until <= now,
            )

            result = await session.execute(
                update(LoginAttempt)
                .where(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.account_key == account_key,
                    or_(
                        LoginAttempt.locked_until.is_(None),
                        LoginAttempt.locked_until <= now,
                    ),
                )
                .values(
                    failure_count=case(
                        (expired_lock, 1),
                        else_=LoginAttempt.failure_count + 1,
                    ),
                    first_failed_at=case(
                        (expired_lock, now),
                        else_=LoginAttempt.first_failed_at,
                    ),
                    # Clear the expired lock so it is not re-applied to every
                    # subsequent failure in the new counting cycle.
                    locked_until=case(
                        (expired_lock, None),
                        else_=LoginAttempt.locked_until,
                    ),
                    last_failed_at=now,
                    updated_at=now,
                )
                .returning(
                    LoginAttempt.failure_count,
                    LoginAttempt.cooldown_level,
                    LoginAttempt.locked_until,
                )
            )

            row = result.first()

            if row is not None:
                failure_count = int(row[0])
                cooldown_level = int(row[1])
                existing_lock = _aware(row[2])

                if (
                    existing_lock is not None
                    and existing_lock > now
                ):
                    try:
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        raise

                    return FailureResult(
                        True,
                        self._retry_after(existing_lock, now),
                        failure_count,
                    )

                break

            # The UPDATE matched nothing. Either the row does not exist or it
            # is already inside a cooldown set by a concurrent request.
            existing = await session.execute(
                select(LoginAttempt.locked_until).where(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.account_key == account_key,
                )
            )
            existing_lock = _aware(existing.scalar_one_or_none())

            if existing_lock is not None and existing_lock > now:
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

                return FailureResult(
                    True,
                    self._retry_after(existing_lock, now),
                    max_attempts,
                )

            if existing_lock is not None:
                # The row exists but the atomic UPDATE raced with another
                # transaction. Retry against the now-committed row.
                await session.rollback()
                continue

            session.add(
                LoginAttempt(
                    ip_address=ip_address,
                    account_key=account_key,
                    failure_count=1,
                    cooldown_level=0,
                    first_failed_at=now,
                    last_failed_at=now,
                    updated_at=now,
                )
            )

            try:
                await session.flush()
                failure_count = 1
                cooldown_level = 0
                break

            except IntegrityError:
                # Another request inserted the same (IP, account) row first.
                # Retry the atomic UPDATE against the row that now exists.
                await session.rollback()
                continue

        else:
            await session.rollback()
            raise RuntimeError("Unable to record a login failure.")

        locked = False
        retry_after = 0

        if failure_count >= max_attempts:
            lock_seconds = self.cooldown_seconds(cooldown_level)
            locked_until = now + timedelta(seconds=lock_seconds)

            # Only one concurrent request may escalate the cooldown. The
            # ``cooldown_level`` guard makes the transition single-shot.
            escalate = await session.execute(
                update(LoginAttempt)
                .where(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.account_key == account_key,
                    LoginAttempt.failure_count >= max_attempts,
                    LoginAttempt.cooldown_level == cooldown_level,
                    or_(
                        LoginAttempt.locked_until.is_(None),
                        LoginAttempt.locked_until <= now,
                    ),
                )
                .values(
                    locked_until=locked_until,
                    cooldown_level=LoginAttempt.cooldown_level + 1,
                    updated_at=now,
                )
                .returning(LoginAttempt.locked_until)
            )

            escalated_row = escalate.first()

            if escalated_row is not None:
                # The cooldown starts now, but this request already received a
                # normal authentication failure. The next request is blocked.
                locked = False
                retry_after = 0
            else:
                current = await session.execute(
                    select(LoginAttempt.locked_until).where(
                        LoginAttempt.ip_address == ip_address,
                        LoginAttempt.account_key == account_key,
                    )
                )
                current_lock = _aware(current.scalar_one_or_none())

                if current_lock is not None and current_lock > now:
                    locked = True
                    retry_after = self._retry_after(current_lock, now)

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception("Failed to persist a login failure.")
            raise

        return FailureResult(locked, retry_after, failure_count)

    async def record_success(
        self,
        session: AsyncSession,
        *,
        ip_address: str,
        account_key: str,
    ) -> None:
        """Clear the failure state after a successful authentication."""

        await session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.ip_address == ip_address,
                LoginAttempt.account_key == account_key,
            )
        )

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            logger.warning("Failed to clear login failure state.")
            raise


login_rate_limit_service = LoginRateLimitService()
