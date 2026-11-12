"""In-app notifications and delivery metadata.

Notifications are snapshots. They keep their own title and body so history
survives deletion of the originating task or reminder. The foreign keys use
``ON DELETE SET NULL`` rather than cascade deletion.

A notification is considered delivered for the in-app channel when its row is
successfully committed. The delivery status and retry fields leave room for
future channels without changing the stored shape.

Snoozing re-arms the associated reminder instead of creating a second
notification. The notification row records the last snooze instant, the next
scheduled delivery instant, and a cumulative counter for audit and limit
enforcement. On re-delivery the same row becomes unread again.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

if TYPE_CHECKING:
    pass


class NotificationType(str, enum.Enum):
    """Supported notification sources."""

    REMINDER = "reminder"
    SYSTEM = "system"


class NotificationDeliveryStatus(str, enum.Enum):
    """Delivery state for a notification."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


def _notification_enum(enum_cls, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=_enum_values,
    )


class Notification(Base):
    """An owner-scoped in-app notification."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Historical notifications must survive task and reminder deletion.
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    reminder_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_reminders.id", ondelete="SET NULL"),
        nullable=True,
    )

    notification_type: Mapped[NotificationType] = mapped_column(
        _notification_enum(NotificationType, "notification_type"),
        nullable=False,
        default=NotificationType.REMINDER,
        server_default=NotificationType.REMINDER.value,
    )

    delivery_status: Mapped[NotificationDeliveryStatus] = mapped_column(
        _notification_enum(
            NotificationDeliveryStatus,
            "notification_delivery_status",
        ),
        nullable=False,
        default=NotificationDeliveryStatus.PENDING,
        server_default=NotificationDeliveryStatus.PENDING.value,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Snapshot of the task title at delivery time. It contains no description,
    # conversation content, memory, or credential material.
    task_title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Snooze audit metadata. Snoozing never creates a second notification: the
    # associated reminder is re-armed and this row is reused for re-delivery.
    snoozed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    snooze_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    dedupe_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_notifications_attempt_count_non_negative",
        ),
        CheckConstraint(
            "snooze_count >= 0",
            name="ck_notifications_snooze_count_non_negative",
        ),
        UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_notifications_user_dedupe",
        ),
        Index(
            "ix_notifications_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_notifications_user_read_created",
            "user_id",
            "is_read",
            "created_at",
        ),
        Index(
            "ix_notifications_delivery",
            "delivery_status",
            "next_attempt_at",
        ),
        Index("ix_notifications_created_at", "created_at"),
    )


__all__ = [
    "Notification",
    "NotificationDeliveryStatus",
    "NotificationType",
]
