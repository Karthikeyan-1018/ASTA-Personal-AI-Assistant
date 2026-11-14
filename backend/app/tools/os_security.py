"""Central OS automation security policy (Phase 12F).

This module is deliberately *not* a second permission engine. It is a narrow,
additive policy layer that the existing ``PermissionPolicy`` and
``ToolExecutor`` consult for built-in operating-system automation tools. The
existing authorization, confirmation, validation, history, and execution flow
remains the only execution path.

The layer provides four things that were previously missing:

* one master switch (``TOOL_OS_AUTOMATION_ENABLED``) in addition to the
  per-capability flags;
* an authenticated-owner emergency stop that is not registered as an LLM tool;
* conservative, owner-isolated, server-side rate limits; and
* a small configuration validator.

Everything fails closed. An unset or malformed flag disables the affected
capability rather than enabling it.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.tools.enums import ToolCategory, ToolErrorCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolDisabledError,
    ToolEmergencyStopError,
    ToolPermissionError,
    ToolRateLimitError,
)


logger = logging.getLogger(__name__)


# Capability marker -> per-capability configuration flag. The flag names are the
# existing Phase 12 names; Phase 12F only adds the master switch and the
# emergency/rate-limit controls.
OS_CAPABILITY_SETTINGS: dict[str, str] = {
    "system_info": "TOOL_SYSTEM_INFO_ENABLED",
    "application_launch": "TOOL_APPLICATION_LAUNCH_ENABLED",
    "filesystem": "TOOL_FILESYSTEM_ENABLED",
    "process_inspection": "TOOL_PROCESS_INSPECTION_ENABLED",
    "clipboard": "TOOL_CLIPBOARD_ENABLED",
    "keyboard": "TOOL_KEYBOARD_ENABLED",
    "mouse": "TOOL_MOUSE_ENABLED",
}

OS_CAPABILITIES = frozenset(OS_CAPABILITY_SETTINGS)

DEFAULT_MAX_CALLS_PER_MINUTE = 30
DEFAULT_MAX_STATE_CHANGES_PER_MINUTE = 10
DEFAULT_MAX_CALLS_PER_HOUR = 300

_MINUTE = 60.0
_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class OsPolicyDecision:
    """A non-mutating OS policy decision."""

    allowed: bool
    reason: str | None = None
    error_category: ToolErrorCategory | None = None


@dataclass(frozen=True, slots=True)
class EmergencyStopState:
    """Read-only snapshot of the process-local emergency stop."""

    engaged: bool
    engaged_at: datetime | None
    engaged_by_user_id: int | None
    recovery_configured: bool


def capability_of(tool: Any) -> str | None:
    """Return the OS capability marker for ``tool``, or ``None``."""

    capability = getattr(tool, "os_capability", None)

    if isinstance(capability, str) and capability in OS_CAPABILITIES:
        return capability

    return None


def is_os_tool(tool: Any) -> bool:
    """Return whether ``tool`` belongs to a Phase 12 OS automation surface."""

    return capability_of(tool) is not None


def is_state_changing(tool: Any) -> bool:
    """Return whether executing ``tool`` may change operating-system state."""

    return getattr(tool, "category", None) != ToolCategory.READ_ONLY


def _flag(name: str, default: bool = False) -> bool:
    try:
        return bool(getattr(settings, name, default))

    except Exception:
        # A broken settings object must not enable a capability.
        return default


def _bounded_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(getattr(settings, name, default))

    except (TypeError, ValueError, AttributeError):
        return default

    return max(minimum, value)


def evaluate(tool: Any) -> OsPolicyDecision:
    """Apply the static OS policy checks for one tool.

    The result is side-effect free so it is safe to call before a confirmation
    proposal, after an approval, and again immediately before execution.
    """

    capability = capability_of(tool)

    if capability is None:
        return OsPolicyDecision(allowed=True)

    if not _flag("TOOL_OS_AUTOMATION_ENABLED"):
        return OsPolicyDecision(
            allowed=False,
            reason="OS automation is disabled by the deployment policy.",
            error_category=ToolErrorCategory.DISABLED,
        )

    if not _flag(OS_CAPABILITY_SETTINGS[capability]):
        return OsPolicyDecision(
            allowed=False,
            reason=f"The '{capability}' OS capability is disabled.",
            error_category=ToolErrorCategory.DISABLED,
        )

    if is_emergency_stop_engaged():
        return OsPolicyDecision(
            allowed=False,
            reason="OS automation is blocked by the emergency stop.",
            error_category=ToolErrorCategory.PERMISSION,
        )

    # Fail closed on a future programming mistake: a state-changing OS tool
    # must never execute without the confirmation path.
    if is_state_changing(tool) and not bool(
        getattr(tool, "requires_confirmation", False)
    ):
        return OsPolicyDecision(
            allowed=False,
            reason=(
                "A state-changing OS tool must require explicit confirmation."
            ),
            error_category=ToolErrorCategory.CONFIGURATION,
        )

    return OsPolicyDecision(allowed=True)


def raise_for_decision(decision: OsPolicyDecision) -> None:
    """Raise the structured exception matching a denied decision."""

    if decision.allowed:
        return

    reason = decision.reason or "OS automation is not permitted."

    if decision.error_category == ToolErrorCategory.DISABLED:
        raise ToolDisabledError(reason)

    if decision.error_category == ToolErrorCategory.CONFIGURATION:
        raise ToolConfigurationError(reason)

    if is_emergency_stop_engaged():
        raise ToolEmergencyStopError(reason)

    raise ToolPermissionError(reason)


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


_stop_lock = threading.Lock()
_stop_engaged = False
_stop_engaged_at: datetime | None = None
_stop_engaged_by: int | None = None


def _recovery_configured() -> bool:
    return bool(
        str(
            getattr(settings, "TOOL_OS_EMERGENCY_RECOVERY_TOKEN", "") or ""
        ).strip()
    )


def is_emergency_stop_engaged() -> bool:
    """Return whether the process-local emergency stop is active."""

    with _stop_lock:
        return _stop_engaged


def emergency_stop_state() -> EmergencyStopState:
    """Return a snapshot of the emergency stop without exposing any token."""

    with _stop_lock:
        return EmergencyStopState(
            engaged=_stop_engaged,
            engaged_at=_stop_engaged_at,
            engaged_by_user_id=_stop_engaged_by,
            recovery_configured=_recovery_configured(),
        )


def engage_emergency_stop(*, user_id: int) -> EmergencyStopState:
    """Engage the emergency stop for the authenticated owner.

    This only flips a process-local flag. It never kills a process, touches
    ASTA/PostgreSQL, deletes a file, or changes any setting.
    """

    global _stop_engaged, _stop_engaged_at, _stop_engaged_by

    with _stop_lock:
        if not _stop_engaged:
            _stop_engaged = True
            _stop_engaged_at = datetime.now(timezone.utc)
            _stop_engaged_by = int(user_id)

    return emergency_stop_state()


def recover_emergency_stop(*, user_id: int, token: str | None) -> bool:
    """Clear the emergency stop only with the trusted recovery token.

    A missing server-side token, a missing caller token, or a mismatch all fail
    closed. The owner identity is accepted for audit purposes only; it can
    never authorize recovery on its own.
    """

    configured = str(
        getattr(settings, "TOOL_OS_EMERGENCY_RECOVERY_TOKEN", "") or ""
    ).strip()

    if not configured or not token:
        return False

    try:
        matched = hmac.compare_digest(str(token), configured)

    except (TypeError, ValueError):
        return False

    if not matched:
        return False

    global _stop_engaged, _stop_engaged_at, _stop_engaged_by

    with _stop_lock:
        _stop_engaged = False
        _stop_engaged_at = None
        _stop_engaged_by = None

    logger.info(
        "os_audit event=emergency_stop_recovered user_id=%s",
        int(user_id),
    )

    return True


def ensure_not_stopped() -> None:
    """Raise when the emergency stop is active.

    Called by the executor immediately before a tool runs, so a stop engaged
    after the policy check still blocks the side effect.
    """

    if is_emergency_stop_engaged():
        raise ToolEmergencyStopError(
            "OS automation is blocked by the emergency stop."
        )


# ---------------------------------------------------------------------------
# Owner-isolated, in-memory rate limiting
# ---------------------------------------------------------------------------


class _SlidingWindowLimiter:
    """A small process-local sliding-window limiter.

    ASTA normally runs as a single process. This limiter is intentionally
    in-memory: it adds no migration and no external dependency. In a
    multi-process deployment each process enforces its own budget, so the
    effective limit is per process. That limitation is documented in
    ``docs/os_automation_security.md``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[tuple[int, str], deque[float]] = {}

    def allow(self, rules: list[tuple[tuple[int, str], int, float]]) -> bool:
        """Check every rule and record the event only when all pass."""

        now = time.monotonic()

        with self._lock:
            checks: dict[tuple[int, str], list[tuple[int, int]]] = {}

            for key, limit, window in rules:
                events = self._events.setdefault(key, deque())
                cutoff = now - window

                while events and events[0] <= cutoff:
                    events.popleft()

                checks.setdefault(key, []).append((len(events), max(1, limit)))

            for key, key_checks in checks.items():
                if any(count >= limit for count, limit in key_checks):
                    return False

            for key in checks:
                self._events[key].append(now)

            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


_limiter = _SlidingWindowLimiter()


def authorize_execution(tool: Any, *, user_id: int) -> str:
    """Authorize one actual OS execution and consume a rate-limit slot.

    Returns a short, metadata-only rate-limit outcome suitable for audit logs:
    ``not_applicable``, ``disabled``, ``allowed``, or ``denied``.

    Static policy is re-checked here because configuration or the emergency
    stop may have changed after the earlier permission evaluation.
    """

    capability = capability_of(tool)

    if capability is None:
        return "not_applicable"

    decision = evaluate(tool)

    if not decision.allowed:
        raise_for_decision(decision)

    ensure_not_stopped()

    if not _flag("TOOL_OS_RATE_LIMIT_ENABLED", True):
        return "disabled"

    owner = int(user_id)
    total_key = (owner, "os_total")
    state_key = (owner, "os_state")

    rules: list[tuple[tuple[int, str], int, float]] = [
        (
            total_key,
            _bounded_int(
                "TOOL_OS_MAX_CALLS_PER_MINUTE",
                DEFAULT_MAX_CALLS_PER_MINUTE,
            ),
            _MINUTE,
        ),
        (
            total_key,
            _bounded_int(
                "TOOL_OS_MAX_CALLS_PER_HOUR",
                DEFAULT_MAX_CALLS_PER_HOUR,
            ),
            _HOUR,
        ),
    ]

    if is_state_changing(tool):
        rules.append(
            (
                state_key,
                _bounded_int(
                    "TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE",
                    DEFAULT_MAX_STATE_CHANGES_PER_MINUTE,
                ),
                _MINUTE,
            )
        )

    if not _limiter.allow(rules):
        raise ToolRateLimitError(
            "Too many OS automation requests. Wait briefly before trying again."
        )

    return "allowed"


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def validate_configuration() -> list[str]:
    """Return human-readable configuration problems.

    Validation never enables a capability. Invalid numeric limits fall back to
    the conservative defaults at use time; this function exists so operators
    and tests can see the problem explicitly.
    """

    problems: list[str] = []

    for name, default in (
        ("TOOL_OS_MAX_CALLS_PER_MINUTE", DEFAULT_MAX_CALLS_PER_MINUTE),
        (
            "TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE",
            DEFAULT_MAX_STATE_CHANGES_PER_MINUTE,
        ),
        ("TOOL_OS_MAX_CALLS_PER_HOUR", DEFAULT_MAX_CALLS_PER_HOUR),
    ):
        raw = getattr(settings, name, default)

        try:
            value = int(raw)

        except (TypeError, ValueError):
            problems.append(f"{name} must be a positive integer.")
            continue

        if value < 1:
            problems.append(f"{name} must be a positive integer.")

    per_minute = _bounded_int(
        "TOOL_OS_MAX_CALLS_PER_MINUTE",
        DEFAULT_MAX_CALLS_PER_MINUTE,
    )
    state_per_minute = _bounded_int(
        "TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE",
        DEFAULT_MAX_STATE_CHANGES_PER_MINUTE,
    )

    if state_per_minute > per_minute:
        problems.append(
            "TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE must not exceed "
            "TOOL_OS_MAX_CALLS_PER_MINUTE."
        )

    if _flag("TOOL_OS_AUTOMATION_ENABLED") and not _recovery_configured():
        problems.append(
            "TOOL_OS_EMERGENCY_RECOVERY_TOKEN is not configured; the "
            "emergency stop can then only be cleared by restarting the "
            "process."
        )

    return problems


def reset_state_for_tests() -> None:
    """Clear process-local state. Intended for tests only."""

    global _stop_engaged, _stop_engaged_at, _stop_engaged_by

    with _stop_lock:
        _stop_engaged = False
        _stop_engaged_at = None
        _stop_engaged_by = None

    _limiter.reset()


__all__ = [
    "EmergencyStopState",
    "OS_CAPABILITIES",
    "OS_CAPABILITY_SETTINGS",
    "OsPolicyDecision",
    "authorize_execution",
    "capability_of",
    "emergency_stop_state",
    "engage_emergency_stop",
    "ensure_not_stopped",
    "evaluate",
    "is_emergency_stop_engaged",
    "is_os_tool",
    "is_state_changing",
    "raise_for_decision",
    "recover_emergency_stop",
    "reset_state_for_tests",
    "validate_configuration",
]
