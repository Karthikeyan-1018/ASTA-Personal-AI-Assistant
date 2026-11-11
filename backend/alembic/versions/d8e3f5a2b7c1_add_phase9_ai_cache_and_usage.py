"""add Phase 9 response cache and AI usage tracking

Revision ID: d8e3f5a2b7c1
Revises: c7d2e8f4a1b6
Create Date: 2026-11-10 00:00:00.000000

Creates two additive tables:

* ``ai_response_cache`` stores per-user cached answers. Raw prompts are never
  stored; only a SHA-256 query hash and an optional pgvector embedding are
  kept. Private requests are never written here.
* ``ai_usage_records`` stores metadata and token counts only. Prompt text,
  response text, credentials, and provider error messages are never stored.

No existing table is altered, dropped, or recreated. The pgvector extension is
already enabled by the earlier semantic-memory migrations.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "d8e3f5a2b7c1"
down_revision: Union[str, Sequence[str], None] = "c7d2e8f4a1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_DIMENSION = 768


def upgrade() -> None:
    """Create the response-cache and usage-tracking tables."""

    op.create_table(
        "ai_response_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "embedding",
            Vector(EMBEDDING_DIMENSION),
            nullable=True,
        ),
        sa.Column(
            "embedding_model",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=150), nullable=True),
        sa.Column("complexity", sa.String(length=40), nullable=True),
        sa.Column(
            "routing_policy",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=60),
            server_default="phase9-v1",
            nullable=False,
        ),
        sa.Column(
            "config_version",
            sa.String(length=40),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "hit_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "query_hash",
            "prompt_version",
            "config_version",
            name="uq_ai_response_cache_user_query_version",
        ),
    )

    op.create_index(
        "ix_ai_response_cache_user_id",
        "ai_response_cache",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_response_cache_query_hash",
        "ai_response_cache",
        ["query_hash"],
        unique=False,
    )
    op.create_index(
        "ix_ai_response_cache_expires_at",
        "ai_response_cache",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_response_cache_user_expires",
        "ai_response_cache",
        ["user_id", "expires_at"],
        unique=False,
    )

    op.create_table(
        "ai_usage_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "request_type",
            sa.String(length=60),
            server_default="chat",
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=150), nullable=True),
        sa.Column("complexity", sa.String(length=40), nullable=True),
        sa.Column(
            "routing_policy",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column(
            "cache_hit",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "fallback_used",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "success",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("error_type", sa.String(length=60), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("token_source", sa.String(length=20), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_ai_usage_records_user_id",
        "ai_usage_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_usage_records_request_type",
        "ai_usage_records",
        ["request_type"],
        unique=False,
    )
    op.create_index(
        "ix_ai_usage_records_created_at",
        "ai_usage_records",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_usage_records_user_created",
        "ai_usage_records",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the Phase 9 tables and their indexes."""

    op.drop_index(
        "ix_ai_usage_records_user_created",
        table_name="ai_usage_records",
    )
    op.drop_index(
        "ix_ai_usage_records_created_at",
        table_name="ai_usage_records",
    )
    op.drop_index(
        "ix_ai_usage_records_request_type",
        table_name="ai_usage_records",
    )
    op.drop_index(
        "ix_ai_usage_records_user_id",
        table_name="ai_usage_records",
    )
    op.drop_table("ai_usage_records")

    op.drop_index(
        "ix_ai_response_cache_user_expires",
        table_name="ai_response_cache",
    )
    op.drop_index(
        "ix_ai_response_cache_expires_at",
        table_name="ai_response_cache",
    )
    op.drop_index(
        "ix_ai_response_cache_query_hash",
        table_name="ai_response_cache",
    )
    op.drop_index(
        "ix_ai_response_cache_user_id",
        table_name="ai_response_cache",
    )
    op.drop_table("ai_response_cache")
