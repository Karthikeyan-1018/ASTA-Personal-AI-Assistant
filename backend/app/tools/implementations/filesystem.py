"""Safe file-system tools confined to explicitly approved workspace roots.

Security properties:

* Every path is resolved (following symlinks) and must live inside one of the
  configured roots. A symlink that points outside a root is rejected.
* Credential-like files and directories are denied regardless of the root.
* Reads are limited by size and must be UTF-8 text.
* There is no delete tool and no recursive mutation.
* Creating or updating a file always requires explicit confirmation.
"""

from __future__ import annotations

import fnmatch
import os
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
    ".gnupg",
    ".git",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".netrc",
    ".pgpass",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".keystore",
    ".jks",
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

MAX_SEARCH_DIRECTORIES = 5000


class _FsBase(BaseTool):
    """Shared path validation for every file-system tool."""

    allowed_in_private: ClassVar[bool] = False

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

    def _resolve(self, raw: str, *, must_exist: bool = False) -> Path:
        return _resolve_path(raw, roots=self._roots(), must_exist=must_exist)


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

    async def execute(
        self,
        context: ToolContext,
        arguments: FsSearchInput,
    ) -> ToolResult:
        pattern = arguments.pattern.strip()

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

        for directory, dir_names, file_names in os.walk(target):
            visited_directories += 1

            if visited_directories > MAX_SEARCH_DIRECTORIES:
                truncated = True
                break

            dir_names[:] = [
                name
                for name in dir_names
                if name.lower() not in IGNORED_DIR_NAMES
                and name.lower() not in SENSITIVE_DIR_NAMES
            ]

            for name in file_names:
                if not fnmatch.fnmatch(name, pattern):
                    continue

                candidate = Path(directory) / name

                if _is_denied(candidate):
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
        "files are denied."
    )
    category: ClassVar[ToolCategory] = ToolCategory.READ_ONLY
    input_model: ClassVar[type[StrictModel]] = FsReadInput

    async def execute(
        self,
        context: ToolContext,
        arguments: FsReadInput,
    ) -> ToolResult:
        target = self._resolve(arguments.path, must_exist=True)

        if not target.is_file():
            raise ToolExecutionError("The requested path is not a file.")

        limit = max(1, int(settings.TOOL_FILE_MAX_READ_BYTES))
        size = target.stat().st_size

        try:
            with target.open("rb") as handle:
                raw = handle.read(limit + 1)

        except OSError as error:
            raise ToolExecutionError("The file could not be read.") from error

        if b"\x00" in raw:
            raise ToolExecutionError(
                "The requested file does not appear to be UTF-8 text."
            )

        try:
            text = raw[:limit].decode("utf-8")

        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                "The requested file does not appear to be UTF-8 text."
            ) from error

        truncated = size > limit or len(raw) > limit

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
        target = self._resolve(arguments.path, must_exist=False)

        if target.exists() and not arguments.overwrite:
            raise ToolExecutionError(
                "That file already exists. Set overwrite to true only if "
                "replacing it is intended."
            )

        if target.is_dir():
            raise ToolExecutionError("The requested path is a directory.")

        if not target.parent.exists():
            raise ToolExecutionError(
                "The parent directory does not exist."
            )

        content = _bounded_bytes(arguments.content, write=True)

        try:
            with target.open("wb") as handle:
                handle.write(content)

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
        target = self._resolve(arguments.path, must_exist=True)

        if not target.is_file():
            raise ToolExecutionError("The requested path is not a file.")

        content = _bounded_bytes(arguments.content, write=True)

        try:
            with target.open("wb") as handle:
                handle.write(content)

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

    async def execute(
        self,
        context: ToolContext,
        arguments: FsMetadataInput,
    ) -> ToolResult:
        target = self._resolve(arguments.path, must_exist=True)

        try:
            info = target.stat()

        except OSError as error:
            raise ToolExecutionError(
                "The requested path could not be inspected."
            ) from error

        return success_result(
            tool_name=self.name,
            data={
                "path": _relative_label(target, self._roots()),
                "name": target.name,
                "type": "directory" if target.is_dir() else "file",
                "size": int(info.st_size),
                "modified": _modified(info.st_mtime),
                "suffix": target.suffix,
                "is_symlink": target.is_symlink(),
            },
        )


def _workspace_roots() -> list[Path]:
    roots: list[Path] = []

    for raw in settings.tool_workspace_roots:
        expanded = os.path.expandvars(os.path.expanduser(raw))

        try:
            resolved = Path(expanded).resolve(strict=False)

        except (OSError, ValueError):
            continue

        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)

    return roots


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


def _is_denied(path: Path) -> bool:
    name = path.name.lower()

    if name in SENSITIVE_FILE_NAMES or name.startswith(".env"):
        return True

    if any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        return True

    for part in path.parts:
        lowered = part.lower()

        if lowered in SENSITIVE_DIR_NAMES:
            return True

    return False


def _resolve_path(
    raw: str,
    *,
    roots: list[Path],
    must_exist: bool,
) -> Path:
    if not isinstance(raw, str):
        raise ToolValidationError("A file path is required.")

    cleaned = raw.strip()

    if not cleaned or "\x00" in cleaned:
        raise ToolValidationError("A valid file path is required.")

    candidate = Path(os.path.expandvars(os.path.expanduser(cleaned)))

    if not candidate.is_absolute():
        candidate = roots[0] / candidate

    try:
        # resolve() follows symlinks so a link that escapes the root is
        # rejected by the containment check below.
        resolved = candidate.resolve(strict=False)

    except (OSError, RuntimeError, ValueError) as error:
        raise ToolValidationError("The file path is invalid.") from error

    if not _inside(resolved, roots):
        raise ToolValidationError(
            "That path is outside the approved workspace."
        )

    if _is_denied(resolved):
        raise ToolValidationError(
            "Access to credential and secret files is denied."
        )

    if must_exist and not resolved.exists():
        raise ToolExecutionError("The requested path does not exist.")

    return resolved


def _bounded_bytes(content: str, *, write: bool) -> bytes:
    if not isinstance(content, str):
        raise ToolValidationError("File content must be text.")

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
        is_directory = entry.is_dir(follow_symlinks=False)
        info = entry.stat(follow_symlinks=False)

        return {
            "name": entry.name,
            "type": "directory" if is_directory else "file",
            "size": None if is_directory else int(info.st_size),
            "modified": _modified(info.st_mtime),
            "is_symlink": entry.is_symlink(),
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
