"""add task-scoped reminders

Revision ID: e1f2a3b4c5d6
Revises: d8e3f5a2b7c1
Create Date: 2026-11-11 00:00:00.000000

Creates one additive, non-destructive ``task_reminders`` table. Every reminder
belongs to an existing task and owner and is removed when either is removed.
No existing table, column, index, or row is altered. Notification delivery is
introduced in a later Phase 10 subphase; this migration only persists the
schedule and its lifecycle metadata.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d8e3f5a2b7c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the task_reminders table and its indexes."""

    op.create_table(
        "task_reminders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column(
            "remind_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("offset_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "delivered",
                "cancelled",
                "suppressed",
                "failed",
                name="task_reminder_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "dedupe_key",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_error",
            sa.String(length=255),
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
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_task_reminders_attempt_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_task_reminders_user_dedupe",
        ),
    )

    op.create_index(
        "ix_task_reminders_status_remind_at",
        "task_reminders",
        ["status", "remind_at"],
        unique=False,
    )

    op.create_index(
        "ix_task_reminders_user_remind_at",
        "task_reminders",
        ["user_id", "remind_at"],
        unique=False,
    )

    op.create_index(
        "ix_task_reminders_task_id",
        "task_reminders",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the task_reminders table and its indexes."""

    op.drop_index(
        "ix_task_reminders_task_id",
        table_name="task_reminders",
    )
    op.drop_index(
        "ix_task_reminders_user_remind_at",
        table_name="task_reminders",
    )
    op.drop_index(
        "ix_task_reminders_status_remind_at",
        table_name="task_reminders",
    )
    op.drop_table("task_reminders")
