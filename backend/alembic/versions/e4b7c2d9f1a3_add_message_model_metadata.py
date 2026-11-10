"""add message model metadata

Revision ID: e4b7c2d9f1a3
Revises: c8e1f3a5d7b9
Create Date: 2026-11-10 00:00:00.000000

Adds the response model name to persisted assistant messages. The column is
nullable, so existing message rows remain valid and no data is rewritten.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4b7c2d9f1a3"
down_revision: Union[str, Sequence[str], None] = "c8e1f3a5d7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable assistant model name column."""

    op.add_column(
        "messages",
        sa.Column("model", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    """Remove the assistant model name column."""

    op.drop_column("messages", "model")
