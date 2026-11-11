"""Notification preferences, quiet-hour windows, and delivery deferral.

Quiet hours are never used to discard a notification. When a reminder becomes
due inside the owner's quiet window, delivery is deferred to the first instant
after the window ends. The window is evaluated in the owner's IANA timezone, so
windows that cross midnight and daylight-saving transitions behave correctly.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.notification_exceptions import (
    NotificationValidationError,
)
from app.models.notification_preference import NotificationPreference
from app.services.task_deadlines import (
    canonical_timezone,
    resolve_timezone,
    utcnow,
)


logger = logging.getLogger(__name__)

UTC = timezone.utc
DEFAULT_START = time(22, 0)
DEFAULT_END = time(7, 0)
DEFAULT_TIMEZONE = "UTC"


def _utcnow() -> datetime:
    """Clock hook used by the service; tests can patch this function."""

    return utcnow()


def _parse_setting_time(raw: str | None, fallback: time) -> time:
    value = (raw or "").strip()

    try:
        parsed = time.fromisoformat(value)

    except ValueError:
        logger.warning("Invalid quiet-hour setting %r; using %s.", raw, fallback)
        return fallback

    return parsed.replace(tzinfo=None)


def _default_start() -> time:
    return _parse_setting_time(
        settings.NOTIFICATION_QUIET_HOURS_DEFAULT_START,
        DEFAULT_START,
    )


def _default_end() -> time:
    return _parse_setting_time(
        settings.NOTIFICATION_QUIET_HOURS_DEFAULT_END,
        DEFAULT_END,
    )


def _default_timezone() -> str:
    raw = settings.NOTIFICATION_QUIET_HOURS_DEFAULT_TIMEZONE or DEFAULT_TIMEZONE

    try:
        return canonical_timezone(raw) or DEFAULT_TIMEZONE

    except Exception:
        logger.warning(
            "Invalid NOTIFICATION_QUIET_HOURS_DEFAULT_TIMEZONE %r; using UTC.",
            raw,
        )
        return DEFAULT_TIMEZONE


def _canonical_preference_timezone(value: str | None) -> str:
    cleaned = (value or "").strip()

    if not cleaned:
        return _default_timezone()

    try:
        canonical = canonical_timezone(cleaned)

    except Exception as error:
        raise NotificationValidationError(
            "timezone must be a recognised IANA timezone name."
        ) from error

    return canonical or _default_timezone()


def _normalise_time(value: time | str) -> time:
    if isinstance(value, time):
        parsed = value
    else:
        try:
            parsed = time.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as error:
            raise NotificationValidationError(
                "Quiet-hour times must use HH:MM format."
            ) from error

    if parsed.tzinfo is not None:
        raise NotificationValidationError(
            "Quiet-hour times must be wall-clock values without a timezone."
        )

    return parsed.replace(tzinfo=None)


def _is_active(local_now: time, start: time, end: time) -> bool:
    if start == end:
        # A zero-length window is treated as "quiet hours off" rather than a
        # permanent deferral that would never deliver anything.
        return False

    if start < end:
        return start <= local_now < end

    return local_now >= start or local_now < end


class NotificationPreferenceService:
    """Read and update owner-scoped notification preferences."""

    async def get_preferences(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> NotificationPreference:
        """Return stored preferences or a transient defaults object."""

        pref = await session.scalar(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
            )
        )

        if pref is not None:
            return pref

        # The object is intentionally not added to the session. Reads must not
        # create rows; the first explicit update persists the defaults.
        return NotificationPreference(
            id=None,
            user_id=user_id,
            in_app_enabled=True,
            quiet_hours_enabled=settings.NOTIFICATION_QUIET_HOURS_DEFAULT_ENABLED,
            quiet_hours_start=_default_start(),
            quiet_hours_end=_default_end(),
            timezone=_default_timezone(),
            created_at=None,
            updated_at=None,
        )

    async def update_preferences(
        self,
        session: AsyncSession,
        user_id: int,
        changes: dict[str, Any],
        now: datetime | None = None,
    ) -> NotificationPreference:
        """Validate and persist a partial preference update."""

        allowed = {
            "in_app_enabled",
            "quiet_hours_enabled",
            "quiet_hours_start",
            "quiet_hours_end",
            "timezone",
        }

        unknown = set(changes) - allowed

        if unknown:
            raise NotificationValidationError(
                "Unsupported preference field(s): "
                + ", ".join(sorted(unknown))
                + "."
            )

        pref = await session.scalar(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
            )
        )

        if pref is None:
            pref = NotificationPreference(
                user_id=user_id,
                in_app_enabled=True,
                quiet_hours_enabled=(
                    settings.NOTIFICATION_QUIET_HOURS_DEFAULT_ENABLED
                ),
                quiet_hours_start=_default_start(),
                quiet_hours_end=_default_end(),
                timezone=_default_timezone(),
            )
            session.add(pref)

        if "in_app_enabled" in changes:
            pref.in_app_enabled = bool(changes["in_app_enabled"])

        if "quiet_hours_enabled" in changes:
            pref.quiet_hours_enabled = bool(changes["quiet_hours_enabled"])

        if "quiet_hours_start" in changes:
            pref.quiet_hours_start = _normalise_time(
                changes["quiet_hours_start"]
            )

        if "quiet_hours_end" in changes:
            pref.quiet_hours_end = _normalise_time(
                changes["quiet_hours_end"]
            )

        if "timezone" in changes:
            pref.timezone = _canonical_preference_timezone(
                changes["timezone"]
            )

        if (
            pref.quiet_hours_enabled
            and pref.quiet_hours_start == pref.quiet_hours_end
        ):
            raise NotificationValidationError(
                "quiet_hours_start and quiet_hours_end must differ."
            )

        try:
            await session.commit()
            await session.refresh(pref)

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update notification preferences for user %s.",
                user_id,
            )
            raise

        return pref

    def effective_timezone(self, pref: NotificationPreference) -> str:
        return _canonical_preference_timezone(pref.timezone)

    def is_quiet_hours_active(
        self,
        pref: NotificationPreference,
        now: datetime | None = None,
    ) -> bool:
        if not pref.quiet_hours_enabled:
            return False

        current = (now or _utcnow()).astimezone(UTC)
        zone = resolve_timezone(self.effective_timezone(pref))
        local_now = current.astimezone(zone).time()

        return _is_active(
            local_now,
            pref.quiet_hours_start,
            pref.quiet_hours_end,
        )

    def quiet_hours_end(
        self,
        pref: NotificationPreference,
        now: datetime | None = None,
    ) -> datetime | None:
        """Return the next quiet-window end in UTC, or ``None``.

        ``None`` means the instant is outside the quiet window. The returned
        instant is always strictly in the future.
        """

        if not pref.quiet_hours_enabled:
            return None

        current = (now or _utcnow()).astimezone(UTC)
        zone = resolve_timezone(self.effective_timezone(pref))
        local_now = current.astimezone(zone)
        start = pref.quiet_hours_start
        end = pref.quiet_hours_end

        if not _is_active(local_now.time(), start, end):
            return None

        if start < end:
            end_date: date = local_now.date()
        elif local_now.time() >= start:
            end_date = local_now.date() + timedelta(days=1)
        else:
            end_date = local_now.date()

        local_end = datetime.combine(end_date, end, tzinfo=zone)
        end_utc = local_end.astimezone(UTC)

        if end_utc <= current:
            end_date = end_date + timedelta(days=1)
            end_utc = datetime.combine(
                end_date,
                end,
                tzinfo=zone,
            ).astimezone(UTC)

        return end_utc


notification_preference_service = NotificationPreferenceService()
