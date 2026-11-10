"""add structured tasks and deadlines

Revision ID: b7d2e6f4a8c1
Revises: a7c2e4f9d1b3
Create Date: 2026-11-09 00:00:00.000000

Creates a dedicated, non-destructive ``tasks`` table. Existing users,
memories, conversations, messages, preferences, and pgvector embeddings are
untouched. Tasks are user-scoped and cascade-delete with their owner.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7d2e6f4a8c1"
down_revision: Union[str, Sequence[str], None] = "a7c2e4f9d1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the tasks table and its indexes."""

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "college",
                "career",
                "personal",
                "fitness",
                "project",
                "other",
                name="task_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="other"
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "low",
                "medium",
                "high",
                "urgent",
                name="task_priority",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="medium"
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "in_progress",
                "completed",
                "cancelled",
                name="task_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending"
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_time", sa.Time(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column(
            "is_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
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
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True
        ),
        sa.CheckConstraint(
            "due_time IS NULL OR due_date IS NOT NULL",
            name="ck_tasks_due_time_requires_due_date"
        ),
        sa.CheckConstraint(
            "timezone IS NULL OR due_date IS NOT NULL",
            name="ck_tasks_timezone_requires_due_date"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_tasks_user_status",
        "tasks",
        ["user_id", "status"],
        unique=False
    )

    op.create_index(
        "ix_tasks_user_category",
        "tasks",
        ["user_id", "category"],
        unique=False
    )

    op.create_index(
        "ix_tasks_user_due_date",
        "tasks",
        ["user_id", "due_date"],
        unique=False
    )

    op.create_index(
        "ix_tasks_status_due_date",
        "tasks",
        ["status", "due_date"],
        unique=False
    )


def downgrade() -> None:
    """Drop the tasks table and its indexes."""

    op.drop_index("ix_tasks_status_due_date", table_name="tasks")
    op.drop_index("ix_tasks_user_due_date", table_name="tasks")
    op.drop_index("ix_tasks_user_category", table_name="tasks")
    op.drop_index("ix_tasks_user_status", table_name="tasks")
    op.drop_table("tasks")
