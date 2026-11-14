"""Phase 11F tests for the safe filesystem tools.

Every test operates inside a temporary directory. No real personal file,
``.env`` file, system directory, or production workspace is read or written.
The suite covers configuration and root validation, path traversal, absolute
path escapes, symlink and junction escapes, Windows path aliases, sensitive
files, binary and oversized content, atomic writes, listing, discovery,
metadata, permissions, confirmations, registry/LLM discovery, and a light
regression check for the Phase 11A-11E tools.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import (
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolExecutionError,
    ToolSizeLimitError,
    ToolValidationError,
)
from app.tools.executor import tool_executor
from app.tools.implementations.filesystem import (
    FS_TOOLS,
    FsCreateTool,
    FsListTool,
    FsMetadataTool,
    FsReadTool,
    FsSearchTool,
    FsUpdateTool,
)
from app.tools.registry import tool_registry
from tests.tool_test_support import ToolEngineTestBase


FS_TOOL_NAMES = {
    "fs_list",
    "fs_search",
    "fs_read",
    "fs_create",
    "fs_update",
    "fs_metadata",
}


class FilesystemUnitTestBase(unittest.IsolatedAsyncioTestCase):
    """Isolated temporary root and patched settings for direct tool calls."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="asta_fs_")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.configure()

    def configure(self, roots=None, **overrides):
        values = {
            "TOOL_OS_AUTOMATION_ENABLED": True,
            "TOOL_FILESYSTEM_ENABLED": True,
            "TOOL_WORKSPACE_ROOTS": (
                str(self.root) if roots is None else roots
            ),
            "TOOL_FILE_MAX_READ_BYTES": 200_000,
            "TOOL_FILE_MAX_WRITE_BYTES": 100_000,
            "TOOL_FILE_MAX_LIST_ENTRIES": 500,
            "TOOL_FILE_MAX_SEARCH_RESULTS": 100,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return path

    def write_bytes(self, relative: str, content: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def temporary_directory(self) -> Path:
        directory = tempfile.TemporaryDirectory(prefix="asta_fs_other_")
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    async def run_tool(self, tool, arguments):
        validated = tool.validate_arguments(arguments)
        return await tool.execute(None, validated)

    def symlink(self, link: Path, target: Path, *, directory=False) -> None:
        try:
            os.symlink(target, link, target_is_directory=directory)

        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symbolic links are unavailable: {error}")

    def junction(self, link: Path, target: Path) -> None:
        if os.name != "nt":
            self.skipTest("Junctions are Windows-only.")

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            self.skipTest(
                "Junctions are unavailable: "
                + (result.stderr or result.stdout).strip()
            )


# ---------------------------------------------------------------------------
# Configuration and root validation
# ---------------------------------------------------------------------------


class FilesystemConfigurationTests(FilesystemUnitTestBase):

    def test_disabled_by_default(self):
        self.configure(TOOL_FILESYSTEM_ENABLED=False)

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_missing_roots_disable_the_tools(self):
        self.configure(roots="")

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_valid_root_enables_the_tools(self):
        self.assertTrue(all(tool.enabled for tool in FS_TOOLS))

    def test_nonexistent_root_is_ignored(self):
        self.configure(roots=str(self.root / "missing"))

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_file_root_is_ignored(self):
        path = self.write("not-a-directory.txt", "x")
        self.configure(roots=str(path))

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_drive_or_filesystem_root_is_rejected(self):
        self.configure(roots=str(Path(self.root.anchor)))

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_windows_system_directory_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        windows = os.environ.get("WINDIR") or os.environ.get("SystemRoot")

        if not windows:
            self.skipTest("Windows directory is unknown.")

        self.configure(roots=windows)

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_windows_system_subdirectory_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        windows = os.environ.get("WINDIR") or os.environ.get("SystemRoot")

        if not windows:
            self.skipTest("Windows directory is unknown.")

        self.configure(roots=str(Path(windows) / "Temp"))

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    def test_sensitive_directory_root_is_rejected(self):
        ssh = self.root / ".ssh"
        ssh.mkdir()
        self.configure(roots=str(ssh))

        self.assertFalse(any(tool.enabled for tool in FS_TOOLS))

    async def test_multiple_roots_are_supported(self):
        second = self.temporary_directory()
        second_file = second / "second.txt"
        second_file.write_text("second", encoding="utf-8")
        self.configure(roots=f"{self.root},{second}")

        self.write("first.txt", "first")

        first = await self.run_tool(FsReadTool(), {"path": "first.txt"})
        second_result = await self.run_tool(
            FsReadTool(),
            {"path": str(second_file)},
        )

        # Both roots are enabled; the absolute path selects the second root.
        self.assertTrue(all(tool.enabled for tool in FS_TOOLS))
        self.assertTrue(first.success)
        self.assertTrue(second_result.success)

    def test_every_tool_has_a_safe_provider_label(self):
        for tool in FS_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.provider_label, "filesystem")

    def test_categories_and_policies_are_preserved(self):
        expected = {
            "fs_list": (ToolCategory.READ_ONLY, False),
            "fs_search": (ToolCategory.READ_ONLY, False),
            "fs_read": (ToolCategory.READ_ONLY, False),
            "fs_metadata": (ToolCategory.READ_ONLY, False),
            "fs_create": (ToolCategory.REVERSIBLE, True),
            "fs_update": (ToolCategory.REVERSIBLE, True),
        }

        for tool in FS_TOOLS:
            category, confirmation = expected[tool.name]
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.category, category)
                self.assertEqual(tool.requires_confirmation, confirmation)
                self.assertFalse(tool.allowed_in_private)


# ---------------------------------------------------------------------------
# Path validation and containment
# ---------------------------------------------------------------------------


class FilesystemPathSafetyTests(FilesystemUnitTestBase):

    async def test_blank_path_is_rejected(self):
        for tool in (FsReadTool(), FsListTool(), FsMetadataTool()):
            with self.subTest(tool=tool.name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(tool, {"path": "   "})

    async def test_control_characters_are_rejected(self):
        for value in ("bad\x00.txt", "bad\n.txt", "bad\t.txt", "bad\x7f.txt"):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": value})

    async def test_parent_traversal_is_rejected(self):
        outside = self.temporary_directory()
        (outside / "outside.txt").write_text("secret", encoding="utf-8")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": f"../{outside.name}/outside.txt"},
            )

        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsReadTool(), {"path": "sub/../../outside.txt"})

    async def test_absolute_path_outside_root_is_rejected(self):
        outside = self.temporary_directory()
        target = outside / "outside.txt"
        target.write_text("secret", encoding="utf-8")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsReadTool(), {"path": str(target)})

    async def test_sibling_with_shared_prefix_is_rejected(self):
        sibling = self.root.parent / f"{self.root.name}_evil"
        sibling.mkdir()
        self.addCleanup(shutil.rmtree, sibling, ignore_errors=True)
        target = sibling / "outside.txt"
        target.write_text("secret", encoding="utf-8")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsReadTool(), {"path": str(target)})

    async def test_absolute_path_inside_root_is_allowed(self):
        target = self.write("inside.txt", "inside")

        result = await self.run_tool(FsReadTool(), {"path": str(target)})

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "inside")

    async def test_relative_path_uses_the_first_root(self):
        first = self.write("first.txt", "first")
        second = self.temporary_directory()
        self.configure(roots=f"{self.root},{second}")

        result = await self.run_tool(FsReadTool(), {"path": "first.txt"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["path"], first.name)

    async def test_windows_backslash_relative_path_is_allowed(self):
        self.write("sub/file.txt", "value")

        result = await self.run_tool(
            FsReadTool(),
            {"path": "sub\\file.txt"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "value")

    async def test_windows_case_insensitive_path_is_allowed(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        self.write("CaseFile.txt", "value")

        result = await self.run_tool(
            FsReadTool(),
            {"path": "casefile.txt"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "value")

    async def test_windows_alternate_data_stream_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        self.write("safe.txt", "value")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsReadTool(), {"path": "safe.txt:hidden"})

    async def test_windows_trailing_dot_or_space_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        for value in ("trailing.", "trailing. ", "trailing "):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": value})

    async def test_windows_reserved_device_names_are_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        for value in ("CON", "aux.txt", "com1.log", "lpt9"):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": value})

    async def test_windows_drive_relative_path_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": f"{self.root.drive}relative.txt"},
            )

    async def test_windows_rooted_path_without_drive_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": "\\relative.txt"},
            )

    async def test_overlong_path_is_rejected_by_schema(self):
        with self.assertRaises(ToolValidationError):
            FsReadTool().validate_arguments({"path": "a" * 1025})

    async def test_symlink_file_outside_root_is_rejected(self):
        outside = self.temporary_directory()
        target = outside / "outside.txt"
        target.write_text("secret", encoding="utf-8")
        link = self.root / "escape.txt"
        self.symlink(link, target)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsReadTool(), {"path": "escape.txt"})

    async def test_symlink_file_inside_root_is_allowed(self):
        target = self.write("target.txt", "inside")
        link = self.root / "link.txt"
        self.symlink(link, target)

        result = await self.run_tool(FsReadTool(), {"path": "link.txt"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "inside")

    async def test_symlink_directory_outside_root_is_rejected(self):
        outside = self.temporary_directory()
        (outside / "outside.txt").write_text("secret", encoding="utf-8")
        link = self.root / "escape-dir"
        self.symlink(link, outside, directory=True)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": "escape-dir/outside.txt"},
            )

    async def test_junction_directory_outside_root_is_rejected(self):
        outside = self.temporary_directory()
        (outside / "outside.txt").write_text("secret", encoding="utf-8")
        link = self.root / "junction-dir"
        self.junction(link, outside)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": "junction-dir/outside.txt"},
            )

    async def test_write_through_symlink_is_rejected(self):
        target = self.write("target.txt", "original")
        link = self.root / "link.txt"
        self.symlink(link, target)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsUpdateTool(),
                {"path": "link.txt", "content": "changed"},
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "original")


# ---------------------------------------------------------------------------
# Sensitive and unsupported file types
# ---------------------------------------------------------------------------


class FilesystemSensitiveFileTests(FilesystemUnitTestBase):

    SENSITIVE_NAMES = (
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "credentials.yaml",
        "secrets.json",
        "app_secrets.json",
        "client-secret.txt",
        "id_rsa",
        "id_rsa_backup",
        "id_ed25519.pub",
        "server.pem",
        "private.key",
        "bundle.p12",
        "state.tfstate",
        ".netrc",
        ".git-credentials",
    )

    async def test_sensitive_files_cannot_be_read(self):
        for name in self.SENSITIVE_NAMES:
            path = self.write(name, "do-not-expose")
            with self.subTest(name=name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(
                        FsReadTool(),
                        {"path": path.name},
                    )

    async def test_sensitive_files_cannot_be_created_or_updated(self):
        for name in self.SENSITIVE_NAMES:
            with self.subTest(name=name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(
                        FsCreateTool(),
                        {"path": name, "content": "x"},
                    )

        target = self.write(".env", "original")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsUpdateTool(),
                {"path": target.name, "content": "changed"},
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    async def test_sensitive_files_cannot_be_inspected(self):
        for name in self.SENSITIVE_NAMES:
            path = self.write(name, "x")
            with self.subTest(name=name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(
                        FsMetadataTool(),
                        {"path": path.name},
                    )

    async def test_sensitive_directories_are_denied(self):
        for directory in (".ssh", ".aws", ".gnupg", ".docker", ".kube"):
            path = self.write(f"{directory}/config", "x")
            with self.subTest(directory=directory):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(
                        FsReadTool(),
                        {"path": str(path.relative_to(self.root))},
                    )

    async def test_sensitive_files_are_hidden_from_listing(self):
        self.write(".env", "secret")
        self.write("secrets.yaml", "secret")
        self.write(".ssh/config", "secret")
        self.write("visible.txt", "value")

        result = await self.run_tool(FsListTool(), {"path": "."})

        names = {entry["name"] for entry in result.data["entries"]}
        self.assertIn("visible.txt", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("secrets.yaml", names)
        self.assertNotIn(".ssh", names)

    async def test_sensitive_files_are_hidden_from_search(self):
        self.write(".env", "secret")
        self.write("secrets.yaml", "secret")
        self.write(".ssh/config", "secret")
        self.write("visible.txt", "value")

        result = await self.run_tool(
            FsSearchTool(),
            {"pattern": "*", "path": "."},
        )

        paths = {match["path"] for match in result.data["matches"]}
        self.assertIn("visible.txt", paths)
        self.assertNotIn(".env", paths)
        self.assertNotIn("secrets.yaml", paths)
        self.assertNotIn(".ssh/config", paths)

    async def test_non_sensitive_similar_name_is_allowed(self):
        self.write("secretary.txt", "value")

        result = await self.run_tool(
            FsReadTool(),
            {"path": "secretary.txt"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "value")

    async def test_windows_trailing_dot_env_alias_is_denied(self):
        if os.name != "nt":
            self.skipTest("Windows-only check.")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": ".env."},
            )

    async def test_binary_suffixes_are_denied_for_reads(self):
        for name in ("image.png", "archive.zip", "program.exe", "doc.pdf"):
            path = self.write(name, "not really binary")
            with self.subTest(name=name):
                with self.assertRaises(ToolExecutionError):
                    await self.run_tool(
                        FsReadTool(),
                        {"path": path.name},
                    )

    async def test_binary_suffixes_are_denied_for_writes(self):
        for name in ("image.png", "archive.zip", "program.exe", "doc.pdf"):
            with self.subTest(name=name):
                with self.assertRaises(ToolExecutionError):
                    await self.run_tool(
                        FsCreateTool(),
                        {"path": name, "content": "text"},
                    )


# ---------------------------------------------------------------------------
# Reading text files
# ---------------------------------------------------------------------------


class FilesystemReadTests(FilesystemUnitTestBase):

    async def test_valid_text_file_is_read(self):
        content = "alpha\nbeta\tgamma\n"
        self.write("notes.txt", content)

        result = await self.run_tool(FsReadTool(), {"path": "notes.txt"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], content)
        self.assertEqual(result.data["size"], len(content.encode("utf-8")))
        self.assertFalse(result.data["truncated"])
        self.assertTrue(result.data["untrusted"])
        self.assertTrue(result.metadata["untrusted_content"])

    async def test_utf8_content_is_read(self):
        content = "café ☕ 日本語"
        self.write("utf8.txt", content)

        result = await self.run_tool(FsReadTool(), {"path": "utf8.txt"})

        self.assertEqual(result.data["content"], content)

    async def test_missing_file_is_reported(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "missing.txt"})

    async def test_directory_is_not_read_as_a_file(self):
        (self.root / "folder").mkdir()

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "folder"})

    async def test_invalid_utf8_is_rejected(self):
        self.write_bytes("invalid.txt", b"\xff\xfe\xfd")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "invalid.txt"})

    async def test_nul_bytes_are_rejected(self):
        self.write_bytes("binary.txt", b"text\x00more")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "binary.txt"})

    async def test_control_character_binary_content_is_rejected(self):
        self.write_bytes("control.txt", b"\x01" * 200)

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "control.txt"})

    async def test_oversized_read_is_truncated(self):
        self.configure(TOOL_FILE_MAX_READ_BYTES=100)
        self.write("large.txt", "a" * 200)

        result = await self.run_tool(FsReadTool(), {"path": "large.txt"})

        self.assertTrue(result.data["truncated"])
        self.assertEqual(result.data["size"], 200)
        self.assertEqual(len(result.data["content"]), 100)

    async def test_utf8_character_split_at_read_limit_is_handled(self):
        self.configure(TOOL_FILE_MAX_READ_BYTES=100)
        self.write("split.txt", "a" * 99 + "€")

        result = await self.run_tool(FsReadTool(), {"path": "split.txt"})

        self.assertTrue(result.data["truncated"])
        self.assertEqual(result.data["content"], "a" * 99)

    async def test_utf8_content_below_limit_is_complete(self):
        self.configure(TOOL_FILE_MAX_READ_BYTES=100)
        content = "€" * 33
        self.write("euro.txt", content)

        result = await self.run_tool(FsReadTool(), {"path": "euro.txt"})

        self.assertFalse(result.data["truncated"])
        self.assertEqual(result.data["content"], content)

    async def test_read_limit_is_configurable(self):
        self.configure(TOOL_FILE_MAX_READ_BYTES=5)
        self.write("small.txt", "abcdefgh")

        result = await self.run_tool(FsReadTool(), {"path": "small.txt"})

        self.assertEqual(result.data["content"], "abcde")
        self.assertTrue(result.data["truncated"])


# ---------------------------------------------------------------------------
# Writing text files
# ---------------------------------------------------------------------------


class FilesystemWriteTests(FilesystemUnitTestBase):

    async def test_file_is_created(self):
        result = await self.run_tool(
            FsCreateTool(),
            {"path": "new.txt", "content": "hello"},
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["created"])
        self.assertFalse(result.data["overwritten"])
        self.assertEqual(result.data["bytes_written"], 5)
        self.assertEqual(
            (self.root / "new.txt").read_text(encoding="utf-8"),
            "hello",
        )

    async def test_empty_file_is_created(self):
        result = await self.run_tool(
            FsCreateTool(),
            {"path": "empty.txt", "content": ""},
        )

        self.assertTrue(result.success)
        self.assertEqual((self.root / "empty.txt").read_bytes(), b"")

    async def test_existing_file_is_not_clobbered(self):
        target = self.write("existing.txt", "original")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsCreateTool(),
                {"path": "existing.txt", "content": "new"},
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    async def test_existing_file_can_be_replaced_with_overwrite(self):
        target = self.write("existing.txt", "original")

        result = await self.run_tool(
            FsCreateTool(),
            {
                "path": "existing.txt",
                "content": "new",
                "overwrite": True,
            },
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["overwritten"])
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    async def test_missing_parent_directory_is_rejected(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsCreateTool(),
                {"path": "missing/new.txt", "content": "x"},
            )

    async def test_directory_target_is_rejected(self):
        (self.root / "folder").mkdir()

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsCreateTool(),
                {
                    "path": "folder",
                    "content": "x",
                    "overwrite": True,
                },
            )

    async def test_oversized_write_is_rejected(self):
        self.configure(TOOL_FILE_MAX_WRITE_BYTES=10)

        with self.assertRaises(ToolSizeLimitError):
            await self.run_tool(
                FsCreateTool(),
                {"path": "large.txt", "content": "a" * 11},
            )

        self.assertFalse((self.root / "large.txt").exists())

    async def test_nul_content_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsCreateTool(),
                {"path": "nul.txt", "content": "bad\x00data"},
            )

    async def test_utf8_write_counts_bytes(self):
        result = await self.run_tool(
            FsCreateTool(),
            {"path": "unicode.txt", "content": "café"},
        )

        self.assertEqual(result.data["bytes_written"], 5)
        self.assertEqual(
            (self.root / "unicode.txt").read_text(encoding="utf-8"),
            "café",
        )

    async def test_existing_file_is_updated(self):
        target = self.write("update.txt", "original")

        result = await self.run_tool(
            FsUpdateTool(),
            {"path": "update.txt", "content": "replacement"},
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["updated"])
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement")

    async def test_missing_update_target_is_rejected(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsUpdateTool(),
                {"path": "missing.txt", "content": "x"},
            )

    async def test_directory_update_target_is_rejected(self):
        (self.root / "folder").mkdir()

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsUpdateTool(),
                {"path": "folder", "content": "x"},
            )

    async def test_oversized_update_is_rejected(self):
        self.configure(TOOL_FILE_MAX_WRITE_BYTES=10)
        target = self.write("small.txt", "original")

        with self.assertRaises(ToolSizeLimitError):
            await self.run_tool(
                FsUpdateTool(),
                {"path": "small.txt", "content": "a" * 11},
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    async def test_atomic_update_failure_preserves_original(self):
        target = self.write("atomic.txt", "original")

        def fail(*_args, **_kwargs):
            raise OSError("simulated replace failure")

        with patch(
            "app.tools.implementations.filesystem.os.replace",
            side_effect=fail,
        ):
            with self.assertRaises(ToolExecutionError):
                await self.run_tool(
                    FsUpdateTool(),
                    {"path": "atomic.txt", "content": "new"},
                )

        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertEqual(
            list(self.root.glob(".asta-write-*.tmp")),
            [],
        )

    async def test_atomic_create_failure_leaves_no_partial_file(self):
        def fail(*_args, **_kwargs):
            raise OSError("simulated fsync failure")

        with patch(
            "app.tools.implementations.filesystem.os.fsync",
            side_effect=fail,
        ):
            with self.assertRaises(ToolExecutionError):
                await self.run_tool(
                    FsCreateTool(),
                    {"path": "partial.txt", "content": "data"},
                )

        self.assertFalse((self.root / "partial.txt").exists())

    async def test_atomic_overwrite_failure_preserves_original(self):
        target = self.write("overwrite.txt", "original")

        def fail(*_args, **_kwargs):
            raise OSError("simulated replace failure")

        with patch(
            "app.tools.implementations.filesystem.os.replace",
            side_effect=fail,
        ):
            with self.assertRaises(ToolExecutionError):
                await self.run_tool(
                    FsCreateTool(),
                    {
                        "path": "overwrite.txt",
                        "content": "new",
                        "overwrite": True,
                    },
                )

        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    async def test_update_preserves_file_permissions(self):
        if os.name == "nt":
            self.skipTest("POSIX mode bits are not meaningful on Windows.")

        target = self.write("mode.txt", "original")
        target.chmod(0o640)

        await self.run_tool(
            FsUpdateTool(),
            {"path": "mode.txt", "content": "new"},
        )

        self.assertEqual(
            stat.S_IMODE(target.stat().st_mode),
            0o640,
        )

    async def test_confirmation_previews_do_not_expose_content(self):
        create = FsCreateTool()
        update = FsUpdateTool()
        secret_content = "TOP-SECRET-VALUE"

        create_preview = create.summarize(
            create.validate_arguments(
                {"path": "preview.txt", "content": secret_content}
            )
        )
        update_preview = update.summarize(
            update.validate_arguments(
                {"path": "preview.txt", "content": secret_content}
            )
        )

        self.assertNotIn(secret_content, create_preview)
        self.assertNotIn(secret_content, update_preview)
        self.assertIn("preview.txt", create_preview)
        self.assertIn("preview.txt", update_preview)


# ---------------------------------------------------------------------------
# Listing, searching, and metadata
# ---------------------------------------------------------------------------


class FilesystemListingTests(FilesystemUnitTestBase):

    async def test_directory_is_listed(self):
        self.write("alpha.txt", "a")
        (self.root / "folder").mkdir()
        self.write("folder/nested.txt", "n")

        result = await self.run_tool(FsListTool(), {"path": "."})

        self.assertTrue(result.success)
        self.assertEqual(result.data["path"], ".")
        self.assertFalse(result.data["truncated"])
        by_name = {entry["name"]: entry for entry in result.data["entries"]}
        self.assertEqual(by_name["alpha.txt"]["type"], "file")
        self.assertEqual(by_name["alpha.txt"]["size"], 1)
        self.assertEqual(by_name["folder"]["type"], "directory")
        self.assertIsNone(by_name["folder"]["size"])
        self.assertEqual(result.data["entries"][0]["type"], "directory")

    async def test_listing_is_bounded(self):
        self.configure(TOOL_FILE_MAX_LIST_ENTRIES=2)
        for index in range(4):
            self.write(f"file{index}.txt", "x")

        result = await self.run_tool(FsListTool(), {"path": "."})

        self.assertEqual(len(result.data["entries"]), 2)
        self.assertTrue(result.data["truncated"])

    async def test_listing_a_file_is_rejected(self):
        self.write("file.txt", "x")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsListTool(), {"path": "file.txt"})

    async def test_listing_a_missing_directory_is_rejected(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsListTool(), {"path": "missing"})

    async def test_symlink_is_listed_without_being_followed(self):
        target = self.write("target.txt", "x")
        link = self.root / "link.txt"
        self.symlink(link, target)

        result = await self.run_tool(FsListTool(), {"path": "."})

        by_name = {entry["name"]: entry for entry in result.data["entries"]}
        self.assertEqual(by_name["link.txt"]["type"], "symlink")
        self.assertTrue(by_name["link.txt"]["is_symlink"])


class FilesystemSearchTests(FilesystemUnitTestBase):

    async def test_files_are_discovered(self):
        self.write("root.txt", "r")
        self.write("sub/deep.txt", "d")
        self.write("other.md", "m")
        self.write("node_modules/hidden.txt", "h")
        self.write(".ssh/config", "s")

        result = await self.run_tool(
            FsSearchTool(),
            {"pattern": "*.txt", "path": "."},
        )

        paths = {match["path"] for match in result.data["matches"]}
        self.assertEqual(paths, {"root.txt", "sub/deep.txt"})
        self.assertFalse(result.data["truncated"])

    async def test_search_is_bounded(self):
        self.configure(TOOL_FILE_MAX_SEARCH_RESULTS=1)
        self.write("a.txt", "a")
        self.write("b.txt", "b")

        result = await self.run_tool(
            FsSearchTool(),
            {"pattern": "*.txt", "path": "."},
        )

        self.assertEqual(len(result.data["matches"]), 1)
        self.assertTrue(result.data["truncated"])

    async def test_pattern_with_separator_is_rejected(self):
        for pattern in ("sub/*.txt", "..\\*.txt", ".", ".."):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(
                        FsSearchTool(),
                        {"pattern": pattern, "path": "."},
                    )

    async def test_pattern_with_control_characters_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsSearchTool(),
                {"pattern": "bad\npattern", "path": "."},
            )

    async def test_missing_search_directory_is_rejected(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsSearchTool(),
                {"pattern": "*.txt", "path": "missing"},
            )

    async def test_searching_a_file_is_rejected(self):
        self.write("file.txt", "x")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsSearchTool(),
                {"pattern": "*.txt", "path": "file.txt"},
            )

    async def test_symlink_directory_is_not_traversed(self):
        outside = self.temporary_directory()
        (outside / "hidden.txt").write_text("secret", encoding="utf-8")
        link = self.root / "escape"
        self.symlink(link, outside, directory=True)

        result = await self.run_tool(
            FsSearchTool(),
            {"pattern": "*.txt", "path": "."},
        )

        self.assertEqual(result.data["matches"], [])

    async def test_junction_directory_is_not_traversed(self):
        outside = self.temporary_directory()
        (outside / "hidden.txt").write_text("secret", encoding="utf-8")
        link = self.root / "junction"
        self.junction(link, outside)

        result = await self.run_tool(
            FsSearchTool(),
            {"pattern": "*.txt", "path": "."},
        )

        self.assertEqual(result.data["matches"], [])


class FilesystemMetadataTests(FilesystemUnitTestBase):

    async def test_file_metadata_is_returned(self):
        self.write("data.json", "{}")

        result = await self.run_tool(
            FsMetadataTool(),
            {"path": "data.json"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["name"], "data.json")
        self.assertEqual(result.data["type"], "file")
        self.assertEqual(result.data["size"], 2)
        self.assertEqual(result.data["suffix"], ".json")
        self.assertFalse(result.data["is_symlink"])
        self.assertIsNotNone(result.data["modified"])

    async def test_directory_metadata_is_returned(self):
        (self.root / "folder").mkdir()

        result = await self.run_tool(FsMetadataTool(), {"path": "folder"})

        self.assertEqual(result.data["type"], "directory")

    async def test_missing_metadata_target_is_rejected(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsMetadataTool(),
                {"path": "missing"},
            )

    async def test_sensitive_metadata_is_denied(self):
        self.write(".env", "secret")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsMetadataTool(),
                {"path": ".env"},
            )

    async def test_symlink_metadata_reports_the_link(self):
        target = self.write("target.txt", "x")
        link = self.root / "link.txt"
        self.symlink(link, target)

        result = await self.run_tool(
            FsMetadataTool(),
            {"path": "link.txt"},
        )

        self.assertTrue(result.data["is_symlink"])
        self.assertEqual(result.data["type"], "file")
        self.assertEqual(result.data["size"], 1)


# ---------------------------------------------------------------------------
# Executor integration: permissions, confirmations, registry discovery
# ---------------------------------------------------------------------------


class FilesystemExecutorTests(ToolEngineTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings(
            TOOL_OS_AUTOMATION_ENABLED=True,
            TOOL_FILESYSTEM_ENABLED=True,
            TOOL_WORKSPACE_ROOTS=self.workspace.name,
            TOOL_FILE_MAX_READ_BYTES=200_000,
            TOOL_FILE_MAX_WRITE_BYTES=100_000,
            TOOL_FILE_MAX_LIST_ENTRIES=500,
            TOOL_FILE_MAX_SEARCH_RESULTS=100,
        )

    def root(self) -> Path:
        return Path(self.workspace.name)

    async def run_exec(
        self,
        tool_name,
        arguments=None,
        *,
        private=False,
        confirmation_id=None,
        create_confirmation=True,
    ):
        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=self.alice_id,
                private=private,
            )
            return await tool_executor.execute(
                tool_name=tool_name,
                arguments=arguments or {},
                context=context,
                confirmation_id=confirmation_id,
                create_confirmation=create_confirmation,
            )

    async def test_read_only_tool_runs_without_confirmation(self):
        (self.root() / "file.txt").write_text("value", encoding="utf-8")

        result = await self.run_exec("fs_read", {"path": "file.txt"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "value")
        self.assertEqual(result.provider, "filesystem")

    async def test_disabled_tool_is_denied(self):
        self.settings(TOOL_FILESYSTEM_ENABLED=False)

        result = await self.run_exec("fs_read", {"path": "file.txt"})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(result.error_category, ToolErrorCategory.DISABLED)

    async def test_missing_root_is_denied(self):
        self.settings(TOOL_WORKSPACE_ROOTS="")

        result = await self.run_exec("fs_list", {"path": "."})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(result.error_category, ToolErrorCategory.DISABLED)

    async def test_private_request_is_denied_for_every_filesystem_tool(self):
        (self.root() / "file.txt").write_text("value", encoding="utf-8")

        cases = [
            ("fs_list", {"path": "."}),
            ("fs_search", {"pattern": "*.txt", "path": "."}),
            ("fs_read", {"path": "file.txt"}),
            ("fs_metadata", {"path": "file.txt"}),
            ("fs_create", {"path": "new.txt", "content": "x"}),
            ("fs_update", {"path": "file.txt", "content": "x"}),
        ]

        for tool_name, arguments in cases:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(
                    tool_name,
                    arguments,
                    private=True,
                )
                self.assertFalse(result.success)
                self.assertEqual(result.status, ToolExecutionStatus.DENIED)
                self.assertEqual(
                    result.error_category,
                    ToolErrorCategory.PERMISSION,
                )

    async def test_create_requires_confirmation_and_then_runs(self):
        result = await self.run_exec(
            "fs_create",
            {"path": "new.txt", "content": "hello"},
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertIsNotNone(result.confirmation_id)
        self.assertFalse((self.root() / "new.txt").exists())

        approved = await self.run_exec(
            "fs_create",
            {"path": "new.txt", "content": "hello"},
            confirmation_id=result.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(
            (self.root() / "new.txt").read_text(encoding="utf-8"),
            "hello",
        )

    async def test_create_without_confirmation_support_is_rejected(self):
        result = await self.run_exec(
            "fs_create",
            {"path": "new.txt", "content": "hello"},
            create_confirmation=False,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertFalse((self.root() / "new.txt").exists())

    async def test_update_requires_confirmation_and_preserves_file(self):
        target = self.root() / "update.txt"
        target.write_text("original", encoding="utf-8")

        result = await self.run_exec(
            "fs_update",
            {"path": "update.txt", "content": "changed"},
        )

        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

        approved = await self.run_exec(
            "fs_update",
            {"path": "update.txt", "content": "changed"},
            confirmation_id=result.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed")

    async def test_approved_arguments_cannot_be_swapped(self):
        result = await self.run_exec(
            "fs_create",
            {"path": "approved.txt", "content": "approved"},
        )

        swapped = await self.run_exec(
            "fs_create",
            {"path": "swapped.txt", "content": "approved"},
            confirmation_id=result.confirmation_id,
        )

        self.assertFalse(swapped.success)
        self.assertEqual(swapped.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            swapped.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertFalse((self.root() / "approved.txt").exists())
        self.assertFalse((self.root() / "swapped.txt").exists())

    async def test_outside_path_is_reported_as_validation_error(self):
        outside = tempfile.TemporaryDirectory(prefix="asta_fs_outside_")
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "outside.txt"
        target.write_text("secret", encoding="utf-8")

        result = await self.run_exec("fs_read", {"path": str(target)})

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, ToolErrorCategory.VALIDATION)
        self.assertNotIn("secret", result.error_message or "")

    async def test_sensitive_file_is_denied_through_the_executor(self):
        (self.root() / ".env").write_text("TOKEN=secret", encoding="utf-8")

        result = await self.run_exec("fs_read", {"path": ".env"})

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, ToolErrorCategory.VALIDATION)
        self.assertNotIn("TOKEN=secret", result.error_message or "")

    async def test_llm_definitions_include_enabled_filesystem_tools(self):
        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertTrue(FS_TOOL_NAMES.issubset(names))

    async def test_llm_definitions_exclude_disabled_filesystem_tools(self):
        self.settings(TOOL_FILESYSTEM_ENABLED=False)

        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertFalse(FS_TOOL_NAMES.intersection(names))

    async def test_filesystem_tools_are_never_exposed_in_private_mode(self):
        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=True)
        }

        self.assertFalse(FS_TOOL_NAMES.intersection(names))

    async def test_provider_schema_is_function_calling_compatible(self):
        definition = tool_registry.get("fs_read").definition()
        schema = definition.provider_schema()

        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "fs_read")
        self.assertEqual(
            schema["function"]["parameters"]["type"],
            "object",
        )
        self.assertFalse(
            schema["function"]["parameters"]["additionalProperties"]
        )


# ---------------------------------------------------------------------------
# Regression checks for earlier phases
# ---------------------------------------------------------------------------


class FilesystemRegressionTests(FilesystemUnitTestBase):

    def test_builtin_registry_still_contains_every_phase_tool(self):
        names = set(tool_registry.names())

        self.assertTrue(FS_TOOL_NAMES.issubset(names))
        self.assertTrue(
            {
                "task_list",
                "task_get",
                "task_create",
                "task_update",
                "web_search",
                "python_execute",
                "http_request",
            }.issubset(names)
        )

    def test_builtin_tool_order_is_stable(self):
        names = [tool.name for tool in BUILTIN_TOOLS]

        self.assertEqual(len(names), len(set(names)))
        self.assertIn("fs_read", names)
        self.assertIn("fs_create", names)


__all__ = [
    "FilesystemConfigurationTests",
    "FilesystemExecutorTests",
    "FilesystemListingTests",
    "FilesystemMetadataTests",
    "FilesystemPathSafetyTests",
    "FilesystemReadTests",
    "FilesystemRegressionTests",
    "FilesystemSearchTests",
    "FilesystemSensitiveFileTests",
    "FilesystemWriteTests",
]
