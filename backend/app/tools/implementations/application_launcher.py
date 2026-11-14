"""Secure allowlisted desktop application launching (Phase 12B).

The model may ask ASTA to open an application by alias, but it can never
supply an executable path or command-line arguments. Every launch is resolved
against a server-side allowlist and requires explicit user confirmation.

Security properties:

* the tool is sensitive, confirmation-required, disabled by default, and
  denied for private requests;
* only aliases present in ``TOOL_APPLICATION_ALLOWLIST`` can be launched;
* an executable path comes exclusively from configuration, never from tool
  arguments;
* each configured path must be an absolute ``.exe`` file on a fixed local
  Windows drive. Relative paths, UNC/network paths, directories, missing
  files, traversal components, symlinks, and reparse points are rejected;
* launching uses ``subprocess.Popen([executable], shell=False)`` with no shell
  interpretation, no extra arguments, and no connected standard streams;
* unsupported operating systems fail closed;
* no environment variable, secret, file content, or process list is read or
  returned, and no existing process is terminated or modified.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext, json_preview
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolError,
    ToolExecutionError,
    ToolValidationError,
)
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


MAX_ALIAS_CHARS = 64
MAX_PATH_CHARS = 260
MAX_ALLOWLIST_ENTRIES = 32

DRIVE_FIXED = 3
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# A path may never be smuggled through the alias field. These characters and
# sequences are rejected before normalization, so ``..\evil`` or ``C:\x.exe``
# cannot be reduced to a configured alias.
_FORBIDDEN_ALIAS_FRAGMENTS = ("/", "\\", ":", "=", "..")

_ALLOWED_APPLICATION_SUFFIX = ".exe"


@dataclass(frozen=True, slots=True)
class ApprovedApplication:
    """One resolved, validated allowlist entry."""

    alias: str
    display_name: str
    executable: str


def _is_supported_platform() -> bool:
    """Application launching is implemented for Windows only."""

    return os.name == "nt"


def _normalize_alias(value: Any) -> str:
    """Return a stable alias key or an empty string.

    Case, whitespace, underscores, hyphens, and punctuation are ignored, so
    ``VS Code``, ``vs-code``, and ``vscode`` resolve to the same entry. Path
    separators, drive syntax, ``=``, and ``..`` are rejected outright so an
    alias cannot be used to inject a path.
    """

    try:
        text = str(value or "").strip()

    except Exception:
        return ""

    if not text or len(text) > MAX_ALIAS_CHARS:
        return ""

    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        return ""

    if any(fragment in text for fragment in _FORBIDDEN_ALIAS_FRAGMENTS):
        return ""

    normalized = re.sub(r"[^a-z0-9]+", "", text.lower())

    if not normalized or len(normalized) > MAX_ALIAS_CHARS:
        return ""

    return normalized


def _validated_alias(value: Any) -> str:
    alias = _normalize_alias(value)

    if not alias:
        raise ToolValidationError("That application name is not valid.")

    return alias


def _is_fixed_local_drive(path: str) -> bool:
    """Return whether ``path`` lives on a fixed local Windows drive.

    ``GetDriveTypeW`` is a read-only kernel query. Network shares, removable
    volumes, CD-ROM drives, and RAM disks are rejected.
    """

    if os.name != "nt":
        return False

    drive, _tail = os.path.splitdrive(path)

    if not drive:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_drive_type = kernel32.GetDriveTypeW
        get_drive_type.argtypes = [wintypes.LPCWSTR]
        get_drive_type.restype = wintypes.UINT

        return get_drive_type(drive + "\\") == DRIVE_FIXED

    except Exception:
        return False


def _has_reparse_point(path: str) -> bool:
    """Detect symlinks and junctions without following them."""

    try:
        attributes = getattr(os.stat(path), "st_file_attributes", 0)

    except OSError:
        return True

    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _validate_executable(raw_path: Any) -> str:
    """Validate one configured executable path or raise a safe error."""

    try:
        text = str(raw_path or "").strip()

    except Exception:
        raise ToolValidationError(
            "The approved executable path is invalid."
        ) from None

    if not text or len(text) > MAX_PATH_CHARS or "\x00" in text:
        raise ToolValidationError("The approved executable path is invalid.")

    # Reject UNC and device paths before any filesystem call.
    if text.startswith("\\\\") or text.startswith("//"):
        raise ToolValidationError("Network executable paths are not allowed.")

    if not os.path.isabs(text):
        raise ToolValidationError(
            "The approved executable path must be absolute."
        )

    if ".." in Path(text).parts:
        raise ToolValidationError("The approved executable path is invalid.")

    if Path(text).suffix.lower() != _ALLOWED_APPLICATION_SUFFIX:
        raise ToolValidationError(
            "Only .exe applications can be launched."
        )

    try:
        resolved = Path(text).resolve(strict=True)

    except (OSError, ValueError):
        raise ToolValidationError(
            "The approved executable is unavailable."
        ) from None

    if not resolved.is_file():
        raise ToolValidationError("The approved executable is not a file.")

    if os.path.islink(text) or _has_reparse_point(text):
        raise ToolValidationError("Links to executables are not allowed.")

    if not _is_fixed_local_drive(str(resolved)):
        raise ToolValidationError(
            "Only fixed local drives are allowed."
        )

    return str(resolved)


def _configured_applications() -> dict[str, tuple[str, str]]:
    """Return ``normalized alias -> (display name, configured path)``.

    Parsing is bounded and never raises. The first normalized alias wins, so a
    later duplicate cannot silently replace an approved entry.
    """

    try:
        configured = settings.tool_application_allowlist or {}

    except Exception:
        return {}

    if not isinstance(configured, dict):
        return {}

    applications: dict[str, tuple[str, str]] = {}

    for index, (alias, path) in enumerate(configured.items()):
        if index >= MAX_ALLOWLIST_ENTRIES:
            break

        key = _normalize_alias(alias)

        if not key or key in applications:
            continue

        display = " ".join(str(alias).split())[:MAX_ALIAS_CHARS] or key
        applications[key] = (display, str(path).strip())

    return applications


def _approved_applications() -> dict[str, ApprovedApplication]:
    """Return only entries whose executable passes validation."""

    approved: dict[str, ApprovedApplication] = {}

    for key, (display, path) in _configured_applications().items():
        try:
            executable = _validate_executable(path)

        except ToolValidationError:
            continue

        approved[key] = ApprovedApplication(
            alias=key,
            display_name=display,
            executable=executable,
        )

    return approved


def _resolve_application(value: Any) -> ApprovedApplication:
    """Resolve an alias to an approved, validated executable."""

    alias = _validated_alias(value)
    configured = _configured_applications()

    if alias not in configured:
        raise ToolValidationError(
            "That application is not on the approved list."
        )

    display, path = configured[alias]
    executable = _validate_executable(path)

    return ApprovedApplication(
        alias=alias,
        display_name=display,
        executable=executable,
    )


def _launch_process(executable: str) -> Any:
    """Start one executable with no shell, arguments, or inherited streams."""

    if not _is_supported_platform():
        raise ToolConfigurationError(
            "Application launching is only supported on Windows."
        )

    return subprocess.Popen(
        [executable],
        shell=False,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class LaunchApplicationInput(StrictModel):
    application: str = Field(
        min_length=1,
        max_length=MAX_ALIAS_CHARS,
        description=(
            "Approved application alias, for example 'vscode' or "
            "'notepad'. Executable paths and command-line arguments are "
            "never accepted."
        ),
    )


class LaunchApplicationTool(BaseTool):
    name: ClassVar[str] = "system_launch_application"
    description: ClassVar[str] = (
        "Launch an approved desktop application by alias. Only applications "
        "on the server-side allowlist can be launched. Every launch requires "
        "explicit user confirmation. Executable paths and command-line "
        "arguments are never accepted from the model."
    )
    category: ClassVar[ToolCategory] = ToolCategory.SENSITIVE
    requires_confirmation: ClassVar[bool] = True
    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str | None] = "application_launcher"
    idempotent: ClassVar[bool] = False
    os_capability: ClassVar[str | None] = "application_launch"
    input_model: ClassVar[type[StrictModel]] = LaunchApplicationInput

    @property
    def enabled(self) -> bool:
        return bool(
            settings.TOOL_OS_AUTOMATION_ENABLED
            and settings.TOOL_APPLICATION_LAUNCH_ENABLED
            and _is_supported_platform()
            and _approved_applications()
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> StrictModel:
        """Reject unknown or invalid applications before confirmation.

        Resolving here means an unapproved alias never creates a pending
        confirmation. The executor resolves again after approval, so a
        configuration change between proposal and execution still fails
        closed.
        """

        validated = super().validate_arguments(arguments)
        _resolve_application(validated.application)

        return validated

    def summarize(self, arguments: StrictModel) -> str:
        try:
            application = _resolve_application(arguments.application)

        except ToolError:
            return json_preview({"application": "<unapproved>"})

        return json_preview(
            {
                "application": application.display_name,
                "alias": application.alias,
            }
        )

    async def execute(
        self,
        context: ToolContext,
        arguments: StrictModel,
    ) -> ToolResult:
        if not _is_supported_platform():
            raise ToolConfigurationError(
                "Application launching is only supported on Windows."
            )

        application = _resolve_application(arguments.application)

        try:
            process = await asyncio.to_thread(
                _launch_process,
                application.executable,
            )

        except ToolError:
            raise

        except Exception:
            # Never expose the operating-system exception or a traceback.
            raise ToolExecutionError(
                "The application could not be launched."
            ) from None

        pid = getattr(process, "pid", None)

        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            pid = None

        return success_result(
            tool_name=self.name,
            data={
                "launched": True,
                "application": application.display_name,
                "alias": application.alias,
                "pid": pid,
                "message": (
                    f"Launched {application.display_name}."
                ),
            },
            provider=self.provider_label,
        )


__all__ = [
    "ApprovedApplication",
    "LaunchApplicationInput",
    "LaunchApplicationTool",
    "MAX_ALLOWLIST_ENTRIES",
    "MAX_ALIAS_CHARS",
]
