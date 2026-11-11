"""add recurring task rules and occurrence links

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-11-11 04:00:00.000000

Creates two additive tables. Recurrence rules are separate from ``tasks`` so
the existing task lifecycle, confirmation workflow, and deadline columns are
untouched. Generated occurrence tasks remain normal tasks, and the occurrence
link is unique per recurrence date to prevent duplicates after retries or
concurrent worker runs.

Deleting a rule sets occurrence links to NULL instead of deleting generated
tasks. Deleting a template task cascades its rule but leaves generated
occurrence tasks intact.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create recurrence rules and occurrence links."""

    op.create_table(
        "task_recurrences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column(
            "frequency",
            sa.Enum(
                "daily",
                "weekly",
                "monthly",
                name="recurrence_frequency",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "interval",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("weekdays", sa.String(length=20), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("anchor_time", sa.Time(timezone=False), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("last_generated_date", sa.Date(), nullable=True),
        sa.Column(
            "next_generation_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(
            "interval >= 1",
            name="ck_task_recurrences_interval_positive",
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
            "task_id",
            name="uq_task_recurrences_task",
        ),
    )

    op.create_index(
        "ix_task_recurrences_user_active",
        "task_recurrences",
        ["user_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_task_recurrences_due",
        "task_recurrences",
        ["is_active", "next_generation_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_recurrences_processing",
        "task_recurrences",
        ["is_active", "processing_started_at"],
        unique=False,
    )

    op.create_table(
        "task_recurrence_occurrences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("recurrence_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_id"],
            ["task_recurrences.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_id",
            "occurrence_date",
            name="uq_recurrence_occurrences_date",
        ),
        sa.UniqueConstraint(
            "task_id",
            name="uq_recurrence_occurrences_task",
        ),
    )

    op.create_index(
        "ix_recurrence_occurrences_user_date",
        "task_recurrence_occurrences",
        ["user_id", "occurrence_date"],
        unique=False,
    )
    op.create_index(
        "ix_recurrence_occurrences_recurrence",
        "task_recurrence_occurrences",
        ["recurrence_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop recurrence tables. Generated tasks are left in place."""

    op.drop_index(
        "ix_recurrence_occurrences_recurrence",
        table_name="task_recurrence_occurrences",
    )
    op.drop_index(
        "ix_recurrence_occurrences_user_date",
        table_name="task_recurrence_occurrences",
    )
    op.drop_table("task_recurrence_occurrences")

    op.drop_index(
        "ix_task_recurrences_processing",
        table_name="task_recurrences",
    )
    op.drop_index(
        "ix_task_recurrences_due",
        table_name="task_recurrences",
    )
    op.drop_index(
        "ix_task_recurrences_user_active",
        table_name="task_recurrences",
    )
    op.drop_table("task_recurrences")
