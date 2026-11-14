"""Read-only system information tools.

The seven tools in this module answer basic questions about the machine ASTA is
running on. They use only the Python standard library and never execute a
shell, PowerShell, CMD, or an arbitrary script.

Security properties:

* Every tool is read-only, idempotent, and disabled by default.
* Only a small, fixed set of scalar facts is collected: OS name/version/
  architecture, hostname, CPU counts and model text, RAM totals, approved
  local disk usage, and Python runtime metadata.
* Environment variables, file contents, process lists, arbitrary registry
  values, browser history, documents, credentials, and other user data are
  never enumerated or returned as data. The standard library's ``platform``
  module may consult documented OS/hardware identifiers read-only to produce
  the bounded facts above.
* Disk usage is limited to an explicit allowlist of local roots, or the single
  system drive when no allowlist is configured. Volumes are never enumerated.
* Values are bounded. Missing information is reported as ``None`` or
  ``available: false`` instead of being invented.
* OS and permission errors become safe structured results. Raw exception
  tracebacks are never exposed to the model or the user.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Callable, ClassVar

from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolError,
    ToolExecutionError,
    ToolValidationError,
)
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


# Keep every scalar small enough for a provider prompt. The global result
# limit still applies on top of these bounds.
MAX_TEXT_CHARS = 200
MAX_PATH_CHARS = 260
MAX_SUMMARY_CHARS = 2000
MAX_DISK_ROOTS = 8


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str | None:
    """Return a bounded, single-line string or ``None``.

    Newlines and control-heavy whitespace are collapsed so a value can never
    smuggle extra instructions or a fake transcript into a tool result.
    """

    if value is None:
        return None

    try:
        text = str(value).strip()

    except Exception:
        return None

    if not text:
        return None

    text = " ".join(text.split())

    return text[:limit] if text else None


def _safe_call(
    collector: Callable[[], Any],
    default: Any = None,
) -> Any:
    """Call a platform helper without letting a failure escape."""

    try:
        return collector()

    except Exception:
        return default


def _gib(value: int) -> float:
    return round(value / (1024**3), 2)


def _max_disks() -> int:
    try:
        configured = int(settings.TOOL_SYSTEM_INFO_MAX_DISKS)

    except (TypeError, ValueError):
        configured = MAX_DISK_ROOTS

    return max(1, min(configured, MAX_DISK_ROOTS))


# ---------------------------------------------------------------------------
# OS information
# ---------------------------------------------------------------------------


def _collect_os_info() -> dict[str, Any]:
    uname = _safe_call(platform.uname)

    system = _text(getattr(uname, "system", None)) if uname else None
    release = _text(getattr(uname, "release", None)) if uname else None
    version = _text(getattr(uname, "version", None)) if uname else None
    machine = _text(getattr(uname, "machine", None)) if uname else None

    architecture = None
    bits = _safe_call(platform.architecture)

    if isinstance(bits, tuple) and bits:
        architecture = _text(bits[0])

    build = None

    if system and system.lower() == "windows" and version:
        parts = version.split(".")

        if parts and parts[-1].isdigit():
            build = parts[-1]

    return {
        "available": bool(system or release or version or machine),
        "system": system,
        "release": release,
        "version": version,
        "build": build,
        "machine": machine,
        "architecture": architecture,
        "platform": _text(_safe_call(platform.platform)),
    }


# ---------------------------------------------------------------------------
# Hostname
# ---------------------------------------------------------------------------


def _collect_hostname() -> dict[str, Any]:
    hostname = _text(_safe_call(socket.gethostname))

    # ``platform.node`` is a local fallback. No DNS lookup or network request
    # is made, so a hostname cannot be resolved to an external address here.
    if hostname is None:
        hostname = _text(_safe_call(platform.node))

    return {
        "available": hostname is not None,
        "hostname": hostname,
    }


# ---------------------------------------------------------------------------
# CPU information
# ---------------------------------------------------------------------------


def _processor_name() -> str | None:
    candidates = [_safe_call(platform.processor)]
    uname = _safe_call(platform.uname)

    if uname is not None:
        candidates.append(getattr(uname, "processor", None))

    for candidate in candidates:
        text = _text(candidate)

        if text:
            return text

    return None


def _logical_cpu_count() -> int | None:
    try:
        count = os.cpu_count()

    except Exception:
        return None

    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count

    return None


def _physical_core_count() -> int | None:
    """Return the physical core count on Windows, when the API is available.

    ``GetLogicalProcessorInformation`` is a read-only kernel query. The tool
    never touches the registry or any process. On other platforms the count is
    reported as unavailable rather than guessed from the logical count.
    """

    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class _ProcessorInfoUnion(ctypes.Union):
            _fields_ = [("Reserved", ctypes.c_uint64 * 2)]

        class _ProcessorInfo(ctypes.Structure):
            _fields_ = [
                ("ProcessorMask", ctypes.c_size_t),
                ("Relationship", ctypes.c_int),
                ("Data", _ProcessorInfoUnion),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_info = kernel32.GetLogicalProcessorInformation
        get_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        get_info.restype = wintypes.BOOL

        length = wintypes.DWORD(0)
        get_info(None, ctypes.byref(length))

        if length.value == 0:
            return None

        buffer = ctypes.create_string_buffer(length.value)

        if not get_info(buffer, ctypes.byref(length)):
            return None

        entry_size = ctypes.sizeof(_ProcessorInfo)
        count = length.value // entry_size

        if count <= 0:
            return None

        entries = ctypes.cast(buffer, ctypes.POINTER(_ProcessorInfo))
        cores = sum(
            1
            for index in range(count)
            if entries[index].Relationship == 0  # RelationProcessorCore
        )

        return cores or None

    except Exception:
        return None


def _collect_cpu_info() -> dict[str, Any]:
    model = _processor_name()
    logical = _logical_cpu_count()
    physical = _physical_core_count()

    return {
        "available": bool(model or logical or physical),
        "model": model,
        "logical_processors": logical,
        "physical_cores": physical,
        "architecture": _text(_safe_call(platform.machine)),
    }


# ---------------------------------------------------------------------------
# Memory information
# ---------------------------------------------------------------------------


def _windows_memory() -> tuple[int, int] | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None

        total = int(status.ullTotalPhys)
        available = int(status.ullAvailPhys)

        if total <= 0:
            return None

        return total, max(0, min(available, total))

    except Exception:
        return None


def _posix_memory() -> tuple[int, int] | None:
    try:
        sysconf = os.sysconf
        page_size = int(sysconf("SC_PAGE_SIZE"))
        total_pages = int(sysconf("SC_PHYS_PAGES"))
        available_pages = int(sysconf("SC_AVPHYS_PAGES"))

    except (AttributeError, OSError, TypeError, ValueError):
        return None

    if page_size <= 0 or total_pages <= 0:
        return None

    total = page_size * total_pages
    available = page_size * max(0, available_pages)

    return total, max(0, min(available, total))


def _collect_memory_info() -> dict[str, Any]:
    values = _windows_memory()

    if values is None:
        values = _posix_memory()

    if values is None:
        return {
            "available": False,
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "usage_percent": None,
            "total_gib": None,
            "available_gib": None,
            "used_gib": None,
        }

    total, available = values

    if total <= 0:
        return {
            "available": False,
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "usage_percent": None,
            "total_gib": None,
            "available_gib": None,
            "used_gib": None,
        }

    available = max(0, min(available, total))
    used = max(0, total - available)

    return {
        "available": True,
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "usage_percent": round(used / total * 100, 1),
        "total_gib": _gib(total),
        "available_gib": _gib(available),
        "used_gib": _gib(used),
    }


# ---------------------------------------------------------------------------
# Disk information
# ---------------------------------------------------------------------------


def _configured_disk_roots() -> list[str]:
    raw = getattr(settings, "TOOL_SYSTEM_INFO_DISK_ROOTS", "") or ""

    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _default_disk_root() -> str | None:
    """Return the drive that contains the running interpreter.

    This is the system/app drive on a normal Windows installation. It is used
    only when the operator has not configured an explicit allowlist.
    """

    for candidate in (sys.executable, str(Path.home())):
        try:
            anchor = Path(candidate).anchor

        except Exception:
            continue

        if anchor:
            return anchor

    return None


def _normalize_root(raw: str) -> str | None:
    text = str(raw or "").strip()

    if not text or len(text) > MAX_PATH_CHARS or "\x00" in text:
        return None

    # UNC and mapped network shares are never treated as approved local roots.
    if os.name == "nt" and (
        text.startswith("\\\\") or text.startswith("//")
    ):
        return None

    try:
        path = Path(text)

        if not path.is_absolute():
            return None

        resolved = path.resolve(strict=False)

        if not resolved.exists() or not resolved.is_dir():
            return None

        return str(resolved)

    except (OSError, ValueError):
        return None


def _is_approved_local_root(root: str) -> bool:
    """Accept only fixed local drives on Windows.

    The drive type is checked for every Windows path, not only bare drive
    roots, so a subdirectory on a mapped network drive or removable volume is
    rejected as well. On other platforms the explicit allowlist is the only
    control.
    """

    if os.name != "nt":
        return True

    drive, _tail = os.path.splitdrive(root)

    if not drive:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_drive_type = kernel32.GetDriveTypeW
        get_drive_type.argtypes = [wintypes.LPCWSTR]
        get_drive_type.restype = wintypes.UINT

        return get_drive_type(drive + "\\") == 3  # DRIVE_FIXED

    except Exception:
        return False


def _approved_disk_roots() -> list[str]:
    configured = _configured_disk_roots()

    if not configured:
        default = _default_disk_root()
        configured = [default] if default else []

    roots: list[str] = []
    seen: set[str] = set()

    for raw in configured:
        root = _normalize_root(raw)

        if root is None or not _is_approved_local_root(root):
            continue

        key = os.path.normcase(root)

        if key in seen:
            continue

        seen.add(key)
        roots.append(root)

        if len(roots) >= _max_disks():
            break

    return roots


def _normalize_drive_selector(value: str) -> str:
    text = str(value or "").strip()

    if not text or len(text) > MAX_PATH_CHARS or "\x00" in text:
        raise ToolValidationError("Invalid drive selector.")

    if os.name == "nt":
        match = re.fullmatch(r"([A-Za-z]):?[\\/]?", text)

        if not match:
            raise ToolValidationError("Invalid drive selector.")

        return f"{match.group(1).upper()}:\\"

    if not text.startswith("/"):
        raise ToolValidationError("Invalid drive selector.")

    try:
        return os.path.abspath(text)

    except (OSError, ValueError):
        raise ToolValidationError("Invalid drive selector.") from None


def _drive_label(root: str) -> str:
    drive, tail = os.path.splitdrive(root)

    if drive and not tail.strip("\\/"):
        return drive.upper() if os.name == "nt" else drive

    return root


def _collect_disk_info(selector: str | None = None) -> dict[str, Any]:
    roots = _approved_disk_roots()

    if not roots:
        raise ToolConfigurationError(
            "No approved local disk is configured."
        )

    if selector:
        requested = _normalize_drive_selector(selector)
        roots = [
            root
            for root in roots
            if os.path.normcase(root) == os.path.normcase(requested)
        ]

        if not roots:
            raise ToolValidationError(
                "The requested drive is not approved."
            )

    drives: list[dict[str, Any]] = []

    for root in roots:
        try:
            usage = shutil.disk_usage(root)

        except (OSError, PermissionError):
            drives.append(
                {
                    "root": root,
                    "label": _drive_label(root),
                    "available": False,
                    "error": "Disk usage is unavailable.",
                }
            )
            continue

        total = int(getattr(usage, "total", 0) or 0)
        free = int(getattr(usage, "free", 0) or 0)

        if total <= 0:
            drives.append(
                {
                    "root": root,
                    "label": _drive_label(root),
                    "available": False,
                    "error": "Disk usage is unavailable.",
                }
            )
            continue

        free = max(0, min(free, total))
        used = max(0, total - free)

        drives.append(
            {
                "root": root,
                "label": _drive_label(root),
                "available": True,
                "total_bytes": total,
                "used_bytes": used,
                "available_bytes": free,
                "usage_percent": round(used / total * 100, 1),
                "total_gib": _gib(total),
                "used_gib": _gib(used),
                "available_gib": _gib(free),
            }
        )

    return {
        "available": any(item["available"] for item in drives),
        "count": len(drives),
        "drives": drives,
    }


# ---------------------------------------------------------------------------
# Python information
# ---------------------------------------------------------------------------


def _collect_python_info() -> dict[str, Any]:
    version = _text(_safe_call(platform.python_version))
    implementation = _text(_safe_call(platform.python_implementation))
    executable = _text(
        _safe_call(lambda: sys.executable),
        limit=MAX_PATH_CHARS,
    )
    compiler = _text(_safe_call(platform.python_compiler))
    build = _safe_call(platform.python_build)
    build_text = None

    if isinstance(build, tuple):
        build_text = _text(" ".join(str(part) for part in build))

    return {
        "available": bool(version or implementation),
        "version": version,
        "implementation": implementation,
        "executable": executable,
        "compiler": compiler,
        "build": build_text,
    }


# ---------------------------------------------------------------------------
# Consolidated summary
# ---------------------------------------------------------------------------


def _summary_line(label: str, value: str | None) -> str:
    separator = "" if label.endswith(":") else ":"
    return f"{label}{separator} {value or 'unavailable'}"


def _build_summary(sections: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []

    os_info = sections["os"]

    if os_info.get("available"):
        detail = " ".join(
            part
            for part in (
                os_info.get("release"),
                (
                    f"build {os_info['build']}"
                    if os_info.get("build")
                    else None
                ),
            )
            if part
        )
        architecture = (
            os_info.get("architecture") or os_info.get("machine")
        )
        text = os_info.get("system") or "Unknown OS"

        if detail:
            text += f" {detail}"

        if architecture:
            text += f" ({architecture})"

        lines.append(_summary_line("OS", text))
    else:
        lines.append(_summary_line("OS", None))

    hostname = sections["hostname"]
    lines.append(
        _summary_line("Hostname", hostname.get("hostname"))
    )

    cpu = sections["cpu"]

    if cpu.get("available"):
        cpu_text = cpu.get("model") or "Unknown CPU"
        counts = []

        if cpu.get("logical_processors"):
            counts.append(
                f"{cpu['logical_processors']} logical processors"
            )

        if cpu.get("physical_cores"):
            counts.append(f"{cpu['physical_cores']} physical cores")

        if counts:
            cpu_text += " (" + ", ".join(counts) + ")"

        lines.append(_summary_line("CPU", cpu_text))
    else:
        lines.append(_summary_line("CPU", None))

    memory = sections["memory"]

    if memory.get("available"):
        memory_text = (
            f"{memory['total_gib']} GiB total, "
            f"{memory['available_gib']} GiB available, "
            f"{memory['usage_percent']}% used"
        )
        lines.append(_summary_line("Memory", memory_text))
    else:
        lines.append(_summary_line("Memory", None))

    disk = sections["disk"]

    if disk.get("available"):
        for drive in disk.get("drives", []):
            if not drive.get("available"):
                lines.append(
                    _summary_line(
                        f"Disk {drive.get('label') or drive.get('root')}",
                        None,
                    )
                )
                continue

            disk_text = (
                f"{drive['total_gib']} GiB total, "
                f"{drive['available_gib']} GiB available, "
                f"{drive['usage_percent']}% used"
            )
            lines.append(
                _summary_line(
                    f"Disk {drive.get('label') or drive.get('root')}",
                    disk_text,
                )
            )
    else:
        lines.append(_summary_line("Disk", None))

    python_info = sections["python"]

    if python_info.get("available"):
        python_text = (
            f"{python_info.get('implementation') or 'Python'} "
            f"{python_info.get('version') or ''}"
        ).strip()

        if python_info.get("executable"):
            python_text += f" at {python_info['executable']}"

        lines.append(_summary_line("Python", python_text))
    else:
        lines.append(_summary_line("Python", None))

    summary = "\n".join(lines)

    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 3].rstrip() + "..."

    return summary


def _collect_summary() -> dict[str, Any]:
    sections = {
        "os": _collect_os_info(),
        "hostname": _collect_hostname(),
        "cpu": _collect_cpu_info(),
        "memory": _collect_memory_info(),
        "python": _collect_python_info(),
    }

    try:
        sections["disk"] = _collect_disk_info()

    except Exception:
        # A missing or unapproved disk is not a reason to lose the rest of the
        # summary. The section is explicitly marked unavailable instead.
        sections["disk"] = {
            "available": False,
            "count": 0,
            "drives": [],
        }

    return {
        "available": True,
        "summary": _build_summary(sections),
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class SystemInfoInput(StrictModel):
    """The read-only system information tools take no arguments."""


class SystemDiskInput(StrictModel):
    drive: str | None = Field(
        default=None,
        max_length=16,
        description=(
            "Optional approved drive selector, for example 'C:'. Omit to "
            "report every approved local drive."
        ),
    )


class _SystemInfoTool(BaseTool):
    """Shared execution and safety behavior for the system tools."""

    category = ToolCategory.READ_ONLY
    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str] = "system"
    idempotent: ClassVar[bool] = True
    requires_confirmation: ClassVar[bool] = False
    os_capability: ClassVar[str | None] = "system_info"

    @property
    def enabled(self) -> bool:
        return bool(
            settings.TOOL_OS_AUTOMATION_ENABLED
            and settings.TOOL_SYSTEM_INFO_ENABLED
        )

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        raise NotImplementedError

    async def execute(
        self,
        context: ToolContext,
        arguments: StrictModel,
    ) -> ToolResult:
        try:
            data = self.collect(arguments)

        except ToolError:
            raise

        except Exception:
            # Never expose a platform exception or traceback. The executor
            # converts this into a structured execution error.
            raise ToolExecutionError(
                "System information is unavailable."
            ) from None

        return success_result(
            tool_name=self.name,
            data=data,
            provider=self.provider_label,
        )


class SystemOsInfoTool(_SystemInfoTool):
    name = "system_os_info"
    description = (
        "Return read-only operating system information: OS name, release, "
        "version, Windows build when available, machine type, and "
        "architecture. No files, environment variables, or secrets are read."
    )
    input_model = SystemInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_os_info()


class SystemHostnameTool(_SystemInfoTool):
    name = "system_hostname"
    description = (
        "Return the local computer hostname. Read-only and local-only; no "
        "DNS or network lookup is performed."
    )
    input_model = SystemInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_hostname()


class SystemCpuInfoTool(_SystemInfoTool):
    name = "system_cpu_info"
    description = (
        "Return CPU model text when available, the logical processor count, "
        "the physical core count when available, and the machine "
        "architecture. Read-only."
    )
    input_model = SystemInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_cpu_info()


class SystemMemoryInfoTool(_SystemInfoTool):
    name = "system_memory_info"
    description = (
        "Return total, available, and used physical RAM plus the memory usage "
        "percentage. Read-only; no process or page-file data is read."
    )
    input_model = SystemInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_memory_info()


class SystemDiskInfoTool(_SystemInfoTool):
    name = "system_disk_info"
    description = (
        "Return total, used, and available space and usage percentage for "
        "approved local drives. An optional 'drive' selector such as 'C:' "
        "limits the result to one approved drive. Read-only; no files or "
        "directories are listed."
    )
    input_model = SystemDiskInput

    @property
    def enabled(self) -> bool:
        return bool(
            settings.TOOL_OS_AUTOMATION_ENABLED
            and settings.TOOL_SYSTEM_INFO_ENABLED
            and _approved_disk_roots()
        )

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_disk_info(getattr(arguments, "drive", None))


class SystemPythonInfoTool(_SystemInfoTool):
    name = "system_python_info"
    description = (
        "Return the running Python version, implementation, build, compiler, "
        "and executable location. Read-only."
    )
    input_model = SystemInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_python_info()


class SystemSummaryTool(_SystemInfoTool):
    name = "system_summary"
    description = (
        "Return a consolidated human-readable summary of the OS, hostname, "
        "CPU, memory, approved local disk usage, and Python runtime. "
        "Read-only."
    )
    input_model = SystemInfoInput

    def collect(self, arguments: StrictModel) -> dict[str, Any]:
        return _collect_summary()


SYSTEM_INFO_TOOLS = (
    SystemOsInfoTool(),
    SystemHostnameTool(),
    SystemCpuInfoTool(),
    SystemMemoryInfoTool(),
    SystemDiskInfoTool(),
    SystemPythonInfoTool(),
    SystemSummaryTool(),
)


__all__ = [
    "SYSTEM_INFO_TOOLS",
    "SystemCpuInfoTool",
    "SystemDiskInfoTool",
    "SystemDiskInput",
    "SystemHostnameTool",
    "SystemInfoInput",
    "SystemMemoryInfoTool",
    "SystemOsInfoTool",
    "SystemPythonInfoTool",
    "SystemSummaryTool",
]
