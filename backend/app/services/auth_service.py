"""Single-owner authentication service.

Security properties:

* passwords are hashed with Argon2id and never stored or returned in plaintext;
* session tokens are 256-bit random values and only their SHA-256 hashes are
  persisted;
* expired and revoked sessions never authenticate a request;
* token and CSRF comparisons use :func:`hmac.compare_digest`;
* no password, token, or hash is written to logs.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.auth_exceptions import (
    InvalidOwnerPasswordError,
    InvalidOwnerUsernameError,
    OwnerAlreadyExistsError,
)
from app.models.auth import AuthSession, OwnerAccount
from app.models.user import User


logger = logging.getLogger(__name__)


OWNER_ACCOUNT_ID = 1
MIN_SESSION_LIFETIME_HOURS = 1
MAX_SESSION_LIFETIME_HOURS = 24 * 365
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,49}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest used for session and CSRF tokens."""

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def token_matches(raw_token: str, stored_hash: str) -> bool:
    """Constant-time comparison of a raw token against a stored hash."""

    if not raw_token or not stored_hash:
        return False

    return hmac.compare_digest(hash_token(raw_token), stored_hash)


class AuthService:
    """Owner-account and session lifecycle operations."""

    def __init__(self) -> None:

        self.password_hasher = PasswordHasher(
            time_cost=max(1, settings.AUTH_ARGON2_TIME_COST),
            memory_cost=max(8192, settings.AUTH_ARGON2_MEMORY_COST),
            parallelism=max(1, settings.AUTH_ARGON2_PARALLELISM),
            type=Type.ID,
        )

        self._dummy_hash: str | None = None

    # ------------------------------------------------------------------
    # Owner account
    # ------------------------------------------------------------------

    def validate_username(self, username: str) -> str:
        cleaned = (username or "").strip()

        if not USERNAME_PATTERN.fullmatch(cleaned):
            raise InvalidOwnerUsernameError(
                "Username must be 3-50 characters and contain only letters, "
                "numbers, dots, underscores, or hyphens."
            )

        return cleaned

    def validate_password(self, password: str) -> str:
        if not isinstance(password, str):
            raise InvalidOwnerPasswordError("Password must be a string.")

        if len(password) < settings.AUTH_PASSWORD_MIN_LENGTH:
            raise InvalidOwnerPasswordError(
                "Password must be at least "
                f"{settings.AUTH_PASSWORD_MIN_LENGTH} characters."
            )

        if len(password) > settings.AUTH_PASSWORD_MAX_LENGTH:
            raise InvalidOwnerPasswordError(
                "Password must not exceed "
                f"{settings.AUTH_PASSWORD_MAX_LENGTH} characters."
            )

        if not password.strip():
            raise InvalidOwnerPasswordError("Password must not be blank.")

        return password

    def hash_password(self, password: str) -> str:
        self.validate_password(password)
        return self.password_hasher.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self.password_hasher.verify(password_hash, password))

        except (VerificationError, InvalidHashError):
            return False

    def _dummy_verify(self, password: str) -> None:
        """Burn a comparable amount of time when no owner exists."""

        if self._dummy_hash is None:
            self._dummy_hash = self.password_hasher.hash(
                "not-the-owner-password"
            )

        self.verify_password(self._dummy_hash, password)

    async def get_owner(self, session: AsyncSession) -> OwnerAccount | None:
        result = await session.execute(
            select(OwnerAccount).order_by(OwnerAccount.id.asc()).limit(1)
        )

        return result.scalar_one_or_none()

    async def owner_exists(self, session: AsyncSession) -> bool:
        return await self.get_owner(session) is not None

    async def get_owner_user(
        self,
        session: AsyncSession,
        owner: OwnerAccount,
    ) -> User | None:
        return await session.get(User, owner.user_id)

    async def create_owner(
        self,
        session: AsyncSession,
        username: str,
        password: str,
    ) -> tuple[OwnerAccount, User]:
        """Create the single owner user and account atomically."""

        cleaned_username = self.validate_username(username)
        password_hash = self.hash_password(password)

        if await self.owner_exists(session):
            raise OwnerAlreadyExistsError("An owner account already exists.")

        existing_user = await session.scalar(
            select(User).where(
                func.lower(User.username) == cleaned_username.lower()
            )
        )

        if existing_user is not None:
            raise OwnerAlreadyExistsError(
                "That username is already in use. Choose a different owner "
                "username."
            )

        user = User(username=cleaned_username)
        session.add(user)
        await session.flush()

        owner = OwnerAccount(
            id=OWNER_ACCOUNT_ID,
            user_id=user.id,
            password_hash=password_hash,
        )
        session.add(owner)

        try:
            await session.commit()

        except IntegrityError as error:
            await session.rollback()
            raise OwnerAlreadyExistsError(
                "An owner account already exists."
            ) from error

        await session.refresh(user)
        await session.refresh(owner)

        logger.info("Single-owner account created.")
        return owner, user

    async def authenticate(
        self,
        session: AsyncSession,
        username: str,
        password: str,
    ) -> OwnerAccount | None:
        """Return the owner account for valid credentials, else ``None``."""

        cleaned_username = (username or "").strip()

        result = await session.execute(
            select(OwnerAccount)
            .join(User, User.id == OwnerAccount.user_id)
            .where(func.lower(User.username) == cleaned_username.lower())
            .limit(1)
        )

        owner = result.scalar_one_or_none()

        if owner is None:
            # Keep the response time roughly independent of whether the
            # username exists.
            self._dummy_verify(password)
            return None

        if not self.verify_password(owner.password_hash, password):
            return None

        if self.password_hasher.check_needs_rehash(owner.password_hash):
            owner.password_hash = self.password_hasher.hash(password)
            await session.commit()
            await session.refresh(owner)

        return owner

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def session_lifetime(self) -> timedelta:
        hours = max(
            MIN_SESSION_LIFETIME_HOURS,
            min(
                settings.AUTH_SESSION_LIFETIME_HOURS,
                MAX_SESSION_LIFETIME_HOURS,
            ),
        )

        return timedelta(hours=hours)

    async def create_session(
        self,
        session: AsyncSession,
        owner: OwnerAccount,
    ) -> tuple[str, str, AuthSession]:
        """Create a session and return ``(raw_token, csrf_token, row)``."""

        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = _utcnow()

        auth_session = AuthSession(
            user_id=owner.user_id,
            token_hash=hash_token(raw_token),
            csrf_token_hash=hash_token(csrf_token),
            created_at=now,
            expires_at=now + self.session_lifetime(),
        )

        session.add(auth_session)

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception("Failed to create an authentication session.")
            raise

        await session.refresh(auth_session)
        return raw_token, csrf_token, auth_session

    async def get_session_by_token(
        self,
        session: AsyncSession,
        raw_token: str | None,
    ) -> AuthSession | None:
        """Return a non-expired, non-revoked session for a raw token."""

        if not raw_token:
            return None

        token_hash = hash_token(raw_token)

        result = await session.execute(
            select(AuthSession)
            .where(AuthSession.token_hash == token_hash)
            .limit(1)
        )

        auth_session = result.scalar_one_or_none()

        if auth_session is None:
            return None

        # Defence in depth: the unique lookup already matched, but compare
        # in constant time before trusting the row.
        if not hmac.compare_digest(auth_session.token_hash, token_hash):
            return None

        if auth_session.revoked_at is not None:
            return None

        expires_at = _aware(auth_session.expires_at)

        if expires_at is None or expires_at <= _utcnow():
            return None

        return auth_session

    async def touch_session(
        self,
        session: AsyncSession,
        auth_session: AuthSession,
    ) -> None:
        """Update ``last_used_at`` at most once per configured interval."""

        now = _utcnow()
        last_used = _aware(auth_session.last_used_at)
        interval = max(0, settings.AUTH_SESSION_LAST_USED_UPDATE_SECONDS)

        if last_used is not None and (now - last_used).total_seconds() < interval:
            return

        auth_session.last_used_at = now

        try:
            await session.commit()
            await session.refresh(auth_session)

        except Exception:
            await session.rollback()
            logger.warning("Failed to update session last-used time.")

    async def revoke_session(
        self,
        session: AsyncSession,
        raw_token: str | None,
    ) -> bool:
        if not raw_token:
            return False

        auth_session = await self.get_session_by_token(session, raw_token)

        if auth_session is None:
            return False

        auth_session.revoked_at = _utcnow()

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            logger.exception("Failed to revoke an authentication session.")
            raise

        return True

    def verify_csrf(
        self,
        auth_session: AuthSession,
        csrf_token: str | None,
    ) -> bool:
        if not csrf_token:
            return False

        return hmac.compare_digest(
            auth_session.csrf_token_hash,
            hash_token(csrf_token),
        )

    async def resolve_user_id(
        self,
        session: AsyncSession,
        raw_token: str | None,
        *,
        touch: bool = True,
    ) -> int | None:
        auth_session = await self.get_session_by_token(session, raw_token)

        if auth_session is None:
            return None

        if touch:
            await self.touch_session(session, auth_session)

        return auth_session.user_id


auth_service = AuthService()
