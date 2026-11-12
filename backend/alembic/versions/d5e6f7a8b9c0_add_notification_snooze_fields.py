"""add notification snooze audit fields

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-11 05:00:00.000000

Adds three nullable/defaulted metadata columns to ``notifications``. Snoozing
does not introduce a new table: the notification row is reused and its
associated reminder is re-armed. Existing rows are backfilled with
``snooze_count = 0`` by the server default. No existing column, index, or row is
altered or deleted.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add snooze audit metadata to notifications."""

    op.add_column(
        "notifications",
        sa.Column(
            "snoozed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "snoozed_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "snooze_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_notifications_snooze_count_non_negative",
        "notifications",
        "snooze_count >= 0",
    )


def downgrade() -> None:
    """Remove snooze audit metadata from notifications."""

    op.drop_constraint(
        "ck_notifications_snooze_count_non_negative",
        "notifications",
        type_="check",
    )
    op.drop_column("notifications", "snooze_count")
    op.drop_column("notifications", "snoozed_until")
    op.drop_column("notifications", "snoozed_at")
