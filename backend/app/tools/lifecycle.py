"""Execution lifecycle states and legal transitions.

A tool execution moves through a small, explicit state machine. Persisting the
lifecycle separately from the coarse ``status`` keeps the existing history API
stable while making duplicate or contradictory state changes detectable.

The transition rules are intentionally conservative:

* terminal outcomes cannot be overwritten by a different terminal outcome;
* a confirmation can only be approved, rejected, expired, or cancelled while it
  is awaiting a decision;
* an execution must be running before it can succeed, fail, or time out;
* a caller cancellation can interrupt any non-terminal state.

No transition here authorizes anything. Permission and confirmation decisions
remain in the executor and confirmation service.
"""

from __future__ import annotations

from typing import Any

from app.tools.enums import (
    ConfirmationState,
    ToolExecutionLifecycle,
    ToolExecutionStatus,
)


TERMINAL_LIFECYCLES = frozenset(
    {
        ToolExecutionLifecycle.SUCCEEDED,
        ToolExecutionLifecycle.FAILED,
        ToolExecutionLifecycle.REJECTED,
        ToolExecutionLifecycle.EXPIRED,
        ToolExecutionLifecycle.CANCELLED,
        ToolExecutionLifecycle.TIMED_OUT,
    }
)

# ``awaiting_confirmation`` is stable but not terminal: a confirmation can still
# be approved, rejected, or expired. The current executor records the approved
# attempt as a separate execution row, so it normally stays unchanged.
LEGAL_TRANSITIONS: dict[
    ToolExecutionLifecycle, frozenset[ToolExecutionLifecycle]
] = {
    ToolExecutionLifecycle.PENDING: frozenset(
        {
            ToolExecutionLifecycle.AWAITING_CONFIRMATION,
            ToolExecutionLifecycle.APPROVED,
            ToolExecutionLifecycle.RUNNING,
            ToolExecutionLifecycle.FAILED,
            ToolExecutionLifecycle.REJECTED,
            ToolExecutionLifecycle.EXPIRED,
            ToolExecutionLifecycle.CANCELLED,
            ToolExecutionLifecycle.SUCCEEDED,
            ToolExecutionLifecycle.TIMED_OUT,
        }
    ),
    ToolExecutionLifecycle.AWAITING_CONFIRMATION: frozenset(
        {
            ToolExecutionLifecycle.APPROVED,
            ToolExecutionLifecycle.REJECTED,
            ToolExecutionLifecycle.EXPIRED,
            ToolExecutionLifecycle.CANCELLED,
        }
    ),
    ToolExecutionLifecycle.APPROVED: frozenset(
        {
            ToolExecutionLifecycle.RUNNING,
            ToolExecutionLifecycle.FAILED,
            ToolExecutionLifecycle.REJECTED,
            ToolExecutionLifecycle.EXPIRED,
            ToolExecutionLifecycle.CANCELLED,
            ToolExecutionLifecycle.SUCCEEDED,
            ToolExecutionLifecycle.TIMED_OUT,
        }
    ),
    ToolExecutionLifecycle.RUNNING: frozenset(
        {
            ToolExecutionLifecycle.SUCCEEDED,
            ToolExecutionLifecycle.FAILED,
            ToolExecutionLifecycle.TIMED_OUT,
            ToolExecutionLifecycle.CANCELLED,
        }
    ),
    ToolExecutionLifecycle.SUCCEEDED: frozenset(),
    ToolExecutionLifecycle.FAILED: frozenset(),
    ToolExecutionLifecycle.REJECTED: frozenset(),
    ToolExecutionLifecycle.EXPIRED: frozenset(),
    ToolExecutionLifecycle.CANCELLED: frozenset(),
    ToolExecutionLifecycle.TIMED_OUT: frozenset(),
}

IN_FLIGHT_LIFECYCLES = frozenset(
    {
        ToolExecutionLifecycle.PENDING,
        ToolExecutionLifecycle.APPROVED,
        ToolExecutionLifecycle.RUNNING,
    }
)


class LifecycleTransitionError(ValueError):
    """Raised when an execution would enter an illegal lifecycle state."""

    def __init__(
        self,
        current: ToolExecutionLifecycle,
        requested: ToolExecutionLifecycle,
    ) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"Illegal tool execution transition: "
            f"{current.value} -> {requested.value}."
        )


def normalize_lifecycle(value: Any) -> ToolExecutionLifecycle:
    """Coerce a stored or caller-supplied value into a lifecycle enum."""

    if isinstance(value, ToolExecutionLifecycle):
        return value

    try:
        return ToolExecutionLifecycle(value)

    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Unknown tool execution lifecycle: {value!r}."
        ) from error


def is_terminal(value: ToolExecutionLifecycle) -> bool:
    return normalize_lifecycle(value) in TERMINAL_LIFECYCLES


def can_transition(
    current: Any,
    requested: Any,
    *,
    allow_same: bool = True,
) -> bool:
    """Return whether ``current -> requested`` is a legal transition."""

    current_state = normalize_lifecycle(current)
    requested_state = normalize_lifecycle(requested)

    if current_state == requested_state:
        return allow_same

    return requested_state in LEGAL_TRANSITIONS[current_state]


def validate_transition(
    current: Any,
    requested: Any,
    *,
    allow_same: bool = True,
) -> ToolExecutionLifecycle:
    """Validate a transition and return the requested state.

    Idempotent writes are allowed by default. A terminal state may be written
    again only when the requested state is identical, which makes retries and
    replay reporting safe without permitting contradictory updates.
    """

    current_state = normalize_lifecycle(current)
    requested_state = normalize_lifecycle(requested)

    if current_state == requested_state and allow_same:
        return requested_state

    if requested_state not in LEGAL_TRANSITIONS[current_state]:
        raise LifecycleTransitionError(current_state, requested_state)

    return requested_state


def lifecycle_from_status(
    status: Any,
    *,
    confirmation_state: Any = None,
    error_code: str | None = None,
) -> ToolExecutionLifecycle:
    """Map a coarse outcome to the lifecycle state used by history rows."""

    try:
        normalized_status = (
            status
            if isinstance(status, ToolExecutionStatus)
            else ToolExecutionStatus(status)
        )

    except (TypeError, ValueError):
        normalized_status = ToolExecutionStatus.ERROR

    try:
        normalized_confirmation = (
            confirmation_state
            if isinstance(confirmation_state, ConfirmationState)
            else ConfirmationState(
                confirmation_state or ConfirmationState.NOT_REQUIRED
            )
        )

    except (TypeError, ValueError):
        normalized_confirmation = ConfirmationState.NOT_REQUIRED

    if normalized_status == ToolExecutionStatus.SUCCESS:
        return ToolExecutionLifecycle.SUCCEEDED

    if normalized_status == ToolExecutionStatus.TIMEOUT:
        return ToolExecutionLifecycle.TIMED_OUT

    if normalized_status == ToolExecutionStatus.CONFIRMATION_REQUIRED:
        return ToolExecutionLifecycle.AWAITING_CONFIRMATION

    if normalized_status == ToolExecutionStatus.DENIED:
        if normalized_confirmation == ConfirmationState.EXPIRED:
            return ToolExecutionLifecycle.EXPIRED

        return ToolExecutionLifecycle.REJECTED

    if normalized_status == ToolExecutionStatus.CANCELLED:
        return ToolExecutionLifecycle.CANCELLED

    if error_code == "ToolCancelled":
        return ToolExecutionLifecycle.CANCELLED

    if normalized_status in (
        ToolExecutionStatus.ERROR,
        ToolExecutionStatus.PENDING,
        ToolExecutionStatus.RUNNING,
    ):
        return ToolExecutionLifecycle.FAILED

    return ToolExecutionLifecycle.FAILED


__all__ = [
    "IN_FLIGHT_LIFECYCLES",
    "LEGAL_TRANSITIONS",
    "LifecycleTransitionError",
    "TERMINAL_LIFECYCLES",
    "can_transition",
    "is_terminal",
    "lifecycle_from_status",
    "normalize_lifecycle",
    "validate_transition",
]
