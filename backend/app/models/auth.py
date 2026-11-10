"""Single-owner authentication models.

ASTA is a personal assistant with exactly one owner. Authentication is kept in
dedicated tables so the existing domain ``users`` table keeps its original
meaning: the owner of conversations, memories, preferences, and tasks.

Only a password hash (Argon2id) and session-token hashes are stored. Plaintext
passwords and raw session tokens are never persisted.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OwnerAccount(Base):
    """The single ASTA owner account.

    ``id`` is constrained to 1 so PostgreSQL enforces that at most one owner
    account can ever exist, even under concurrent setup requests.
    """

    __tablename__ = "owner_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_owner_accounts_singleton"),
    )


class LoginAttempt(Base):
    """Database-backed failed-login counter for one IP and account key.

    Rows are keyed by the normalised source IP and a normalised account
    identifier. No password, password hash, session token, or other credential
    is ever stored here. Cooldowns always expire, so this table can never
    permanently lock the owner out.
    """

    __tablename__ = "auth_login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    ip_address: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    account_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    failure_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    # Number of cooldowns already applied. Kept across cooldown expiry so
    # repeated abuse escalates even after an attacker waits out the first
    # timeout.
    cooldown_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "ip_address",
            "account_key",
            name="uq_auth_login_attempts_ip_account",
        ),
        CheckConstraint(
            "failure_count >= 0",
            name="ck_auth_login_attempts_failure_count_nonnegative",
        ),
        CheckConstraint(
            "cooldown_level >= 0",
            name="ck_auth_login_attempts_cooldown_level_nonnegative",
        ),
    )


class AuthSession(Base):
    """A server-side, revocable authentication session."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 hex digest of the raw session token. The raw token only ever
    # exists in the client's HttpOnly cookie.
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    # SHA-256 hex digest of the CSRF token bound to this session. The raw CSRF
    # token is readable by JavaScript and must be sent in X-CSRF-Token.
    csrf_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expiry_after_creation",
        ),
        Index(
            "ix_auth_sessions_user_revoked",
            "user_id",
            "revoked_at",
        ),
    )
