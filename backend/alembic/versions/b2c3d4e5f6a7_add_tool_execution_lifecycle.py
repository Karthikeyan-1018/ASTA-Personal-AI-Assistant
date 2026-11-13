"""add tool execution lifecycle, retries, and idempotency

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-12 00:00:00.000000

Phase 11I extends the metadata-only ``tool_executions`` table with:

* a fine-grained ``lifecycle_status`` state;
* ``started_at`` / ``completed_at`` timestamps;
* ``retry_count`` and ``truncated`` flags;
* a hashed, owner-scoped idempotency key with a unique index.

Existing rows are backfilled from their coarse ``status`` value. The status
check constraint is widened to include ``pending``, ``running`` and
``cancelled``. No existing column is dropped, reordered, or rewritten, and no
other table is touched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_STATUSES = (
    "success",
    "error",
    "timeout",
    "denied",
    "confirmation_required",
)

NEW_STATUSES = OLD_STATUSES + (
    "pending",
    "running",
    "cancelled",
)

LIFECYCLES = (
    "pending",
    "awaiting_confirmation",
    "approved",
    "running",
    "succeeded",
    "failed",
    "rejected",
    "expired",
    "cancelled",
    "timed_out",
)


def _status_check(values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({rendered})"


def _lifecycle_check() -> str:
    rendered = ", ".join(f"'{value}'" for value in LIFECYCLES)
    return f"lifecycle_status IN ({rendered})"


def upgrade() -> None:
    """Add lifecycle, timestamp, retry, and idempotency metadata."""

    op.add_column(
        "tool_executions",
        sa.Column("lifecycle_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "tool_executions",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "tool_executions",
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "tool_executions",
        sa.Column("retry_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tool_executions",
        sa.Column("truncated", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "tool_executions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )

    # Backfill from the coarse status. Every historical row was terminal.
    op.execute(
        """
        UPDATE tool_executions
        SET lifecycle_status = CASE status
            WHEN 'success' THEN 'succeeded'
            WHEN 'timeout' THEN 'timed_out'
            WHEN 'denied' THEN 'rejected'
            WHEN 'confirmation_required' THEN 'awaiting_confirmation'
            ELSE 'failed'
        END
        """
    )
    op.execute(
        "UPDATE tool_executions SET retry_count = 0 "
        "WHERE retry_count IS NULL"
    )
    op.execute(
        "UPDATE tool_executions SET truncated = false "
        "WHERE truncated IS NULL"
    )
    op.execute(
        "UPDATE tool_executions SET started_at = created_at "
        "WHERE started_at IS NULL"
    )
    op.execute(
        "UPDATE tool_executions SET completed_at = created_at "
        "WHERE completed_at IS NULL"
    )

    op.alter_column(
        "tool_executions",
        "lifecycle_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="pending",
    )
    op.alter_column(
        "tool_executions",
        "retry_count",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="0",
    )
    op.alter_column(
        "tool_executions",
        "truncated",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )

    # Widen the coarse status constraint for in-flight and cancelled attempts.
    op.drop_constraint(
        "tool_execution_status",
        "tool_executions",
        type_="check",
    )
    op.create_check_constraint(
        "tool_execution_status",
        "tool_executions",
        _status_check(NEW_STATUSES),
    )
    op.create_check_constraint(
        "tool_execution_lifecycle",
        "tool_executions",
        _lifecycle_check(),
    )
    op.create_check_constraint(
        "ck_tool_executions_retry_count_non_negative",
        "tool_executions",
        "retry_count >= 0",
    )

    op.create_index(
        "ix_tool_executions_user_lifecycle",
        "tool_executions",
        ["user_id", "lifecycle_status"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_lifecycle_started",
        "tool_executions",
        ["lifecycle_status", "started_at"],
        unique=False,
    )
    op.create_index(
        "ux_tool_executions_user_idempotency_key",
        "tool_executions",
        ["user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    """Remove Phase 11I metadata and restore the original status constraint."""

    op.drop_index(
        "ux_tool_executions_user_idempotency_key",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_lifecycle_started",
        table_name="tool_executions",
    )
    op.drop_index(
        "ix_tool_executions_user_lifecycle",
        table_name="tool_executions",
    )

    op.drop_constraint(
        "ck_tool_executions_retry_count_non_negative",
        "tool_executions",
        type_="check",
    )
    op.drop_constraint(
        "tool_execution_lifecycle",
        "tool_executions",
        type_="check",
    )

    # The old constraint cannot represent in-flight or cancelled rows. Coerce
    # them to the nearest original terminal value before restoring it.
    op.execute(
        "UPDATE tool_executions SET status = 'error' "
        "WHERE status IN ('pending', 'running', 'cancelled')"
    )
    op.drop_constraint(
        "tool_execution_status",
        "tool_executions",
        type_="check",
    )
    op.create_check_constraint(
        "tool_execution_status",
        "tool_executions",
        _status_check(OLD_STATUSES),
    )

    op.drop_column("tool_executions", "idempotency_key")
    op.drop_column("tool_executions", "truncated")
    op.drop_column("tool_executions", "retry_count")
    op.drop_column("tool_executions", "completed_at")
    op.drop_column("tool_executions", "started_at")
    op.drop_column("tool_executions", "lifecycle_status")
