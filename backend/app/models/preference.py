from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class PreferenceCategory(str, enum.Enum):
    """Stable categories for user preferences."""

    COMMUNICATION = "communication"
    LEARNING = "learning"
    PROGRAMMING = "programming"
    CAREER = "career"
    PRODUCTIVITY = "productivity"
    GENERAL = "general"


class PreferenceStatus(str, enum.Enum):
    """Lifecycle states for a stored user preference."""

    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    REJECTED = "rejected"


class PreferenceSource(str, enum.Enum):
    """How a preference entered the system."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"


class UserPreference(Base):
    """A structured, user-owned preference."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    value: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=PreferenceCategory.GENERAL.value,
        server_default=PreferenceCategory.GENERAL.value
    )

    status: Mapped[PreferenceStatus] = mapped_column(
        SAEnum(
            PreferenceStatus,
            name="preference_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ]
        ),
        nullable=False,
        default=PreferenceStatus.PENDING,
        server_default=PreferenceStatus.PENDING.value
    )

    source: Mapped[PreferenceSource] = mapped_column(
        SAEnum(
            PreferenceSource,
            name="preference_source",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ]
        ),
        nullable=False,
        default=PreferenceSource.EXPLICIT,
        server_default=PreferenceSource.EXPLICIT.value
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

    user: Mapped["User"] = relationship(back_populates="preferences")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "key",
            name="uq_user_preferences_user_id_key"
        ),
        Index(
            "ix_user_preferences_user_status",
            "user_id",
            "status"
        ),
        Index(
            "ix_user_preferences_user_category",
            "user_id",
            "category"
        ),
    )
