"""resize memory embeddings to 768 dimensions

Revision ID: 9b7e4c1a2f60
Revises: c4a9e2f7b1d3
Create Date: 2026-11-08 00:00:00.000000

The configured model ``intfloat/multilingual-e5-base`` produces 768-dimensional
vectors, so the semantic storage column must match. This migration only alters
the embedding column's typmod and rebuilds the partial HNSW index; it never
drops tables or touches non-embedding columns.

This migration assumes existing embeddings are dimensionally compatible with
the target type. It intentionally fails instead of truncating or discarding any
stored vector. The column must be empty of embeddings of the previous
dimension before this migration is applied.
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "9b7e4c1a2f60"
down_revision: Union[str, Sequence[str], None] = "c4a9e2f7b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PREVIOUS_DIMENSION = 1024
EMBEDDING_DIMENSION = 768
EMBEDDING_INDEX = "ix_memories_embedding_hnsw"


def _create_partial_hnsw_index() -> None:
    """Recreate the approved-only cosine HNSW index."""

    op.execute(
        f"CREATE INDEX {EMBEDDING_INDEX} "
        "ON memories USING hnsw (embedding vector_cosine_ops) "
        "WHERE status = 'approved' AND embedding IS NOT NULL"
    )


def upgrade() -> None:
    """Change ``memories.embedding`` from vector(1024) to vector(768)."""

    op.execute(f"DROP INDEX IF EXISTS {EMBEDDING_INDEX}")

    op.alter_column(
        "memories",
        "embedding",
        existing_type=Vector(PREVIOUS_DIMENSION),
        type_=Vector(EMBEDDING_DIMENSION),
        existing_nullable=True
    )

    _create_partial_hnsw_index()


def downgrade() -> None:
    """Restore ``memories.embedding`` to vector(1024).

    A downgrade fails safely if 768-dimensional embeddings are still stored:
    PostgreSQL rejects the type change rather than truncating vectors.
    """

    op.execute(f"DROP INDEX IF EXISTS {EMBEDDING_INDEX}")

    op.alter_column(
        "memories",
        "embedding",
        existing_type=Vector(EMBEDDING_DIMENSION),
        type_=Vector(PREVIOUS_DIMENSION),
        existing_nullable=True
    )

    _create_partial_hnsw_index()
