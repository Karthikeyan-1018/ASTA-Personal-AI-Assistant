"""add notification preferences and quiet hours

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-11-11 03:00:00.000000

Creates the additive ``notification_preferences`` table. A missing row means the
configured defaults apply, so existing notifications keep their current
behaviour. No existing table is altered.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the notification_preferences table."""

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "in_app_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "quiet_hours_start",
            sa.Time(timezone=False),
            nullable=False,
            server_default="22:00:00",
        ),
        sa.Column(
            "quiet_hours_end",
            sa.Time(timezone=False),
            nullable=False,
            server_default="07:00:00",
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="UTC",
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            name="uq_notification_preferences_user",
        ),
    )

    op.create_index(
        "ix_notification_preferences_user",
        "notification_preferences",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the notification_preferences table."""

    op.drop_index(
        "ix_notification_preferences_user",
        table_name="notification_preferences",
    )
    op.drop_table("notification_preferences")
