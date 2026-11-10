"""add login rate limiting

Revision ID: c7d2e8f4a1b6
Revises: b1f4a7c2e9d5
Create Date: 2026-11-10 00:00:00.000000

Creates the additive ``auth_login_attempts`` table used by Phase 8.1. It stores
only a normalised source IP, a normalised account identifier, failure counters,
and expiry timestamps. Passwords, password hashes, session tokens, and CSRF
tokens are never stored here.

Existing users, conversations, messages, memories, preferences, tasks, pending
actions, owner accounts, sessions, and pgvector embeddings are untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7d2e8f4a1b6"
down_revision: Union[str, Sequence[str], None] = "b1f4a7c2e9d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the login-attempt counter table and indexes."""

    op.create_table(
        "auth_login_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("account_key", sa.String(length=255), nullable=False),
        sa.Column(
            "failure_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cooldown_level",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "first_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cooldown_level >= 0",
            name="ck_auth_login_attempts_cooldown_level_nonnegative",
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_auth_login_attempts_failure_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ip_address",
            "account_key",
            name="uq_auth_login_attempts_ip_account",
        ),
    )

    op.create_index(
        "ix_auth_login_attempts_last_failed_at",
        "auth_login_attempts",
        ["last_failed_at"],
        unique=False,
    )

    op.create_index(
        "ix_auth_login_attempts_locked_until",
        "auth_login_attempts",
        ["locked_until"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the login-attempt counter table and indexes."""

    op.drop_index(
        "ix_auth_login_attempts_locked_until",
        table_name="auth_login_attempts",
    )
    op.drop_index(
        "ix_auth_login_attempts_last_failed_at",
        table_name="auth_login_attempts",
    )
    op.drop_table("auth_login_attempts")
