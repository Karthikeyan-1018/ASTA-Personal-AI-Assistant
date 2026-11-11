"""Per-owner in-app notification and quiet-hour preferences.

A missing row means the deployment defaults apply. Quiet hours are stored as
wall-clock times plus an IANA timezone so windows that cross midnight remain
unambiguous and daylight-saving transitions are handled by ``zoneinfo``.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Time,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

if TYPE_CHECKING:
    pass


class NotificationPreference(Base):
    """One preference row per user."""

    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    in_app_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    quiet_hours_start: Mapped[time] = mapped_column(
        Time(timezone=False),
        nullable=False,
        default=time(22, 0),
        server_default="22:00:00",
    )

    quiet_hours_end: Mapped[time] = mapped_column(
        Time(timezone=False),
        nullable=False,
        default=time(7, 0),
        server_default="07:00:00",
    )

    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="UTC",
        server_default="UTC",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            name="uq_notification_preferences_user",
        ),
        Index(
            "ix_notification_preferences_user",
            "user_id",
        ),
    )


__all__ = ["NotificationPreference"]
