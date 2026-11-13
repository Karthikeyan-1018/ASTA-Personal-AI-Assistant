"""Safe file-system tools confined to explicitly approved workspace roots.

Security properties:

* Every path is resolved (following symlinks) and must live inside one of the
  configured roots. A symlink or junction that points outside a root is
  rejected.
* Configuration rejects drive roots, Windows system directories, and roots that
  contain them, so a broad root can never expose the operating system.
* Credential-like files and directories are denied regardless of the root.
* Windows aliases (trailing dots or spaces, alternate data streams, and
  reserved device names) are rejected before the filesystem is touched.
* Reads are limited by size and must be UTF-8 text. Known binary formats are
  rejected.
* Writes are bounded, text-only, and atomic. Creating or updating a file always
  requires explicit confirmation.
* There is no delete tool, no recursive mutation, and no way to execute a file.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Iterable

from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolExecutionError,
    ToolSizeLimitError,
    ToolValidationError,
)
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


SENSITIVE_DIR_NAMES = {
    ".ssh",
    ".aws",
    ".azure",
    ".docker",
    ".gnupg",
    ".git",
    ".kube",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".git-credentials",
    ".htpasswd",
    ".netrc",
    ".npmrc",
    ".pgpass",
    ".pypirc",
    ".vault-token",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}

# Matches a credential-ish token with a clear boundary so ordinary words such
# as ``secretary.py`` are not denied.
_SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:^|[._-])(?:credentials?|secrets?)(?:[._-]|$)",
    re.IGNORECASE,
)

SENSITIVE_SUFFIXES = {
    ".asc",
    ".der",
    ".env",
    ".gpg",
    ".jks",
    ".kdbx",
    ".key",
    ".keystore",
    ".p12",
    ".p8",
    ".pem",
    ".pfx",
    ".ppk",
    ".tfstate",
    ".tfvars",
}

PRIVATE_KEY_PREFIXES = (
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
)

# File formats that are never treated as readable or writable text. Unknown
# binary content is still rejected by the content heuristic in ``fs_read``.
BINARY_SUFFIXES = {
    ".7z",
    ".a",
    ".apk",
    ".avi",
    ".bin",
    ".bmp",
    ".bz2",
    ".cab",
    ".class",
    ".com",
    ".cpl",
    ".db",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".drv",
    ".ear",
    ".eot",
    ".exe",
    ".flac",
    ".gif",
    ".gz",
    ".heic",
    ".ico",
    ".ipa",
    ".iso",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lib",
    ".lnk",
    ".m4a",
    ".mdb",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".msp",
    ".msi",
    ".o",
    ".obj",
    ".ocx",
    ".ogg",
    ".otf",
    ".pdb",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".pyc",
    ".pyo",
    ".rar",
    ".rpm",
    ".scr",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".sys",
    ".tar",
    ".tgz",
    ".tif",
    ".tiff",
    ".ttf",
    ".war",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".xz",
    ".zip",
    ".zst",
}

IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
}

# Windows reserved device names. They alias devices rather than files and can
# bypass ordinary path expectations.
WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}

# Windows system locations. A configured root may not be one of these, live
# inside one, or contain one.
WINDOWS_SYSTEM_ENV_VARS = (
    "WINDIR",
    "SystemRoot",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
)

POSIX_SYSTEM_DIRECTORIES = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/lib",
    "/lib64",
    "/var/lib",
)

MAX_SEARCH_DIRECTORIES = 5000
MAX_BINARY_SAMPLE_BYTES = 8192


class _FsBase(BaseTool):
    """Shared path validation for every file-system tool."""

    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str] = "filesystem"

    @property
    def enabled(self) -> bool:
        return bool(settings.TOOL_FILESYSTEM_ENABLED and _workspace_roots())

    def _roots(self) -> list[Path]:
        roots = _workspace_roots()

        if not roots:
            raise ToolConfigurationError(
                "No approved workspace directory is configured."
            )

        return roots

    def _resolve(
        self,
        raw: str,
        *,
        must_exist: bool = False,
        for_write: bool = False,
    ) -> Path:
        return _resolve_path(
            raw,
            roots=self._roots(),
            must_exist=must_exist,
            for_write=for_write,
        )


class FsListInput(StrictModel):
    path: str = Field(
        default=".",
        max_length=1024,
        description=(
            "Directory path relative to an approved workspace root, or an "
            "absolute path inside one."
        ),
    )


class FsListTool(_FsBase):
    name: ClassVar[str] = "fs_list"
    description: ClassVar[str] = (
        "List files and directories inside an approved workspace. Does not "
        "read file contents."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = FsListInput
    idempotent: ClassVar[bool] = True

    async def execute(
        self,
        context: ToolContext,
        arguments: FsListInput,
    ) -> ToolResult:
        target = self._resolve(arguments.path, must_exist=True)

        if not target.is_dir():
            raise ToolExecutionError("The requested path is not a directory.")

        limit = max(1, int(settings.TOOL_FILE_MAX_LIST_ENTRIES))
        entries: list[dict[str, Any]] = []
        truncated = False

        try:
            with os.scandir(target) as iterator:
                for entry in iterator:
                    if _is_denied(Path(entry.path)):
                        continue

                    if len(entries) >= limit:
                        truncated = True
                        break

                    entries.append(_entry_summary(entry))

        except OSError as error:
            raise ToolExecutionError(
                "The directory could not be listed."
            ) from error

        entries.sort(key=lambda item: (item["type"] != "directory", item["name"]))

        return success_result(
            tool_name=self.name,
            data={
                "path": _relative_label(target, self._roots()),
                "entries": entries,
                "truncated": truncated,
            },
        )


class FsSearchInput(StrictModel):
    pattern: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Glob pattern for file names, for example '*.py'. Path "
            "separators are not allowed."
        ),
    )
    path: str = Field(
        default=".",
        max_length=1024,
        description="Directory to search within an approved workspace.",
    )


class FsSearchTool(_FsBase):
    name: ClassVar[str] = "fs_search"
    description: ClassVar[str] = (
        "Search for files by name glob inside an approved workspace."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = FsSearchInput
    idempotent: ClassVar[bool] = True

    async def execute(
        self,
        context: ToolContext,
        arguments: FsSearchInput,
    ) -> ToolResult:
        pattern = arguments.pattern.strip()

        if _has_control_characters(pattern):
            raise ToolValidationError(
                "The pattern contains unsupported characters."
            )

        if "/" in pattern or "\\" in pattern or pattern in {".", ".."}:
            raise ToolValidationError(
                "The pattern must be a file-name glob without separators."
            )

        target = self._resolve(arguments.path, must_exist=True)

        if not target.is_dir():
            raise ToolExecutionError("The requested path is not a directory.")

        limit = max(1, int(settings.TOOL_FILE_MAX_SEARCH_RESULTS))
        matches: list[dict[str, Any]] = []
        visited_directories = 0
        truncated = False
        roots = self._roots()

        for directory, dir_names, file_names in os.walk(
            target,
            followlinks=False,
        ):
            visited_directories += 1

            if visited_directories > MAX_SEARCH_DIRECTORIES:
                truncated = True
                break

            # Re-resolve every child directory. A junction can be reported as
            # a directory even when ``followlinks`` is false, so containment
            # and the denied-name policy are checked explicitly.
            dir_names[:] = _safe_subdirectories(
                Path(directory),
                dir_names,
                roots,
            )

            for name in file_names:
                if not fnmatch.fnmatch(name, pattern):
                    continue

                candidate = Path(directory) / name

                if _is_denied(candidate):
                    continue

                try:
                    resolved = candidate.resolve(strict=False)

                except (OSError, RuntimeError, ValueError):
                    continue

                if not _inside(resolved, roots) or _is_denied(resolved):
                    continue

                try:
                    info = candidate.stat(follow_symlinks=False)

                except OSError:
                    continue

                matches.append(
                    {
                        "path": _relative_label(candidate, roots),
                        "size": int(info.st_size),
                        "modified": _modified(info.st_mtime),
                        "type": "file",
                    }
                )

                if len(matches) >= limit:
                    truncated = True
                    break

            if truncated:
                break

        return success_result(
            tool_name=self.name,
            data={
                "pattern": pattern,
                "matches": matches,
                "truncated": truncated,
            },
        )


class FsReadInput(StrictModel):
    path: str = Field(
        min_length=1,
        max_length=1024,
        description="File path inside an approved workspace.",
    )


class FsReadTool(_FsBase):
    name: ClassVar[str] = "fs_read"
    description: ClassVar[str] = (
        "Read a UTF-8 text file inside an approved workspace. Credential "
        "files and binary formats are denied."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = FsReadInput
    idempotent: ClassVar[bool] = True

    async def execute(
        self,
        context: ToolContext,
        arguments: FsReadInput,
    ) -> ToolResult:
        target = self._resolve(arguments.path, must_exist=True)

        if not target.is_file():
            raise ToolExecutionError("The requested path is not a file.")

        _require_text_suffix(target)

        limit = max(1, int(settings.TOOL_FILE_MAX_READ_BYTES))

        try:
            size = target.stat().st_size

            with target.open("rb") as handle:
                # Read a few extra bytes so a multibyte character split at the
                # configured limit can still be decoded correctly.
                raw = handle.read(limit + 4)

        except OSError as error:
            raise ToolExecutionError("The file could not be read.") from error

        if _looks_binary(raw):
            raise ToolExecutionError(
                "The requested file does not appear to be UTF-8 text."
            )

        truncated = size > limit or len(raw) > limit
        text = _decode_bounded_utf8(raw, limit=limit, truncated=truncated)

        return success_result(
            tool_name=self.name,
            data={
                "path": _relative_label(target, self._roots()),
                "content": text,
                "size": int(size),
                "truncated": truncated,
                "untrusted": True,
            },
            metadata={"untrusted_content": True},
        )


class FsCreateInput(StrictModel):
    path: str = Field(
        min_length=1,
        max_length=1024,
        description="New file path inside an approved workspace.",
    )
    content: str = Field(
        default="",
        max_length=200000,
        description="UTF-8 text content to write.",
    )
    overwrite: bool = Field(
        default=False,
        description="Replace an existing file. Still requires confirmation.",
    )


class FsCreateTool(_FsBase):
    name: ClassVar[str] = "fs_create"
    description: ClassVar[str] = (
        "Create a new text file in an approved workspace. Existing files are "
        "not replaced unless overwrite is explicitly true. Requires user "
        "confirmation."
    )
    category: ClassVar[ToolCategory] = ToolCategory.REVERSIBLE
    requires_confirmation: ClassVar[bool] = True
    input_model: ClassVar[type[StrictModel]] = FsCreateInput

    async def execute(
        self,
        context: ToolContext,
        arguments: FsCreateInput,
    ) -> ToolResult:
        target = self._resolve(
            arguments.path,
            must_exist=False,
            for_write=True,
        )

        exists = target.exists()

        if exists and not arguments.overwrite:
            raise ToolExecutionError(
                "That file already exists. Set overwrite to true only if "
                "replacing it is intended."
            )

        if target.is_dir():
            raise ToolExecutionError("The requested path is a directory.")

        if not target.parent.is_dir():
            raise ToolExecutionError(
                "The parent directory does not exist."
            )

        _require_text_suffix(target)
        content = _bounded_bytes(arguments.content, write=True)

        try:
            _write_atomic(
                target,
                content,
                replace=bool(arguments.overwrite),
            )

        except OSError as error:
            raise ToolExecutionError(
                "The file could not be created."
            ) from error

        return success_result(
            tool_name=self.name,
            data={
                "path": _relative_label(target, self._roots()),
                "bytes_written": len(content),
                "created": True,
                "overwritten": exists,
            },
        )

    def summarize(self, arguments: FsCreateInput) -> str:
        action = "Replace" if arguments.overwrite else "Create"
        return (
            f"{action} file '{arguments.path}' "
            f"({len(arguments.content.encode('utf-8'))} bytes)."
        )


class FsUpdateInput(StrictModel):
    path: str = Field(
        min_length=1,
        max_length=1024,
        description="Existing file path inside an approved workspace.",
    )
    content: str = Field(
        default="",
        max_length=200000,
        description="Replacement UTF-8 text content.",
    )


class FsUpdateTool(_FsBase):
    name: ClassVar[str] = "fs_update"
    description: ClassVar[str] = (
        "Replace the contents of an existing text file in an approved "
        "workspace. Requires user confirmation."
    )
    category: ClassVar[ToolCategory] = ToolCategory.REVERSIBLE
    requires_confirmation: ClassVar[bool] = True
    input_model: ClassVar[type[StrictModel]] = FsUpdateInput

    async def execute(
        self,
        context: ToolContext,
        arguments: FsUpdateInput,
    ) -> ToolResult:
        target = self._resolve(
            arguments.path,
            must_exist=True,
            for_write=True,
        )

        if not target.is_file():
            raise ToolExecutionError("The requested path is not a file.")

        _require_text_suffix(target)
        content = _bounded_bytes(arguments.content, write=True)

        try:
            _write_atomic(target, content, replace=True)

        except OSError as error:
            raise ToolExecutionError(
                "The file could not be updated."
            ) from error

        return success_result(
            tool_name=self.name,
            data={
                "path": _relative_label(target, self._roots()),
                "bytes_written": len(content),
                "updated": True,
            },
        )

    def summarize(self, arguments: FsUpdateInput) -> str:
        return (
            f"Replace contents of '{arguments.path}' "
            f"({len(arguments.content.encode('utf-8'))} bytes)."
        )


class FsMetadataInput(StrictModel):
    path: str = Field(
        min_length=1,
        max_length=1024,
        description="File or directory path inside an approved workspace.",
    )


class FsMetadataTool(_FsBase):
    name: ClassVar[str] = "fs_metadata"
    description: ClassVar[str] = (
        "Return metadata for a file or directory inside an approved "
        "workspace. Does not read file contents."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = FsMetadataInput
    idempotent: ClassVar[bool] = True

    async def execute(
        self,
        context: ToolContext,
        arguments: FsMetadataInput,
    ) -> ToolResult:
        roots = self._roots()
        target = self._resolve(arguments.path, must_exist=True)
        requested = _requested_candidate(arguments.path, roots)

        if not target.is_file() and not target.is_dir():
            raise ToolExecutionError(
                "The requested path is not a supported file or directory."
            )

        try:
            info = target.stat()

        except OSError as error:
            raise ToolExecutionError(
                "The requested path could not be inspected."
            ) from error

        return success_result(
            tool_name=self.name,
            data={
                "path": _relative_label(target, roots),
                "name": target.name,
                "type": "directory" if target.is_dir() else "file",
                "size": int(info.st_size),
                "modified": _modified(info.st_mtime),
                "suffix": target.suffix,
                "is_symlink": requested.is_symlink(),
            },
        )


def _workspace_roots() -> list[Path]:
    roots: list[Path] = []

    for raw in settings.tool_workspace_roots:
        expanded = os.path.expandvars(os.path.expanduser(raw))

        try:
            resolved = Path(expanded).resolve(strict=False)

        except (OSError, RuntimeError, ValueError):
            continue

        if not resolved.is_dir():
            continue

        if _forbidden_root(resolved):
            continue

        if resolved not in roots:
            roots.append(resolved)

    return roots


def _forbidden_root(path: Path) -> bool:
    """Return True for a drive root, a system directory, or a parent of one."""

    try:
        resolved = path.resolve(strict=False)

    except (OSError, RuntimeError, ValueError):
        return True

    if resolved.parent == resolved:
        # A drive root such as ``C:\\`` or the POSIX root ``/``.
        return True

    if _is_denied(resolved):
        return True

    for system in _system_directories():
        # Reject both "root is inside a system directory" and "root contains a
        # system directory". The second rule rejects broad roots such as C:\.
        if _inside(resolved, [system]) or _inside(system, [resolved]):
            return True

    return False


def _system_directories() -> list[Path]:
    directories: list[Path] = []

    if os.name == "nt":
        candidates = [
            os.environ.get(name)
            for name in WINDOWS_SYSTEM_ENV_VARS
        ]
    else:
        candidates = list(POSIX_SYSTEM_DIRECTORIES)

    for raw in candidates:
        if not raw:
            continue

        try:
            resolved = Path(raw).resolve(strict=False)

        except (OSError, RuntimeError, ValueError):
            continue

        if resolved not in directories:
            directories.append(resolved)

    return directories


def _norm(value: Path) -> str:
    return os.path.normcase(str(value))


def _inside(path: Path, roots: Iterable[Path]) -> bool:
    candidate = _norm(path)

    for root in roots:
        base = _norm(root)

        if candidate == base:
            return True

        if candidate.startswith(base + os.sep):
            return True

    return False


def _normalized_name(name: str) -> str:
    """Normalize a path component for case- and Windows-alias comparisons."""

    return name.strip().rstrip(". ").lower()


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_denied(path: Path) -> bool:
    for part in path.parts:
        normalized = _normalized_name(part)

        if not normalized:
            continue

        if normalized in SENSITIVE_FILE_NAMES:
            return True

        if normalized in SENSITIVE_DIR_NAMES:
            return True

        if normalized.startswith(".env"):
            return True

        if normalized.startswith(PRIVATE_KEY_PREFIXES):
            return True

        if any(
            normalized.endswith(suffix)
            for suffix in SENSITIVE_SUFFIXES
        ):
            return True

        if _SENSITIVE_NAME_PATTERN.search(normalized):
            return True

        if os.name == "nt" and _reserved_windows_name(normalized):
            return True

    return False


def _reserved_windows_name(component: str) -> bool:
    stem = component.split(".", 1)[0].strip().rstrip(". ")

    return stem.lower() in WINDOWS_RESERVED_NAMES


def _validate_raw_path(raw: str) -> str:
    if not isinstance(raw, str):
        raise ToolValidationError("A file path is required.")

    cleaned = raw.strip()

    if not cleaned:
        raise ToolValidationError("A valid file path is required.")

    if _has_control_characters(cleaned):
        raise ToolValidationError(
            "The file path contains unsupported control characters."
        )

    if os.name == "nt":
        # Validate the original value so a trailing space cannot be silently
        # normalized into a different Windows path.
        _validate_windows_path(raw)

    return cleaned


def _validate_windows_path(raw: str) -> None:
    """Reject Windows path aliases that can bypass name-based checks."""

    drive_prefix = (
        len(raw) >= 2
        and raw[0].isalpha()
        and raw[1] == ":"
    )

    if drive_prefix and len(raw) > 2 and raw[2] not in "\\/":
        raise ToolValidationError(
            "Drive-relative paths are not allowed; include a full path."
        )

    if raw.startswith(("\\", "/")) and not raw.startswith("\\\\"):
        raise ToolValidationError(
            "Absolute paths must include a drive letter."
        )

    remainder = raw[2:] if drive_prefix else raw

    if ":" in remainder:
        raise ToolValidationError(
            "Alternate data streams are not allowed in file paths."
        )

    for segment in re.split(r"[\\/]+", raw):
        if not segment or segment in {".", ".."}:
            continue

        if len(segment) >= 2 and segment[1] == ":" and segment[0].isalpha():
            continue

        if segment.endswith((" ", ".")):
            raise ToolValidationError(
                "Windows paths may not end a name with a dot or space."
            )

        if _reserved_windows_name(segment):
            raise ToolValidationError(
                "Windows reserved device names are not allowed."
            )


def _requested_candidate(raw: str, roots: list[Path]) -> Path:
    """Return the un-resolved path the caller asked for.

    ``_resolve_path`` has already validated the input. This helper only exists
    so metadata can report whether the final component itself is a symlink.
    """

    expanded = os.path.expandvars(os.path.expanduser(raw.strip()))
    candidate = Path(expanded)

    if not candidate.is_absolute():
        candidate = roots[0] / candidate

    return candidate


def _resolve_path(
    raw: str,
    *,
    roots: list[Path],
    must_exist: bool,
    for_write: bool = False,
) -> Path:
    cleaned = _validate_raw_path(raw)
    expanded = os.path.expandvars(os.path.expanduser(cleaned))

    if expanded != cleaned:
        _validate_raw_path(expanded)

    candidate = Path(expanded)

    if not candidate.is_absolute():
        candidate = roots[0] / candidate

    try:
        # resolve() follows symlinks and junctions so a link that escapes the
        # root is rejected by the containment check below.
        resolved = candidate.resolve(strict=False)

    except (OSError, RuntimeError, ValueError) as error:
        raise ToolValidationError("The file path is invalid.") from error

    if not _inside(resolved, roots):
        raise ToolValidationError(
            "That path is outside the approved workspace."
        )

    if _is_denied(candidate) or _is_denied(resolved):
        raise ToolValidationError(
            "Access to credential and secret files is denied."
        )

    if for_write and candidate.is_symlink():
        raise ToolValidationError(
            "Writing through a symbolic link is not allowed."
        )

    if must_exist and not resolved.exists():
        raise ToolExecutionError("The requested path does not exist.")

    return resolved


def _require_text_suffix(path: Path) -> None:
    if path.suffix.lower() in BINARY_SUFFIXES:
        raise ToolExecutionError(
            "This file type is not supported for text operations."
        )


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False

    sample = raw[:MAX_BINARY_SAMPLE_BYTES]

    if b"\x00" in sample:
        return True

    control = sum(
        1
        for byte in sample
        if (byte < 32 and byte not in (9, 10, 12, 13)) or byte == 127
    )

    return control > max(8, len(sample) // 10)


def _decode_bounded_utf8(
    raw: bytes,
    *,
    limit: int,
    truncated: bool,
) -> str:
    """Decode up to ``limit`` bytes without failing on a split character."""

    if not truncated:
        try:
            return raw.decode("utf-8")

        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                "The requested file does not appear to be UTF-8 text."
            ) from error

    # A UTF-8 code point is at most four bytes. Retry only the last three
    # positions so genuine invalid bytes elsewhere in the sample still fail.
    for cut in range(limit, max(-1, limit - 4), -1):
        try:
            return raw[:cut].decode("utf-8")

        except UnicodeDecodeError:
            continue

    raise ToolExecutionError(
        "The requested file does not appear to be UTF-8 text."
    )


def _safe_subdirectories(
    directory: Path,
    names: list[str],
    roots: list[Path],
) -> list[str]:
    safe: list[str] = []

    for name in names:
        lowered = name.lower()

        if lowered in IGNORED_DIR_NAMES or lowered in SENSITIVE_DIR_NAMES:
            continue

        candidate = directory / name

        try:
            if candidate.is_symlink():
                continue

            resolved = candidate.resolve(strict=False)

        except (OSError, RuntimeError, ValueError):
            continue

        if not _inside(resolved, roots) or _is_denied(candidate):
            continue

        safe.append(name)

    return safe


def _bounded_bytes(content: str, *, write: bool) -> bytes:
    if not isinstance(content, str):
        raise ToolValidationError("File content must be text.")

    if "\x00" in content:
        raise ToolValidationError(
            "File content must be text without NUL bytes."
        )

    encoded = content.encode("utf-8")
    limit = max(
        1,
        int(
            settings.TOOL_FILE_MAX_WRITE_BYTES
            if write
            else settings.TOOL_FILE_MAX_READ_BYTES
        ),
    )

    if len(encoded) > limit:
        raise ToolSizeLimitError(
            f"File content exceeds the {limit}-byte limit."
        )

    return encoded


def _write_atomic(target: Path, content: bytes, *, replace: bool) -> None:
    """Write text without exposing a partially written destination.

    ``replace`` uses a same-directory temporary file and ``os.replace``, which
    is atomic on the supported platforms. Non-replacing creates use an
    exclusive open so a concurrent writer can never be silently clobbered.
    """

    parent = target.parent

    if not replace:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
        )

        try:
            descriptor = os.open(str(target), flags, 0o600)

        except FileExistsError as error:
            raise OSError("The destination already exists.") from error

        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

        except BaseException:
            _safe_unlink(target)
            raise

        return

    descriptor, temporary = tempfile.mkstemp(
        prefix=".asta-write-",
        suffix=".tmp",
        dir=str(parent),
    )

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        try:
            mode = stat.S_IMODE(target.stat().st_mode)
            os.chmod(temporary, mode)

        except OSError:
            pass

        os.replace(temporary, target)

    except BaseException:
        _safe_unlink(Path(temporary))
        raise


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()

    except OSError:
        pass


def _relative_label(path: Path, roots: list[Path]) -> str:
    for root in roots:
        if _inside(path, [root]):
            try:
                return path.relative_to(root).as_posix() or "."

            except ValueError:
                continue

    return path.name


def _modified(value: float) -> str:
    return datetime.fromtimestamp(
        value,
        tz=timezone.utc,
    ).isoformat()


def _entry_summary(entry: os.DirEntry) -> dict[str, Any]:
    try:
        is_symlink = entry.is_symlink()
        is_directory = entry.is_dir(follow_symlinks=False)
        info = entry.stat(follow_symlinks=False)

        if is_symlink:
            entry_type = "symlink"
        elif is_directory:
            entry_type = "directory"
        else:
            entry_type = "file"

        return {
            "name": entry.name,
            "type": entry_type,
            "size": None if is_directory else int(info.st_size),
            "modified": _modified(info.st_mtime),
            "is_symlink": is_symlink,
        }

    except OSError:
        return {
            "name": entry.name,
            "type": "unknown",
            "size": None,
            "modified": None,
            "is_symlink": False,
        }


FS_TOOLS = (
    FsListTool(),
    FsSearchTool(),
    FsReadTool(),
    FsCreateTool(),
    FsUpdateTool(),
    FsMetadataTool(),
)


__all__ = [
    "FS_TOOLS",
    "FsCreateTool",
    "FsListTool",
    "FsMetadataTool",
    "FsReadTool",
    "FsSearchTool",
    "FsUpdateTool",
]
