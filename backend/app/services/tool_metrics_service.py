"""Process-local operational metrics for tool execution.

Metrics are intentionally aggregate and metadata-only. No tool arguments, tool
results, credentials, prompts, or file contents are recorded. Counters live in
memory and reset when the application process restarts; durable history remains
the source of truth in the database.

A lock keeps updates consistent if the executor is ever called from more than
one thread (for example during test teardown or a synchronous bridge).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


class ToolMetricsService:
    """Aggregate counters suitable for an operational metrics endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._started_at = _utcnow()
            self._executions_total = 0
            self._success_total = 0
            self._failure_total = 0
            self._timeout_total = 0
            self._cancelled_total = 0
            self._provider_failure_total = 0
            self._retry_total = 0
            self._confirmation_approved_total = 0
            self._confirmation_rejected_total = 0
            self._duplicate_prevented_total = 0
            self._recovery_total = 0
            self._duration_samples = 0
            self._duration_total_ms = 0.0
            self._max_duration_ms = 0.0
            self._by_tool: dict[str, dict[str, int]] = {}

    def record_execution(
        self,
        *,
        tool_name: str | None,
        status: Any,
        success: bool,
        duration_ms: float | None = None,
        lifecycle: Any = None,
    ) -> None:
        """Record one completed (or interrupted) execution attempt."""

        status_value = _value(status)

        with self._lock:
            self._executions_total += 1

            if success:
                self._success_total += 1
            else:
                self._failure_total += 1

            if status_value == "timeout":
                self._timeout_total += 1

            if status_value == "cancelled" or _value(lifecycle) == "cancelled":
                self._cancelled_total += 1

            if duration_ms is not None:
                value = max(0.0, float(duration_ms))
                self._duration_samples += 1
                self._duration_total_ms += value
                self._max_duration_ms = max(self._max_duration_ms, value)

            safe_name = (tool_name or "unknown")[:100]
            entry = self._by_tool.setdefault(
                safe_name,
                {"count": 0, "success": 0, "failure": 0, "timeout": 0},
            )
            entry["count"] += 1

            if success:
                entry["success"] += 1
            else:
                entry["failure"] += 1

            if status_value == "timeout":
                entry["timeout"] += 1

    def record_retry(self, count: int = 1) -> None:
        with self._lock:
            self._retry_total += max(0, int(count))

    def record_provider_failure(self) -> None:
        with self._lock:
            self._provider_failure_total += 1

    def record_confirmation_approval(self) -> None:
        with self._lock:
            self._confirmation_approved_total += 1

    def record_confirmation_rejection(self) -> None:
        with self._lock:
            self._confirmation_rejected_total += 1

    def record_duplicate_prevented(self) -> None:
        with self._lock:
            self._duplicate_prevented_total += 1

    def record_recovery(self, count: int = 1) -> None:
        with self._lock:
            self._recovery_total += max(0, int(count))

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot without any argument or result data."""

        with self._lock:
            average = (
                None
                if self._duration_samples == 0
                else round(
                    self._duration_total_ms / self._duration_samples,
                    2,
                )
            )

            return {
                "executions_total": self._executions_total,
                "success_total": self._success_total,
                "failure_total": self._failure_total,
                "timeout_total": self._timeout_total,
                "cancelled_total": self._cancelled_total,
                "provider_failure_total": self._provider_failure_total,
                "retry_total": self._retry_total,
                "confirmation_approved_total": (
                    self._confirmation_approved_total
                ),
                "confirmation_rejected_total": (
                    self._confirmation_rejected_total
                ),
                "duplicate_prevented_total": (
                    self._duplicate_prevented_total
                ),
                "recovery_total": self._recovery_total,
                "duration_samples": self._duration_samples,
                "average_duration_ms": average,
                "max_duration_ms": (
                    None
                    if self._duration_samples == 0
                    else round(self._max_duration_ms, 2)
                ),
                "by_tool": {
                    name: dict(values)
                    for name, values in sorted(self._by_tool.items())
                },
                "since": self._started_at.isoformat(),
            }


tool_metrics_service = ToolMetricsService()


__all__ = ["ToolMetricsService", "tool_metrics_service"]
