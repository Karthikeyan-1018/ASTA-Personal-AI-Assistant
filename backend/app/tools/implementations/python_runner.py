"""Controlled Python execution as a separate subprocess.

IMPORTANT SECURITY NOTE
=======================
This is **not** a hardened sandbox. On Windows there is no portable
OS-level jail available to this application without external tooling
(containers, Windows Sandbox, or a dedicated restricted user). The runner
therefore:

* executes in a fresh temporary directory,
* starts the interpreter in isolated mode (``-I``) with a scrubbed environment
  that contains no ASTA credentials or database settings,
* applies a wall-clock timeout and bounded stdout/stderr capture,
* rejects a small set of obviously dangerous constructs as defence in depth.

A determined program can still read files (including an absolute path to the
ASTA ``.env``), start processes, or use the network. The tool is therefore
disabled by default and requires two explicit opt-in flags plus per-call user
confirmation. Treat it as a convenience runner for trusted code, not as a
security boundary for untrusted code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolExecutionError,
    ToolSizeLimitError,
)
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


logger = logging.getLogger(__name__)

READ_CHUNK = 8192

# Defence in depth only. A determined program can bypass this filtering, so it
# must never be presented as a sandbox boundary.
BLOCKED_SNIPPETS = (
    "os.environ",
    "os.getenv",
    "subprocess",
    "ctypes",
    "socket",
    "shutil.rmtree",
    "winreg",
    "importlib",
    "__import__",
)


class PythonExecuteInput(StrictModel):
    code: str = Field(
        min_length=1,
        description=(
            "Python source code to execute in a separate, unprivileged "
            "subprocess. Standard output and standard error are captured."
        ),
    )


class PythonExecuteTool(BaseTool):
    name: ClassVar[str] = "python_execute"
    description: ClassVar[str] = (
        "Run short Python code in a separate subprocess with a timeout. This "
        "is not a hardened sandbox and is disabled by default. Requires "
        "explicit user confirmation."
    )
    category: ClassVar[ToolCategory] = ToolCategory.SENSITIVE
    requires_confirmation: ClassVar[bool] = True
    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str | None] = "python_subprocess"
    input_model: ClassVar[type[StrictModel]] = PythonExecuteInput

    @property
    def enabled(self) -> bool:
        return bool(
            settings.PYTHON_EXECUTION_ENABLED
            and settings.PYTHON_EXECUTION_ALLOW_UNSAFE
        )

    async def execute(
        self,
        context: ToolContext,
        arguments: PythonExecuteInput,
    ) -> ToolResult:
        if not self.enabled:
            raise ToolConfigurationError(
                "Python execution is disabled. Both PYTHON_EXECUTION_ENABLED "
                "and PYTHON_EXECUTION_ALLOW_UNSAFE must be enabled."
            )

        code = arguments.code
        max_code_bytes = max(1, int(settings.PYTHON_EXECUTION_MAX_CODE_BYTES))

        if len(code.encode("utf-8")) > max_code_bytes:
            raise ToolSizeLimitError(
                f"Python code exceeds the {max_code_bytes}-byte limit."
            )

        lowered = code.lower()

        for snippet in BLOCKED_SNIPPETS:
            if snippet in lowered:
                raise ToolExecutionError(
                    "The code contains a construct that this runner blocks. "
                    "This filter is defence in depth, not a sandbox."
                )

        timeout = max(1, int(settings.PYTHON_EXECUTION_TIMEOUT_SECONDS))
        max_output = max(1, int(settings.PYTHON_EXECUTION_MAX_OUTPUT_BYTES))

        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(
                    _run_subprocess,
                    code,
                    timeout,
                    max_output,
                ),
                timeout=timeout + 5,
            )

        except asyncio.TimeoutError:
            return success_result(
                tool_name=self.name,
                data={
                    "timed_out": True,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "sandboxed": False,
                },
                metadata={"unsafe_runner": True, "timed_out": True},
            )

        return success_result(
            tool_name=self.name,
            data=output,
            metadata={"unsafe_runner": True},
        )

    def summarize(self, arguments: PythonExecuteInput) -> str:
        return (
            "Run Python code in a separate subprocess "
            f"({len(arguments.code.encode('utf-8'))} bytes). "
            "This runner is not a hardened sandbox."
        )


def _run_subprocess(code: str, timeout: int, max_output: int) -> dict[str, Any]:
    """Run the script synchronously in a worker thread.

    A thread is used so the asynchronous FastAPI event loop is never blocked.
    The child process itself is bounded by the wall-clock timeout.
    """

    with tempfile.TemporaryDirectory(prefix="asta_python_") as directory:
        workdir = Path(directory)
        script = workdir / "main.py"
        script.write_text(code, encoding="utf-8")

        env = _scrubbed_environment(workdir)
        command = [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            str(script),
        ]

        try:
            completed = _spawn_and_capture(
                command=command,
                cwd=workdir,
                env=env,
                timeout=timeout,
                max_output=max_output,
            )

        except FileNotFoundError as error:
            raise ToolExecutionError(
                "The Python interpreter could not be started."
            ) from error

        except OSError as error:
            raise ToolExecutionError(
                "The Python subprocess could not be started."
            ) from error

        return completed


def _spawn_and_capture(
    *,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    max_output: int,
) -> dict[str, Any]:
    import subprocess

    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # A new process group allows a hard kill without signalling ASTA.
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        ),
        start_new_session=os.name != "nt",
    )

    timed_out = False

    try:
        stdout, stderr = process.communicate(timeout=timeout)

    except subprocess.TimeoutExpired:
        timed_out = True

        try:
            process.kill()

        except OSError:
            pass

        stdout, stderr = process.communicate()

        stdout = stdout or b""
        stderr = stderr or b""

    stdout_text, stdout_truncated = _decode_bounded(stdout, max_output)
    stderr_text, stderr_truncated = _decode_bounded(stderr, max_output)

    return {
        "timed_out": timed_out,
        "exit_code": None if timed_out else process.returncode,
        "stdout": "" if timed_out else stdout_text,
        "stderr": "" if timed_out else stderr_text,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "sandboxed": False,
    }


def _decode_bounded(raw: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(raw) > limit
    return (
        raw[:limit].decode("utf-8", errors="replace"),
        truncated,
    )


def _scrubbed_environment(workdir: Path) -> dict[str, str]:
    """Return a minimal environment with no ASTA credentials."""

    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TEMP": str(workdir),
        "TMP": str(workdir),
    }

    # Windows needs SystemRoot for basic runtime behaviour. It is not a
    # credential. PATH is intentionally omitted so common tools are not on
    # the search path by default.
    for name in ("SystemRoot", "WINDIR", "COMSPEC"):
        value = os.environ.get(name)

        if value:
            env[name] = value

    return env


__all__ = ["PythonExecuteInput", "PythonExecuteTool"]
