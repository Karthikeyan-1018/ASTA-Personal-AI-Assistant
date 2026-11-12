"""add tool execution and confirmation tables

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-11-12 00:00:00.000000

Additive migration for the Phase 11 tool engine. Creates two new tables:

* ``tool_executions`` stores metadata-only history (tool name, category,
  outcome, duration, confirmation state, safe error category). It never stores
  argument values, results, prompts, or credentials.
* ``tool_confirmations`` stores short-lived, single-use approvals bound to one
  exact tool call by a SHA-256 argument digest. Arguments are cleared when the
  confirmation is claimed, rejected, or expired.

No existing table, column, index, or row is modified.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _category_enum(name: str) -> sa.Enum:
    return sa.Enum(
        "read_only",
        "reversible",
        "sensitive",
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    """Create the two additive tool-engine tables."""

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column(
            "category",
            _category_enum("tool_execution_category"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "success",
                "error",
                "timeout",
                "denied",
                "confirmation_required",
                name="tool_execution_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "confirmation_status",
            sa.Enum(
                "not_required",
                "pending",
                "confirmed",
                "rejected",
                "expired",
                name="tool_confirmation_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="not_required",
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "error_category",
            sa.Enum(
                "validation",
                "not_found",
                "disabled",
                "permission",
                "confirmation",
                "timeout",
                "execution",
                "provider",
                "malformed_call",
                "size_limit",
                "configuration",
                "internal",
                name="tool_error_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("arguments_hash", sa.String(length=64), nullable=True),
        sa.Column("argument_keys", sa.JSON(), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_tool_executions_duration_non_negative",
        ),
        sa.CheckConstraint(
            "result_bytes IS NULL OR result_bytes >= 0",
            name="ck_tool_executions_result_bytes_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_tool_executions_user_id",
        "tool_executions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_conversation_id",
        "tool_executions",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_tool_name",
        "tool_executions",
        ["tool_name"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_user_created",
        "tool_executions",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_user_tool",
        "tool_executions",
        ["user_id", "tool_name"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_status",
        "tool_executions",
        ["status"],
        unique=False,
    )

    op.create_table(
        "tool_confirmations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column(
            "category",
            _category_enum("tool_confirmation_category"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "executed",
                "rejected",
                "expired",
                "failed",
                name="tool_confirmation_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("arguments_preview", sa.Text(), nullable=False),
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
        sa.Column("execution_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_tool_confirmations_expiry_after_creation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["tool_executions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_tool_confirmations_user_id",
        "tool_confirmations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_tool_confirmations_conversation_id",
        "tool_confirmations",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_tool_confirmations_tool_name",
        "tool_confirmations",
        ["tool_name"],
        unique=False,
    )
    op.create_index(
        "ix_tool_confirmations_user_status",
        "tool_confirmations",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_tool_confirmations_user_conversation",
        "tool_confirmations",
        ["user_id", "conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_tool_confirmations_expires_at",
        "tool_confirmations",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop only the Phase 11 tables and their indexes."""

    op.drop_index(
        "ix_tool_confirmations_expires_at",
        table_name="tool_confirmations",
    )
    op.drop_index(
        "ix_tool_confirmations_user_conversation",
        table_name="tool_confirmations",
    )
    op.drop_index(
        "ix_tool_confirmations_user_status",
        table_name="tool_confirmations",
    )
    op.drop_index(
        "ix_tool_confirmations_tool_name",
        table_name="tool_confirmations",
    )
    op.drop_index(
        "ix_tool_confirmations_conversation_id",
        table_name="tool_confirmations",
    )
    op.drop_index(
        "ix_tool_confirmations_user_id",
        table_name="tool_confirmations",
    )
    op.drop_table("tool_confirmations")

    op.drop_index(
        "ix_tool_executions_status",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_user_tool",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_user_created",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_tool_name",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_conversation_id",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_user_id",
        table_name="tool_executions",
    )
    op.drop_table("tool_executions")
