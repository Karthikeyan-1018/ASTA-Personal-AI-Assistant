"""Phase 11G tests for the Python execution sandbox.

Unit and executor tests use mocked sandbox results so they run everywhere and
never execute submitted code. The Windows integration tests exercise the real
AppContainer/Job Object boundary and are skipped when the runtime is
unavailable. Every integration test uses a unique temporary workspace; no
personal file, ASTA source file, ``.env`` file, or ``agriculture_db`` is read
or written.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app.tools.executor as executor_module
from app.core.config import settings
from app.models.tool_execution import ToolConfirmation
from app.services.tool_confirmation_service import tool_confirmation_service
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolExecutionError,
    ToolSizeLimitError,
)
from app.tools.executor import tool_executor
from app.tools.implementations import python_runner, python_sandbox
from app.tools.implementations.python_runner import (
    BLOCKED_IMPORT_ROOTS,
    PythonExecuteTool,
    _blocked_import,
)
from app.tools.registry import (
    ToolRegistry,
    ensure_builtin_tools,
    tool_registry,
)
from tests.tool_test_support import ToolEngineTestBase


SANDBOX_TOOL = "python_execute"
APPCONTAINER_EXIT_QUOTA = 0xC0000044
WAIT_TIMEOUT = 0x00000102
SYNCHRONIZE = 0x00100000


def make_result(**overrides):
    values = {
        "exit_code": 0,
        "stdout": "hello\n",
        "stderr": "",
        "timed_out": False,
        "resource_limited": False,
        "output_limited": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "elapsed_seconds": 0.02,
        "pid": 4242,
    }
    values.update(overrides)
    return python_sandbox.SandboxResult(**values)


def available_runtime(interpreter: str = "C:\\sandbox\\python.exe"):
    return python_sandbox.SandboxRuntimeStatus(
        available=True,
        reason="",
        interpreter=interpreter,
    )


def unavailable_runtime(reason: str = "test runtime unavailable"):
    return python_sandbox.SandboxRuntimeStatus(
        available=False,
        reason=reason,
        interpreter=None,
    )


def process_is_alive(pid: int) -> bool:
    """Return whether ``pid`` still refers to a running process."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)

    if not handle:
        return False

    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT

    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Unit tests: configuration, validation, mapping, and boundaries
# ---------------------------------------------------------------------------


class SandboxUnitTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.configure()
        self.use_runtime()

    def configure(self, **overrides):
        values = {
            "PYTHON_SANDBOX_ENABLED": True,
            "PYTHON_SANDBOX_TIMEOUT_SECONDS": 5,
            "PYTHON_SANDBOX_CPU_SECONDS": 5,
            "PYTHON_SANDBOX_MEMORY_MB": 256,
            "PYTHON_SANDBOX_MAX_SOURCE_BYTES": 20000,
            "PYTHON_SANDBOX_MAX_OUTPUT_BYTES": 20000,
            "PYTHON_SANDBOX_ALLOW_NETWORK": False,
            "PYTHON_SANDBOX_WORKSPACE_PARENT": "",
            "TOOL_EXECUTION_TIMEOUT_SECONDS": 15,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def use_runtime(self, status=None):
        patcher = patch.object(
            python_sandbox,
            "runtime_status",
            return_value=status or available_runtime(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_run_code(self, result=None, **kwargs):
        patcher = patch.object(
            python_sandbox,
            "run_code",
            return_value=result if result is not None else make_result(),
            **kwargs,
        )
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    async def execute(self, code: str):
        tool = PythonExecuteTool()
        arguments = tool.validate_arguments({"code": code})
        return await tool.execute(None, arguments)


class SandboxConfigurationTests(SandboxUnitTestBase):

    def test_tool_metadata_is_sensitive_and_confirmable(self):
        tool = PythonExecuteTool()

        self.assertEqual(tool.name, SANDBOX_TOOL)
        self.assertEqual(tool.category, ToolCategory.SENSITIVE)
        self.assertTrue(tool.requires_confirmation)
        self.assertFalse(tool.allowed_in_private)
        self.assertEqual(tool.provider_label, "python_sandbox")

    def test_tool_is_registered_as_a_builtin(self):
        names = {tool.name for tool in BUILTIN_TOOLS}
        ensure_builtin_tools()

        self.assertIn(SANDBOX_TOOL, names)
        self.assertIs(
            type(tool_registry.get(SANDBOX_TOOL)),
            PythonExecuteTool,
        )

    def test_disabled_setting_disables_the_tool(self):
        self.configure(PYTHON_SANDBOX_ENABLED=False)

        tool = PythonExecuteTool()

        self.assertFalse(tool.enabled)
        self.assertFalse(tool.definition().enabled)

    async def test_disabled_sandbox_fails_closed(self):
        self.configure(PYTHON_SANDBOX_ENABLED=False)
        run_code = self.patch_run_code()

        with self.assertRaises(ToolConfigurationError):
            await self.execute("print('hello')")

        run_code.assert_not_called()

    async def test_missing_runtime_disables_and_fails_closed(self):
        self.use_runtime(unavailable_runtime("no AppContainer here"))
        run_code = self.patch_run_code()

        tool = PythonExecuteTool()

        self.assertFalse(tool.enabled)

        with self.assertRaises(ToolConfigurationError) as raised:
            await self.execute("print('hello')")

        self.assertIn("no AppContainer here", str(raised.exception))
        run_code.assert_not_called()

    async def test_network_request_fails_closed(self):
        self.configure(PYTHON_SANDBOX_ALLOW_NETWORK=True)
        run_code = self.patch_run_code()

        with self.assertRaises(ToolConfigurationError):
            await self.execute("print('hello')")

        run_code.assert_not_called()

    async def test_source_size_limit_is_enforced(self):
        self.configure(PYTHON_SANDBOX_MAX_SOURCE_BYTES=10)
        run_code = self.patch_run_code()

        with self.assertRaises(ToolSizeLimitError):
            await self.execute("print('this is too large')")

        run_code.assert_not_called()

    async def test_sandbox_limits_come_from_server_settings(self):
        self.configure(
            PYTHON_SANDBOX_TIMEOUT_SECONDS=30,
            TOOL_EXECUTION_TIMEOUT_SECONDS=15,
            PYTHON_SANDBOX_CPU_SECONDS=4,
            PYTHON_SANDBOX_MEMORY_MB=128,
            PYTHON_SANDBOX_MAX_OUTPUT_BYTES=1234,
            PYTHON_SANDBOX_WORKSPACE_PARENT=r"C:\sandbox-parent",
        )
        run_code = self.patch_run_code()

        await self.execute("print('hello')")

        limits = run_code.call_args.args[1]

        # The wall timeout is clamped below the executor's 15-second timeout.
        self.assertEqual(limits.timeout_seconds, 13.0)
        self.assertEqual(limits.cpu_seconds, 4)
        self.assertEqual(limits.memory_bytes, 128 * 1024 * 1024)
        self.assertEqual(limits.max_output_bytes, 1234)
        self.assertFalse(limits.allow_network)
        self.assertEqual(
            limits.workspace_parent,
            r"C:\sandbox-parent",
        )

    async def test_short_configured_timeout_is_preserved(self):
        self.configure(
            PYTHON_SANDBOX_TIMEOUT_SECONDS=2,
            TOOL_EXECUTION_TIMEOUT_SECONDS=15,
        )
        run_code = self.patch_run_code()

        await self.execute("print('hello')")

        self.assertEqual(
            run_code.call_args.args[1].timeout_seconds,
            2.0,
        )

    async def test_memory_limit_has_a_documented_floor(self):
        self.configure(PYTHON_SANDBOX_MEMORY_MB=1)
        run_code = self.patch_run_code()

        await self.execute("print('hello')")

        self.assertEqual(
            run_code.call_args.args[1].memory_bytes,
            32 * 1024 * 1024,
        )

    async def test_result_data_is_structured_and_flagged(self):
        self.patch_run_code(
            make_result(
                exit_code=3,
                stdout="out",
                stderr="err",
                elapsed_seconds=0.1234,
            )
        )

        result = await self.execute("print('hello')")

        self.assertTrue(result.success)
        self.assertEqual(result.data["exit_code"], 3)
        self.assertEqual(result.data["stdout"], "out")
        self.assertEqual(result.data["stderr"], "err")
        self.assertFalse(result.data["timed_out"])
        self.assertTrue(result.data["sandboxed"])
        self.assertEqual(result.data["backend"], "windows_appcontainer_job")
        self.assertEqual(result.data["elapsed_seconds"], 0.123)
        self.assertTrue(result.metadata["sandboxed"])
        self.assertEqual(result.provider, None)

    async def test_timeout_result_is_flagged(self):
        self.patch_run_code(make_result(exit_code=1, timed_out=True))

        result = await self.execute("while True: pass")

        self.assertTrue(result.data["timed_out"])
        self.assertTrue(result.metadata["timed_out"])

    async def test_output_limit_result_is_flagged(self):
        self.patch_run_code(
            make_result(
                output_limited=True,
                stdout_truncated=True,
                stdout="x" * 100,
            )
        )

        result = await self.execute("print('x')")

        self.assertTrue(result.data["output_limited"])
        self.assertTrue(result.data["stdout_truncated"])

    async def test_sandbox_unavailable_error_is_a_configuration_error(self):
        patcher = patch.object(
            python_sandbox,
            "run_code",
            side_effect=python_sandbox.SandboxUnavailableError("no profile"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        with self.assertRaises(ToolConfigurationError):
            await self.execute("print('hello')")

    async def test_sandbox_execution_error_is_a_tool_execution_error(self):
        patcher = patch.object(
            python_sandbox,
            "run_code",
            side_effect=python_sandbox.SandboxExecutionError("no process"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        with self.assertRaises(ToolExecutionError):
            await self.execute("print('hello')")

    async def test_outer_wait_timeout_returns_a_timed_out_result(self):
        def slow_run(code, limits):
            time.sleep(1.5)
            return make_result()

        self.configure(
            PYTHON_SANDBOX_TIMEOUT_SECONDS=1,
            TOOL_EXECUTION_TIMEOUT_SECONDS=15,
        )
        patcher = patch.object(python_runner, "OUTER_WAIT_GRACE_SECONDS", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(
            python_sandbox,
            "run_code",
            side_effect=slow_run,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        result = await self.execute("print('hello')")

        self.assertTrue(result.data["timed_out"])
        self.assertTrue(result.metadata["outer_timeout"])

    def test_summary_does_not_leak_source_code(self):
        tool = PythonExecuteTool()
        summary = tool.summarize(
            tool.validate_arguments({"code": "SECRET_MARKER = 1"})
        )

        self.assertNotIn("SECRET_MARKER", summary)
        self.assertIn("AppContainer", summary)


class SandboxDenylistTests(unittest.TestCase):

    def test_escape_oriented_imports_are_rejected(self):
        cases = {
            "import ctypes": "ctypes",
            "import ctypes.wintypes": "ctypes",
            "from ctypes import CDLL": "ctypes",
            "import _winreg": "_winreg",
            "from winreg import HKEY_LOCAL_MACHINE": "winreg",
            "import importlib.util": "importlib",
            "import multiprocessing": "multiprocessing",
            "import msvcrt": "msvcrt",
            "import pkgutil": "pkgutil",
            "import runpy": "runpy",
            "import site": "site",
            "value = __import__": "__import__",
            '__import__("os")': "__import__",
        }

        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(_blocked_import(code), expected)

    def test_all_listed_modules_are_covered(self):
        self.assertTrue(
            {
                "ctypes",
                "cffi",
                "_ctypes",
                "winreg",
                "_winreg",
                "msvcrt",
                "multiprocessing",
                "importlib",
                "imp",
                "runpy",
                "pkgutil",
                "site",
                "__import__",
            }.issubset(BLOCKED_IMPORT_ROOTS)
        )

    def test_socket_and_subprocess_are_not_blocked(self):
        code = "import socket\nimport subprocess\n"

        self.assertIsNone(_blocked_import(code))

    def test_syntax_errors_are_not_pre_rejected(self):
        # The interpreter must be allowed to produce the real SyntaxError
        # traceback for the caller.
        self.assertIsNone(_blocked_import("def broken("))


class SandboxDenylistToolTests(SandboxUnitTestBase):

    async def test_tool_rejects_blocked_imports_before_the_runtime(self):
        run_code = self.patch_run_code()

        with self.assertRaises(ToolExecutionError):
            await self.execute("import ctypes\nprint('escape')")

        run_code.assert_not_called()

    async def test_tool_allows_socket_and_subprocess_for_os_boundary_tests(
        self,
    ):
        run_code = self.patch_run_code()

        result = await self.execute(
            "import socket\nimport subprocess\nprint('ok')"
        )

        self.assertTrue(result.success)
        run_code.assert_called_once()


# ---------------------------------------------------------------------------
# Executor tests: permissions, confirmations, and registry discovery
# ---------------------------------------------------------------------------


class SandboxExecutorTests(ToolEngineTestBase):
    def setUp(self):
        self.configure()

        registry = ToolRegistry()
        registry.register(PythonExecuteTool())
        patcher = patch.object(
            executor_module,
            "tool_registry",
            registry,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        status = patch.object(
            python_sandbox,
            "runtime_status",
            return_value=available_runtime(),
        )
        status.start()
        self.addCleanup(status.stop)

        self.run_code = MagicMock(return_value=make_result())
        patcher = patch.object(
            python_sandbox,
            "run_code",
            self.run_code,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, **overrides):
        values = {
            "PYTHON_SANDBOX_ENABLED": True,
            "PYTHON_SANDBOX_TIMEOUT_SECONDS": 5,
            "PYTHON_SANDBOX_CPU_SECONDS": 5,
            "PYTHON_SANDBOX_MEMORY_MB": 256,
            "PYTHON_SANDBOX_MAX_SOURCE_BYTES": 20000,
            "PYTHON_SANDBOX_MAX_OUTPUT_BYTES": 20000,
            "PYTHON_SANDBOX_ALLOW_NETWORK": False,
            "PYTHON_SANDBOX_WORKSPACE_PARENT": "",
            "TOOL_EXECUTION_TIMEOUT_SECONDS": 15,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def create_pending(
        self,
        code: str,
    ) -> ToolConfirmation:
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name=SANDBOX_TOOL,
                category=ToolCategory.SENSITIVE,
                arguments={"code": code},
                arguments_preview=f"{len(code)} bytes of Python",
            )

    async def test_disabled_tool_is_denied_by_the_permission_policy(self):
        self.configure(PYTHON_SANDBOX_ENABLED=False)

        result = await self.execute(SANDBOX_TOOL, {"code": "print('x')"})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.DISABLED,
        )
        self.run_code.assert_not_called()

    async def test_sensitive_opt_in_is_required(self):
        self.configure(TOOL_ALLOW_SENSITIVE=False)

        result = await self.execute(SANDBOX_TOOL, {"code": "print('x')"})

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )
        self.run_code.assert_not_called()

    async def test_private_mode_is_denied(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)

        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                private=True,
            )
            result = await tool_executor.execute(
                tool_name=SANDBOX_TOOL,
                arguments={"code": "print('x')"},
                context=context,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.PERMISSION,
        )
        self.run_code.assert_not_called()

    async def test_execution_requires_confirmation_and_does_not_run(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)

        result = await self.execute(SANDBOX_TOOL, {"code": "print('x')"})

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
        self.run_code.assert_not_called()

    async def test_approved_confirmation_executes_once(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)
        code = "print('approved')"
        confirmation = await self.create_pending(code)

        result = await self.execute(
            SANDBOX_TOOL,
            {"code": code},
            confirmation_id=confirmation.id,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertEqual(result.data["stdout"], "hello\n")
        self.run_code.assert_called_once()
        self.assertEqual(self.run_code.call_args.args[0], code)

    async def test_changed_code_after_approval_is_rejected(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)
        confirmation = await self.create_pending("print('one')")

        result = await self.execute(
            SANDBOX_TOOL,
            {"code": "print('two')"},
            confirmation_id=confirmation.id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.run_code.assert_not_called()

    def test_tool_definition_requires_confirmation_when_enabled(self):
        self.configure(TOOL_ALLOW_SENSITIVE=True)

        definition = PythonExecuteTool().definition()

        self.assertTrue(definition.requires_confirmation)
        self.assertEqual(definition.category, ToolCategory.SENSITIVE)
        self.assertTrue(definition.enabled)


# ---------------------------------------------------------------------------
# Real Windows AppContainer integration tests
# ---------------------------------------------------------------------------


RUNTIME = python_sandbox.runtime_status()


@unittest.skipUnless(
    RUNTIME.available,
    f"The Windows AppContainer sandbox runtime is unavailable: {RUNTIME.reason}",
)
class WindowsSandboxIntegrationTests(unittest.TestCase):
    """Exercise the real OS-level boundary on a supported Windows host."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="asta_py11g_"
        )
        self.addCleanup(self._temporary.cleanup)
        self.parent = self._temporary.name

    def limits(self, **overrides):
        values = {
            "timeout_seconds": 15.0,
            "cpu_seconds": 15,
            "memory_bytes": 256 * 1024 * 1024,
            "max_output_bytes": 20000,
            "allow_network": False,
            "workspace_parent": self.parent,
        }
        values.update(overrides)
        return python_sandbox.SandboxLimits(**values)

    def run_code(self, code, **overrides):
        return python_sandbox.run_code(code, self.limits(**overrides))

    def assert_workspace_cleaned(self):
        leftovers = [
            name
            for name in os.listdir(self.parent)
            if name.startswith("asta_py_")
        ]
        self.assertEqual(leftovers, [])

    def test_valid_execution_captures_output_and_exit_status(self):
        result = self.run_code(
            "import sys\n"
            "print('hello from sandbox')\n"
            "print('problem', file=sys.stderr)\n"
            "sys.exit(4)\n"
        )

        self.assertEqual(result.exit_code, 4)
        self.assertIn("hello from sandbox", result.stdout)
        self.assertIn("problem", result.stderr)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.output_limited)
        self.assertIsNotNone(result.pid)
        self.assert_workspace_cleaned()

    def test_syntax_error_is_reported_safely(self):
        result = self.run_code("def broken(\n")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("SyntaxError", result.stderr)
        self.assertFalse(result.timed_out)

    def test_runtime_error_is_reported_safely(self):
        result = self.run_code("print(1 / 0)\n")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("ZeroDivisionError", result.stderr)
        self.assertFalse(result.timed_out)

    def test_workspace_is_writable_and_is_the_working_directory(self):
        result = self.run_code(
            "import os\n"
            "with open('inside.txt', 'w', encoding='utf-8') as handle:\n"
            "    handle.write('ok')\n"
            "print(os.getcwd())\n"
            "print(sorted(os.listdir('.')))\n"
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(self.parent, result.stdout)
        self.assertIn("inside.txt", result.stdout)
        self.assert_workspace_cleaned()

    def test_filesystem_is_confined_to_the_workspace(self):
        canary = Path(self.parent) / "canary.txt"
        canary.write_text("CANARY", encoding="utf-8")

        result = self.run_code(
            "import os\n"
            "for path in (r'C:\\Asta\\README.md',\n"
            "             r'" + str(canary) + "'):\n"
            "    try:\n"
            "        open(path, 'rb').read(1)\n"
            "        print('read-ok', path)\n"
            "    except Exception as error:\n"
            "        print('read-blocked', type(error).__name__)\n"
            "try:\n"
            "    open(r'" + str(Path(self.parent) / "escape.txt") + "', 'w')\n"
            "    print('write-ok')\n"
            "except Exception as error:\n"
            "    print('write-blocked', type(error).__name__)\n"
            "try:\n"
            "    os.listdir(r'" + self.parent + "')\n"
            "    print('list-ok')\n"
            "except Exception as error:\n"
            "    print('list-blocked', type(error).__name__)\n"
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("read-blocked PermissionError", result.stdout)
        self.assertIn("write-blocked PermissionError", result.stdout)
        self.assertIn("list-blocked PermissionError", result.stdout)
        self.assertNotIn("read-ok", result.stdout)
        self.assertFalse(
            (Path(self.parent) / "escape.txt").exists()
        )

    def test_symlink_cannot_be_used_to_escape_the_workspace(self):
        result = self.run_code(
            "import os\n"
            "try:\n"
            "    os.symlink(r'C:\\Asta\\README.md', 'escape-link')\n"
            "except Exception as error:\n"
            "    print('symlink-blocked', type(error).__name__)\n"
            "else:\n"
            "    try:\n"
            "        open('escape-link', 'rb').read(1)\n"
            "        print('symlink-read-ok')\n"
            "    except Exception as error:\n"
            "        print('symlink-read-blocked', type(error).__name__)\n"
        )

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("symlink-read-ok", result.stdout)
        self.assertTrue(
            "symlink-blocked" in result.stdout
            or "symlink-read-blocked" in result.stdout
        )

    def test_environment_is_scrubbed(self):
        os.environ["ASTA_SANDBOX_TEST_CANARY"] = "do-not-leak"

        try:
            result = self.run_code(
                "import os\n"
                "print('CANARY', os.environ.get("
                "'ASTA_SANDBOX_TEST_CANARY'))\n"
                "print('KEYS', ','.join(sorted(os.environ)))\n"
            )

        finally:
            del os.environ["ASTA_SANDBOX_TEST_CANARY"]

        self.assertEqual(result.exit_code, 0)
        self.assertIn("CANARY None", result.stdout)

        upper = result.stdout.upper()
        self.assertNotIn("ASTA_", upper)
        self.assertNotIn("OPENCODE", upper)
        self.assertNotIn("DATABASE_URL", upper)
        self.assertNotIn("USERPROFILE", upper)
        self.assertNotIn("PATH", upper)
        self.assertIn("LOCALAPPDATA", result.stdout)

    def test_network_is_blocked(self):
        result = self.run_code(
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
            "    print('network-ok')\n"
            "except Exception as error:\n"
            "    print('network-blocked', type(error).__name__)\n"
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("network-blocked", result.stdout)
        self.assertNotIn("network-ok", result.stdout)

    def test_subprocess_creation_is_blocked(self):
        result = self.run_code(
            "import subprocess\n"
            "try:\n"
            "    subprocess.run(['cmd', '/c', 'echo child'], check=True)\n"
            "    print('spawn-ok')\n"
            "except OSError as error:\n"
            "    print('spawn-blocked', error.winerror)\n"
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("spawn-blocked 1816", result.stdout)
        self.assertNotIn("spawn-ok", result.stdout)

    def test_timeout_terminates_the_process_tree(self):
        started = time.monotonic()

        result = self.run_code(
            "import os\n"
            "print(os.getpid(), flush=True)\n"
            "while True:\n"
            "    pass\n",
            timeout_seconds=1.0,
            cpu_seconds=20,
        )

        elapsed = time.monotonic() - started

        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 15.0)
        self.assertIsNotNone(result.pid)
        self.assertFalse(process_is_alive(result.pid))
        self.assert_workspace_cleaned()

    def test_output_limit_terminates_and_bounds_capture(self):
        result = self.run_code(
            "import sys\n"
            "while True:\n"
            "    sys.stdout.write('x' * 1024)\n",
            max_output_bytes=4096,
        )

        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 4096)
        self.assert_workspace_cleaned()

    def test_memory_limit_is_enforced_or_visible_to_the_program(self):
        result = self.run_code(
            "block = bytearray(512 * 1024 * 1024)\n"
            "print(len(block))\n",
            memory_bytes=128 * 1024 * 1024,
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(
            "MemoryError" in result.stderr or result.resource_limited
        )

    def test_cpu_limit_is_enforced(self):
        result = self.run_code(
            "while True:\n"
            "    pass\n",
            timeout_seconds=25.0,
            cpu_seconds=1,
        )

        self.assertTrue(result.resource_limited)
        self.assertEqual(result.exit_code, APPCONTAINER_EXIT_QUOTA)
        self.assertFalse(result.timed_out)

    def test_invalid_limits_fail_closed(self):
        with self.assertRaises(python_sandbox.SandboxUnavailableError):
            self.run_code(
                "print('hello')",
                memory_bytes=8 * 1024 * 1024,
            )

        with self.assertRaises(python_sandbox.SandboxUnavailableError):
            self.run_code(
                "print('hello')",
                timeout_seconds=0,
            )

        with self.assertRaises(python_sandbox.SandboxUnavailableError):
            self.run_code(
                "print('hello')",
                allow_network=True,
            )

    def test_invalid_workspace_parent_fails_closed(self):
        file_path = Path(self.parent) / "not-a-directory.txt"
        file_path.write_text("x", encoding="utf-8")

        with self.assertRaises(python_sandbox.SandboxUnavailableError):
            python_sandbox.run_code(
                "print('hello')",
                self.limits(workspace_parent=str(file_path)),
            )

    def test_appcontainer_profiles_are_cleaned_up(self):
        packages = Path(os.environ["LOCALAPPDATA"]) / "Packages"
        before = {item.name for item in packages.glob("asta-py-*")}

        result = self.run_code("print('profile')\n")

        after = {item.name for item in packages.glob("asta-py-*")}

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(after - before, set())


class MissingRuntimeTests(unittest.TestCase):
    """Platform and runtime gate without touching the OS sandbox."""

    def test_non_windows_platform_is_reported_unavailable(self):
        with patch.object(python_sandbox, "IS_WINDOWS", False):
            status = python_sandbox._compute_runtime_status()

        self.assertFalse(status.available)
        self.assertEqual(status.platform, "unsupported")
        self.assertIn("Windows", status.reason)

    def test_missing_interpreter_is_reported_unavailable(self):
        with patch.object(
            python_sandbox,
            "select_interpreter",
            return_value=None,
        ):
            status = python_sandbox._compute_runtime_status()

        self.assertFalse(status.available)
        self.assertIn("interpreter", status.reason.lower())


__all__ = []
