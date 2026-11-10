"""add task pending confirmation actions

Revision ID: c8e1f3a5d7b9
Revises: b7d2e6f4a8c1
Create Date: 2026-11-09 00:00:00.000000

Creates a dedicated, non-destructive ``task_pending_actions`` table used to
gate natural-language task mutations behind explicit user confirmation. The
table is user- and conversation-scoped, expires records, and stores only
already-validated task payloads. Existing users, memories, conversations,
messages, preferences, tasks, and pgvector embeddings are untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8e1f3a5d7b9"
down_revision: Union[str, Sequence[str], None] = "b7d2e6f4a8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the pending-action table and its indexes."""

    op.create_table(
        "task_pending_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum(
                "create",
                "update",
                "complete",
                "reopen",
                "cancel",
                "delete",
                name="task_pending_action_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "executed",
                "rejected",
                "expired",
                "superseded",
                "failed",
                name="task_pending_action_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("target_task_id", sa.Integer(), nullable=True),
        sa.Column("result_task_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_task_pending_actions_expiry_after_creation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["result_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_task_pending_actions_user_id",
        "task_pending_actions",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_task_pending_actions_conversation_id",
        "task_pending_actions",
        ["conversation_id"],
        unique=False,
    )

    op.create_index(
        "ix_task_pending_actions_user_conversation_status",
        "task_pending_actions",
        ["user_id", "conversation_id", "status"],
        unique=False,
    )

    op.create_index(
        "ix_task_pending_actions_status",
        "task_pending_actions",
        ["status"],
        unique=False,
    )

    op.create_index(
        "ix_task_pending_actions_expires_at",
        "task_pending_actions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the pending-action table and its indexes."""

    op.drop_index(
        "ix_task_pending_actions_expires_at",
        table_name="task_pending_actions",
    )
    op.drop_index(
        "ix_task_pending_actions_status",
        table_name="task_pending_actions",
    )
    op.drop_index(
        "ix_task_pending_actions_user_conversation_status",
        table_name="task_pending_actions",
    )
    op.drop_index(
        "ix_task_pending_actions_conversation_id",
        table_name="task_pending_actions",
    )
    op.drop_index(
        "ix_task_pending_actions_user_id",
        table_name="task_pending_actions",
    )
    op.drop_table("task_pending_actions")
