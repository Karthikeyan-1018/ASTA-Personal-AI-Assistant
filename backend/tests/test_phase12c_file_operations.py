"""Phase 12C tests for secure approved file and folder operations.

Every test uses a temporary directory. No real user document, ``.env`` file,
system directory, browser profile, credential store, or ``agriculture_db`` is
read or modified.

The suite covers approved-root configuration, traversal and UNC/device path
rejection, symlink/junction/reparse-point handling, sensitive names, binary and
oversized content, bounded listings and searches, existence checks, folder
creation, file creation and update, rename, move, destination conflicts,
confirmation binding and expiry, post-approval path and permission
revalidation, private mode, disabled tools, permission policy, and executor
integration.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.models.conversation import Conversation
from app.services.tool_confirmation_service import (
    tool_confirmation_service,
)
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import (
    ConfirmationState,
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
from app.tools.implementations import filesystem
from app.tools.implementations.filesystem import (
    FS_12C_TOOLS,
    FS_ALL_TOOLS,
    FS_TOOLS,
    FsCreateTool,
    FsExistsTool,
    FsListTool,
    FsMetadataTool,
    FsMkdirTool,
    FsMoveTool,
    FsReadTool,
    FsRenameTool,
    FsSearchTool,
    FsUpdateTool,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from tests.tool_test_support import ToolEngineTestBase


FS_12C_NAMES = {
    "fs_exists",
    "fs_mkdir",
    "fs_rename",
    "fs_move",
}


class FileOperationsUnitTestBase(unittest.IsolatedAsyncioTestCase):
    """Isolated temporary root and patched settings for direct tool calls."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="asta_fs12c_")
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
        directory = tempfile.TemporaryDirectory(prefix="asta_fs12c_other_")
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
# Registration, policy, and approved-root configuration
# ---------------------------------------------------------------------------


class FileOperationsConfigurationTests(FileOperationsUnitTestBase):

    def test_original_six_tool_contract_is_unchanged(self):
        self.assertEqual(
            {tool.name for tool in FS_TOOLS},
            {
                "fs_list",
                "fs_search",
                "fs_read",
                "fs_create",
                "fs_update",
                "fs_metadata",
            },
        )

    def test_phase_12c_tools_are_registered(self):
        self.assertEqual({tool.name for tool in FS_12C_TOOLS}, FS_12C_NAMES)
        self.assertEqual(
            {tool.name for tool in FS_ALL_TOOLS},
            {tool.name for tool in FS_TOOLS} | FS_12C_NAMES,
        )

        builtin_names = {tool.name for tool in BUILTIN_TOOLS}
        self.assertTrue(FS_12C_NAMES.issubset(builtin_names))

    def test_no_delete_tool_is_registered(self):
        builtin_names = {tool.name for tool in BUILTIN_TOOLS}

        self.assertNotIn("fs_delete", builtin_names)
        self.assertNotIn("fs_remove", builtin_names)
        self.assertNotIn("fs_trash", builtin_names)

    def test_tools_are_disabled_by_default(self):
        self.configure(TOOL_FILESYSTEM_ENABLED=False)

        self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_missing_roots_disable_the_new_tools(self):
        self.configure(roots="")

        self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_categories_and_confirmation_policy(self):
        expected = {
            "fs_exists": (ToolCategory.READ_ONLY, False),
            "fs_mkdir": (ToolCategory.REVERSIBLE, True),
            "fs_rename": (ToolCategory.REVERSIBLE, True),
            "fs_move": (ToolCategory.REVERSIBLE, True),
        }

        for tool in FS_12C_TOOLS:
            category, confirmation = expected[tool.name]
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.category, category)
                self.assertEqual(tool.requires_confirmation, confirmation)
                self.assertFalse(tool.allowed_in_private)
                self.assertEqual(tool.provider_label, "filesystem")

    def test_user_home_root_is_rejected(self):
        fake_home = self.temporary_directory() / "home"
        fake_home.mkdir()

        with patch.object(
            filesystem,
            "_user_home",
            return_value=fake_home,
        ):
            self.configure(roots=str(fake_home))
            self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_root_that_contains_user_home_is_rejected(self):
        fake_home = self.temporary_directory() / "home"
        fake_home.mkdir()

        with patch.object(
            filesystem,
            "_user_home",
            return_value=fake_home,
        ):
            self.configure(roots=str(fake_home.parent))
            self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_root_inside_browser_profile_is_rejected(self):
        fake_local = self.temporary_directory()
        profile = fake_local / "Google" / "Chrome" / "User Data"
        profile.mkdir(parents=True)

        with patch.dict(os.environ, {"LOCALAPPDATA": str(fake_local)}):
            self.configure(roots=str(profile))
            self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_root_that_contains_browser_profile_is_rejected(self):
        fake_local = self.temporary_directory()
        profile = fake_local / "Google" / "Chrome" / "User Data"
        profile.mkdir(parents=True)

        with patch.dict(os.environ, {"LOCALAPPDATA": str(fake_local)}):
            self.configure(roots=str(fake_local))
            self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_root_inside_credential_store_is_rejected(self):
        fake_profile = self.temporary_directory()
        store = fake_profile / ".password-store"
        store.mkdir()

        with patch.dict(
            os.environ,
            {"USERPROFILE": str(fake_profile)},
        ):
            self.configure(roots=str(store))
            self.assertFalse(any(tool.enabled for tool in FS_12C_TOOLS))

    def test_permission_policy_integration(self):
        exists = permission_policy.evaluate(FsExistsTool(), private=False)
        mkdir = permission_policy.evaluate(FsMkdirTool(), private=False)
        rename = permission_policy.evaluate(FsRenameTool(), private=False)
        move = permission_policy.evaluate(FsMoveTool(), private=False)

        self.assertTrue(exists.allowed)
        self.assertFalse(exists.requires_confirmation)

        for decision in (mkdir, rename, move):
            self.assertTrue(decision.allowed)
            self.assertTrue(decision.requires_confirmation)

    def test_llm_definitions_include_enabled_new_tools(self):
        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertTrue(FS_12C_NAMES.issubset(names))


# ---------------------------------------------------------------------------
# Path validation and containment
# ---------------------------------------------------------------------------


class FileOperationsPathSafetyTests(FileOperationsUnitTestBase):

    async def test_parent_traversal_is_rejected_even_when_it_resolves_inside(
        self,
    ):
        self.write("inside.txt", "value")
        (self.root / "sub").mkdir()

        for value in (
            "sub/../inside.txt",
            "sub\\..\\inside.txt",
            "../inside.txt",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": value})

    async def test_unc_paths_are_rejected(self):
        for value in (
            r"\\server\share\file.txt",
            "//server/share/file.txt",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": value})

    async def test_windows_device_paths_are_rejected(self):
        for value in (
            r"\\?\C:\Windows\win.ini",
            r"\\.\PhysicalDrive0",
            "//?/C:/Windows/win.ini",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": value})

    async def test_absolute_path_outside_root_is_rejected(self):
        outside = self.temporary_directory()
        target = outside / "outside.txt"
        target.write_text("secret", encoding="utf-8")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsReadTool(), {"path": str(target)})

    async def test_symlink_escape_is_rejected_for_exists_and_read(self):
        outside = self.temporary_directory()
        target = outside / "outside.txt"
        target.write_text("secret", encoding="utf-8")
        link = self.root / "escape.txt"
        self.symlink(link, target)

        for tool in (FsExistsTool(), FsReadTool(), FsMetadataTool()):
            with self.subTest(tool=tool.name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(tool, {"path": "escape.txt"})

    async def test_junction_escape_is_rejected(self):
        outside = self.temporary_directory()
        (outside / "outside.txt").write_text("secret", encoding="utf-8")
        link = self.root / "junction-dir"
        self.junction(link, outside)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsReadTool(),
                {"path": "junction-dir/outside.txt"},
            )

    async def test_rename_rejects_symlink_source(self):
        target = self.write("target.txt", "value")
        link = self.root / "link.txt"
        self.symlink(link, target)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsRenameTool(),
                {"path": "link.txt", "new_name": "renamed.txt"},
            )

    async def test_move_rejects_symlink_source(self):
        target = self.write("target.txt", "value")
        link = self.root / "link.txt"
        self.symlink(link, target)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsMoveTool(),
                {"source": "link.txt", "destination": "moved.txt"},
            )

    async def test_move_rejects_junction_source(self):
        target = self.root / "target-dir"
        target.mkdir()
        link = self.root / "junction-dir"
        self.junction(link, target)

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsMoveTool(),
                {"source": "junction-dir", "destination": "moved-dir"},
            )

    async def test_sensitive_files_are_rejected(self):
        for name in (
            ".env",
            ".env.local",
            "credentials.json",
            "secrets.json",
            "id_rsa",
            "server.pem",
            "login data",
            "logins.json",
            "local state",
        ):
            self.write(name, "do-not-expose")
            with self.subTest(name=name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(FsReadTool(), {"path": name})

    async def test_sensitive_directories_are_rejected(self):
        for name in (".ssh", ".aws", ".gnupg", "agriculture_db"):
            (self.root / name).mkdir(exist_ok=True)
            self.write(f"{name}/secret.txt", "do-not-expose")

            with self.subTest(name=name):
                with self.assertRaises(ToolValidationError):
                    await self.run_tool(
                        FsReadTool(),
                        {"path": f"{name}/secret.txt"},
                    )

    async def test_agriculture_db_is_hidden_from_listing_and_search(self):
        (self.root / "agriculture_db").mkdir()
        self.write("agriculture_db/secret.txt", "do-not-expose")
        self.write("visible.txt", "visible")

        listing = await self.run_tool(FsListTool(), {"path": "."})
        search = await self.run_tool(
            FsSearchTool(),
            {"path": ".", "pattern": "*.txt"},
        )

        self.assertNotIn(
            "agriculture_db",
            {entry["name"] for entry in listing.data["entries"]},
        )
        self.assertNotIn(
            "agriculture_db/secret.txt",
            {item["path"] for item in search.data["matches"]},
        )

    async def test_binary_suffixes_are_rejected_for_reads(self):
        for name in ("image.png", "archive.zip", "program.exe"):
            self.write_bytes(name, b"\x00binary")
            with self.subTest(name=name):
                with self.assertRaises(ToolExecutionError):
                    await self.run_tool(FsReadTool(), {"path": name})

    async def test_nul_bytes_are_rejected_for_reads(self):
        self.write_bytes("nulbytes.txt", b"text\x00binary")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "nulbytes.txt"})

    async def test_invalid_utf8_is_rejected_for_reads(self):
        self.write_bytes("invalid.txt", b"\xff\xfe\xfd")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsReadTool(), {"path": "invalid.txt"})

    async def test_write_content_size_limit_is_enforced(self):
        self.configure(TOOL_FILE_MAX_WRITE_BYTES=5)

        with self.assertRaises(ToolSizeLimitError):
            await self.run_tool(
                FsCreateTool(),
                {"path": "large.txt", "content": "abcdef"},
            )

        self.assertFalse((self.root / "large.txt").exists())

    async def test_read_size_limit_truncates(self):
        self.configure(TOOL_FILE_MAX_READ_BYTES=5)
        self.write("long.txt", "abcdefghij")

        result = await self.run_tool(FsReadTool(), {"path": "long.txt"})

        self.assertTrue(result.data["truncated"])
        self.assertEqual(result.data["content"], "abcde")

    async def test_listing_result_count_is_bounded(self):
        self.configure(TOOL_FILE_MAX_LIST_ENTRIES=2)

        for index in range(5):
            self.write(f"file{index}.txt", "x")

        result = await self.run_tool(FsListTool(), {"path": "."})

        self.assertEqual(len(result.data["entries"]), 2)
        self.assertTrue(result.data["truncated"])

    async def test_search_result_count_is_bounded(self):
        self.configure(TOOL_FILE_MAX_SEARCH_RESULTS=1)

        for index in range(5):
            self.write(f"file{index}.txt", "x")

        result = await self.run_tool(
            FsSearchTool(),
            {"path": ".", "pattern": "*.txt"},
        )

        self.assertEqual(len(result.data["matches"]), 1)
        self.assertTrue(result.data["truncated"])

    async def test_results_do_not_expose_absolute_paths(self):
        self.write("sub/file.txt", "value")

        read = await self.run_tool(FsReadTool(), {"path": "sub/file.txt"})
        exists = await self.run_tool(
            FsExistsTool(),
            {"path": "sub/file.txt"},
        )

        rendered = json.dumps(read.data) + json.dumps(exists.data)

        self.assertEqual(read.data["path"], "sub/file.txt")
        self.assertEqual(exists.data["path"], "sub/file.txt")
        self.assertNotIn(str(self.root), rendered)


# ---------------------------------------------------------------------------
# Existence checks
# ---------------------------------------------------------------------------


class FileOperationsExistsTests(FileOperationsUnitTestBase):

    async def test_existing_file_is_reported(self):
        self.write("file.txt", "value")

        result = await self.run_tool(FsExistsTool(), {"path": "file.txt"})

        self.assertTrue(result.success)
        self.assertTrue(result.data["exists"])
        self.assertEqual(result.data["type"], "file")
        self.assertFalse(result.data["is_symlink"])

    async def test_existing_directory_is_reported(self):
        (self.root / "folder").mkdir()

        result = await self.run_tool(FsExistsTool(), {"path": "folder"})

        self.assertTrue(result.data["exists"])
        self.assertEqual(result.data["type"], "directory")

    async def test_missing_path_is_reported_without_error(self):
        result = await self.run_tool(FsExistsTool(), {"path": "missing.txt"})

        self.assertTrue(result.success)
        self.assertFalse(result.data["exists"])
        self.assertIsNone(result.data["type"])

    async def test_symlink_inside_root_is_reported_as_a_link(self):
        target = self.write("target.txt", "value")
        link = self.root / "link.txt"
        self.symlink(link, target)

        result = await self.run_tool(FsExistsTool(), {"path": "link.txt"})

        self.assertTrue(result.data["exists"])
        self.assertTrue(result.data["is_symlink"])

    async def test_missing_path_outside_root_is_rejected(self):
        outside = self.temporary_directory()

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsExistsTool(),
                {"path": str(outside / "missing.txt")},
            )


# ---------------------------------------------------------------------------
# Folder and file creation
# ---------------------------------------------------------------------------


class FileOperationsCreationTests(FileOperationsUnitTestBase):

    async def test_folder_is_created(self):
        result = await self.run_tool(FsMkdirTool(), {"path": "College Projects"})

        self.assertTrue(result.success)
        self.assertTrue(result.data["created"])
        self.assertTrue((self.root / "College Projects").is_dir())

    async def test_nested_folder_is_created_when_parent_exists(self):
        (self.root / "parent").mkdir()

        result = await self.run_tool(
            FsMkdirTool(),
            {"path": "parent/child"},
        )

        self.assertTrue(result.success)
        self.assertTrue((self.root / "parent" / "child").is_dir())

    async def test_existing_folder_is_rejected(self):
        (self.root / "folder").mkdir()

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsMkdirTool(), {"path": "folder"})

    async def test_existing_file_is_rejected(self):
        self.write("file.txt", "value")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(FsMkdirTool(), {"path": "file.txt"})

    async def test_missing_parent_is_rejected(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsMkdirTool(),
                {"path": "missing/child"},
            )

        self.assertFalse((self.root / "missing").exists())

    async def test_sensitive_folder_name_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            await self.run_tool(FsMkdirTool(), {"path": ".ssh"})

    async def test_file_is_created_without_overwriting(self):
        result = await self.run_tool(
            FsCreateTool(),
            {"path": "new.txt", "content": "hello"},
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["created"])
        self.assertFalse(result.data["overwritten"])
        self.assertEqual(
            (self.root / "new.txt").read_text(encoding="utf-8"),
            "hello",
        )

    async def test_existing_file_is_not_overwritten_by_default(self):
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

        self.assertTrue(result.data["overwritten"])
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

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

    async def test_binary_suffix_is_rejected_for_creation(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsCreateTool(),
                {"path": "image.png", "content": "not an image"},
            )


# ---------------------------------------------------------------------------
# Rename and move
# ---------------------------------------------------------------------------


class FileOperationsRenameMoveTests(FileOperationsUnitTestBase):

    async def test_file_is_renamed(self):
        self.write("old.txt", "value")

        result = await self.run_tool(
            FsRenameTool(),
            {"path": "old.txt", "new_name": "new.txt"},
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["renamed"])
        self.assertEqual(result.data["path"], "old.txt")
        self.assertEqual(result.data["new_path"], "new.txt")
        self.assertFalse((self.root / "old.txt").exists())
        self.assertEqual(
            (self.root / "new.txt").read_text(encoding="utf-8"),
            "value",
        )

    async def test_directory_is_renamed(self):
        (self.root / "old-dir").mkdir()
        self.write("old-dir/file.txt", "value")

        result = await self.run_tool(
            FsRenameTool(),
            {"path": "old-dir", "new_name": "new-dir"},
        )

        self.assertTrue(result.success)
        self.assertTrue((self.root / "new-dir" / "file.txt").is_file())

    async def test_rename_does_not_overwrite_an_existing_destination(self):
        self.write("first.txt", "first")
        self.write("second.txt", "second")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsRenameTool(),
                {"path": "first.txt", "new_name": "second.txt"},
            )

        self.assertEqual(
            (self.root / "first.txt").read_text(encoding="utf-8"),
            "first",
        )
        self.assertEqual(
            (self.root / "second.txt").read_text(encoding="utf-8"),
            "second",
        )

    async def test_rename_rejects_path_separators(self):
        self.write("file.txt", "value")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsRenameTool(),
                {"path": "file.txt", "new_name": "sub/file.txt"},
            )

    async def test_rename_rejects_sensitive_destination_name(self):
        self.write("file.txt", "value")

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsRenameTool(),
                {"path": "file.txt", "new_name": ".env"},
            )

    async def test_rename_rejects_a_workspace_root(self):
        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsRenameTool(),
                {"path": ".", "new_name": "renamed-root"},
            )

    async def test_file_is_moved_between_approved_directories(self):
        (self.root / "source").mkdir()
        (self.root / "destination").mkdir()
        self.write("source/file.txt", "value")

        result = await self.run_tool(
            FsMoveTool(),
            {
                "source": "source/file.txt",
                "destination": "destination/file.txt",
            },
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["moved"])
        self.assertFalse((self.root / "source" / "file.txt").exists())
        self.assertEqual(
            (self.root / "destination" / "file.txt").read_text(
                encoding="utf-8"
            ),
            "value",
        )

    async def test_directory_is_moved(self):
        (self.root / "source-dir").mkdir()
        self.write("source-dir/file.txt", "value")

        result = await self.run_tool(
            FsMoveTool(),
            {
                "source": "source-dir",
                "destination": "moved-dir",
            },
        )

        self.assertTrue(result.success)
        self.assertTrue((self.root / "moved-dir" / "file.txt").is_file())

    async def test_move_does_not_overwrite_an_existing_destination(self):
        self.write("first.txt", "first")
        self.write("second.txt", "second")

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsMoveTool(),
                {"source": "first.txt", "destination": "second.txt"},
            )

        self.assertEqual(
            (self.root / "first.txt").read_text(encoding="utf-8"),
            "first",
        )
        self.assertEqual(
            (self.root / "second.txt").read_text(encoding="utf-8"),
            "second",
        )

    async def test_move_rejects_destination_outside_root(self):
        self.write("file.txt", "value")
        outside = self.temporary_directory()

        with self.assertRaises(ToolValidationError):
            await self.run_tool(
                FsMoveTool(),
                {
                    "source": "file.txt",
                    "destination": str(outside / "file.txt"),
                },
            )

    async def test_move_rejects_directory_into_its_descendant(self):
        parent = self.root / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsMoveTool(),
                {
                    "source": "parent",
                    "destination": "parent/child/moved",
                },
            )

        self.assertTrue(parent.is_dir())
        self.assertTrue(child.is_dir())

    async def test_move_rejects_a_workspace_root(self):
        (self.root / "target").mkdir()

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                FsMoveTool(),
                {"source": ".", "destination": "target/root-copy"},
            )

    async def test_cross_volume_move_is_rejected(self):
        self.write("file.txt", "value")

        with patch.object(
            filesystem.os,
            "rename",
            side_effect=OSError(errno.EXDEV, "cross-device link"),
        ):
            with self.assertRaises(ToolExecutionError) as raised:
                await self.run_tool(
                    FsMoveTool(),
                    {"source": "file.txt", "destination": "moved.txt"},
                )

        self.assertIn("volumes", str(raised.exception).lower())
        self.assertTrue((self.root / "file.txt").is_file())
        self.assertFalse((self.root / "moved.txt").exists())


# ---------------------------------------------------------------------------
# Executor integration: confirmations, binding, and revalidation
# ---------------------------------------------------------------------------


class FileOperationsExecutorTests(ToolEngineTestBase):

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

        async with self.SessionLocal() as session:
            first = Conversation(user_id=self.alice_id, title="first")
            second = Conversation(user_id=self.alice_id, title="second")
            session.add_all([first, second])
            await session.commit()
            await session.refresh(first)
            await session.refresh(second)
            self.conversation_one = first.id
            self.conversation_two = second.id

    def root(self) -> Path:
        return Path(self.workspace.name)

    def write(self, relative: str, content: str) -> Path:
        path = self.root() / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return path

    async def run_exec(
        self,
        tool_name,
        arguments=None,
        *,
        user_id=None,
        conversation_id=None,
        private=False,
        confirmation_id=None,
        create_confirmation=True,
    ):
        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=user_id or self.alice_id,
                conversation_id=conversation_id,
                private=private,
            )
            return await tool_executor.execute(
                tool_name=tool_name,
                arguments=arguments or {},
                context=context,
                confirmation_id=confirmation_id,
                create_confirmation=create_confirmation,
            )

    async def test_exists_runs_without_confirmation(self):
        self.write("file.txt", "value")

        result = await self.run_exec("fs_exists", {"path": "file.txt"})

        self.assertTrue(result.success)
        self.assertTrue(result.data["exists"])
        self.assertFalse(result.confirmation_required)

    async def test_mkdir_requires_confirmation_and_then_runs(self):
        result = await self.run_exec("fs_mkdir", {"path": "new-folder"})

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertEqual(result.confirmation_status, ConfirmationState.PENDING)
        self.assertFalse((self.root() / "new-folder").exists())

        approved = await self.run_exec(
            "fs_mkdir",
            {"path": "new-folder"},
            confirmation_id=result.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(
            approved.confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertTrue((self.root() / "new-folder").is_dir())

    async def test_rename_requires_confirmation_and_then_runs(self):
        self.write("old.txt", "value")

        result = await self.run_exec(
            "fs_rename",
            {"path": "old.txt", "new_name": "new.txt"},
        )

        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue((self.root() / "old.txt").exists())

        approved = await self.run_exec(
            "fs_rename",
            {"path": "old.txt", "new_name": "new.txt"},
            confirmation_id=result.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertFalse((self.root() / "old.txt").exists())
        self.assertTrue((self.root() / "new.txt").is_file())

    async def test_move_requires_confirmation_and_then_runs(self):
        (self.root() / "source").mkdir()
        (self.root() / "destination").mkdir()
        self.write("source/file.txt", "value")

        result = await self.run_exec(
            "fs_move",
            {
                "source": "source/file.txt",
                "destination": "destination/file.txt",
            },
        )

        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue((self.root() / "source" / "file.txt").is_file())

        approved = await self.run_exec(
            "fs_move",
            {
                "source": "source/file.txt",
                "destination": "destination/file.txt",
            },
            confirmation_id=result.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertFalse((self.root() / "source" / "file.txt").exists())
        self.assertTrue(
            (self.root() / "destination" / "file.txt").is_file()
        )

    async def test_create_overwrite_requires_confirmation(self):
        target = self.write("existing.txt", "original")

        result = await self.run_exec(
            "fs_create",
            {
                "path": "existing.txt",
                "content": "new",
                "overwrite": True,
            },
        )

        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

        approved = await self.run_exec(
            "fs_create",
            {
                "path": "existing.txt",
                "content": "new",
                "overwrite": True,
            },
            confirmation_id=result.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    async def test_invalid_path_is_rejected_before_confirmation(self):
        outside = tempfile.TemporaryDirectory(prefix="asta_fs12c_outside_")
        self.addCleanup(outside.cleanup)

        cases = [
            ("fs_create", {"path": "../escape.txt", "content": "x"}),
            ("fs_mkdir", {"path": ".ssh"}),
            (
                "fs_move",
                {
                    "source": "missing.txt",
                    "destination": str(Path(outside.name) / "x.txt"),
                },
            ),
            ("fs_read", {"path": r"\\server\share\file.txt"}),
        ]

        for tool_name, arguments in cases:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(tool_name, arguments)

                self.assertFalse(result.success)
                self.assertIsNone(result.confirmation_id)
                self.assertEqual(
                    result.error_category,
                    ToolErrorCategory.VALIDATION,
                )

    async def test_rejected_confirmation_does_not_change_filesystem(self):
        pending = await self.run_exec("fs_mkdir", {"path": "rejected-dir"})

        async with self.SessionLocal() as session:
            await tool_confirmation_service.reject(
                session=session,
                user_id=self.alice_id,
                confirmation_id=pending.confirmation_id,
            )

        result = await self.run_exec(
            "fs_mkdir",
            {"path": "rejected-dir"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertFalse((self.root() / "rejected-dir").exists())

    async def test_expired_confirmation_does_not_change_filesystem(self):
        async with self.SessionLocal() as session:
            pending = await tool_confirmation_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="fs_mkdir",
                category=ToolCategory.REVERSIBLE,
                arguments={"path": "expired-dir"},
                arguments_preview="path=expired-dir",
                ttl_seconds=30,
            )

        from app.services import tool_confirmation_service as service_module

        original_now = service_module._utcnow()

        with patch.object(
            service_module,
            "_utcnow",
            return_value=original_now + timedelta(hours=1),
        ):
            result = await self.run_exec(
                "fs_mkdir",
                {"path": "expired-dir"},
                confirmation_id=pending.id,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.EXPIRED,
        )
        self.assertFalse((self.root() / "expired-dir").exists())

    async def test_replayed_confirmation_does_not_run_twice(self):
        self.write("once.txt", "original")

        pending = await self.run_exec(
            "fs_update",
            {"path": "once.txt", "content": "first"},
        )

        first = await self.run_exec(
            "fs_update",
            {"path": "once.txt", "content": "first"},
            confirmation_id=pending.confirmation_id,
        )
        replay = await self.run_exec(
            "fs_update",
            {"path": "once.txt", "content": "second"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(first.success)
        self.assertFalse(replay.success)
        self.assertEqual(replay.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            replay.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(
            (self.root() / "once.txt").read_text(encoding="utf-8"),
            "first",
        )

    async def test_altered_arguments_are_rejected(self):
        pending = await self.run_exec(
            "fs_mkdir",
            {"path": "approved-dir"},
        )

        altered = await self.run_exec(
            "fs_mkdir",
            {"path": "altered-dir"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(altered.success)
        self.assertEqual(altered.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            altered.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertFalse((self.root() / "approved-dir").exists())
        self.assertFalse((self.root() / "altered-dir").exists())

    async def test_confirmation_is_bound_to_the_owner(self):
        pending = await self.run_exec("fs_mkdir", {"path": "owner-dir"})

        result = await self.run_exec(
            "fs_mkdir",
            {"path": "owner-dir"},
            user_id=self.bob_id,
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertFalse((self.root() / "owner-dir").exists())

    async def test_confirmation_is_bound_to_the_conversation(self):
        pending = await self.run_exec(
            "fs_mkdir",
            {"path": "conversation-dir"},
            conversation_id=self.conversation_one,
        )

        result = await self.run_exec(
            "fs_mkdir",
            {"path": "conversation-dir"},
            conversation_id=self.conversation_two,
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertFalse((self.root() / "conversation-dir").exists())

    async def test_confirmation_is_bound_to_the_tool(self):
        self.write("source.txt", "value")
        pending = await self.run_exec("fs_mkdir", {"path": "tool-dir"})

        result = await self.run_exec(
            "fs_move",
            {"source": "source.txt", "destination": "moved.txt"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertTrue((self.root() / "source.txt").is_file())
        self.assertFalse((self.root() / "moved.txt").exists())
        self.assertFalse((self.root() / "tool-dir").exists())

    async def test_path_is_revalidated_after_approval(self):
        pending = await self.run_exec(
            "fs_create",
            {"path": "raced.txt", "content": "safe"},
        )

        original_claim = tool_confirmation_service.claim

        async def claim_then_create_race(**kwargs):
            claimed = await original_claim(**kwargs)
            (self.root() / "raced.txt").write_text(
                "attacker",
                encoding="utf-8",
            )
            return claimed

        with patch.object(
            tool_confirmation_service,
            "claim",
            new=claim_then_create_race,
        ):
            result = await self.run_exec(
                "fs_create",
                {"path": "raced.txt", "content": "safe"},
                confirmation_id=pending.confirmation_id,
            )

        self.assertFalse(result.success)
        self.assertEqual(
            (self.root() / "raced.txt").read_text(encoding="utf-8"),
            "attacker",
        )

    async def test_permission_is_revalidated_after_approval(self):
        pending = await self.run_exec("fs_mkdir", {"path": "disabled-dir"})

        original_claim = tool_confirmation_service.claim

        async def claim_then_disable(**kwargs):
            claimed = await original_claim(**kwargs)
            self.settings(TOOL_FILESYSTEM_ENABLED=False)
            return claimed

        with patch.object(
            tool_confirmation_service,
            "claim",
            new=claim_then_disable,
        ):
            result = await self.run_exec(
                "fs_mkdir",
                {"path": "disabled-dir"},
                confirmation_id=pending.confirmation_id,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )
        self.assertFalse((self.root() / "disabled-dir").exists())

    async def test_private_mode_denies_every_new_tool(self):
        self.write("file.txt", "value")
        (self.root() / "folder").mkdir()

        cases = [
            ("fs_exists", {"path": "file.txt"}),
            ("fs_mkdir", {"path": "new-folder"}),
            (
                "fs_rename",
                {"path": "file.txt", "new_name": "renamed.txt"},
            ),
            (
                "fs_move",
                {"source": "file.txt", "destination": "folder/file.txt"},
            ),
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

    async def test_disabled_tool_is_denied(self):
        self.settings(TOOL_FILESYSTEM_ENABLED=False)

        for tool_name, arguments in (
            ("fs_exists", {"path": "file.txt"}),
            ("fs_mkdir", {"path": "new-folder"}),
        ):
            with self.subTest(tool=tool_name):
                result = await self.run_exec(tool_name, arguments)

                self.assertFalse(result.success)
                self.assertEqual(result.status, ToolExecutionStatus.DENIED)
                self.assertEqual(
                    result.error_category,
                    ToolErrorCategory.DISABLED,
                )

    async def test_confirmation_preview_does_not_expose_content(self):
        secret = "TOP-SECRET-VALUE"
        tool = FsCreateTool()
        validated = tool.validate_arguments(
            {"path": "preview.txt", "content": secret}
        )

        preview = tool.summarize(validated)

        self.assertNotIn(secret, preview)
        self.assertIn("preview.txt", preview)


if __name__ == "__main__":
    unittest.main()
