from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class MemoryStatus(str, enum.Enum):
    """Lifecycle states for a long-term memory."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Memory(Base):
    """A long-term fact or preference belonging to a user."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="general"
    )

    status: Mapped[MemoryStatus] = mapped_column(
        SAEnum(
            MemoryStatus,
            name="memory_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ]
        ),
        nullable=False,
        default=MemoryStatus.PENDING,
        server_default=MemoryStatus.PENDING.value
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="memories")
