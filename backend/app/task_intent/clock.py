"""Injectable clock used by the task-intent components.

Tests patch this function so relative date resolution, confirmation expiry, and
pending-action timestamps stay deterministic without hardcoding a date.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current timezone-aware UTC instant."""

    return datetime.now(timezone.utc)
