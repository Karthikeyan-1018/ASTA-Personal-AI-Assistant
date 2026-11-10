"""add message response metadata

Revision ID: f3a1c9d7e5b2
Revises: 9b7e4c1a2f60
Create Date: 2026-11-09 00:00:00.000000

Adds optional response metadata to persisted chat messages. Both columns are
non-destructive: ``provider`` is nullable and ``is_private`` has a server
default, so existing message rows remain valid.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3a1c9d7e5b2"
down_revision: Union[str, Sequence[str], None] = "9b7e4c1a2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add provider and privacy metadata to messages."""

    op.add_column(
        "messages",
        sa.Column("provider", sa.String(length=50), nullable=True)
    )

    op.add_column(
        "messages",
        sa.Column(
            "is_private",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        )
    )


def downgrade() -> None:
    """Remove message response metadata."""

    op.drop_column("messages", "is_private")
    op.drop_column("messages", "provider")
