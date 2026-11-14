"""Phase 12D tests for read-only process and active-window inspection.

Every Windows API call is mocked. No real process is opened, inspected,
modified, or terminated, and no real desktop window is read. The suite is
deterministic on any platform and uses only temporary fixtures.

Coverage: process enumeration and filtering, process metrics and failure
modes, the Phase 12B application allowlist integration, foreground-window
metadata, bounded resource summaries, configuration defaults, permission
policy, registry/executor integration, and source-level checks that the module
cannot modify processes or windows.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.core.config import settings
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import ToolCategory, ToolErrorCategory, ToolExecutionStatus
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolPermissionError,
    ToolPrivateRequestError,
    ToolValidationError,
)
from app.tools.executor import tool_executor
from app.tools.implementations import application_launcher, process_inspection
from app.tools.implementations.application_launcher import (
    ApprovedApplication,
)
from app.tools.implementations.process_inspection import (
    ActiveWindowInfo,
    MAX_RESULTS_LIMIT,
    PROCESS_INSPECTION_TOOLS,
    ProcessEntry,
    ProcessMetrics,
    SystemActiveWindowTool,
    SystemApplicationStatusTool,
    SystemProcessInfoTool,
    SystemProcessListTool,
    SystemResourceSummaryTool,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from tests.tool_test_support import ToolEngineTestBase


TOOL_NAMES = {
    "system_process_list",
    "system_process_info",
    "system_application_status",
    "system_active_window",
    "system_resource_summary",
}

_MISSING = object()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def entry(pid: int, name: str, threads: int = 4) -> ProcessEntry:
    return ProcessEntry(pid=pid, name=name, thread_count=threads)


def metrics(
    *,
    accessible: bool = True,
    cpu_time: float | None = 1.0,
    cpu_percent: float | None = 5.0,
    memory: int | None = 1024 * 1024,
    status: str = "running",
) -> ProcessMetrics:
    return ProcessMetrics(
        accessible=accessible,
        cpu_time_seconds=cpu_time,
        cpu_percent=cpu_percent,
        memory_bytes=memory,
        status=status,
    )


def approved_application(
    alias: str = "notepad",
    display: str = "Notepad",
    executable: str = r"C:\Windows\notepad.exe",
) -> ApprovedApplication:
    return ApprovedApplication(
        alias=alias,
        display_name=display,
        executable=executable,
    )


class ProcessInspectionTestBase(unittest.IsolatedAsyncioTestCase):
    """Patched configuration and a fake supported platform."""

    def setUp(self):
        self.configure()
        self.patch_object(
            process_inspection,
            "_is_supported_platform",
            return_value=True,
        )
        self.patch_object(
            application_launcher,
            "_approved_applications",
            return_value={"notepad": approved_application()},
        )

    def configure(self, **overrides):
        values = {
            "TOOL_OS_AUTOMATION_ENABLED": True,
            "TOOL_PROCESS_INSPECTION_ENABLED": True,
            "TOOL_PROCESS_MAX_RESULTS": 25,
            "TOOL_PROCESS_MAX_MATCHES": 20,
            "TOOL_PROCESS_MAX_NAME_CHARS": 128,
            "TOOL_PROCESS_MAX_TITLE_CHARS": 300,
            "TOOL_PROCESS_CPU_SAMPLE_MS": 0,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def patch_object(self, target, name, new=_MISSING, **kwargs):
        if new is _MISSING:
            patcher = patch.object(target, name, **kwargs)
        else:
            patcher = patch.object(target, name, new, **kwargs)

        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_tool(self, tool, arguments=None):
        validated = tool.validate_arguments(arguments or {})
        return await tool.execute(None, validated)


# ---------------------------------------------------------------------------
# Configuration and metadata
# ---------------------------------------------------------------------------


class ConfigurationTests(ProcessInspectionTestBase):

    def test_tools_are_disabled_by_default(self):
        self.configure(TOOL_PROCESS_INSPECTION_ENABLED=False)

        for tool in PROCESS_INSPECTION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertFalse(tool.enabled)

    def test_enabled_flag_enables_every_tool(self):
        self.assertTrue(
            all(tool.enabled for tool in PROCESS_INSPECTION_TOOLS)
        )

    def test_application_status_requires_an_approved_application(self):
        self.patch_object(
            application_launcher,
            "_approved_applications",
            return_value={},
        )

        self.assertFalse(SystemApplicationStatusTool().enabled)

    def test_tools_are_read_only_idempotent_and_private_denied(self):
        for tool in PROCESS_INSPECTION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.category, ToolCategory.READ_ONLY)
                self.assertFalse(tool.requires_confirmation)
                self.assertFalse(tool.allowed_in_private)
                self.assertTrue(tool.idempotent)
                self.assertEqual(
                    tool.provider_label,
                    "process_inspection",
                )

    def test_tools_are_not_sensitive(self):
        for tool in PROCESS_INSPECTION_TOOLS:
            self.assertNotEqual(tool.category, ToolCategory.SENSITIVE)

    def test_exactly_five_tools_are_defined(self):
        self.assertEqual(
            {tool.name for tool in PROCESS_INSPECTION_TOOLS},
            TOOL_NAMES,
        )

    def test_unsupported_platform_disables_every_tool(self):
        self.patch_object(
            process_inspection,
            "_is_supported_platform",
            return_value=False,
        )

        for tool in PROCESS_INSPECTION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertFalse(tool.enabled)

    def test_unsupported_platform_fails_closed(self):
        self.patch_object(
            process_inspection,
            "_is_supported_platform",
            return_value=False,
        )

        with self.assertRaises(ToolConfigurationError):
            process_inspection._enumerate_processes()

        with self.assertRaises(ToolConfigurationError):
            process_inspection._read_active_window()


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class InputSchemaTests(ProcessInspectionTestBase):

    def test_unknown_arguments_are_rejected(self):
        payloads = (
            (SystemProcessListTool(), {"path": r"C:\Windows"}),
            (SystemProcessInfoTool(), {"name": "chrome"}),
            (SystemApplicationStatusTool(), {"path": "C:\\evil.exe"}),
            (SystemActiveWindowTool(), {"capture": True}),
            (SystemResourceSummaryTool(), {"name": "chrome"}),
        )

        for tool, payload in payloads:
            with self.subTest(tool=tool.name):
                with self.assertRaises(ToolValidationError):
                    tool.validate_arguments(payload)

    def test_process_id_bounds_are_enforced(self):
        tool = SystemProcessInfoTool()

        for value in (-1, 0x100000000):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    tool.validate_arguments({"pid": value})

        self.assertIsNotNone(tool.validate_arguments({"pid": 0}))

    def test_result_limits_are_bounded(self):
        for tool in (SystemProcessListTool(), SystemResourceSummaryTool()):
            for value in (0, MAX_RESULTS_LIMIT + 1):
                with self.subTest(tool=tool.name, value=value):
                    with self.assertRaises(ToolValidationError):
                        tool.validate_arguments({"limit": value})

    def test_name_filter_rejects_paths_and_control_characters(self):
        tool = SystemProcessListTool()

        for value in (
            r"C:\Windows\chrome.exe",
            r"\\server\share\chrome.exe",
            "chrome/../../secret",
            "chrome\x00exe",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    process_inspection._validated_name_filter(value)

    def test_name_filter_is_bounded(self):
        tool = SystemProcessListTool()

        with self.assertRaises(ToolValidationError):
            tool.validate_arguments({"name_filter": "a" * 65})

    def test_application_status_accepts_only_an_alias(self):
        tool = SystemApplicationStatusTool()

        def resolve(value):
            alias = application_launcher._validated_alias(value)

            if alias != "notepad":
                raise ToolValidationError(
                    "That application is not on the approved list."
                )

            return approved_application()

        self.patch_object(
            application_launcher,
            "_resolve_application",
            side_effect=resolve,
        )

        for value in (
            r"C:\Windows\notepad.exe",
            r"\\server\share\notepad.exe",
            "../notepad",
            "notepad --flag",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    tool.validate_arguments({"application": value})

    def test_active_window_takes_no_arguments(self):
        tool = SystemActiveWindowTool()

        self.assertIsNotNone(tool.validate_arguments({}))

        with self.assertRaises(ToolValidationError):
            tool.validate_arguments({"anything": 1})


# ---------------------------------------------------------------------------
# Process listing
# ---------------------------------------------------------------------------


class ProcessListTests(ProcessInspectionTestBase):

    def setUp(self):
        super().setUp()
        self.tool = SystemProcessListTool()

    async def test_successful_enumeration_is_bounded_and_structured(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(10, "chrome.exe"),
                entry(20, "notepad.exe"),
            ],
        )

        result = await self.run_tool(self.tool)

        self.assertTrue(result.success)
        self.assertEqual(result.data["count"], 2)
        self.assertEqual(result.data["total_matched"], 2)
        self.assertFalse(result.data["truncated"])
        self.assertEqual(
            result.data["processes"],
            [
                {"pid": 10, "name": "chrome.exe"},
                {"pid": 20, "name": "notepad.exe"},
            ],
        )

    async def test_empty_process_list(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[],
        )

        result = await self.run_tool(self.tool)

        self.assertTrue(result.success)
        self.assertEqual(result.data["count"], 0)
        self.assertEqual(result.data["processes"], [])

    async def test_maximum_result_limit_is_applied(self):
        self.configure(TOOL_PROCESS_MAX_RESULTS=2)
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(index, f"app{index}.exe") for index in range(10, 15)],
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(result.data["count"], 2)
        self.assertEqual(result.data["total_matched"], 5)
        self.assertTrue(result.data["truncated"])

    async def test_explicit_limit_is_applied(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(index, f"app{index}.exe") for index in range(1, 6)],
        )

        result = await self.run_tool(self.tool, {"limit": 1})

        self.assertEqual(result.data["count"], 1)

    async def test_application_name_filter_is_case_insensitive(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(10, "chrome.exe"),
                entry(20, "notepad.exe"),
            ],
        )

        result = await self.run_tool(
            self.tool,
            {"name_filter": "CHROME"},
        )

        self.assertEqual(
            result.data["processes"],
            [{"pid": 10, "name": "chrome.exe"}],
        )
        self.assertEqual(result.data["filter"], "chrome")

    async def test_duplicate_process_names_are_preserved(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(10, "chrome.exe"),
                entry(11, "chrome.exe"),
            ],
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(
            [item["pid"] for item in result.data["processes"]],
            [10, 11],
        )

    async def test_protected_processes_are_filtered(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(4, "System"),
                entry(500, "csrss.exe"),
                entry(600, "lsass.exe"),
                entry(700, "explorer.exe"),
            ],
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(
            result.data["processes"],
            [{"pid": 700, "name": "explorer.exe"}],
        )

    async def test_listing_never_opens_process_handles(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(10, "chrome.exe")],
        )
        reader = Mock()
        self.patch_object(
            process_inspection,
            "_read_process_metrics",
            reader,
        )

        await self.run_tool(self.tool)

        reader.assert_not_called()

    async def test_process_names_are_bounded(self):
        self.configure(TOOL_PROCESS_MAX_NAME_CHARS=16)
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(10, "a" * 200)],
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(len(result.data["processes"][0]["name"]), 16)

    async def test_snapshot_failure_is_a_safe_error(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            side_effect=OSError("password=super-secret"),
        )

        with self.assertRaises(
            process_inspection.ToolExecutionError
        ) as raised:
            await self.run_tool(self.tool)

        message = str(raised.exception)

        self.assertEqual(message, "Process information is unavailable.")
        self.assertNotIn("super-secret", message)
        self.assertNotIn("Traceback", message)


# ---------------------------------------------------------------------------
# Process information
# ---------------------------------------------------------------------------


class ProcessInfoTests(ProcessInspectionTestBase):

    def setUp(self):
        super().setUp()
        self.tool = SystemProcessInfoTool()

    async def test_valid_process_returns_bounded_metrics(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(100, "chrome.exe", threads=12)],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                100: metrics(
                    cpu_time=12.5,
                    cpu_percent=3.2,
                    memory=104857600,
                )
            },
        )

        result = await self.run_tool(self.tool, {"pid": 100})

        self.assertTrue(result.success)
        self.assertEqual(result.data["pid"], 100)
        self.assertTrue(result.data["exists"])
        self.assertTrue(result.data["accessible"])
        self.assertEqual(result.data["name"], "chrome.exe")
        self.assertEqual(result.data["status"], "running")
        self.assertEqual(result.data["thread_count"], 12)
        self.assertEqual(result.data["cpu"]["percent"], 3.2)
        self.assertEqual(result.data["cpu"]["time_seconds"], 12.5)
        self.assertEqual(result.data["memory"]["bytes"], 104857600)
        self.assertEqual(result.data["memory"]["mib"], 100.0)

    async def test_negative_process_id_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            self.tool.validate_arguments({"pid": -5})

    async def test_nonexistent_process_is_reported_as_not_running(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(200, "notepad.exe")],
        )
        sampler = Mock()
        self.patch_object(
            process_inspection,
            "_sample_processes",
            sampler,
        )

        result = await self.run_tool(self.tool, {"pid": 100})

        self.assertTrue(result.success)
        self.assertFalse(result.data["exists"])
        self.assertEqual(result.data["status"], "not_running")
        self.assertEqual(
            result.data["cpu"]["reason"],
            "process_not_found",
        )
        sampler.assert_not_called()

    async def test_protected_process_id_is_refused(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(4, "System")],
        )

        with self.assertRaises(ToolPermissionError):
            await self.run_tool(self.tool, {"pid": 4})

    async def test_protected_process_name_is_refused(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(600, "lsass.exe")],
        )

        with self.assertRaises(ToolPermissionError):
            await self.run_tool(self.tool, {"pid": 600})

    async def test_access_denied_is_distinct_from_not_running(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            side_effect=[
                [entry(100, "chrome.exe")],
                [entry(100, "chrome.exe")],
            ],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                100: ProcessMetrics(
                    accessible=False,
                    status="access_denied",
                )
            },
        )

        result = await self.run_tool(self.tool, {"pid": 100})

        self.assertTrue(result.data["exists"])
        self.assertFalse(result.data["accessible"])
        self.assertEqual(result.data["status"], "access_denied")
        self.assertEqual(
            result.data["cpu"]["reason"],
            "access_denied",
        )
        self.assertEqual(
            result.data["memory"]["reason"],
            "access_denied",
        )

    async def test_unavailable_metrics_are_marked_unavailable(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(100, "chrome.exe")],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                100: metrics(
                    cpu_time=None,
                    cpu_percent=None,
                    memory=None,
                )
            },
        )

        result = await self.run_tool(self.tool, {"pid": 100})

        self.assertFalse(result.data["cpu"]["available"])
        self.assertEqual(result.data["cpu"]["reason"], "unavailable")
        self.assertFalse(result.data["memory"]["available"])
        self.assertEqual(result.data["memory"]["reason"], "unavailable")

    async def test_process_disappearing_during_inspection_is_reported(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            side_effect=[[entry(100, "chrome.exe")], []],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                100: ProcessMetrics(
                    accessible=False,
                    status="access_denied",
                )
            },
        )

        result = await self.run_tool(self.tool, {"pid": 100})

        self.assertFalse(result.data["exists"])
        self.assertEqual(result.data["status"], "process_exited")
        self.assertEqual(
            result.data["cpu"]["reason"],
            "process_exited",
        )

    async def test_process_name_is_bounded(self):
        self.configure(TOOL_PROCESS_MAX_NAME_CHARS=16)
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(100, "a" * 200)],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={100: metrics()},
        )

        result = await self.run_tool(self.tool, {"pid": 100})

        self.assertEqual(len(result.data["name"]), 16)

    async def test_result_contains_no_sensitive_fields(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(100, "chrome.exe")],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={100: metrics()},
        )

        result = await self.run_tool(self.tool, {"pid": 100})
        rendered = json.dumps(result.data, default=str).lower()

        for marker in (
            "command_line",
            "cmdline",
            "environment",
            "username",
            "executable_path",
            "password",
            "token",
        ):
            self.assertNotIn(marker, rendered)


# ---------------------------------------------------------------------------
# CPU sampling
# ---------------------------------------------------------------------------


class CpuSamplingTests(unittest.TestCase):

    def test_cpu_percentage_is_computed_and_clamped(self):
        first = {
            1: metrics(cpu_time=1.0),
            2: metrics(cpu_time=1.0),
        }
        second = {
            1: metrics(cpu_time=2.0),
            2: metrics(cpu_time=1.5),
        }
        reader = Mock(side_effect=[first[1], first[2], second[1], second[2]])

        with patch.object(
            process_inspection,
            "_read_process_metrics",
            reader,
        ), patch.object(
            process_inspection.time,
            "sleep",
        ) as sleep, patch.object(
            process_inspection.time,
            "monotonic",
            side_effect=[10.0, 11.0],
        ), patch.object(
            process_inspection.os,
            "cpu_count",
            return_value=2,
        ):
            results = process_inspection._sample_processes([1, 2], 10)

        sleep.assert_called_once_with(0.01)
        self.assertEqual(results[1].cpu_percent, 50.0)
        self.assertEqual(results[2].cpu_percent, 25.0)

    def test_zero_sample_interval_reports_no_percentage(self):
        reader = Mock(
            side_effect=[metrics(cpu_time=1.0), metrics(cpu_time=2.0)]
        )

        with patch.object(
            process_inspection,
            "_read_process_metrics",
            reader,
        ), patch.object(
            process_inspection.time,
            "sleep",
        ) as sleep:
            results = process_inspection._sample_processes([1], 0)

        sleep.assert_not_called()
        self.assertIsNone(results[1].cpu_percent)

    def test_duplicate_pids_are_sampled_once(self):
        reader = Mock(
            side_effect=[metrics(), metrics(), metrics(), metrics()]
        )

        with patch.object(
            process_inspection,
            "_read_process_metrics",
            reader,
        ), patch.object(process_inspection.time, "sleep"):
            process_inspection._sample_processes([1, 1], 0)

        self.assertEqual(reader.call_count, 2)

    def test_empty_pid_list(self):
        self.assertEqual(process_inspection._sample_processes([], 10), {})

    def test_inaccessible_second_sample_is_reported(self):
        reader = Mock(
            side_effect=[
                metrics(cpu_time=1.0),
                ProcessMetrics(
                    accessible=False,
                    status="access_denied",
                ),
            ]
        )

        with patch.object(
            process_inspection,
            "_read_process_metrics",
            reader,
        ), patch.object(process_inspection.time, "sleep"):
            results = process_inspection._sample_processes([1], 0)

        self.assertFalse(results[1].accessible)
        self.assertEqual(results[1].status, "access_denied")


# ---------------------------------------------------------------------------
# Application status
# ---------------------------------------------------------------------------


class ApplicationStatusTests(ProcessInspectionTestBase):

    def setUp(self):
        super().setUp()
        self.tool = SystemApplicationStatusTool()
        self.application = approved_application(
            alias="chrome",
            display="Google Chrome",
            executable=r"C:\Program Files\Google\Chrome\chrome.exe",
        )
        self.patch_object(
            application_launcher,
            "_resolve_application",
            return_value=self.application,
        )

    async def test_approved_application_running(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(10, "chrome.exe"),
                entry(11, "chrome.exe"),
                entry(20, "notepad.exe"),
            ],
        )

        result = await self.run_tool(
            self.tool,
            {"application": "chrome"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["application"], "Google Chrome")
        self.assertEqual(result.data["alias"], "chrome")
        self.assertTrue(result.data["running"])
        self.assertEqual(result.data["pids"], [10, 11])
        self.assertEqual(result.data["match_count"], 2)
        self.assertFalse(result.data["truncated"])

    async def test_approved_application_not_running(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(20, "notepad.exe")],
        )

        result = await self.run_tool(
            self.tool,
            {"application": "chrome"},
        )

        self.assertFalse(result.data["running"])
        self.assertEqual(result.data["pids"], [])
        self.assertEqual(result.data["match_count"], 0)

    async def test_unknown_alias_is_rejected(self):
        self.patch_object(
            application_launcher,
            "_resolve_application",
            side_effect=ToolValidationError("not approved"),
        )

        with self.assertRaises(ToolValidationError):
            self.tool.validate_arguments({"application": "unknown"})

    async def test_multiple_matches_are_bounded(self):
        self.configure(TOOL_PROCESS_MAX_MATCHES=2)
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(pid, "chrome.exe") for pid in range(10, 15)
            ],
        )

        result = await self.run_tool(
            self.tool,
            {"application": "chrome"},
        )

        self.assertEqual(result.data["pids"], [10, 11])
        self.assertEqual(result.data["match_count"], 5)
        self.assertTrue(result.data["truncated"])

    async def test_application_status_never_launches_anything(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(10, "chrome.exe")],
        )
        launch = Mock()
        self.patch_object(
            application_launcher,
            "_launch_process",
            launch,
        )

        await self.run_tool(self.tool, {"application": "chrome"})

        launch.assert_not_called()


class ApplicationStatusResolutionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real Phase 12B allowlist resolver."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="asta_phase12d_"
        )
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.executable = self.root / "notepad.exe"
        self.executable.write_bytes(b"MZ")

        for name, value in (
            ("TOOL_OS_AUTOMATION_ENABLED", True),
            ("TOOL_PROCESS_INSPECTION_ENABLED", True),
            (
                "TOOL_APPLICATION_ALLOWLIST",
                f"notepad={self.executable}",
            ),
        ):
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        for target, name, value in (
            (process_inspection, "_is_supported_platform", True),
            (application_launcher, "_is_supported_platform", True),
            (application_launcher, "_is_fixed_local_drive", True),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.patch_enumerate([entry(42, "notepad.exe")])

    def patch_enumerate(self, value):
        patcher = patch.object(
            process_inspection,
            "_enumerate_processes",
            return_value=value,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_alias_normalization_and_resolution(self):
        tool = SystemApplicationStatusTool()
        self.patch_enumerate([entry(42, "notepad.exe")])

        for value in ("notepad", "NotePad", "NOTE PAD", "note-pad"):
            with self.subTest(value=value):
                validated = tool.validate_arguments(
                    {"application": value}
                )
                result = await tool.execute(None, validated)

                self.assertTrue(result.data["running"])
                self.assertEqual(result.data["alias"], "notepad")
                self.assertEqual(result.data["pids"], [42])

    def test_arbitrary_executable_path_is_rejected(self):
        tool = SystemApplicationStatusTool()

        for value in (
            str(self.executable),
            r"C:\Windows\System32\cmd.exe",
            r"\\server\share\notepad.exe",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolValidationError):
                    tool.validate_arguments({"application": value})


# ---------------------------------------------------------------------------
# Active window
# ---------------------------------------------------------------------------


class ActiveWindowTests(ProcessInspectionTestBase):

    def setUp(self):
        super().setUp()
        self.tool = SystemActiveWindowTool()

    async def test_valid_foreground_window(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(
                title="Report - Notepad",
                pid=123,
            ),
        )
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(123, "notepad.exe")],
        )

        result = await self.run_tool(self.tool)

        self.assertTrue(result.success)
        self.assertTrue(result.data["available"])
        self.assertEqual(result.data["title"], "Report - Notepad")
        self.assertTrue(result.data["title_present"])
        self.assertFalse(result.data["title_truncated"])
        self.assertEqual(result.data["pid"], 123)
        self.assertEqual(result.data["process_name"], "notepad.exe")
        self.assertTrue(result.data["untrusted"])

    async def test_empty_window_title(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(title="", pid=123),
        )
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(123, "notepad.exe")],
        )

        result = await self.run_tool(self.tool)

        self.assertIsNone(result.data["title"])
        self.assertFalse(result.data["title_present"])

    async def test_no_active_window(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=None,
        )

        result = await self.run_tool(self.tool)

        self.assertTrue(result.success)
        self.assertFalse(result.data["available"])
        self.assertEqual(result.data["reason"], "no_active_window")
        self.assertIsNone(result.data["title"])
        self.assertIsNone(result.data["pid"])
        self.assertIsNone(result.data["process_name"])

    async def test_process_identifier_unavailable(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(title="Untitled", pid=None),
        )
        enumerate_processes = Mock()
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            enumerate_processes,
        )

        result = await self.run_tool(self.tool)

        self.assertIsNone(result.data["pid"])
        self.assertIsNone(result.data["process_name"])
        enumerate_processes.assert_not_called()

    async def test_protected_owner_process_is_not_named(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(title="Secure", pid=4),
        )
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(4, "System")],
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(result.data["pid"], 4)
        self.assertIsNone(result.data["process_name"])

    async def test_window_title_is_bounded(self):
        self.configure(TOOL_PROCESS_MAX_TITLE_CHARS=32)
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(
                title="a" * 200,
                pid=None,
                title_truncated=True,
            ),
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(len(result.data["title"]), 32)
        self.assertTrue(result.data["title_truncated"])

    async def test_window_title_is_single_line(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(
                title="Line one\nLine two\tTabbed",
                pid=None,
            ),
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(
            result.data["title"],
            "Line one Line two Tabbed",
        )

    async def test_snapshot_failure_still_returns_the_title(self):
        self.patch_object(
            process_inspection,
            "_read_active_window",
            return_value=ActiveWindowInfo(title="Visible", pid=123),
        )
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            side_effect=process_inspection.ToolExecutionError(
                "Process information is unavailable."
            ),
        )

        result = await self.run_tool(self.tool)

        self.assertTrue(result.success)
        self.assertEqual(result.data["title"], "Visible")
        self.assertEqual(result.data["pid"], 123)
        self.assertIsNone(result.data["process_name"])


# ---------------------------------------------------------------------------
# Resource summary
# ---------------------------------------------------------------------------


class ResourceSummaryTests(ProcessInspectionTestBase):

    def setUp(self):
        super().setUp()
        self.tool = SystemResourceSummaryTool()

    async def test_processes_are_sorted_by_cpu_then_memory(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(1, "low.exe"),
                entry(2, "high.exe"),
                entry(3, "mid.exe"),
                entry(5, "tie.exe"),
            ],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                1: metrics(cpu_percent=5.0, memory=100),
                2: metrics(cpu_percent=50.0, memory=100),
                3: metrics(cpu_percent=10.0, memory=100),
                5: metrics(cpu_percent=50.0, memory=200),
            },
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(
            [item["pid"] for item in result.data["processes"]],
            [5, 2, 3, 1],
        )

    async def test_missing_metrics_are_marked_and_sorted_last(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(1, "hidden.exe"),
                entry(2, "visible.exe"),
            ],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                1: ProcessMetrics(
                    accessible=False,
                    status="access_denied",
                ),
                2: metrics(cpu_percent=1.0, memory=10),
            },
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(
            [item["pid"] for item in result.data["processes"]],
            [2, 1],
        )
        hidden = result.data["processes"][1]
        self.assertFalse(hidden["cpu"]["available"])
        self.assertEqual(hidden["cpu"]["reason"], "access_denied")
        self.assertFalse(hidden["memory"]["available"])

    async def test_result_count_is_bounded(self):
        self.configure(TOOL_PROCESS_MAX_RESULTS=2)
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(index, f"app{index}.exe") for index in range(10, 15)],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                index: metrics(cpu_percent=float(index))
                for index in range(10, 15)
            },
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(result.data["count"], 2)
        self.assertEqual(result.data["total_considered"], 5)
        self.assertTrue(result.data["truncated"])

    async def test_explicit_limit_is_applied(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[entry(index, f"app{index}.exe") for index in range(1, 5)],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                index: metrics(cpu_percent=float(index))
                for index in range(1, 5)
            },
        )

        result = await self.run_tool(self.tool, {"limit": 1})

        self.assertEqual(result.data["count"], 1)

    async def test_empty_process_list(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[],
        )
        sampler = Mock(return_value={})
        self.patch_object(
            process_inspection,
            "_sample_processes",
            sampler,
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(result.data["count"], 0)
        self.assertEqual(result.data["total_considered"], 0)
        sampler.assert_called_once_with([], 0)

    async def test_protected_processes_are_not_sampled(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(4, "System"),
                entry(500, "csrss.exe"),
                entry(700, "explorer.exe"),
            ],
        )
        sampler = Mock(
            return_value={700: metrics(cpu_percent=2.0)}
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            sampler,
        )

        result = await self.run_tool(self.tool)

        sampler.assert_called_once_with([700], 0)
        self.assertEqual(
            [item["pid"] for item in result.data["processes"]],
            [700],
        )

    async def test_memory_breaks_cpu_ties_when_cpu_is_unavailable(self):
        self.patch_object(
            process_inspection,
            "_enumerate_processes",
            return_value=[
                entry(1, "small.exe"),
                entry(2, "large.exe"),
            ],
        )
        self.patch_object(
            process_inspection,
            "_sample_processes",
            return_value={
                1: metrics(cpu_time=1.0, cpu_percent=None, memory=10),
                2: metrics(cpu_time=1.0, cpu_percent=None, memory=20),
            },
        )

        result = await self.run_tool(self.tool)

        self.assertEqual(
            [item["pid"] for item in result.data["processes"]],
            [2, 1],
        )


# ---------------------------------------------------------------------------
# Permission policy and registration
# ---------------------------------------------------------------------------


class PermissionPolicyTests(ProcessInspectionTestBase):

    def test_read_only_tool_is_allowed_without_confirmation(self):
        decision = permission_policy.evaluate(
            SystemProcessListTool(),
            private=False,
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_confirmation)
        self.assertEqual(decision.category, ToolCategory.READ_ONLY)

    def test_private_mode_is_denied(self):
        decision = permission_policy.evaluate(
            SystemProcessListTool(),
            private=True,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.PERMISSION,
        )

    def test_private_mode_raises_private_request_error(self):
        with self.assertRaises(ToolPrivateRequestError):
            permission_policy.require_allowed(
                SystemActiveWindowTool(),
                private=True,
            )

    def test_disabled_tool_is_denied(self):
        self.configure(TOOL_PROCESS_INSPECTION_ENABLED=False)

        decision = permission_policy.evaluate(
            SystemResourceSummaryTool(),
            private=False,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.DISABLED,
        )

    def test_llm_definitions_include_enabled_tools(self):
        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertTrue(TOOL_NAMES.issubset(names))

    def test_llm_definitions_exclude_disabled_tools(self):
        self.configure(TOOL_PROCESS_INSPECTION_ENABLED=False)

        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertFalse(TOOL_NAMES & names)

    def test_llm_definitions_exclude_private_mode(self):
        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=True)
        }

        self.assertFalse(TOOL_NAMES & names)


class RegistrationTests(unittest.TestCase):

    def test_tools_are_registered(self):
        self.assertTrue(TOOL_NAMES.issubset(set(tool_registry.names())))

    def test_builtin_tool_list_contains_each_tool_once(self):
        names = [tool.name for tool in BUILTIN_TOOLS]

        for name in TOOL_NAMES:
            with self.subTest(name=name):
                self.assertEqual(names.count(name), 1)

    def test_existing_phase_tools_are_still_registered(self):
        names = set(tool_registry.names())

        self.assertTrue(
            {
                "task_list",
                "web_search",
                "fs_read",
                "fs_exists",
                "python_execute",
                "http_request",
                "system_os_info",
                "system_summary",
                "system_launch_application",
            }.issubset(names)
        )

    def test_provider_schema_is_function_calling_compatible(self):
        for name in TOOL_NAMES:
            with self.subTest(name=name):
                schema = tool_registry.get(name).definition().provider_schema()

                self.assertEqual(schema["type"], "function")
                self.assertEqual(schema["function"]["name"], name)
                self.assertEqual(
                    schema["function"]["parameters"]["type"],
                    "object",
                )


# ---------------------------------------------------------------------------
# Source-level safety checks
# ---------------------------------------------------------------------------


class SourceSafetyTests(unittest.TestCase):

    def setUp(self):
        self.source = Path(process_inspection.__file__).read_text(
            encoding="utf-8"
        )

    def test_no_subprocess_or_shell_invocation(self):
        for token in (
            "subprocess",
            "Popen",
            "os.system",
            "os.popen",
            "powershell",
            "cmd.exe",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_process_modification_apis(self):
        for token in (
            "TerminateProcess",
            "PROCESS_TERMINATE",
            "PROCESS_VM_WRITE",
            "PROCESS_VM_OPERATION",
            "CreateRemoteThread",
            "DebugActiveProcess",
            "SetPriorityClass",
            "SuspendThread",
            "ResumeThread",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_window_or_input_manipulation_apis(self):
        for token in (
            "GetAsyncKeyState",
            "BitBlt",
            "PrintWindow",
            "GetDC",
            "SendInput",
            "keybd_event",
            "mouse_event",
            "PostMessage",
            "EndTask",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_administrator_privilege_escalation(self):
        for token in (
            "AdjustTokenPrivileges",
            "SeDebugPrivilege",
            "runas",
            "ShellExecute",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_uses_least_privilege_query_access(self):
        self.assertIn(
            "PROCESS_QUERY_LIMITED_INFORMATION",
            self.source,
        )
        self.assertNotIn("PROCESS_ALL_ACCESS", self.source)


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------


class ExecutorIntegrationTests(ToolEngineTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.settings(
            TOOL_OS_AUTOMATION_ENABLED=True,
            TOOL_PROCESS_INSPECTION_ENABLED=True,
        )

        for target, name, value in (
            (process_inspection, "_is_supported_platform", True),
            (process_inspection, "_cpu_sample_ms", 0),
            (
                process_inspection,
                "_enumerate_processes",
                [entry(10, "chrome.exe")],
            ),
            (
                application_launcher,
                "_approved_applications",
                {"notepad": approved_application()},
            ),
            (
                application_launcher,
                "_resolve_application",
                approved_application(),
            ),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = patch.object(
            process_inspection,
            "_sample_processes",
            return_value={10: metrics(cpu_percent=1.5)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_exec(
        self,
        tool_name="system_process_list",
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

    async def test_executor_runs_process_list_without_confirmation(self):
        result = await self.run_exec()

        self.assertTrue(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertFalse(result.confirmation_required)
        self.assertIsNone(result.confirmation_id)
        self.assertEqual(result.provider, "process_inspection")
        self.assertEqual(result.data["count"], 1)

    async def test_executor_runs_resource_summary(self):
        result = await self.run_exec(
            "system_resource_summary",
            {"limit": 1},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["count"], 1)
        self.assertEqual(result.data["processes"][0]["pid"], 10)

    async def test_private_request_is_denied(self):
        result = await self.run_exec(private=True)

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )

    async def test_disabled_tool_is_denied(self):
        self.settings(TOOL_PROCESS_INSPECTION_ENABLED=False)

        result = await self.run_exec()

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )

    async def test_invalid_arguments_are_rejected_by_the_executor(self):
        result = await self.run_exec(
            "system_process_info",
            {"pid": -1},
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )


if __name__ == "__main__":
    unittest.main()
