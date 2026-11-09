"""add semantic embedding storage to memories

Revision ID: c4a9e2f7b1d3
Revises: 407d2eb450f0
Create Date: 2026-11-08 00:00:00.000000

This migration requires the PostgreSQL ``vector`` extension to be installed
on the server. It does not drop or recreate existing tables and keeps the new
columns nullable so existing memories remain valid until backfilled.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "c4a9e2f7b1d3"
down_revision: Union[str, Sequence[str], None] = "407d2eb450f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_DIMENSION = 1024
EMBEDDING_INDEX = "ix_memories_embedding_hnsw"


def upgrade() -> None:
    """Enable pgvector, add embedding columns, and create a partial index."""

    # Fails clearly if the server package is missing; it never drops data.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "memories",
        sa.Column(
            "embedding",
            Vector(EMBEDDING_DIMENSION),
            nullable=True
        )
    )
    op.add_column(
        "memories",
        sa.Column(
            "embedding_model",
            sa.String(length=255),
            nullable=True
        )
    )
    op.add_column(
        "memories",
        sa.Column(
            "embedding_updated_at",
            sa.DateTime(timezone=True),
            nullable=True
        )
    )

    # Only approved, indexed memories participate in semantic retrieval.
    # HNSW with cosine distance matches the retriever's cosine similarity.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {EMBEDDING_INDEX} "
        "ON memories USING hnsw (embedding vector_cosine_ops) "
        "WHERE status = 'approved' AND embedding IS NOT NULL"
    )


def downgrade() -> None:
    """Remove semantic storage without touching the shared vector extension."""

    op.execute(f"DROP INDEX IF EXISTS {EMBEDDING_INDEX}")

    op.drop_column("memories", "embedding_updated_at")
    op.drop_column("memories", "embedding_model")
    op.drop_column("memories", "embedding")

    # The extension is shared database infrastructure and may be used by
    # other tables or applications, so it is intentionally left installed.
