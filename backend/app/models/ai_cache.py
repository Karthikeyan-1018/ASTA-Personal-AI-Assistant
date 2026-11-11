"""Phase 9 semantic response cache storage.

Cache entries are strictly user-scoped. A row stores a normalised query hash,
an optional query embedding, and the generated answer. Raw prompts are never
stored. Private requests are never written here; the chat service enforces that
rule before calling the cache service.

The table is additive. It does not alter existing conversations, messages,
memories, preferences, or tasks.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.database.base import Base


class AIResponseCache(Base):
    """A cached assistant answer for one user and normalised query."""

    __tablename__ = "ai_response_cache"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 of the normalised query. The raw prompt is not persisted.
    query_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSION),
        nullable=True,
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    response: Mapped[str] = mapped_column(Text, nullable=False)

    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    model: Mapped[str | None] = mapped_column(String(150), nullable=True)

    complexity: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    routing_policy: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    prompt_version: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="phase9-v1",
    )

    config_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="1",
    )

    hit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "query_hash",
            "prompt_version",
            "config_version",
            name="uq_ai_response_cache_user_query_version",
        ),
        Index(
            "ix_ai_response_cache_user_expires",
            "user_id",
            "expires_at",
        ),
    )
