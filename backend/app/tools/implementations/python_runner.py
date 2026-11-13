"""Sandboxed Python execution tool.

Execution is delegated to :mod:`app.tools.implementations.python_sandbox`,
which provides a real Windows operating-system boundary (a per-execution
AppContainer token plus a Job Object). The tool itself is intentionally thin:

* configuration is read from ``PYTHON_SANDBOX_*`` settings and never from the
  LLM or the submitted source;
* an AST denylist rejects a few escape-oriented imports as defence in depth;
* the blocking runtime call runs in a worker thread so the event loop stays
  free;
* results are structured and bounded before they reach the executor.

The AST denylist is **not** the security boundary. A determined program can
bypass static filtering, which is why the OS-level AppContainer/Job boundary is
mandatory and execution fails closed when it is unavailable.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from typing import ClassVar

from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolExecutionError,
    ToolSizeLimitError,
)
from app.tools.implementations import python_sandbox as sandbox
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


logger = logging.getLogger(__name__)

SANDBOX_BACKEND = "windows_appcontainer_job"

# Defence in depth only. These modules are escape-oriented: native code
# access, registry access, alternate import machinery, and process helpers.
# ``socket`` and ``subprocess`` are deliberately *not* blocked so the OS
# boundary can be exercised and verified rather than hidden behind a filter.
BLOCKED_IMPORT_ROOTS = frozenset(
    {
        "__import__",
        "_ctypes",
        "_winreg",
        "cffi",
        "ctypes",
        "imp",
        "importlib",
        "msvcrt",
        "multiprocessing",
        "pkgutil",
        "runpy",
        "site",
        "winreg",
    }
)

# Reserves time for process termination, ACL revocation, profile deletion, and
# workspace cleanup inside the executor's overall tool timeout.
TIMEOUT_RESERVE_SECONDS = 2.0
OUTER_WAIT_GRACE_SECONDS = 5.0
MIN_MEMORY_MB = 32


class PythonExecuteInput(StrictModel):
    code: str = Field(
        min_length=1,
        description=(
            "Python source code to execute inside an isolated, "
            "resource-limited sandbox. Standard output and standard error "
            "are captured."
        ),
    )


class PythonExecuteTool(BaseTool):
    name: ClassVar[str] = "python_execute"
    description: ClassVar[str] = (
        "Run short Python code inside a Windows AppContainer sandbox with a "
        "wall-clock timeout, CPU, memory, and output limits. Filesystem access "
        "is restricted to a disposable workspace, network access is denied, "
        "and child processes are blocked. Disabled by default; requires "
        "explicit user confirmation."
    )
    category: ClassVar[ToolCategory] = ToolCategory.SENSITIVE
    requires_confirmation: ClassVar[bool] = True
    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str | None] = "python_sandbox"
    input_model: ClassVar[type[StrictModel]] = PythonExecuteInput

    @property
    def enabled(self) -> bool:
        """Return whether configuration and the OS runtime both allow use."""

        return bool(
            settings.PYTHON_SANDBOX_ENABLED
            and sandbox.runtime_status().available
        )

    async def execute(
        self,
        context: ToolContext,
        arguments: PythonExecuteInput,
    ) -> ToolResult:
        if not settings.PYTHON_SANDBOX_ENABLED:
            raise ToolConfigurationError(
                "Python execution is disabled. Set PYTHON_SANDBOX_ENABLED=true "
                "and TOOL_ALLOW_SENSITIVE=true to enable it."
            )

        status = sandbox.runtime_status()

        if not status.available:
            raise ToolConfigurationError(
                "The Python sandbox runtime is unavailable. "
                f"{status.reason or 'No supported isolation backend was found.'}"
            )

        if settings.PYTHON_SANDBOX_ALLOW_NETWORK:
            raise ToolConfigurationError(
                "PYTHON_SANDBOX_ALLOW_NETWORK is not supported by the "
                "AppContainer backend. Network access must remain disabled."
            )

        code = arguments.code
        max_source = max(1, int(settings.PYTHON_SANDBOX_MAX_SOURCE_BYTES))

        if len(code.encode("utf-8")) > max_source:
            raise ToolSizeLimitError(
                f"Python code exceeds the {max_source}-byte source limit."
            )

        blocked = _blocked_import(code)

        if blocked:
            raise ToolExecutionError(
                f"The code imports a module that this sandbox rejects: "
                f"{blocked}. Static filtering is defence in depth, not the "
                "isolation boundary."
            )

        limits = _build_limits()
        timeout = limits.timeout_seconds + OUTER_WAIT_GRACE_SECONDS

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(sandbox.run_code, code, limits),
                timeout=timeout,
            )

        except asyncio.TimeoutError:
            logger.warning(
                "The Python sandbox did not return within its outer wait "
                "window; the sandbox timeout still bounds the process tree."
            )

            return success_result(
                tool_name=self.name,
                data=_empty_result_data(timed_out=True),
                metadata={
                    "sandboxed": True,
                    "backend": SANDBOX_BACKEND,
                    "timed_out": True,
                    "outer_timeout": True,
                },
            )

        except sandbox.SandboxUnavailableError as error:
            raise ToolConfigurationError(str(error)) from error

        except sandbox.SandboxExecutionError as error:
            raise ToolExecutionError(str(error)) from error

        except OSError as error:
            raise ToolExecutionError(
                "The Python sandbox could not be started."
            ) from error

        return success_result(
            tool_name=self.name,
            data=_result_data(result),
            metadata={
                "sandboxed": True,
                "backend": SANDBOX_BACKEND,
                "timed_out": result.timed_out,
                "resource_limited": result.resource_limited,
                "output_limited": result.output_limited,
            },
        )

    def summarize(self, arguments: PythonExecuteInput) -> str:
        return (
            "Run untrusted Python code in a Windows AppContainer sandbox "
            f"({len(arguments.code.encode('utf-8'))} bytes; "
            f"{_effective_timeout():g}s wall timeout, "
            f"{_memory_mb()} MB memory, output bounded). "
            "Requires explicit confirmation."
        )


def _build_limits() -> sandbox.SandboxLimits:
    """Return limits derived only from server-side configuration."""

    configured_wall = _positive_float(
        settings.PYTHON_SANDBOX_TIMEOUT_SECONDS,
        5.0,
    )
    tool_timeout = _positive_float(
        settings.TOOL_EXECUTION_TIMEOUT_SECONDS,
        15.0,
    )
    effective_wall = min(
        configured_wall,
        max(1.0, tool_timeout - TIMEOUT_RESERVE_SECONDS),
    )

    return sandbox.SandboxLimits(
        timeout_seconds=effective_wall,
        cpu_seconds=max(1, int(settings.PYTHON_SANDBOX_CPU_SECONDS)),
        memory_bytes=_memory_mb() * 1024 * 1024,
        max_output_bytes=max(
            1,
            int(settings.PYTHON_SANDBOX_MAX_OUTPUT_BYTES),
        ),
        allow_network=bool(settings.PYTHON_SANDBOX_ALLOW_NETWORK),
        workspace_parent=(
            settings.PYTHON_SANDBOX_WORKSPACE_PARENT or None
        ),
    )


def _effective_timeout() -> float:
    return _build_limits().timeout_seconds


def _memory_mb() -> int:
    try:
        configured = int(settings.PYTHON_SANDBOX_MEMORY_MB)

    except (TypeError, ValueError):
        configured = 256

    return max(MIN_MEMORY_MB, configured)


def _positive_float(value: object, fallback: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]

    except (TypeError, ValueError):
        return fallback

    return parsed if parsed > 0 else fallback


def _blocked_import(code: str) -> str | None:
    """Return the first rejected import/name, or ``None``.

    A syntax error is intentionally not reported here: it is handed to the
    interpreter so the caller receives the real ``SyntaxError`` traceback.
    """

    try:
        tree = ast.parse(code)

    except (SyntaxError, ValueError):
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0].lower()

                if root in BLOCKED_IMPORT_ROOTS:
                    return root

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue

            module = node.module or ""
            root = module.split(".", 1)[0].lower()

            if root in BLOCKED_IMPORT_ROOTS:
                return root

        elif isinstance(node, ast.Name):
            if node.id == "__import__":
                return "__import__"

        elif isinstance(node, ast.Attribute):
            if node.attr == "__import__":
                return "__import__"

    return None


def _result_data(result: sandbox.SandboxResult) -> dict[str, object]:
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "resource_limited": result.resource_limited,
        "output_limited": result.output_limited,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "sandboxed": True,
        "backend": SANDBOX_BACKEND,
    }


def _empty_result_data(*, timed_out: bool) -> dict[str, object]:
    return {
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "timed_out": timed_out,
        "resource_limited": False,
        "output_limited": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "elapsed_seconds": None,
        "sandboxed": True,
        "backend": SANDBOX_BACKEND,
    }


__all__ = [
    "BLOCKED_IMPORT_ROOTS",
    "PythonExecuteInput",
    "PythonExecuteTool",
]
