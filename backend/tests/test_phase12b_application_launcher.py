"""Phase 12B tests for secure allowlisted application launching.

Every launch is mocked. No real application is started. Path validation runs
against empty ``.exe`` files in a temporary directory, and the fixed-drive
check is patched so the suite is deterministic on any Windows machine.

The suite covers allowlist parsing and normalization, executable-path
validation, confirmation binding, executor integration, permission policy,
private-mode denial, unsupported platforms, and secret/bounds protections.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.config import settings
from app.services.tool_confirmation_service import (
    tool_confirmation_service,
)
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolConfirmationStatus,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolExecutionError,
    ToolValidationError,
)
from app.tools.executor import tool_executor
from app.tools.implementations import application_launcher
from app.tools.implementations.application_launcher import (
    MAX_ALIAS_CHARS,
    MAX_ALLOWLIST_ENTRIES,
    LaunchApplicationTool,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from tests.tool_test_support import ToolEngineTestBase


WINDOWS_ONLY = unittest.skipUnless(
    os.name == "nt",
    "Application launching is implemented for Windows only.",
)

TOOL_NAME = "system_launch_application"

_MISSING = object()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class LauncherUnitTestBase(unittest.IsolatedAsyncioTestCase):
    """A temporary approved executable and patched configuration."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="asta_launch_"
        )
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.executable = self.root / "approved.exe"
        self.executable.write_bytes(b"MZ")
        self.configure()
        self.patch_object(
            application_launcher,
            "_is_fixed_local_drive",
            return_value=True,
        )

    def configure(self, **overrides):
        values = {
            "TOOL_OS_AUTOMATION_ENABLED": True,
            "TOOL_APPLICATION_LAUNCH_ENABLED": True,
            "TOOL_APPLICATION_ALLOWLIST": f"notepad={self.executable}",
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

    async def run_tool(self, arguments=None):
        tool = LaunchApplicationTool()
        validated = tool.validate_arguments(arguments or {})

        return await tool.execute(None, validated)


# ---------------------------------------------------------------------------
# Alias normalization
# ---------------------------------------------------------------------------


class AliasNormalizationTests(unittest.TestCase):

    def test_case_space_and_punctuation_are_ignored(self):
        for value in ("vscode", "VS Code", "vs-code", "VS_Code"):
            with self.subTest(value=value):
                self.assertEqual(
                    application_launcher._normalize_alias(value),
                    "vscode",
                )

        for value in (
            "  Visual   Studio Code  ",
            "visual.studio.code",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    application_launcher._normalize_alias(value),
                    "visualstudiocode",
                )

    def test_empty_and_blank_aliases_are_rejected(self):
        for value in (None, "", "   ", "\t"):
            with self.subTest(value=value):
                self.assertEqual(
                    application_launcher._normalize_alias(value),
                    "",
                )

    def test_path_like_aliases_are_rejected(self):
        for value in (
            r"C:\Windows\System32\notepad.exe",
            "../notepad",
            "..\\notepad",
            "folder/notepad",
            "folder\\notepad",
            "notepad=other",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    application_launcher._normalize_alias(value),
                    "",
                )

    def test_control_characters_are_rejected(self):
        self.assertEqual(
            application_launcher._normalize_alias("note\x00pad"),
            "",
        )
        self.assertEqual(
            application_launcher._normalize_alias("note\x1fpad"),
            "",
        )

    def test_length_is_bounded(self):
        self.assertEqual(
            application_launcher._normalize_alias("a" * MAX_ALIAS_CHARS),
            "a" * MAX_ALIAS_CHARS,
        )
        self.assertEqual(
            application_launcher._normalize_alias(
                "a" * (MAX_ALIAS_CHARS + 1)
            ),
            "",
        )

    def test_validated_alias_raises_for_invalid_input(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validated_alias("../notepad")


# ---------------------------------------------------------------------------
# Allowlist parsing and enablement
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class ConfigurationTests(LauncherUnitTestBase):

    def test_disabled_by_default(self):
        self.configure(TOOL_APPLICATION_LAUNCH_ENABLED=False)

        self.assertFalse(LaunchApplicationTool().enabled)

    def test_enabled_with_flag_and_valid_allowlist(self):
        self.assertTrue(LaunchApplicationTool().enabled)

    def test_empty_allowlist_disables_the_tool(self):
        self.configure(TOOL_APPLICATION_ALLOWLIST="")

        self.assertFalse(LaunchApplicationTool().enabled)

    def test_only_valid_entries_are_approved(self):
        missing = self.root / "missing.exe"
        directory = self.root / "directory.exe"
        directory.mkdir()
        script = self.root / "script.bat"
        script.write_bytes(b"echo")

        self.configure(
            TOOL_APPLICATION_ALLOWLIST=(
                f"notepad={self.executable},"
                f"missing={missing},"
                f"directory={directory},"
                f"script={script}"
            )
        )

        approved = application_launcher._approved_applications()

        self.assertEqual(set(approved), {"notepad"})
        self.assertEqual(
            approved["notepad"].executable,
            str(self.executable.resolve()),
        )

    def test_duplicate_normalized_alias_keeps_the_first_entry(self):
        other = self.root / "other.exe"
        other.write_bytes(b"MZ")
        self.configure(
            TOOL_APPLICATION_ALLOWLIST=(
                f"VS Code={self.executable},"
                f"vscode={other}"
            )
        )

        approved = application_launcher._approved_applications()

        self.assertEqual(
            approved["vscode"].executable,
            str(self.executable.resolve()),
        )

    def test_allowlist_is_capped(self):
        entries = []

        for index in range(MAX_ALLOWLIST_ENTRIES + 5):
            path = self.root / f"app{index}.exe"
            path.write_bytes(b"MZ")
            entries.append(f"app{index}={path}")

        self.configure(TOOL_APPLICATION_ALLOWLIST=",".join(entries))

        configured = application_launcher._configured_applications()

        self.assertEqual(len(configured), MAX_ALLOWLIST_ENTRIES)

    def test_config_property_parses_commas_newlines_and_duplicates(self):
        self.configure(
            TOOL_APPLICATION_ALLOWLIST=(
                " vscode = C:\\Code.exe , chrome=C:\\Chrome.exe\n"
                "vscode=C:\\Other.exe "
            )
        )

        self.assertEqual(
            settings.tool_application_allowlist,
            {
                "vscode": "C:\\Code.exe",
                "chrome": "C:\\Chrome.exe",
            },
        )

    def test_display_name_is_preserved(self):
        self.configure(
            TOOL_APPLICATION_ALLOWLIST=(
                f"Visual Studio Code={self.executable}"
            )
        )

        approved = application_launcher._approved_applications()

        self.assertEqual(
            approved["visualstudiocode"].display_name,
            "Visual Studio Code",
        )


# ---------------------------------------------------------------------------
# Executable path validation
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class PathValidationTests(LauncherUnitTestBase):

    def test_missing_executable_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(
                str(self.root / "missing.exe")
            )

    def test_directory_is_rejected(self):
        directory = self.root / "directory.exe"
        directory.mkdir()

        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(str(directory))

    def test_relative_path_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable("approved.exe")

    def test_unc_path_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(
                r"\\server\share\approved.exe"
            )

    def test_forward_slash_unc_path_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(
                "//server/share/approved.exe"
            )

    def test_traversal_is_rejected(self):
        traversal = str(
            self.root / ".." / "Windows" / "System32" / "notepad.exe"
        )

        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(traversal)

    def test_non_exe_is_rejected(self):
        script = self.root / "script.bat"
        script.write_bytes(b"echo")

        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(str(script))

    def test_nul_byte_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(
                str(self.executable) + "\x00.exe"
            )

    def test_overlong_path_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(
                "C:\\" + ("a" * 300) + ".exe"
            )

    def test_symlink_is_rejected(self):
        self.patch_object(
            application_launcher.os.path,
            "islink",
            return_value=True,
        )

        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(str(self.executable))

    def test_reparse_point_is_rejected(self):
        self.patch_object(
            application_launcher,
            "_has_reparse_point",
            return_value=True,
        )

        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(str(self.executable))

    def test_non_fixed_drive_is_rejected(self):
        self.patch_object(
            application_launcher,
            "_is_fixed_local_drive",
            return_value=False,
        )

        with self.assertRaises(ToolValidationError):
            application_launcher._validate_executable(str(self.executable))

    def test_valid_executable_resolves_to_an_absolute_path(self):
        resolved = application_launcher._validate_executable(
            str(self.executable)
        )

        self.assertTrue(os.path.isabs(resolved))
        self.assertEqual(resolved, str(self.executable.resolve()))


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class ResolutionTests(LauncherUnitTestBase):

    def test_approved_application_is_resolved(self):
        application = application_launcher._resolve_application("notepad")

        self.assertEqual(application.alias, "notepad")
        self.assertEqual(application.display_name, "notepad")
        self.assertEqual(
            application.executable,
            str(self.executable.resolve()),
        )

    def test_alias_normalization_resolves(self):
        for value in ("NotePad", "NOTE PAD", "note-pad", " note_pad "):
            with self.subTest(value=value):
                application = application_launcher._resolve_application(
                    value
                )
                self.assertEqual(application.alias, "notepad")

    def test_unknown_application_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._resolve_application("chrome")

    def test_path_as_application_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._resolve_application(
                str(self.executable)
            )

    def test_traversal_alias_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            application_launcher._resolve_application("../notepad")

    def test_unapproved_alias_does_not_create_a_confirmation(self):
        tool = LaunchApplicationTool()

        with self.assertRaises(ToolValidationError):
            tool.validate_arguments({"application": "chrome"})


# ---------------------------------------------------------------------------
# Metadata, schema, and discovery
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class MetadataTests(LauncherUnitTestBase):

    def test_tool_is_sensitive_and_requires_confirmation(self):
        tool = LaunchApplicationTool()

        self.assertEqual(tool.category, ToolCategory.SENSITIVE)
        self.assertTrue(tool.requires_confirmation)
        self.assertFalse(tool.allowed_in_private)
        self.assertFalse(tool.idempotent)
        self.assertEqual(tool.provider_label, "application_launcher")

    def test_input_schema_accepts_only_an_application_alias(self):
        schema = LaunchApplicationTool().definition().parameters

        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"application"})
        self.assertNotIn("path", schema["properties"])
        self.assertNotIn("arguments", schema["properties"])

    def test_provider_schema_is_function_calling_compatible(self):
        schema = tool_registry.get(TOOL_NAME).definition().provider_schema()

        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], TOOL_NAME)

    def test_unknown_arguments_are_rejected(self):
        tool = LaunchApplicationTool()

        for payload in (
            {"application": "notepad", "arguments": ["/x"]},
            {"application": "notepad", "path": "C:\\evil.exe"},
            {"application": "notepad", "command": "calc.exe"},
            {"path": "C:\\evil.exe"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ToolValidationError):
                    tool.validate_arguments(payload)

    def test_application_must_be_a_string(self):
        with self.assertRaises(ToolValidationError):
            LaunchApplicationTool().validate_arguments(
                {"application": 123}
            )

    def test_application_length_is_bounded(self):
        with self.assertRaises(ToolValidationError):
            LaunchApplicationTool().validate_arguments(
                {"application": "a" * (MAX_ALIAS_CHARS + 1)}
            )

    def test_summary_hides_the_executable_path(self):
        tool = LaunchApplicationTool()
        validated = tool.validate_arguments({"application": "notepad"})
        preview = tool.summarize(validated)

        self.assertIn("notepad", preview)
        self.assertNotIn(str(self.executable), preview)

    def test_llm_definitions_include_enabled_tool(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)

        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertIn(TOOL_NAME, names)

    def test_llm_definitions_exclude_disabled_tool(self):
        self.configure(TOOL_APPLICATION_LAUNCH_ENABLED=False)

        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=False)
        }

        self.assertNotIn(TOOL_NAME, names)

    def test_llm_definitions_exclude_private_mode(self):
        names = {
            item["function"]["name"]
            for item in tool_registry.llm_definitions(private=True)
        }

        self.assertNotIn(TOOL_NAME, names)


# ---------------------------------------------------------------------------
# Process launch and result bounds
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class LaunchExecutionTests(LauncherUnitTestBase):

    async def test_successful_launch_is_mocked_and_bounded(self):
        launch = Mock(return_value=SimpleNamespace(pid=4321))
        self.patch_object(
            application_launcher,
            "_launch_process",
            launch,
        )

        result = await self.run_tool({"application": "notepad"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["launched"], True)
        self.assertEqual(result.data["alias"], "notepad")
        self.assertEqual(result.data["pid"], 4321)
        launch.assert_called_once_with(str(self.executable.resolve()))
        self.assertNotIn(
            str(self.executable),
            json.dumps(result.data, default=str),
        )

    async def test_process_launch_failure_is_safe(self):
        self.patch_object(
            application_launcher,
            "_launch_process",
            side_effect=OSError("password=supersecret"),
        )

        with self.assertRaises(ToolExecutionError) as raised:
            await self.run_tool({"application": "notepad"})

        message = str(raised.exception)

        self.assertEqual(
            message,
            "The application could not be launched.",
        )
        self.assertNotIn("supersecret", message)
        self.assertNotIn("Traceback", message)

    async def test_unsupported_platform_fails_closed(self):
        self.patch_object(
            application_launcher,
            "_is_supported_platform",
            return_value=False,
        )
        launch = Mock()
        self.patch_object(
            application_launcher,
            "_launch_process",
            launch,
        )

        with self.assertRaises(ToolConfigurationError):
            await self.run_tool({"application": "notepad"})

        launch.assert_not_called()

    def test_launch_uses_no_shell_and_no_arguments(self):
        popen = Mock(return_value=SimpleNamespace(pid=99))
        self.patch_object(
            application_launcher,
            "_is_supported_platform",
            return_value=True,
        )
        self.patch_object(
            application_launcher.subprocess,
            "Popen",
            popen,
        )

        process = application_launcher._launch_process(
            str(self.executable.resolve())
        )

        self.assertEqual(process.pid, 99)
        popen.assert_called_once()
        args, kwargs = popen.call_args

        self.assertEqual(
            args[0],
            [str(self.executable.resolve())],
        )
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["close_fds"], True)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(
            kwargs["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_unsupported_platform_launcher_raises(self):
        self.patch_object(
            application_launcher,
            "_is_supported_platform",
            return_value=False,
        )

        with self.assertRaises(ToolConfigurationError):
            application_launcher._launch_process(
                str(self.executable.resolve())
            )

    async def test_missing_pid_is_reported_as_null(self):
        self.patch_object(
            application_launcher,
            "_launch_process",
            Mock(return_value=SimpleNamespace()),
        )

        result = await self.run_tool({"application": "notepad"})

        self.assertTrue(result.success)
        self.assertIsNone(result.data["pid"])

    async def test_result_has_no_environment_or_credential_fields(self):
        self.patch_object(
            application_launcher,
            "_launch_process",
            Mock(return_value=SimpleNamespace(pid=4321)),
        )
        secret = "sk-phase12b-super-secret-value"

        with patch.dict(
            os.environ,
            {"ASTA_PHASE12B_TEST_SECRET": secret},
        ):
            result = await self.run_tool({"application": "notepad"})

        rendered = json.dumps(result.data, default=str)

        self.assertNotIn(secret, rendered)
        self.assertNotIn("ASTA_PHASE12B_TEST_SECRET", rendered)

        for marker in (
            "password",
            "passwd",
            "api_key",
            "access_token",
            "refresh_token",
            "private_key",
        ):
            self.assertNotIn(marker, rendered.lower())


# ---------------------------------------------------------------------------
# Permission policy
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class PermissionPolicyTests(LauncherUnitTestBase):

    def test_sensitive_tool_is_allowed_only_when_deployment_permits_it(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)

        decision = permission_policy.evaluate(
            LaunchApplicationTool(),
            private=False,
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_confirmation)
        self.assertEqual(decision.category, ToolCategory.SENSITIVE)

    def test_sensitive_tool_is_denied_when_not_permitted(self):
        self.configure(TOOL_ALLOW_SENSITIVE=False)

        decision = permission_policy.evaluate(
            LaunchApplicationTool(),
            private=False,
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_confirmation)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.PERMISSION,
        )

    def test_private_mode_is_denied(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)

        decision = permission_policy.evaluate(
            LaunchApplicationTool(),
            private=True,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.PERMISSION,
        )

    def test_disabled_tool_is_denied(self):
        self.configure(TOOL_APPLICATION_LAUNCH_ENABLED=False)

        decision = permission_policy.evaluate(
            LaunchApplicationTool(),
            private=False,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.error_category,
            ToolErrorCategory.DISABLED,
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class RegistrationTests(unittest.TestCase):

    def test_tool_is_registered(self):
        self.assertIn(TOOL_NAME, tool_registry.names())

    def test_builtin_tool_list_contains_the_launcher_once(self):
        names = [tool.name for tool in BUILTIN_TOOLS]

        self.assertEqual(names.count(TOOL_NAME), 1)

    def test_existing_phase_tools_are_still_registered(self):
        names = set(tool_registry.names())

        self.assertTrue(
            {
                "task_list",
                "web_search",
                "fs_read",
                "python_execute",
                "http_request",
                "system_os_info",
                "system_summary",
            }.issubset(names)
        )


# ---------------------------------------------------------------------------
# Executor integration and confirmation workflow
# ---------------------------------------------------------------------------


@WINDOWS_ONLY
class ExecutorIntegrationTests(ToolEngineTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.executable = Path(self.workspace.name) / "approved.exe"
        self.executable.write_bytes(b"MZ")
        self.other_executable = (
            Path(self.workspace.name) / "other.exe"
        )
        self.other_executable.write_bytes(b"MZ")

        self.settings(
            TOOL_OS_AUTOMATION_ENABLED=True,
            TOOL_APPLICATION_LAUNCH_ENABLED=True,
            TOOL_APPLICATION_ALLOWLIST=(
                f"notepad={self.executable},"
                f"other={self.other_executable}"
            ),
            TOOL_ALLOW_SENSITIVE=True,
        )

        patcher = patch.object(
            application_launcher,
            "_is_fixed_local_drive",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.launch = Mock(return_value=SimpleNamespace(pid=4321))
        patcher = patch.object(
            application_launcher,
            "_launch_process",
            self.launch,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_exec(
        self,
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
                tool_name=TOOL_NAME,
                arguments=arguments or {"application": "notepad"},
                context=context,
                confirmation_id=confirmation_id,
                create_confirmation=create_confirmation,
            )

    async def stored_confirmation(self, confirmation_id):
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.get(
                session=session,
                user_id=self.alice_id,
                confirmation_id=confirmation_id,
            )

    async def test_launch_requires_confirmation_and_does_not_run(self):
        result = await self.run_exec()

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertIsNotNone(result.confirmation_id)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.PENDING,
        )
        self.launch.assert_not_called()

    async def test_approved_confirmation_launches_once(self):
        pending = await self.run_exec()

        approved = await self.run_exec(
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(approved.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(
            approved.confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertEqual(approved.data["pid"], 4321)
        self.launch.assert_called_once_with(
            str(self.executable.resolve())
        )

    async def test_replayed_confirmation_does_not_launch_again(self):
        pending = await self.run_exec()
        first = await self.run_exec(
            confirmation_id=pending.confirmation_id,
        )
        replay = await self.run_exec(
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(first.success)
        self.assertFalse(replay.success)
        self.assertEqual(replay.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            replay.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(self.launch.call_count, 1)

    async def test_rejected_confirmation_does_not_launch(self):
        pending = await self.run_exec()

        async with self.SessionLocal() as session:
            await tool_confirmation_service.reject(
                session=session,
                user_id=self.alice_id,
                confirmation_id=pending.confirmation_id,
            )

        result = await self.run_exec(
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.launch.assert_not_called()

    async def test_expired_confirmation_does_not_launch(self):
        async with self.SessionLocal() as session:
            pending = await tool_confirmation_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name=TOOL_NAME,
                category=ToolCategory.SENSITIVE,
                arguments={"application": "notepad"},
                arguments_preview="application=notepad",
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
                confirmation_id=pending.id,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.EXPIRED,
        )
        self.launch.assert_not_called()

    async def test_altered_arguments_do_not_launch(self):
        pending = await self.run_exec()

        altered = await self.run_exec(
            {"application": "other"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(altered.success)
        self.assertEqual(altered.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            altered.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.launch.assert_not_called()

        approved = await self.run_exec(
            {"application": "notepad"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(self.launch.call_count, 1)

    async def test_unknown_application_is_rejected_before_confirmation(self):
        result = await self.run_exec({"application": "chrome"})

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.assertIsNone(result.confirmation_id)
        self.launch.assert_not_called()

    async def test_arbitrary_path_argument_is_rejected(self):
        result = await self.run_exec(
            {"application": str(self.executable)}
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.launch.assert_not_called()

    async def test_arbitrary_extra_arguments_are_rejected(self):
        result = await self.run_exec(
            {
                "application": "notepad",
                "arguments": ["/e", "C:\\secret.txt"],
            }
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.launch.assert_not_called()

    async def test_private_request_is_denied(self):
        result = await self.run_exec(private=True)

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )
        self.launch.assert_not_called()

    async def test_disabled_tool_is_denied(self):
        self.settings(TOOL_APPLICATION_LAUNCH_ENABLED=False)

        result = await self.run_exec()

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )
        self.launch.assert_not_called()

    async def test_sensitive_policy_denial_does_not_create_confirmation(self):
        self.settings(TOOL_ALLOW_SENSITIVE=False)

        result = await self.run_exec()

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )
        self.assertIsNone(result.confirmation_id)
        self.launch.assert_not_called()

    async def test_launch_failure_marks_confirmation_failed(self):
        pending = await self.run_exec()
        self.launch.side_effect = OSError("launch failed")

        result = await self.run_exec(
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.EXECUTION,
        )
        self.assertEqual(self.launch.call_count, 1)

        stored = await self.stored_confirmation(pending.confirmation_id)
        self.assertEqual(stored.status, ToolConfirmationStatus.FAILED)

    async def test_execution_history_records_the_launcher_provider(self):
        pending = await self.run_exec()
        result = await self.run_exec(
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "application_launcher")
        self.assertIsNotNone(result.execution_id)


# ---------------------------------------------------------------------------
# Unsupported operating systems
# ---------------------------------------------------------------------------


class UnsupportedPlatformTests(LauncherUnitTestBase):

    def test_tool_is_disabled_on_unsupported_platform(self):
        self.patch_object(
            application_launcher,
            "_is_supported_platform",
            return_value=False,
        )

        self.assertFalse(LaunchApplicationTool().enabled)

    async def test_execution_fails_closed_on_unsupported_platform(self):
        self.patch_object(
            application_launcher,
            "_is_supported_platform",
            return_value=False,
        )
        launch = Mock()
        self.patch_object(
            application_launcher,
            "_launch_process",
            launch,
        )
        validated = application_launcher.LaunchApplicationInput(
            application="notepad"
        )

        with self.assertRaises(ToolConfigurationError):
            await LaunchApplicationTool().execute(None, validated)

        launch.assert_not_called()


__all__ = [
    "AliasNormalizationTests",
    "ConfigurationTests",
    "ExecutorIntegrationTests",
    "LaunchExecutionTests",
    "LauncherUnitTestBase",
    "MetadataTests",
    "PathValidationTests",
    "PermissionPolicyTests",
    "RegistrationTests",
    "ResolutionTests",
    "UnsupportedPlatformTests",
]
