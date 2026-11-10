"""add structured user preferences

Revision ID: a7c2e4f9d1b3
Revises: f3a1c9d7e5b2
Create Date: 2026-11-09 00:00:00.000000

Creates a dedicated, non-destructive table for structured user preferences.
Existing users, memories, conversations, messages, and pgvector embeddings are
untouched. Preferences are user-scoped and unique by ``(user_id, key)``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c2e4f9d1b3"
down_revision: Union[str, Sequence[str], None] = "f3a1c9d7e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the user_preferences table and its indexes."""

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            server_default="general"
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "inactive",
                "rejected",
                name="preference_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending"
        ),
        sa.Column(
            "source",
            sa.Enum(
                "explicit",
                "inferred",
                name="preference_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="explicit"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "key",
            name="uq_user_preferences_user_id_key"
        ),
    )

    op.create_index(
        "ix_user_preferences_user_status",
        "user_preferences",
        ["user_id", "status"],
        unique=False
    )

    op.create_index(
        "ix_user_preferences_user_category",
        "user_preferences",
        ["user_id", "category"],
        unique=False
    )


def downgrade() -> None:
    """Drop the user_preferences table and its indexes."""

    op.drop_index(
        "ix_user_preferences_user_category",
        table_name="user_preferences"
    )
    op.drop_index(
        "ix_user_preferences_user_status",
        table_name="user_preferences"
    )
    op.drop_table("user_preferences")
