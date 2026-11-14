"""Read-only process and active-window inspection (Phase 12D).

The five tools in this module answer questions about running processes and the
foreground desktop window. They are built on the existing ``BaseTool``,
``ToolRegistry``, ``ToolExecutor``, and ``PermissionPolicy`` architecture.

Security properties:

* every tool is read-only, idempotent, disabled by default, and denied for
  private requests;
* process enumeration uses the read-only Toolhelp32 snapshot API, and process
  details use ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)``,
  ``GetProcessTimes``, ``GetProcessMemoryInfo``, and ``GetExitCodeProcess``;
* the foreground window is inspected with ``GetForegroundWindow``,
  ``GetWindowTextW``, and ``GetWindowThreadProcessId`` only;
* no process is terminated, suspended, resumed, reprioritized, injected, or
  debugged, and no window is closed;
* no keyboard or mouse input is generated, no screenshot or window content is
  captured, and no keystroke is recorded;
* command-line arguments, environment variables, process memory contents,
  executable paths, usernames, and personal profile paths are never returned;
* protected operating-system processes are filtered from listings and refused
  by direct inspection;
* application status reuses the Phase 12B server-side allowlist. The model
  supplies only an alias, never an executable path;
* every string and result list is bounded, and unavailable measurements are
  reported explicitly rather than guessed;
* unsupported operating systems fail closed, and raw operating-system
  exceptions or tracebacks are never exposed.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolError,
    ToolExecutionError,
    ToolPermissionError,
    ToolValidationError,
)
from app.tools.implementations import application_launcher
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


# Hard bounds. Configuration values are clamped into these ranges so a bad
# environment value cannot create an unbounded result.
MAX_RESULTS_LIMIT = 100
MAX_NAME_CHARS = 260
MAX_TITLE_CHARS = 1024
MAX_SCAN_PROCESSES = 2000
MAX_FILTER_CHARS = 64

DEFAULT_MAX_RESULTS = 25
DEFAULT_MAX_MATCHES = 20
DEFAULT_MAX_NAME_CHARS = 128
DEFAULT_MAX_TITLE_CHARS = 300
DEFAULT_CPU_SAMPLE_MS = 100

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Core operating-system processes are never listed or inspected. The names are
# compared without a trailing ``.exe`` and case-insensitively.
_PROTECTED_PROCESS_NAMES = frozenset(
    {
        "system",
        "system idle process",
        "idle",
        "registry",
        "memory compression",
        "secure system",
        "smss",
        "csrss",
        "wininit",
        "winlogon",
        "services",
        "lsass",
        "lsaiso",
    }
)
_PROTECTED_PIDS = frozenset({0, 4})


@dataclass(frozen=True, slots=True)
class ProcessEntry:
    """One process reported by the Toolhelp32 snapshot."""

    pid: int
    name: str
    thread_count: int | None = None


@dataclass(frozen=True, slots=True)
class ProcessMetrics:
    """Bounded, non-sensitive measurements for one process."""

    accessible: bool
    cpu_time_seconds: float | None = None
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    status: str = "unknown"


@dataclass(frozen=True, slots=True)
class ActiveWindowInfo:
    """Raw foreground-window metadata from the read-only Windows API."""

    title: str | None
    pid: int | None
    title_truncated: bool = False


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _is_supported_platform() -> bool:
    """Process and window inspection is implemented for Windows only."""

    return os.name == "nt"


def _clamped_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)

    except (TypeError, ValueError):
        value = default

    return max(minimum, min(value, maximum))


def _max_results() -> int:
    return _clamped_int(
        getattr(settings, "TOOL_PROCESS_MAX_RESULTS", DEFAULT_MAX_RESULTS),
        DEFAULT_MAX_RESULTS,
        1,
        MAX_RESULTS_LIMIT,
    )


def _max_matches() -> int:
    return _clamped_int(
        getattr(settings, "TOOL_PROCESS_MAX_MATCHES", DEFAULT_MAX_MATCHES),
        DEFAULT_MAX_MATCHES,
        1,
        MAX_RESULTS_LIMIT,
    )


def _max_name_chars() -> int:
    return _clamped_int(
        getattr(
            settings,
            "TOOL_PROCESS_MAX_NAME_CHARS",
            DEFAULT_MAX_NAME_CHARS,
        ),
        DEFAULT_MAX_NAME_CHARS,
        16,
        MAX_NAME_CHARS,
    )


def _max_title_chars() -> int:
    return _clamped_int(
        getattr(
            settings,
            "TOOL_PROCESS_MAX_TITLE_CHARS",
            DEFAULT_MAX_TITLE_CHARS,
        ),
        DEFAULT_MAX_TITLE_CHARS,
        32,
        MAX_TITLE_CHARS,
    )


def _cpu_sample_ms() -> int:
    return _clamped_int(
        getattr(
            settings,
            "TOOL_PROCESS_CPU_SAMPLE_MS",
            DEFAULT_CPU_SAMPLE_MS,
        ),
        DEFAULT_CPU_SAMPLE_MS,
        0,
        1000,
    )


def _effective_limit(requested: int | None, maximum: int) -> int:
    if requested is None:
        return maximum

    return max(1, min(int(requested), maximum))


# ---------------------------------------------------------------------------
# Bounded text helpers
# ---------------------------------------------------------------------------


def _clean_text(
    value: Any,
    limit: int,
) -> tuple[str | None, bool]:
    """Return a single-line, bounded string and a truncation flag.

    Whitespace is collapsed so a process name or window title cannot inject
    extra lines or a fake transcript into the result. Control characters are
    removed by the same whitespace normalization.
    """

    if value is None:
        return None, False

    try:
        text = str(value).strip()

    except Exception:
        return None, False

    if not text:
        return None, False

    text = " ".join(text.split())

    if not text:
        return None, False

    if len(text) > limit:
        return text[:limit], True

    return text, False


def _bounded_name(value: Any) -> str | None:
    text, _truncated = _clean_text(value, _max_name_chars())
    return text


def _protected_key(name: str) -> str:
    key = name.strip().lower()

    if key.endswith(".exe"):
        key = key[: -len(".exe")]

    return key


def _is_protected_entry(entry: ProcessEntry) -> bool:
    if entry.pid in _PROTECTED_PIDS:
        return True

    return _protected_key(entry.name) in _PROTECTED_PROCESS_NAMES


def _protected_pid_reason(
    pid: int,
    entries: list[ProcessEntry],
) -> bool:
    if pid in _PROTECTED_PIDS:
        return True

    for entry in entries:
        if entry.pid == pid:
            return _is_protected_entry(entry)

    return False


# ---------------------------------------------------------------------------
# Windows API structures and primitives
# ---------------------------------------------------------------------------


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * MAX_NAME_CHARS),
    ]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _filetime_seconds(value: _FILETIME) -> float:
    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return ticks / 10_000_000.0


def _enumerate_processes() -> list[ProcessEntry]:
    """Return one bounded snapshot of running processes.

    Only the process id, image base name, and thread count are read. Command
    lines, paths, owners, and environment blocks are never requested.
    """

    if not _is_supported_platform():
        raise ToolConfigurationError(
            "Process inspection is only supported on Windows."
        )

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        create_snapshot.restype = ctypes.c_void_p

        process_first = kernel32.Process32FirstW
        process_first.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        process_first.restype = ctypes.c_int

        process_next = kernel32.Process32NextW
        process_next.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        process_next.restype = ctypes.c_int

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int

        snapshot = create_snapshot(TH32CS_SNAPPROCESS, 0)

        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            raise ToolExecutionError(
                "Process information is unavailable."
            )

        entries: list[ProcessEntry] = []

        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            has_entry = bool(process_first(snapshot, ctypes.byref(entry)))

            while has_entry and len(entries) < MAX_SCAN_PROCESSES:
                pid = int(entry.th32ProcessID)
                name = _bounded_name(entry.szExeFile)

                if pid >= 0 and name:
                    entries.append(
                        ProcessEntry(
                            pid=pid,
                            name=name,
                            thread_count=int(entry.cntThreads),
                        )
                    )

                entry = _PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
                has_entry = bool(
                    process_next(snapshot, ctypes.byref(entry))
                )

        finally:
            close_handle(snapshot)

        return entries

    except ToolError:
        raise

    except Exception:
        raise ToolExecutionError(
            "Process information is unavailable."
        ) from None


def _read_process_metrics(pid: int) -> ProcessMetrics | None:
    """Read bounded measurements for one process without modifying it.

    ``None`` means the process handle could not be opened at all. An
    inaccessible process is reported as ``accessible=False`` so a caller can
    distinguish it from a process that is not running.
    """

    if not _is_supported_platform():
        raise ToolConfigurationError(
            "Process inspection is only supported on Windows."
        )

    if pid <= 0:
        return None

    handle = None

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        open_process = kernel32.OpenProcess
        open_process.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        open_process.restype = ctypes.c_void_p

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int

        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        get_times.restype = ctypes.c_int

        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_exit_code.restype = ctypes.c_int

        get_memory = psapi.GetProcessMemoryInfo
        get_memory.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
            ctypes.c_uint32,
        ]
        get_memory.restype = ctypes.c_int

        handle = open_process(
            PROCESS_QUERY_LIMITED_INFORMATION,
            0,
            pid,
        )

        if not handle:
            return ProcessMetrics(
                accessible=False,
                status="access_denied",
            )

        cpu_time: float | None = None
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel_time = _FILETIME()
        user_time = _FILETIME()

        if get_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            cpu_time = _filetime_seconds(kernel_time) + _filetime_seconds(
                user_time
            )

        memory_bytes: int | None = None
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)

        if get_memory(handle, ctypes.byref(counters), counters.cb):
            memory_bytes = int(counters.WorkingSetSize)

        status = "unknown"
        exit_code = ctypes.c_uint32(0)

        if get_exit_code(handle, ctypes.byref(exit_code)):
            status = (
                "running"
                if int(exit_code.value) == STILL_ACTIVE
                else "exited"
            )

        return ProcessMetrics(
            accessible=True,
            cpu_time_seconds=cpu_time,
            memory_bytes=memory_bytes,
            status=status,
        )

    except ToolError:
        raise

    except Exception:
        return ProcessMetrics(
            accessible=False,
            status="access_denied",
        )

    finally:
        if handle:
            try:
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                    handle
                )

            except Exception:
                pass


def _sample_processes(
    pids: list[int],
    sample_ms: int,
) -> dict[int, ProcessMetrics]:
    """Read CPU times twice and compute a bounded percentage per process.

    The percentage is normalized by the logical processor count and clamped to
    0-100. When a sample interval is zero, or a process disappears, the
    percentage is reported as unavailable instead of being invented.
    """

    unique: list[int] = []

    for pid in pids:
        if pid not in unique:
            unique.append(pid)

    if not unique:
        return {}

    first = {pid: _read_process_metrics(pid) for pid in unique}
    elapsed = 0.0

    if sample_ms > 0:
        started = time.monotonic()
        time.sleep(sample_ms / 1000.0)
        elapsed = time.monotonic() - started

    second = {pid: _read_process_metrics(pid) for pid in unique}
    cpu_count = os.cpu_count() or 1
    results: dict[int, ProcessMetrics] = {}

    for pid in unique:
        start = first.get(pid)
        end = second.get(pid)

        if end is None:
            results[pid] = ProcessMetrics(
                accessible=False,
                status="not_running",
            )
            continue

        percent: float | None = None

        if (
            start is not None
            and start.accessible
            and end.accessible
            and start.cpu_time_seconds is not None
            and end.cpu_time_seconds is not None
            and elapsed > 0
        ):
            delta = max(
                0.0,
                end.cpu_time_seconds - start.cpu_time_seconds,
            )
            percent = round(
                min(
                    100.0,
                    delta / elapsed / max(1, cpu_count) * 100.0,
                ),
                1,
            )

        results[pid] = ProcessMetrics(
            accessible=end.accessible,
            cpu_time_seconds=end.cpu_time_seconds,
            cpu_percent=percent,
            memory_bytes=end.memory_bytes,
            status=end.status,
        )

    return results


def _read_active_window() -> ActiveWindowInfo | None:
    """Read the foreground window title and owner process id."""

    if not _is_supported_platform():
        raise ToolConfigurationError(
            "Window inspection is only supported on Windows."
        )

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        get_foreground = user32.GetForegroundWindow
        get_foreground.argtypes = []
        get_foreground.restype = ctypes.c_void_p

        get_length = user32.GetWindowTextLengthW
        get_length.argtypes = [ctypes.c_void_p]
        get_length.restype = ctypes.c_int

        get_text = user32.GetWindowTextW
        get_text.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        get_text.restype = ctypes.c_int

        get_pid = user32.GetWindowThreadProcessId
        get_pid.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_pid.restype = ctypes.c_uint32

        handle = get_foreground()

        if not handle:
            return None

        length = max(0, int(get_length(handle)))
        maximum = _max_title_chars()
        buffer_size = min(length, maximum) + 1
        buffer = ctypes.create_unicode_buffer(buffer_size)
        copied = int(get_text(handle, buffer, buffer_size))
        title = buffer.value if copied > 0 else None
        truncated = length > buffer_size - 1

        process_id = ctypes.c_uint32(0)
        get_pid(handle, ctypes.byref(process_id))
        pid = int(process_id.value) or None

        return ActiveWindowInfo(
            title=title,
            pid=pid,
            title_truncated=truncated,
        )

    except ToolError:
        raise

    except Exception:
        raise ToolExecutionError(
            "Active window information is unavailable."
        ) from None


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def _validated_name_filter(value: Any) -> str | None:
    if value is None:
        return None

    try:
        text = str(value).strip()

    except Exception:
        raise ToolValidationError("Invalid process name filter.") from None

    if not text:
        return None

    if len(text) > MAX_FILTER_CHARS:
        raise ToolValidationError("The process name filter is too long.")

    if any(
        ord(character) < 32 or ord(character) == 127
        for character in text
    ):
        raise ToolValidationError(
            "The process name filter contains unsupported characters."
        )

    if any(fragment in text for fragment in ("/", "\\", ":", "..")):
        raise ToolValidationError(
            "The process name filter must be a plain name, not a path."
        )

    return text.casefold()


def _collect_process_list(
    name_filter: Any = None,
    limit: int | None = None,
) -> dict[str, Any]:
    entries = _enumerate_processes()
    needle = _validated_name_filter(name_filter)
    visible = [
        entry
        for entry in entries
        if not _is_protected_entry(entry)
        and (
            needle is None
            or needle in entry.name.casefold()
        )
    ]
    visible.sort(key=lambda entry: (entry.name.casefold(), entry.pid))
    maximum = _effective_limit(limit, _max_results())
    selected = visible[:maximum]

    return {
        "available": True,
        "count": len(selected),
        "total_matched": len(visible),
        "truncated": len(visible) > len(selected),
        "filter": needle,
        "processes": [
            {"pid": entry.pid, "name": _bounded_name(entry.name) or ""}
            for entry in selected
        ],
    }


def _metric_reason(metrics: ProcessMetrics) -> str:
    if not metrics.accessible:
        return "access_denied"

    if metrics.status in {"not_running", "exited"}:
        return metrics.status

    return "unavailable"


def _cpu_payload(metrics: ProcessMetrics) -> dict[str, Any]:
    if not metrics.accessible or metrics.cpu_time_seconds is None:
        return {
            "available": False,
            "reason": _metric_reason(metrics),
        }

    return {
        "available": True,
        "time_seconds": round(metrics.cpu_time_seconds, 2),
        "percent": metrics.cpu_percent,
    }


def _memory_payload(metrics: ProcessMetrics) -> dict[str, Any]:
    if not metrics.accessible or metrics.memory_bytes is None:
        return {
            "available": False,
            "reason": _metric_reason(metrics),
        }

    return {
        "available": True,
        "bytes": int(metrics.memory_bytes),
        "mib": round(metrics.memory_bytes / (1024**2), 2),
    }


def _collect_process_info(pid: int) -> dict[str, Any]:
    entries = _enumerate_processes()

    if _protected_pid_reason(pid, entries):
        raise ToolPermissionError(
            "That process is protected and cannot be inspected."
        )

    entry = next(
        (item for item in entries if item.pid == pid),
        None,
    )

    if entry is None:
        return {
            "pid": pid,
            "exists": False,
            "accessible": False,
            "name": None,
            "status": "not_running",
            "thread_count": None,
            "cpu": {
                "available": False,
                "reason": "process_not_found",
            },
            "memory": {
                "available": False,
                "reason": "process_not_found",
            },
        }

    metrics = _sample_processes([pid], _cpu_sample_ms()).get(pid)

    if metrics is None:
        metrics = ProcessMetrics(
            accessible=False,
            status="process_exited",
        )

    if not metrics.accessible:
        # A failed handle open can mean either "access denied" or "the process
        # exited between the snapshot and the measurement". Re-enumerate once
        # so the two cases are reported distinctly and an inaccessible process
        # is never mistaken for a stopped one.
        try:
            still_present = any(
                item.pid == pid for item in _enumerate_processes()
            )

        except ToolError:
            # The process existed at the first snapshot. If the second
            # snapshot fails, keep the conservative access-denied result.
            still_present = True

        if not still_present:
            return {
                "pid": pid,
                "exists": False,
                "accessible": False,
                "name": None,
                "status": "process_exited",
                "thread_count": None,
                "cpu": {
                    "available": False,
                    "reason": "process_exited",
                },
                "memory": {
                    "available": False,
                    "reason": "process_exited",
                },
            }

        status = "access_denied"
    else:
        status = metrics.status

    return {
        "pid": pid,
        "exists": True,
        "accessible": bool(metrics.accessible),
        "name": _bounded_name(entry.name),
        "status": status,
        "thread_count": entry.thread_count,
        "cpu": _cpu_payload(metrics),
        "memory": _memory_payload(metrics),
    }


def _collect_application_status(alias: str) -> dict[str, Any]:
    application = application_launcher._resolve_application(alias)
    executable_name = Path(application.executable).name.casefold()

    if not executable_name:
        raise ToolValidationError(
            "That application has no inspectable executable name."
        )

    entries = _enumerate_processes()
    matches = sorted(
        entry.pid
        for entry in entries
        if not _is_protected_entry(entry)
        and entry.name.casefold() == executable_name
    )
    maximum = _max_matches()
    selected = matches[:maximum]

    return {
        "application": application.display_name,
        "alias": application.alias,
        "running": bool(matches),
        "match_count": len(matches),
        "pids": selected,
        "truncated": len(matches) > len(selected),
        "match_basis": "executable_name",
    }


def _collect_active_window() -> dict[str, Any]:
    window = _read_active_window()

    if window is None:
        return {
            "available": False,
            "reason": "no_active_window",
            "title": None,
            "title_present": False,
            "title_truncated": False,
            "pid": None,
            "process_name": None,
            "untrusted": True,
        }

    title, title_truncated = _clean_text(
        window.title,
        _max_title_chars(),
    )
    process_name: str | None = None

    if window.pid is not None:
        try:
            entries = _enumerate_processes()

        except ToolError:
            # The title is still useful when the process snapshot fails. Do
            # not turn a partial success into a total failure.
            entries = []

        if entries and not _protected_pid_reason(window.pid, entries):
            entry = next(
                (item for item in entries if item.pid == window.pid),
                None,
            )

            if entry is not None:
                process_name = _bounded_name(entry.name)

    return {
        "available": True,
        "reason": None,
        "title": title,
        "title_present": title is not None,
        "title_truncated": bool(
            window.title_truncated or title_truncated
        ),
        "pid": window.pid,
        "process_name": process_name,
        "untrusted": True,
    }


def _collect_resource_summary(
    limit: int | None = None,
) -> dict[str, Any]:
    entries = [
        entry
        for entry in _enumerate_processes()
        if not _is_protected_entry(entry)
    ]
    sample_ms = _cpu_sample_ms()
    metrics = _sample_processes(
        [entry.pid for entry in entries],
        sample_ms,
    )
    items: list[dict[str, Any]] = []

    for entry in entries:
        measurement = metrics.get(entry.pid)

        if measurement is None:
            measurement = ProcessMetrics(
                accessible=False,
                status="process_exited",
            )

        items.append(
            {
                "pid": entry.pid,
                "name": _bounded_name(entry.name) or "",
                "thread_count": entry.thread_count,
                "cpu": _cpu_payload(measurement),
                "memory": _memory_payload(measurement),
            }
        )

    def sort_key(item: dict[str, Any]) -> tuple[float, int, int]:
        cpu = item["cpu"]
        memory = item["memory"]
        cpu_value = (
            float(cpu["percent"])
            if cpu.get("available") and cpu.get("percent") is not None
            else -1.0
        )
        memory_value = (
            int(memory["bytes"])
            if memory.get("available")
            else -1
        )

        return (-cpu_value, -memory_value, int(item["pid"]))

    items.sort(key=sort_key)
    maximum = _effective_limit(limit, _max_results())
    selected = items[:maximum]

    return {
        "available": True,
        "count": len(selected),
        "total_considered": len(items),
        "truncated": len(items) > len(selected),
        "cpu_sample_ms": sample_ms,
        "processes": selected,
    }


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class ProcessListInput(StrictModel):
    name_filter: str | None = Field(
        default=None,
        max_length=MAX_FILTER_CHARS,
        description=(
            "Optional case-insensitive application-name filter, for example "
            "'chrome'. Paths and command lines are not accepted."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=MAX_RESULTS_LIMIT,
        description="Optional smaller result limit.",
    )


class ProcessInfoInput(StrictModel):
    pid: int = Field(
        ge=0,
        le=0xFFFFFFFF,
        description="Process identifier to inspect.",
    )


class ApplicationStatusInput(StrictModel):
    application: str = Field(
        min_length=1,
        max_length=application_launcher.MAX_ALIAS_CHARS,
        description=(
            "Approved application alias, for example 'vscode'. Executable "
            "paths and command-line arguments are never accepted."
        ),
    )


class ActiveWindowInput(StrictModel):
    """The active-window tool takes no arguments."""


class ResourceSummaryInput(StrictModel):
    limit: int | None = Field(
        default=None,
        ge=1,
        le=MAX_RESULTS_LIMIT,
        description="Optional smaller result limit.",
    )


class _ProcessInspectionTool(BaseTool):
    """Shared read-only execution behavior for the Phase 12D tools."""

    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    requires_confirmation: ClassVar[bool] = False
    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str | None] = "process_inspection"
    idempotent: ClassVar[bool] = True
    os_capability: ClassVar[str | None] = "process_inspection"
    unavailable_message: ClassVar[str] = (
        "Process information is unavailable."
    )

    @property
    def enabled(self) -> bool:
        return bool(
            settings.TOOL_OS_AUTOMATION_ENABLED
            and settings.TOOL_PROCESS_INSPECTION_ENABLED
            and _is_supported_platform()
        )

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        raise NotImplementedError

    async def execute(
        self,
        context: ToolContext,
        arguments: StrictModel,
    ) -> ToolResult:
        try:
            # Collection is synchronous and may sleep briefly for a CPU sample.
            # Running it in a worker thread keeps the event loop responsive.
            data = await asyncio.to_thread(self.collect, arguments)

        except ToolError:
            raise

        except Exception:
            # Never expose an operating-system exception or traceback.
            raise ToolExecutionError(self.unavailable_message) from None

        return success_result(
            tool_name=self.name,
            data=data,
            provider=self.provider_label,
        )


class SystemProcessListTool(_ProcessInspectionTool):
    name = "system_process_list"
    description = (
        "List running processes with their process id and image name. An "
        "optional name filter narrows the result. Protected system processes "
        "are omitted, results are bounded, and command lines, environment "
        "variables, executable paths, and owners are never read or returned. "
        "Read-only."
    )
    input_model = ProcessListInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_process_list(
            getattr(arguments, "name_filter", None),
            getattr(arguments, "limit", None),
        )


class SystemProcessInfoTool(_ProcessInspectionTool):
    name = "system_process_info"
    description = (
        "Inspect one running process by pid. Returns its image name, status, "
        "thread count, and CPU time/percentage and working-set memory when "
        "available. Protected processes are refused, and command lines, "
        "environment variables, memory contents, and executable paths are "
        "never read. Read-only."
    )
    input_model = ProcessInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_process_info(int(arguments.pid))


class SystemApplicationStatusTool(_ProcessInspectionTool):
    name = "system_application_status"
    description = (
        "Check whether an approved application is running by alias. The alias "
        "is resolved against the server-side application allowlist, and only "
        "bounded matching process ids are returned. Executable paths are never "
        "accepted and nothing is launched. Read-only."
    )
    input_model = ApplicationStatusInput

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False

        try:
            return bool(application_launcher._approved_applications())

        except Exception:
            return False

    def validate_arguments(self, arguments: dict[str, Any]) -> StrictModel:
        validated = super().validate_arguments(arguments)
        # Resolve at proposal time so an unknown or path-like alias never
        # reaches execution. Execution resolves again immediately before use.
        application_launcher._resolve_application(validated.application)
        return validated

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_application_status(arguments.application)


class SystemActiveWindowTool(_ProcessInspectionTool):
    name = "system_active_window"
    description = (
        "Return the title, process id, and process image name of the current "
        "foreground desktop window. The title is bounded and untrusted. No "
        "screenshot, window content, keyboard input, or mouse input is "
        "captured. Read-only."
    )
    input_model = ActiveWindowInput
    unavailable_message = "Active window information is unavailable."

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_active_window()


class SystemResourceSummaryTool(_ProcessInspectionTool):
    name = "system_resource_summary"
    description = (
        "Return a bounded summary of the top CPU- and memory-consuming "
        "processes. Protected system processes are omitted, and unavailable "
        "CPU or memory measurements are marked explicitly. Read-only."
    )
    input_model = ResourceSummaryInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_resource_summary(
            getattr(arguments, "limit", None)
        )


PROCESS_INSPECTION_TOOLS = (
    SystemProcessListTool(),
    SystemProcessInfoTool(),
    SystemApplicationStatusTool(),
    SystemActiveWindowTool(),
    SystemResourceSummaryTool(),
)


__all__ = [
    "ActiveWindowInfo",
    "ApplicationStatusInput",
    "MAX_RESULTS_LIMIT",
    "PROCESS_INSPECTION_TOOLS",
    "ProcessEntry",
    "ProcessInfoInput",
    "ProcessListInput",
    "ProcessMetrics",
    "ResourceSummaryInput",
    "SystemActiveWindowTool",
    "SystemApplicationStatusTool",
    "SystemProcessInfoTool",
    "SystemProcessListTool",
    "SystemResourceSummaryTool",
]
