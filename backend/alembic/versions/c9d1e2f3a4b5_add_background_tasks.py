"""add background task records

Revision ID: c9d1e2f3a4b5
Revises: b2c3d4e5f6a7
Create Date: 2026-11-14 00:00:00.000000

Phase 13 adds a durable, owner-scoped ``background_tasks`` table for the
in-process background task runner. The table stores only allowlisted task
metadata: a task type, small validated parameters, status timestamps, and a
metadata-only result. It never stores prompts, credentials, raw tool output, or
arbitrary commands.

No existing table is modified. The upgrade is additive and the downgrade drops
only the new table and its indexes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATUSES = ("queued", "running", "completed", "failed", "cancelled")


def _status_check() -> str:
    rendered = ", ".join(f"'{value}'" for value in STATUSES)
    return f"status IN ({rendered})"


def upgrade() -> None:
    """Create the background task table, constraint, and indexes."""

    op.create_table(
        "background_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _status_check(),
            name="background_task_status",
        ),
    )

    op.create_index(
        "ix_background_tasks_user_id",
        "background_tasks",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_background_tasks_task_type",
        "background_tasks",
        ["task_type"],
        unique=False,
    )
    op.create_index(
        "ix_background_tasks_status",
        "background_tasks",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_background_tasks_user_created",
        "background_tasks",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_background_tasks_user_status",
        "background_tasks",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the Phase 13 background task table and its indexes."""

    op.drop_index(
        "ix_background_tasks_user_status",
        table_name="background_tasks",
    )
    op.drop_index(
        "ix_background_tasks_user_created",
        table_name="background_tasks",
    )
    op.drop_index(
        "ix_background_tasks_status",
        table_name="background_tasks",
    )
    op.drop_index(
        "ix_background_tasks_task_type",
        table_name="background_tasks",
    )
    op.drop_index(
        "ix_background_tasks_user_id",
        table_name="background_tasks",
    )
    op.drop_table("background_tasks")
