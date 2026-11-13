"""add message tool metadata

Revision ID: a1b2c3d4e5f6
Revises: e6f7a8b9c0d1
Create Date: 2026-11-12 00:00:00.000000

Adds an optional JSON column for safe, structured message metadata. Phase 11H
uses it to persist assistant tool-call and tool-result transcript entries
without storing argument values, tool output, or credentials. The column is
nullable, so existing message rows remain valid and no data is rewritten.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable message metadata column."""

    op.add_column(
        "messages",
        sa.Column("message_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove the message metadata column."""

    op.drop_column("messages", "message_metadata")
