"""Phase 9 structured AI usage records.

Rows contain metadata and token counts only. Prompt text, response text,
credentials, and provider error messages are never stored.

The table is additive and does not modify existing domain tables.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AIUsageRecord(Base):
    """One LLM request outcome for usage and performance reporting."""

    __tablename__ = "ai_usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Nullable so internal requests (for example classification) can be
    # recorded even before a trusted user is available.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    request_type: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="chat",
        index=True,
    )

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

    cache_hit: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    fallback_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    error_type: Mapped[str | None] = mapped_column(
        String(60),
        nullable=True,
    )

    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # "provider" when the provider reported counts, "estimated" otherwise.
    token_source: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    cost_usd: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index(
            "ix_ai_usage_records_user_created",
            "user_id",
            "created_at",
        ),
    )
