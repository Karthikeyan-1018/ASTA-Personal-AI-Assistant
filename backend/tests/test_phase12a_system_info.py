"""Phase 12A tests for the read-only system information tools.

Every operating-system value is mocked so the suite is deterministic. A small
live smoke section checks that the current host exposes the expected structure
without asserting machine-specific values. No real ``.env`` file, secret,
personal document, browser history, or user file is read.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from collections import namedtuple
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
from app.tools.exceptions import ToolExecutionError, ToolValidationError
from app.tools.executor import tool_executor
from app.tools.implementations import system_info
from app.tools.implementations.system_info import (
    MAX_DISK_ROOTS,
    MAX_TEXT_CHARS,
    SYSTEM_INFO_TOOLS,
    SystemCpuInfoTool,
    SystemDiskInfoTool,
    SystemHostnameTool,
    SystemMemoryInfoTool,
    SystemOsInfoTool,
    SystemPythonInfoTool,
    SystemSummaryTool,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from tests.tool_test_support import ToolEngineTestBase


DiskUsage = namedtuple("usage", "total used free")
Uname = namedtuple(
    "uname_result",
    "system node release version machine processor",
)


SYSTEM_INFO_NAMES = {
    "system_os_info",
    "system_hostname",
    "system_cpu_info",
    "system_memory_info",
    "system_disk_info",
    "system_python_info",
    "system_summary",
}


class SystemInfoUnitTestBase(unittest.IsolatedAsyncioTestCase):
    """Isolated temporary disk root and patched settings."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="asta_sysinfo_"
        )
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.configure()

    def configure(self, **overrides):
        values = {
            "TOOL_OS_AUTOMATION_ENABLED": True,
            "TOOL_SYSTEM_INFO_ENABLED": True,
            "TOOL_SYSTEM_INFO_DISK_ROOTS": str(self.root),
            "TOOL_SYSTEM_INFO_MAX_DISKS": 8,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def patch_object(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_tool(self, tool, arguments=None):
        validated = tool.validate_arguments(arguments or {})
        return await tool.execute(None, validated)


# ---------------------------------------------------------------------------
# Configuration and metadata
# ---------------------------------------------------------------------------


class SystemInfoConfigurationTests(SystemInfoUnitTestBase):

    def test_disabled_by_default(self):
        self.configure(TOOL_SYSTEM_INFO_ENABLED=False)

        self.assertFalse(any(tool.enabled for tool in SYSTEM_INFO_TOOLS))

    def test_enabled_flag_enables_every_tool(self):
        self.assertTrue(all(tool.enabled for tool in SYSTEM_INFO_TOOLS))

    def test_every_tool_is_read_only_and_idempotent(self):
        for tool in SYSTEM_INFO_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.category, ToolCategory.READ_ONLY)
                self.assertFalse(tool.requires_confirmation)
                self.assertFalse(tool.allowed_in_private)
                self.assertTrue(tool.idempotent)
                self.assertEqual(tool.provider_label, "system")

    def test_tools_are_not_sensitive(self):
        for tool in SYSTEM_INFO_TOOLS:
            self.assertNotEqual(tool.category, ToolCategory.SENSITIVE)

    def test_default_disk_root_is_used_when_allowlist_is_empty(self):
        self.configure(TOOL_SYSTEM_INFO_DISK_ROOTS="")
        self.patch_object(
            system_info,
            "_default_disk_root",
            new=lambda: str(self.root),
        )

        roots = system_info._approved_disk_roots()

        self.assertEqual(roots, [str(self.root.resolve())])

    def test_invalid_disk_root_is_ignored(self):
        missing = self.root / "missing"
        self.configure(
            TOOL_SYSTEM_INFO_DISK_ROOTS=f"{missing},{self.root}"
        )

        roots = system_info._approved_disk_roots()

        self.assertEqual(roots, [str(self.root.resolve())])

    def test_unc_root_is_rejected_on_windows(self):
        if os.name != "nt":
            self.skipTest("UNC paths are Windows-only.")

        self.configure(
            TOOL_SYSTEM_INFO_DISK_ROOTS=r"\\server\share"
        )

        self.assertEqual(system_info._approved_disk_roots(), [])

    def test_disk_roots_are_capped(self):
        directories = []

        for index in range(MAX_DISK_ROOTS + 3):
            directory = tempfile.TemporaryDirectory(
                prefix=f"asta_sysinfo_extra_{index}_"
            )
            self.addCleanup(directory.cleanup)
            directories.append(directory.name)

        self.configure(
            TOOL_SYSTEM_INFO_DISK_ROOTS=",".join(directories),
            TOOL_SYSTEM_INFO_MAX_DISKS=99,
        )
        self.patch_object(
            system_info.shutil,
            "disk_usage",
            return_value=DiskUsage(1000, 600, 400),
        )

        data = system_info._collect_disk_info()

        self.assertEqual(data["count"], MAX_DISK_ROOTS)

    def test_disk_roots_property_parses_commas(self):
        self.configure(
            TOOL_SYSTEM_INFO_DISK_ROOTS=f" {self.root} , , C:\\ "
        )

        self.assertEqual(
            settings.tool_system_info_disk_roots,
            [str(self.root), "C:\\"],
        )


# ---------------------------------------------------------------------------
# Successful collection
# ---------------------------------------------------------------------------


class SystemInfoCollectionTests(SystemInfoUnitTestBase):

    def test_os_info_reads_platform_values(self):
        uname = Uname(
            "Windows",
            "laptop",
            "11",
            "10.0.22631",
            "AMD64",
            "Intel64 Family 6",
        )
        self.patch_object(
            system_info.platform,
            "uname",
            new=lambda: uname,
        )
        self.patch_object(
            system_info.platform,
            "architecture",
            new=lambda: ("64bit", "WindowsPE"),
        )
        self.patch_object(
            system_info.platform,
            "platform",
            new=lambda: "Windows-11-10.0.22631-SP0",
        )

        data = system_info._collect_os_info()

        self.assertTrue(data["available"])
        self.assertEqual(data["system"], "Windows")
        self.assertEqual(data["release"], "11")
        self.assertEqual(data["version"], "10.0.22631")
        self.assertEqual(data["build"], "22631")
        self.assertEqual(data["machine"], "AMD64")
        self.assertEqual(data["architecture"], "64bit")
        self.assertEqual(
            data["platform"],
            "Windows-11-10.0.22631-SP0",
        )

    def test_hostname_reads_local_socket_value(self):
        self.patch_object(
            system_info.socket,
            "gethostname",
            new=lambda: "ASTA-LAPTOP",
        )

        data = system_info._collect_hostname()

        self.assertTrue(data["available"])
        self.assertEqual(data["hostname"], "ASTA-LAPTOP")

    def test_hostname_falls_back_to_platform_node(self):
        self.patch_object(
            system_info.socket,
            "gethostname",
            side_effect=OSError("unavailable"),
        )
        self.patch_object(
            system_info.platform,
            "node",
            new=lambda: "FALLBACK-HOST",
        )

        data = system_info._collect_hostname()

        self.assertTrue(data["available"])
        self.assertEqual(data["hostname"], "FALLBACK-HOST")

    def test_cpu_info_reads_model_and_counts(self):
        self.patch_object(
            system_info,
            "_processor_name",
            new=lambda: "Intel(R) Core(TM) i7-9750H",
        )
        self.patch_object(
            system_info,
            "_logical_cpu_count",
            new=lambda: 12,
        )
        self.patch_object(
            system_info,
            "_physical_core_count",
            new=lambda: 6,
        )
        self.patch_object(
            system_info.platform,
            "machine",
            new=lambda: "AMD64",
        )

        data = system_info._collect_cpu_info()

        self.assertTrue(data["available"])
        self.assertEqual(
            data["model"],
            "Intel(R) Core(TM) i7-9750H",
        )
        self.assertEqual(data["logical_processors"], 12)
        self.assertEqual(data["physical_cores"], 6)
        self.assertEqual(data["architecture"], "AMD64")

    def test_memory_info_computes_used_and_percentage(self):
        total = 16 * 1024**3
        available = 4 * 1024**3
        self.patch_object(
            system_info,
            "_windows_memory",
            new=lambda: (total, available),
        )
        self.patch_object(
            system_info,
            "_posix_memory",
            new=lambda: None,
        )

        data = system_info._collect_memory_info()

        self.assertTrue(data["available"])
        self.assertEqual(data["total_bytes"], total)
        self.assertEqual(data["available_bytes"], available)
        self.assertEqual(data["used_bytes"], total - available)
        self.assertEqual(data["usage_percent"], 75.0)
        self.assertEqual(data["total_gib"], 16.0)
        self.assertEqual(data["available_gib"], 4.0)
        self.assertEqual(data["used_gib"], 12.0)

    def test_disk_info_uses_approved_roots(self):
        other = tempfile.TemporaryDirectory(
            prefix="asta_sysinfo_other_"
        )
        self.addCleanup(other.cleanup)
        self.configure(
            TOOL_SYSTEM_INFO_DISK_ROOTS=f"{self.root},{other.name}"
        )
        self.patch_object(
            system_info.shutil,
            "disk_usage",
            return_value=DiskUsage(1000, 600, 400),
        )

        data = system_info._collect_disk_info()

        self.assertTrue(data["available"])
        self.assertEqual(data["count"], 2)

        for drive in data["drives"]:
            self.assertTrue(drive["available"])
            self.assertEqual(drive["total_bytes"], 1000)
            self.assertEqual(drive["used_bytes"], 600)
            self.assertEqual(drive["available_bytes"], 400)
            self.assertEqual(drive["usage_percent"], 60.0)

    def test_disk_selector_limits_to_one_approved_drive(self):
        anchor = Path(self.root).anchor or os.sep
        self.configure(TOOL_SYSTEM_INFO_DISK_ROOTS=anchor)
        self.patch_object(
            system_info.shutil,
            "disk_usage",
            return_value=DiskUsage(1000, 600, 400),
        )

        data = system_info._collect_disk_info(anchor)

        self.assertEqual(data["count"], 1)

    def test_python_info_reads_runtime_values(self):
        self.patch_object(
            system_info.platform,
            "python_version",
            new=lambda: "3.12.10",
        )
        self.patch_object(
            system_info.platform,
            "python_implementation",
            new=lambda: "CPython",
        )
        self.patch_object(
            system_info.platform,
            "python_compiler",
            new=lambda: "MSC v.1938 64 bit",
        )
        self.patch_object(
            system_info.platform,
            "python_build",
            new=lambda: ("main", "Nov 13 2026"),
        )
        self.patch_object(
            system_info.sys,
            "executable",
            new=r"C:\Python312\python.exe",
        )

        data = system_info._collect_python_info()

        self.assertTrue(data["available"])
        self.assertEqual(data["version"], "3.12.10")
        self.assertEqual(data["implementation"], "CPython")
        self.assertEqual(
            data["executable"],
            r"C:\Python312\python.exe",
        )
        self.assertEqual(data["compiler"], "MSC v.1938 64 bit")
        self.assertEqual(data["build"], "main Nov 13 2026")

    def test_summary_contains_every_section(self):
        self.patch_object(
            system_info,
            "_collect_os_info",
            new=lambda: {
                "available": True,
                "system": "Windows",
                "release": "11",
                "build": "22631",
                "architecture": "64bit",
                "machine": "AMD64",
            },
        )
        self.patch_object(
            system_info,
            "_collect_hostname",
            new=lambda: {
                "available": True,
                "hostname": "ASTA-LAPTOP",
            },
        )
        self.patch_object(
            system_info,
            "_collect_cpu_info",
            new=lambda: {
                "available": True,
                "model": "Test CPU",
                "logical_processors": 8,
                "physical_cores": 4,
            },
        )
        self.patch_object(
            system_info,
            "_collect_memory_info",
            new=lambda: {
                "available": True,
                "total_gib": 16.0,
                "available_gib": 8.0,
                "usage_percent": 50.0,
            },
        )
        self.patch_object(
            system_info,
            "_collect_disk_info",
            new=lambda selector=None: {
                "available": True,
                "count": 1,
                "drives": [
                    {
                        "root": "C:\\",
                        "label": "C:",
                        "available": True,
                        "total_gib": 500.0,
                        "available_gib": 100.0,
                        "usage_percent": 80.0,
                    }
                ],
            },
        )
        self.patch_object(
            system_info,
            "_collect_python_info",
            new=lambda: {
                "available": True,
                "implementation": "CPython",
                "version": "3.12.10",
                "executable": r"C:\Python312\python.exe",
            },
        )

        data = system_info._collect_summary()
        summary = data["summary"]

        self.assertTrue(data["available"])
        self.assertIn("OS: Windows 11 build 22631 (64bit)", summary)
        self.assertIn("Hostname: ASTA-LAPTOP", summary)
        self.assertIn("CPU: Test CPU", summary)
        self.assertIn("Memory: 16.0 GiB total", summary)
        self.assertIn("Disk C: 500.0 GiB total", summary)
        self.assertIn("Python: CPython 3.12.10", summary)

    def test_summary_marks_missing_disk_as_unavailable(self):
        self.patch_object(
            system_info,
            "_collect_os_info",
            new=lambda: {"available": False},
        )
        self.patch_object(
            system_info,
            "_collect_hostname",
            new=lambda: {"available": False, "hostname": None},
        )
        self.patch_object(
            system_info,
            "_collect_cpu_info",
            new=lambda: {"available": False},
        )
        self.patch_object(
            system_info,
            "_collect_memory_info",
            new=lambda: {"available": False},
        )
        self.patch_object(
            system_info,
            "_collect_python_info",
            new=lambda: {"available": False},
        )
        self.patch_object(
            system_info,
            "_collect_disk_info",
            side_effect=RuntimeError("disk subsystem failed"),
        )

        data = system_info._collect_summary()

        self.assertFalse(data["sections"]["disk"]["available"])
        self.assertIn("Disk: unavailable", data["summary"])
        self.assertNotIn("disk subsystem failed", data["summary"])


# ---------------------------------------------------------------------------
# Unavailable data and permission errors
# ---------------------------------------------------------------------------


class SystemInfoUnavailableTests(SystemInfoUnitTestBase):

    def test_os_info_failure_is_reported_as_unavailable(self):
        self.patch_object(
            system_info.platform,
            "uname",
            side_effect=OSError("denied"),
        )
        self.patch_object(
            system_info.platform,
            "architecture",
            side_effect=OSError("denied"),
        )
        self.patch_object(
            system_info.platform,
            "platform",
            side_effect=OSError("denied"),
        )

        data = system_info._collect_os_info()

        self.assertFalse(data["available"])
        self.assertIsNone(data["system"])
        self.assertIsNone(data["architecture"])
        self.assertNotIn("denied", json.dumps(data))

    def test_hostname_failure_is_reported_as_unavailable(self):
        self.patch_object(
            system_info.socket,
            "gethostname",
            side_effect=OSError("denied"),
        )
        self.patch_object(
            system_info.platform,
            "node",
            side_effect=OSError("denied"),
        )

        data = system_info._collect_hostname()

        self.assertFalse(data["available"])
        self.assertIsNone(data["hostname"])

    def test_cpu_info_failure_is_reported_as_unavailable(self):
        self.patch_object(
            system_info,
            "_processor_name",
            new=lambda: None,
        )
        self.patch_object(
            system_info,
            "_logical_cpu_count",
            new=lambda: None,
        )
        self.patch_object(
            system_info,
            "_physical_core_count",
            new=lambda: None,
        )
        self.patch_object(
            system_info.platform,
            "machine",
            side_effect=OSError("denied"),
        )

        data = system_info._collect_cpu_info()

        self.assertFalse(data["available"])
        self.assertIsNone(data["model"])
        self.assertIsNone(data["logical_processors"])
        self.assertIsNone(data["physical_cores"])

    def test_memory_info_failure_is_reported_as_unavailable(self):
        self.patch_object(
            system_info,
            "_windows_memory",
            new=lambda: None,
        )
        self.patch_object(
            system_info,
            "_posix_memory",
            new=lambda: None,
        )

        data = system_info._collect_memory_info()

        self.assertFalse(data["available"])
        self.assertIsNone(data["total_bytes"])
        self.assertIsNone(data["usage_percent"])

    def test_disk_permission_error_is_reported_safely(self):
        self.patch_object(
            system_info.shutil,
            "disk_usage",
            side_effect=PermissionError("access denied"),
        )

        data = system_info._collect_disk_info()

        self.assertFalse(data["available"])
        self.assertEqual(data["count"], 1)
        self.assertFalse(data["drives"][0]["available"])
        self.assertEqual(
            data["drives"][0]["error"],
            "Disk usage is unavailable.",
        )
        self.assertNotIn("access denied", json.dumps(data))

    def test_disk_without_approved_root_is_a_configuration_error(self):
        self.configure(TOOL_SYSTEM_INFO_DISK_ROOTS="")
        self.patch_object(
            system_info,
            "_default_disk_root",
            new=lambda: None,
        )

        with self.assertRaises(Exception) as raised:
            system_info._collect_disk_info()

        self.assertEqual(
            type(raised.exception).__name__,
            "ToolConfigurationError",
        )

    def test_unexpected_error_becomes_safe_execution_error(self):
        self.patch_object(
            system_info,
            "_collect_os_info",
            side_effect=RuntimeError("password=supersecret"),
        )

        with self.assertRaises(ToolExecutionError) as raised:
            asyncio.run(self.run_tool(SystemOsInfoTool()))

        message = str(raised.exception)

        self.assertNotIn("supersecret", message)
        self.assertNotIn("Traceback", message)
        self.assertEqual(message, "System information is unavailable.")


# ---------------------------------------------------------------------------
# Input validation and bounds
# ---------------------------------------------------------------------------


class SystemInfoValidationTests(SystemInfoUnitTestBase):

    def test_unknown_argument_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            SystemOsInfoTool().validate_arguments({"unexpected": 1})

    def test_disk_selector_rejects_malformed_value(self):
        with self.assertRaises(ToolValidationError):
            asyncio.run(
                self.run_tool(
                    SystemDiskInfoTool(),
                    {"drive": "not a drive"},
                )
            )

    def test_disk_selector_rejects_unapproved_drive(self):
        configured_drive = Path(self.root).drive.upper() or "/"
        other = "Z:" if configured_drive != "Z:" else "Y:"

        with self.assertRaises(ToolValidationError):
            asyncio.run(
                self.run_tool(
                    SystemDiskInfoTool(),
                    {"drive": other},
                )
            )

    def test_disk_selector_is_length_bounded(self):
        with self.assertRaises(ToolValidationError):
            SystemDiskInfoTool().validate_arguments(
                {"drive": "C" * 17}
            )

    def test_disk_selector_must_be_a_string(self):
        with self.assertRaises(ToolValidationError):
            SystemDiskInfoTool().validate_arguments({"drive": 123})

    def test_text_values_are_collapsed_and_bounded(self):
        self.assertEqual(
            system_info._text("a\nb\tc"),
            "a b c",
        )
        self.assertEqual(
            len(system_info._text("x" * 500)),
            MAX_TEXT_CHARS,
        )
        self.assertIsNone(system_info._text("   "))
        self.assertIsNone(system_info._text(None))

    def test_os_fields_are_bounded(self):
        long_value = "x" * 1000
        uname = Uname(
            long_value,
            "laptop",
            long_value,
            long_value,
            long_value,
            long_value,
        )
        self.patch_object(
            system_info.platform,
            "uname",
            new=lambda: uname,
        )
        self.patch_object(
            system_info.platform,
            "architecture",
            new=lambda: (long_value, "WindowsPE"),
        )
        self.patch_object(
            system_info.platform,
            "platform",
            new=lambda: long_value,
        )

        data = system_info._collect_os_info()

        for key in (
            "system",
            "release",
            "version",
            "machine",
            "architecture",
            "platform",
        ):
            with self.subTest(field=key):
                self.assertLessEqual(
                    len(data[key] or ""),
                    MAX_TEXT_CHARS,
                )

    def test_large_tool_result_is_truncated(self):
        self.patch_object(
            system_info,
            "_collect_summary",
            new=lambda: {"summary": "x" * 50000},
        )
        self.patch_object(settings, "TOOL_MAX_RESULT_BYTES", new=1000)

        result = asyncio.run(self.run_tool(SystemSummaryTool()))

        self.assertTrue(result.success)
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.result_bytes, 1000)

    def test_summary_text_is_bounded(self):
        self.patch_object(
            system_info,
            "_collect_os_info",
            new=lambda: {"available": True, "system": "x" * 500},
        )
        self.patch_object(
            system_info,
            "_collect_hostname",
            new=lambda: {"available": True, "hostname": "x" * 500},
        )
        self.patch_object(
            system_info,
            "_collect_cpu_info",
            new=lambda: {
                "available": True,
                "model": "x" * 500,
                "logical_processors": 8,
            },
        )
        self.patch_object(
            system_info,
            "_collect_memory_info",
            new=lambda: {
                "available": True,
                "total_gib": 16.0,
                "available_gib": 8.0,
                "usage_percent": 50.0,
            },
        )
        self.patch_object(
            system_info,
            "_collect_python_info",
            new=lambda: {
                "available": True,
                "implementation": "CPython",
                "version": "3.12.10",
            },
        )
        self.patch_object(
            system_info,
            "_collect_disk_info",
            new=lambda selector=None: {
                "available": True,
                "drives": [],
            },
        )

        data = system_info._collect_summary()

        self.assertLessEqual(
            len(data["summary"]),
            system_info.MAX_SUMMARY_CHARS,
        )


# ---------------------------------------------------------------------------
# Secret protection
# ---------------------------------------------------------------------------


class SystemInfoSecretTests(SystemInfoUnitTestBase):

    def test_output_never_contains_environment_values(self):
        secret = "sk-test-phase12a-super-secret-value"

        with patch.dict(
            os.environ,
            {"ASTA_PHASE12A_TEST_SECRET": secret},
        ):
            async def collect():
                return [
                    (await self.run_tool(tool)).data
                    for tool in SYSTEM_INFO_TOOLS
                ]

            rendered = json.dumps(
                asyncio.run(collect()),
                default=str,
            )

        self.assertNotIn(secret, rendered)
        self.assertNotIn("ASTA_PHASE12A_TEST_SECRET", rendered)

    def test_output_has_no_credential_field_names(self):
        async def collect():
            return [
                (await self.run_tool(tool)).data
                for tool in SYSTEM_INFO_TOOLS
            ]

        rendered = json.dumps(
            asyncio.run(collect()),
            default=str,
        ).lower()

        for marker in (
            "password",
            "passwd",
            "api_key",
            "access_token",
            "refresh_token",
            "private_key",
        ):
            self.assertNotIn(marker, rendered)

    def test_error_message_does_not_echo_collector_exception(self):
        self.patch_object(
            system_info,
            "_collect_memory_info",
            side_effect=RuntimeError("token=supersecret"),
        )

        with self.assertRaises(ToolExecutionError) as raised:
            asyncio.run(self.run_tool(SystemMemoryInfoTool()))

        self.assertNotIn("supersecret", str(raised.exception))
        self.assertNotIn("token", str(raised.exception).lower())


# ---------------------------------------------------------------------------
# Registry, discovery, and permission policy
# ---------------------------------------------------------------------------


class SystemInfoRegistrationTests(unittest.TestCase):

    def test_all_seven_tools_are_registered(self):
        self.assertTrue(
            SYSTEM_INFO_NAMES.issubset(set(tool_registry.names()))
        )

    def test_builtin_tool_list_contains_every_system_tool(self):
        names = [tool.name for tool in BUILTIN_TOOLS]

        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(SYSTEM_INFO_NAMES.issubset(names))

    def test_existing_phase_tools_are_still_registered(self):
        names = set(tool_registry.names())

        self.assertTrue(
            {
                "task_list",
                "task_get",
                "task_create",
                "task_update",
                "web_search",
                "fs_read",
                "fs_create",
                "python_execute",
                "http_request",
            }.issubset(names)
        )

    def test_provider_schema_is_function_calling_compatible(self):
        definition = tool_registry.get("system_os_info").definition()
        schema = definition.provider_schema()

        self.assertEqual(schema["type"], "function")
        self.assertEqual(
            schema["function"]["name"],
            "system_os_info",
        )
        self.assertEqual(
            schema["function"]["parameters"]["type"],
            "object",
        )
        self.assertFalse(
            schema["function"]["parameters"]["additionalProperties"]
        )

    def test_llm_definitions_include_enabled_tools(self):
        with (
            patch.object(
                settings,
                "TOOL_OS_AUTOMATION_ENABLED",
                True,
            ),
            patch.object(
                settings,
                "TOOL_SYSTEM_INFO_ENABLED",
                True,
            ),
        ):
            names = {
                item["function"]["name"]
                for item in tool_registry.llm_definitions(
                    private=False
                )
            }

        self.assertTrue(SYSTEM_INFO_NAMES.issubset(names))

    def test_llm_definitions_exclude_disabled_tools(self):
        with patch.object(
            settings,
            "TOOL_SYSTEM_INFO_ENABLED",
            False,
        ):
            names = {
                item["function"]["name"]
                for item in tool_registry.llm_definitions(
                    private=False
                )
            }

        self.assertFalse(SYSTEM_INFO_NAMES.intersection(names))

    def test_llm_definitions_exclude_private_mode(self):
        with (
            patch.object(
                settings,
                "TOOL_OS_AUTOMATION_ENABLED",
                True,
            ),
            patch.object(
                settings,
                "TOOL_SYSTEM_INFO_ENABLED",
                True,
            ),
        ):
            names = {
                item["function"]["name"]
                for item in tool_registry.llm_definitions(
                    private=True
                )
            }

        self.assertFalse(SYSTEM_INFO_NAMES.intersection(names))


class SystemInfoPermissionTests(SystemInfoUnitTestBase):

    def test_read_only_tools_are_allowed_without_confirmation(self):
        for tool in SYSTEM_INFO_TOOLS:
            with self.subTest(tool=tool.name):
                decision = permission_policy.evaluate(
                    tool,
                    private=False,
                )
                self.assertTrue(decision.allowed)
                self.assertFalse(decision.requires_confirmation)

    def test_private_mode_is_denied(self):
        for tool in SYSTEM_INFO_TOOLS:
            with self.subTest(tool=tool.name):
                decision = permission_policy.evaluate(
                    tool,
                    private=True,
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.error_category,
                    ToolErrorCategory.PERMISSION,
                )

    def test_disabled_tools_are_denied(self):
        self.configure(TOOL_SYSTEM_INFO_ENABLED=False)

        for tool in SYSTEM_INFO_TOOLS:
            with self.subTest(tool=tool.name):
                decision = permission_policy.evaluate(
                    tool,
                    private=False,
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.error_category,
                    ToolErrorCategory.DISABLED,
                )


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------


class SystemInfoExecutorTests(ToolEngineTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings(
            TOOL_OS_AUTOMATION_ENABLED=True,
            TOOL_SYSTEM_INFO_ENABLED=True,
            TOOL_SYSTEM_INFO_DISK_ROOTS=self.workspace.name,
            TOOL_SYSTEM_INFO_MAX_DISKS=8,
        )

        collectors = {
            "_collect_os_info": lambda: {
                "available": True,
                "system": "Windows",
                "release": "11",
                "build": "22631",
                "architecture": "64bit",
                "machine": "AMD64",
                "platform": "Windows-11",
            },
            "_collect_hostname": lambda: {
                "available": True,
                "hostname": "ASTA-LAPTOP",
            },
            "_collect_cpu_info": lambda: {
                "available": True,
                "model": "Test CPU",
                "logical_processors": 8,
                "physical_cores": 4,
                "architecture": "AMD64",
            },
            "_collect_memory_info": lambda: {
                "available": True,
                "total_bytes": 17179869184,
                "available_bytes": 8589934592,
                "used_bytes": 8589934592,
                "usage_percent": 50.0,
                "total_gib": 16.0,
                "available_gib": 8.0,
                "used_gib": 8.0,
            },
            "_collect_python_info": lambda: {
                "available": True,
                "version": "3.12.10",
                "implementation": "CPython",
                "executable": r"C:\Python312\python.exe",
                "compiler": "MSC v.1938",
                "build": "main",
            },
        }

        for name, collector in collectors.items():
            patcher = patch.object(
                system_info,
                name,
                new=collector,
            )
            patcher.start()
            self.addCleanup(patcher.stop)

        disk_patcher = patch.object(
            system_info,
            "_collect_disk_info",
            new=self._disk_info,
        )
        disk_patcher.start()
        self.addCleanup(disk_patcher.stop)

    @staticmethod
    def _disk_info(selector=None):
        return {
            "available": True,
            "count": 1,
            "drives": [
                {
                    "root": "C:\\",
                    "label": "C:",
                    "available": True,
                    "total_bytes": 500 * 1024**3,
                    "used_bytes": 400 * 1024**3,
                    "available_bytes": 100 * 1024**3,
                    "usage_percent": 80.0,
                    "total_gib": 500.0,
                    "used_gib": 400.0,
                    "available_gib": 100.0,
                }
            ],
        }

    async def run_exec(
        self,
        tool_name,
        arguments=None,
        *,
        private=False,
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
            )

    async def test_every_tool_runs_without_confirmation(self):
        cases = [
            ("system_os_info", {}),
            ("system_hostname", {}),
            ("system_cpu_info", {}),
            ("system_memory_info", {}),
            ("system_disk_info", {}),
            ("system_python_info", {}),
            ("system_summary", {}),
        ]

        for tool_name, arguments in cases:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(
                    tool_name,
                    arguments,
                )

                self.assertTrue(result.success)
                self.assertFalse(result.confirmation_required)
                self.assertIsNone(result.confirmation_id)
                self.assertEqual(result.provider, "system")
                self.assertIsNotNone(result.data)

    async def test_disabled_tool_is_denied(self):
        self.settings(TOOL_SYSTEM_INFO_ENABLED=False)

        result = await self.run_exec("system_os_info")

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.DENIED,
        )
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )

    async def test_private_request_is_denied_for_every_tool(self):
        for tool_name in SYSTEM_INFO_NAMES:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(
                    tool_name,
                    private=True,
                )

                self.assertFalse(result.success)
                self.assertEqual(
                    result.status,
                    ToolExecutionStatus.DENIED,
                )
                self.assertEqual(
                    result.error_category,
                    ToolErrorCategory.PERMISSION,
                )

    async def test_invalid_arguments_are_rejected_by_executor(self):
        result = await self.run_exec(
            "system_os_info",
            {"unexpected": 1},
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )

    async def test_executor_redacts_credential_like_output(self):
        with patch.object(
            system_info,
            "_collect_os_info",
            new=lambda: {
                "available": True,
                "system": "Windows token=supersecretvalue",
            },
        ):
            result = await self.run_exec("system_os_info")

        rendered = json.dumps(result.data, default=str)

        self.assertTrue(result.success)
        self.assertNotIn("supersecretvalue", rendered)

    async def test_execution_history_records_system_provider(self):
        result = await self.run_exec("system_hostname")

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "system")
        self.assertIsNotNone(result.execution_id)


# ---------------------------------------------------------------------------
# Live smoke checks
# ---------------------------------------------------------------------------


class SystemInfoLiveSmokeTests(unittest.TestCase):
    """Check the real host structure without asserting machine values."""

    def test_live_os_info_structure(self):
        data = system_info._collect_os_info()

        self.assertIsInstance(data, dict)
        self.assertIn("available", data)

        if not data["available"]:
            self.skipTest("OS information is unavailable on this host.")

        self.assertIsInstance(data["system"], str)
        self.assertLessEqual(len(data["system"]), MAX_TEXT_CHARS)

    def test_live_memory_structure(self):
        data = system_info._collect_memory_info()

        self.assertIsInstance(data, dict)

        if not data["available"]:
            self.skipTest(
                "Memory information is unavailable on this host."
            )

        self.assertGreater(data["total_bytes"], 0)
        self.assertGreaterEqual(data["used_bytes"], 0)
        self.assertLessEqual(
            data["available_bytes"],
            data["total_bytes"],
        )
        self.assertGreaterEqual(data["usage_percent"], 0.0)
        self.assertLessEqual(data["usage_percent"], 100.0)

    def test_live_disk_structure(self):
        if not system_info._approved_disk_roots():
            self.skipTest("No approved local disk is available.")

        data = system_info._collect_disk_info()

        self.assertIsInstance(data, dict)
        self.assertGreaterEqual(data["count"], 1)

        for drive in data["drives"]:
            self.assertIn("root", drive)
            self.assertIn("available", drive)


__all__ = [
    "SystemInfoCollectionTests",
    "SystemInfoConfigurationTests",
    "SystemInfoExecutorTests",
    "SystemInfoLiveSmokeTests",
    "SystemInfoPermissionTests",
    "SystemInfoRegistrationTests",
    "SystemInfoSecretTests",
    "SystemInfoUnavailableTests",
    "SystemInfoUnitTestBase",
    "SystemInfoValidationTests",
]
