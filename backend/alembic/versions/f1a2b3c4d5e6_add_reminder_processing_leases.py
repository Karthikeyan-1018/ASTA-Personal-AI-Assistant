"""add reminder processing leases

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-11-11 01:00:00.000000

Extends ``task_reminders`` for the Phase 10B database-backed scheduler. The
change is additive except for replacing the status check constraint so the new
internal ``processing`` lease state is accepted.

No row is deleted or rewritten. Existing reminders retain their status,
schedule, attempt metadata, and deliverability.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add claim metadata and allow the processing lifecycle state."""

    op.add_column(
        "task_reminders",
        sa.Column("claim_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "task_reminders",
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.drop_constraint(
        "task_reminder_status",
        "task_reminders",
        type_="check",
    )
    op.create_check_constraint(
        "task_reminder_status",
        "task_reminders",
        "status IN ('pending', 'processing', 'delivered', "
        "'cancelled', 'suppressed', 'failed')",
    )

    op.create_index(
        "ix_task_reminders_due",
        "task_reminders",
        ["status", "next_attempt_at", "remind_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_reminders_processing",
        "task_reminders",
        ["status", "processing_started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove claim metadata and restore the Phase 10A status constraint."""

    # A processing reminder cannot exist in the pre-lease schema. Returning it
    # to pending preserves the valid pending work instead of discarding it.
    op.execute(
        "UPDATE task_reminders SET status = 'pending' "
        "WHERE status = 'processing'"
    )

    op.drop_index(
        "ix_task_reminders_processing",
        table_name="task_reminders",
    )
    op.drop_index(
        "ix_task_reminders_due",
        table_name="task_reminders",
    )

    op.drop_constraint(
        "task_reminder_status",
        "task_reminders",
        type_="check",
    )
    op.create_check_constraint(
        "task_reminder_status",
        "task_reminders",
        "status IN ('pending', 'delivered', 'cancelled', "
        "'suppressed', 'failed')",
    )

    op.drop_column("task_reminders", "processing_started_at")
    op.drop_column("task_reminders", "claim_token")
