"""Errors raised by task reminder operations."""

from __future__ import annotations


class ReminderError(Exception):
    """Base class for reminder-specific failures."""


class ReminderNotFoundError(ReminderError):
    """The reminder does not exist or is not owned by the caller."""


class ReminderValidationError(ReminderError):
    """The supplied reminder data is invalid."""


class ReminderConflictError(ReminderError):
    """The reminder conflicts with its current state or an existing row."""
