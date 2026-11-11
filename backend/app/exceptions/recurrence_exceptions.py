"""Errors raised by recurring-task operations."""

from __future__ import annotations


class RecurrenceError(Exception):
    """Base class for recurrence-specific failures."""


class RecurrenceNotFoundError(RecurrenceError):
    """The recurrence does not exist or is not owned by the caller."""


class RecurrenceValidationError(RecurrenceError):
    """The supplied recurrence data is invalid."""


class RecurrenceConflictError(RecurrenceError):
    """The recurrence conflicts with an existing rule or its state."""
