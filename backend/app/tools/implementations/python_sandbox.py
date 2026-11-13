"""Windows AppContainer sandbox runtime for untrusted Python code.

Security boundary
=================
This module implements a real operating-system isolation boundary on Windows;
it is not an ``exec``-with-restricted-globals trick and it is not a plain
subprocess.

* **AppContainer token.** The child runs under a fresh, per-execution
  AppContainer profile with no capabilities. AppContainer ACLs therefore deny
  access to ASTA source, ``.env`` files, user documents, and any other path
  that was not explicitly granted. Windows network isolation blocks outbound
  sockets (including loopback) because no network capability is granted.
* **Job object.** A job object enforces a job/process memory limit and
  per-process/job CPU-time limit, permits only one active process (so the
  sandbox cannot create child processes), kills every process in the job when
  the job handle closes, and is terminated explicitly on timeout or excessive
  output.
* **Handle discipline.** ``PROC_THREAD_ATTRIBUTE_HANDLE_LIST`` ensures that
  only stdin, stdout, and stderr are inherited. ASTA database connections,
  log files, sockets, and other open handles are never exposed.
* **Environment discipline.** The child receives an explicit, minimal
  environment block (no ``PATH``, no ``USERPROFILE``, no ASTA variables) and
  ``LOCALAPPDATA`` is redirected to the disposable AppContainer profile.
* **Filesystem discipline.** Only the per-execution workspace is granted
  ``Modify`` access, and only the interpreter installation is granted
  read/execute. AppContainer containment is enforced by the OS, so ``..``,
  absolute paths, symlinks, and junctions cannot escape it.
* **Interpreter discipline.** The interpreter runs in isolated mode with
  ``-I -B -S -X utf8``; user site-packages and environment-based import
  configuration are disabled.

The parent process (ASTA) grants and revokes ACLs and owns the job object. If
any required API, the interpreter, the profile, or the ACL change is
unavailable, execution fails closed instead of falling back to an unsafe
subprocess.

Residual risks are documented in ``docs/python_execution_sandbox.md``.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IS_WINDOWS = os.name == "nt"

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

STARTF_USESTDHANDLES = 0x00000100
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
EXTENDED_STARTUPINFO_PRESENT = 0x00080000

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009

JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# STATUS_QUOTA_EXCEEDED (CPU/job quota) and STATUS_COMMITMENT_LIMIT (memory).
RESOURCE_LIMIT_EXIT_CODES = frozenset({0xC0000044, 0xC000012D})

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080

PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000

READ_CHUNK_BYTES = 65536
CLEANUP_WAIT_SECONDS = 5.0
MIN_MEMORY_BYTES = 32 * 1024 * 1024
ACL_TIMEOUT_SECONDS = 60


class SandboxUnavailableError(RuntimeError):
    """The required OS sandbox runtime cannot be used."""


class SandboxExecutionError(RuntimeError):
    """The sandbox process could not be started or observed safely."""


# ---------------------------------------------------------------------------
# ctypes structures
# ---------------------------------------------------------------------------

HANDLE = ctypes.c_void_p
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int
LPCWSTR = ctypes.c_wchar_p
LPWSTR = ctypes.c_wchar_p
SIZE_T = ctypes.c_size_t
HRESULT = ctypes.c_long


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", DWORD)]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
        ("CapabilityCount", DWORD),
        ("Reserved", DWORD),
    ]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", BOOL),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", LPWSTR),
        ("lpDesktop", LPWSTR),
        ("lpTitle", LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", ctypes.c_ushort),
        ("cbReserved2", ctypes.c_ushort),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", SIZE_T),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SandboxRuntimeStatus:
    available: bool
    reason: str
    interpreter: str | None = None
    platform: str = "windows" if IS_WINDOWS else "unsupported"


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_seconds: float
    cpu_seconds: int
    memory_bytes: int
    max_output_bytes: int
    allow_network: bool = False
    workspace_parent: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    resource_limited: bool
    output_limited: bool
    stdout_truncated: bool
    stderr_truncated: bool
    elapsed_seconds: float
    pid: int | None = None


# ---------------------------------------------------------------------------
# Runtime discovery
# ---------------------------------------------------------------------------


class _WindowsApi:
    """Configured function pointers for the required Win32 entry points."""

    def __init__(self) -> None:
        self.userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        self.userenv.CreateAppContainerProfile.restype = HRESULT
        self.userenv.CreateAppContainerProfile.argtypes = [
            LPCWSTR,
            LPCWSTR,
            LPCWSTR,
            ctypes.c_void_p,
            DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.userenv.DeleteAppContainerProfile.restype = HRESULT
        self.userenv.DeleteAppContainerProfile.argtypes = [LPCWSTR]

        self.advapi32.ConvertSidToStringSidW.restype = BOOL
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(LPWSTR),
        ]
        self.advapi32.FreeSid.restype = ctypes.c_void_p
        self.advapi32.FreeSid.argtypes = [ctypes.c_void_p]

        self.kernel32.LocalFree.restype = ctypes.c_void_p
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = BOOL
        self.kernel32.CloseHandle.argtypes = [HANDLE]
        self.kernel32.WaitForSingleObject.restype = DWORD
        self.kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        self.kernel32.GetExitCodeProcess.restype = BOOL
        self.kernel32.GetExitCodeProcess.argtypes = [
            HANDLE,
            ctypes.POINTER(DWORD),
        ]
        self.kernel32.ResumeThread.restype = DWORD
        self.kernel32.ResumeThread.argtypes = [HANDLE]
        self.kernel32.TerminateProcess.restype = BOOL
        self.kernel32.TerminateProcess.argtypes = [HANDLE, DWORD]

        self.kernel32.InitializeProcThreadAttributeList.restype = BOOL
        self.kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            DWORD,
            DWORD,
            ctypes.POINTER(SIZE_T),
        ]
        self.kernel32.DeleteProcThreadAttributeList.restype = None
        self.kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        self.kernel32.UpdateProcThreadAttribute.restype = BOOL
        self.kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            DWORD,
            SIZE_T,
            ctypes.c_void_p,
            SIZE_T,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]

        self.kernel32.CreateJobObjectW.restype = HANDLE
        self.kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, LPCWSTR]
        self.kernel32.SetInformationJobObject.restype = BOOL
        self.kernel32.SetInformationJobObject.argtypes = [
            HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            DWORD,
        ]
        self.kernel32.AssignProcessToJobObject.restype = BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
        self.kernel32.TerminateJobObject.restype = BOOL
        self.kernel32.TerminateJobObject.argtypes = [HANDLE, DWORD]

        self.kernel32.CreatePipe.restype = BOOL
        self.kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(HANDLE),
            ctypes.POINTER(HANDLE),
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            DWORD,
        ]
        self.kernel32.CreateFileW.restype = HANDLE
        self.kernel32.CreateFileW.argtypes = [
            LPCWSTR,
            DWORD,
            DWORD,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            DWORD,
            DWORD,
            HANDLE,
        ]
        self.kernel32.CreateProcessW.restype = BOOL
        self.kernel32.CreateProcessW.argtypes = [
            LPCWSTR,
            LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            BOOL,
            DWORD,
            ctypes.c_void_p,
            LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]


@lru_cache(maxsize=1)
def _load_api() -> _WindowsApi | None:
    if not IS_WINDOWS:
        return None

    try:
        return _WindowsApi()

    except (AttributeError, OSError):
        return None


def select_interpreter() -> str | None:
    """Return the base interpreter that can run without a venv launcher.

    A virtual-environment ``python.exe`` is a launcher that spawns the real
    interpreter. The sandbox forbids child processes, so the base interpreter
    is used directly instead. The base installation also gives the sandbox the
    standard library only, not ASTA's dependencies.
    """

    candidates = [
        getattr(sys, "_base_executable", "") or "",
        sys.executable or "",
    ]

    for raw in candidates:
        if not raw:
            continue

        try:
            resolved = os.path.realpath(raw)

        except (OSError, ValueError):
            continue

        name = os.path.basename(resolved).lower()

        if not name.startswith("python"):
            continue

        if os.path.isfile(resolved):
            return resolved

    return None


def _compute_runtime_status() -> SandboxRuntimeStatus:
    if not IS_WINDOWS:
        return SandboxRuntimeStatus(
            available=False,
            reason=(
                "The Python sandbox backend requires Windows AppContainer "
                "support; this platform is not supported."
            ),
            platform="unsupported",
        )

    try:
        version = sys.getwindowsversion()

        if (version.major, version.minor) < (6, 2):
            return SandboxRuntimeStatus(
                available=False,
                reason=(
                    "Windows AppContainer requires Windows 8 or later."
                ),
            )

    except AttributeError:
        pass

    if _load_api() is None:
        return SandboxRuntimeStatus(
            available=False,
            reason=(
                "Required Windows sandbox APIs are unavailable on this host."
            ),
        )

    interpreter = select_interpreter()

    if not interpreter:
        return SandboxRuntimeStatus(
            available=False,
            reason=(
                "A standalone Python interpreter for the sandbox was not "
                "found."
            ),
        )

    return SandboxRuntimeStatus(
        available=True,
        reason="",
        interpreter=interpreter,
    )


@lru_cache(maxsize=1)
def runtime_status() -> SandboxRuntimeStatus:
    """Return cached sandbox availability.

    The check is intentionally cheap: it verifies the platform, the required
    APIs, and the interpreter. ACL and profile failures are detected when an
    execution is attempted and fail closed.
    """

    return _compute_runtime_status()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_code(
    code: str,
    limits: SandboxLimits,
    *,
    interpreter: str | None = None,
) -> SandboxResult:
    """Execute ``code`` inside a fresh AppContainer workspace.

    All failures are normalized to ``SandboxUnavailableError`` (the runtime is
    not usable) or ``SandboxExecutionError`` (the child could not be started or
    observed). The workspace, AppContainer profile, ACLs, job object, and
    process handles are always cleaned up in ``finally`` blocks.
    """

    if not IS_WINDOWS:
        raise SandboxUnavailableError(
            "The Python sandbox is only available on Windows."
        )

    api = _load_api()

    if api is None:
        raise SandboxUnavailableError(
            "Required Windows sandbox APIs are unavailable."
        )

    if limits.allow_network:
        raise SandboxUnavailableError(
            "Network access cannot be granted by the AppContainer sandbox "
            "runtime. Network access must remain disabled."
        )

    interpreter = interpreter or select_interpreter()

    if not interpreter or not os.path.isfile(interpreter):
        raise SandboxUnavailableError(
            "A standalone Python interpreter for the sandbox was not found."
        )

    _validate_limits(limits)

    workspace = _create_workspace(limits.workspace_parent)
    profile: _Profile | None = None
    job: int | None = None
    process: _PROCESS_INFORMATION | None = None
    readers: list[_OutputReader] = []
    granted: list[tuple[str, str]] = []
    attr_list: ctypes.Array | None = None
    handles: dict[str, Any] | None = None
    started = time.monotonic()

    try:
        script = os.path.join(workspace, "main.py")

        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(code)

        profile = _create_profile(api)

        interpreter_dir = os.path.dirname(interpreter)
        _grant_acl(interpreter_dir, profile.sid_string, "(OI)(CI)RX")
        granted.append((interpreter_dir, profile.sid_string))
        _grant_acl(workspace, profile.sid_string, "(OI)(CI)M")
        granted.append((workspace, profile.sid_string))

        job = _create_job(api, limits)
        attr_list, handles = _build_startup(api, profile, workspace, code)

        environment = _build_environment(
            workspace=workspace,
            profile_local_appdata=profile.ac_path,
        )
        environment_buffer = ctypes.create_unicode_buffer(environment)
        command_line = subprocess.list2cmdline(
            [
                interpreter,
                "-I",
                "-B",
                "-S",
                "-X",
                "utf8",
                script,
            ]
        )
        command_buffer = ctypes.create_unicode_buffer(command_line)

        process = _PROCESS_INFORMATION()
        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.lpAttributeList = ctypes.addressof(attr_list)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = handles["stdin"]
        startup.StartupInfo.hStdOutput = handles["stdout_write"]
        startup.StartupInfo.hStdError = handles["stderr_write"]

        created = api.kernel32.CreateProcessW(
            interpreter,
            command_buffer,
            None,
            None,
            True,
            (
                EXTENDED_STARTUPINFO_PRESENT
                | CREATE_UNICODE_ENVIRONMENT
                | CREATE_SUSPENDED
                | CREATE_NO_WINDOW
            ),
            ctypes.cast(environment_buffer, ctypes.c_void_p),
            workspace,
            ctypes.byref(startup.StartupInfo),
            ctypes.byref(process),
        )

        _close_handle_quietly(api, handles["stdout_write"])
        _close_handle_quietly(api, handles["stderr_write"])
        _close_handle_quietly(api, handles["stdin"])
        handles["stdout_write"] = None
        handles["stderr_write"] = None
        handles["stdin"] = None

        if not created:
            raise SandboxExecutionError(
                "The sandbox process could not be started."
            )

        pid = int(process.dwProcessId)

        if not api.kernel32.AssignProcessToJobObject(job, process.hProcess):
            api.kernel32.TerminateProcess(process.hProcess, 1)
            raise SandboxExecutionError(
                "The sandbox process could not be placed in its resource "
                "job."
            )

        if api.kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
            api.kernel32.TerminateJobObject(job, 1)
            raise SandboxExecutionError(
                "The sandbox process could not be resumed."
            )

        _close_handle_quietly(api, process.hThread)
        process.hThread = None

        capture = _OutputCapture(limits.max_output_bytes)
        reader_stdout = _OutputReader(
            api,
            handles["stdout_read"],
            capture,
            "stdout",
            job,
        )
        reader_stderr = _OutputReader(
            api,
            handles["stderr_read"],
            capture,
            "stderr",
            job,
        )
        handles["stdout_read"] = None
        handles["stderr_read"] = None
        readers = [reader_stdout, reader_stderr]

        for reader in readers:
            reader.start()

        wait_result = api.kernel32.WaitForSingleObject(
            process.hProcess,
            max(1, int(limits.timeout_seconds * 1000)),
        )
        timed_out = wait_result == WAIT_TIMEOUT

        if timed_out:
            api.kernel32.TerminateJobObject(job, 1)
            api.kernel32.WaitForSingleObject(
                process.hProcess,
                int(CLEANUP_WAIT_SECONDS * 1000),
            )

        for reader in readers:
            reader.join(CLEANUP_WAIT_SECONDS)

        exit_code = _exit_code(api, process.hProcess)

        return SandboxResult(
            exit_code=exit_code,
            stdout=capture.stdout_text(),
            stderr=capture.stderr_text(),
            timed_out=timed_out,
            resource_limited=exit_code in RESOURCE_LIMIT_EXIT_CODES,
            output_limited=capture.exceeded.is_set(),
            stdout_truncated=capture.stream_truncated["stdout"],
            stderr_truncated=capture.stream_truncated["stderr"],
            elapsed_seconds=time.monotonic() - started,
            pid=pid,
        )

    finally:
        if process is not None:
            if process.hThread:
                _close_handle_quietly(api, process.hThread)

            if process.hProcess:
                if job:
                    api.kernel32.TerminateJobObject(job, 1)

                api.kernel32.WaitForSingleObject(
                    process.hProcess,
                    int(CLEANUP_WAIT_SECONDS * 1000),
                )
                _close_handle_quietly(api, process.hProcess)

        for reader in readers:
            reader.join(1.0)

        if handles:
            for key in (
                "stdin",
                "stdout_write",
                "stderr_write",
                "stdout_read",
                "stderr_read",
            ):
                value = handles.get(key)

                if value:
                    _close_handle_quietly(api, value)
                    handles[key] = None

        if job:
            _close_handle_quietly(api, job)

        if attr_list is not None:
            try:
                api.kernel32.DeleteProcThreadAttributeList(attr_list)

            except (AttributeError, OSError):
                pass

        for path, sid in reversed(granted):
            _revoke_acl(path, sid)

        if profile is not None:
            _delete_profile(api, profile)

        shutil.rmtree(workspace, ignore_errors=True)


# ---------------------------------------------------------------------------
# Profile and ACL management
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Profile:
    name: str
    sid: ctypes.c_void_p
    sid_string: str
    ac_path: str


def _create_profile(api: _WindowsApi) -> _Profile:
    name = f"asta-py-{uuid.uuid4().hex[:24]}"
    sid = ctypes.c_void_p()
    result = api.userenv.CreateAppContainerProfile(
        name,
        name,
        "ASTA Python sandbox",
        None,
        0,
        ctypes.byref(sid),
    )

    if result < 0 or not sid.value:
        raise SandboxUnavailableError(
            "The sandbox AppContainer profile could not be created."
        )

    try:
        sid_string = _sid_to_string(api, sid)

    except SandboxExecutionError:
        api.advapi32.FreeSid(sid)
        raise

    local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get(
        "USERPROFILE"
    )

    if not local_appdata:
        api.advapi32.FreeSid(sid)
        api.userenv.DeleteAppContainerProfile(name)
        raise SandboxUnavailableError(
            "The AppContainer profile directory could not be determined."
        )

    ac_path = os.path.join(local_appdata, "Packages", name, "AC")
    try:
        os.makedirs(ac_path, exist_ok=True)

    except OSError as error:
        api.advapi32.FreeSid(sid)
        api.userenv.DeleteAppContainerProfile(name)
        raise SandboxUnavailableError(
            "The AppContainer profile directory could not be created."
        ) from error

    return _Profile(
        name=name,
        sid=sid,
        sid_string=sid_string,
        ac_path=ac_path,
    )


def _sid_to_string(api: _WindowsApi, sid: ctypes.c_void_p) -> str:
    output = LPWSTR()

    if not api.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
        raise SandboxExecutionError(
            "The sandbox security identifier could not be read."
        )

    try:
        return output.value or ""

    finally:
        api.kernel32.LocalFree(output)


def _delete_profile(api: _WindowsApi, profile: _Profile) -> None:
    try:
        api.userenv.DeleteAppContainerProfile(profile.name)

    except OSError:
        logger.warning("Could not delete an AppContainer profile.")

    try:
        api.advapi32.FreeSid(profile.sid)

    except OSError:
        pass


# Serialize ACL changes. AppContainer names are unique, but concurrent icacls
# calls against the same interpreter directory can otherwise fail transiently.
_ACL_LOCK = threading.Lock()


def _icacls_path() -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")

    if not root:
        raise SandboxUnavailableError(
            "The Windows system directory could not be determined."
        )

    return os.path.join(root, "System32", "icacls.exe")


def _run_icacls(path: str, *arguments: str) -> None:
    try:
        with _ACL_LOCK:
            completed = subprocess.run(
                [_icacls_path(), path, "/Q", *arguments],
                capture_output=True,
                text=True,
                timeout=ACL_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    except (OSError, subprocess.SubprocessError) as error:
        raise SandboxUnavailableError(
            "The sandbox could not adjust filesystem permissions."
        ) from error

    if completed.returncode != 0:
        raise SandboxUnavailableError(
            "The sandbox could not adjust filesystem permissions."
        )


def _grant_acl(path: str, sid: str, permissions: str) -> None:
    _run_icacls(path, "/grant", f"*{sid}:{permissions}")


def _revoke_acl(path: str, sid: str) -> None:
    try:
        _run_icacls(path, "/remove", f"*{sid}")

    except SandboxUnavailableError:
        logger.warning("Could not revoke an AppContainer ACL grant.")


# ---------------------------------------------------------------------------
# Job object
# ---------------------------------------------------------------------------


def _create_job(api: _WindowsApi, limits: SandboxLimits) -> int:
    job = api.kernel32.CreateJobObjectW(None, None)

    if not job:
        raise SandboxExecutionError(
            "The sandbox resource job could not be created."
        )

    information = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_PROCESS_TIME
        | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    information.BasicLimitInformation.PerProcessUserTimeLimit = (
        limits.cpu_seconds * 10_000_000
    )
    information.BasicLimitInformation.PerJobUserTimeLimit = (
        limits.cpu_seconds * 10_000_000
    )
    information.BasicLimitInformation.ActiveProcessLimit = 1
    information.ProcessMemoryLimit = limits.memory_bytes
    information.JobMemoryLimit = limits.memory_bytes

    applied = api.kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )

    if not applied:
        _close_handle_quietly(api, job)
        raise SandboxExecutionError(
            "The sandbox resource limits could not be applied."
        )

    return job


# ---------------------------------------------------------------------------
# Process startup
# ---------------------------------------------------------------------------


def _build_startup(
    api: _WindowsApi,
    profile: _Profile,
    workspace: str,
    code: str,
) -> tuple[ctypes.Array, dict[str, Any]]:
    """Create the attribute list, pipes, and NUL stdin handle.

    The returned dictionary keeps the raw handle values; ownership of each
    handle is transferred to the caller once it is closed or wrapped by a
    reader.
    """

    del code  # reserved for future per-execution metadata

    size = SIZE_T()
    api.kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))

    if size.value == 0:
        raise SandboxExecutionError(
            "The sandbox process attributes could not be sized."
        )

    attribute_list = ctypes.create_string_buffer(size.value)

    if not api.kernel32.InitializeProcThreadAttributeList(
        attribute_list,
        2,
        0,
        ctypes.byref(size),
    ):
        raise SandboxExecutionError(
            "The sandbox process attributes could not be initialized."
        )

    capabilities = _SECURITY_CAPABILITIES(profile.sid, None, 0, 0)

    if not api.kernel32.UpdateProcThreadAttribute(
        attribute_list,
        0,
        PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
        ctypes.byref(capabilities),
        ctypes.sizeof(capabilities),
        None,
        None,
    ):
        api.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise SandboxExecutionError(
            "The sandbox AppContainer token could not be configured."
        )

    security = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES),
        None,
        True,
    )
    stdout_read = HANDLE()
    stdout_write = HANDLE()
    stderr_read = HANDLE()
    stderr_write = HANDLE()

    if not api.kernel32.CreatePipe(
        ctypes.byref(stdout_read),
        ctypes.byref(stdout_write),
        ctypes.byref(security),
        0,
    ):
        api.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise SandboxExecutionError(
            "The sandbox output pipe could not be created."
        )

    if not api.kernel32.CreatePipe(
        ctypes.byref(stderr_read),
        ctypes.byref(stderr_write),
        ctypes.byref(security),
        0,
    ):
        for handle in (stdout_read, stdout_write):
            _close_handle_quietly(api, handle.value)

        api.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise SandboxExecutionError(
            "The sandbox error pipe could not be created."
        )

    stdin_handle = api.kernel32.CreateFileW(
        "NUL",
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        ctypes.byref(security),
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )

    stdin_value = int(stdin_handle or 0)

    if not stdin_value or stdin_value == INVALID_HANDLE_VALUE:
        for handle in (
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
        ):
            _close_handle_quietly(api, handle.value)

        api.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise SandboxExecutionError(
            "The sandbox standard input handle could not be created."
        )

    inherited = (HANDLE * 3)(
        stdin_handle,
        stdout_write,
        stderr_write,
    )

    if not api.kernel32.UpdateProcThreadAttribute(
        attribute_list,
        0,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        ctypes.byref(inherited),
        ctypes.sizeof(inherited),
        None,
        None,
    ):
        for handle in (
            stdin_handle,
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
        ):
            _close_handle_quietly(api, handle)

        api.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise SandboxExecutionError(
            "The sandbox handle inheritance list could not be configured."
        )

    # Keep the handle array and the security-capabilities structure alive until
    # CreateProcess returns. The attribute list stores pointers to both, so
    # freeing them early would silently start the child without an
    # AppContainer token.
    return attribute_list, {
        "_inherited": inherited,
        "_capabilities": capabilities,
        "stdin": stdin_handle,
        "stdout_read": stdout_read.value,
        "stdout_write": stdout_write.value,
        "stderr_read": stderr_read.value,
        "stderr_write": stderr_write.value,
    }


def _build_environment(*, workspace: str, profile_local_appdata: str) -> str:
    """Return a minimal, NUL-separated environment block.

    No value is copied from ASTA's environment except the Windows directory,
    which is required by the loader and contains no credentials. ``PATH`` is
    deliberately omitted so common system tools are not discoverable.
    """

    system_root = (
        os.environ.get("SystemRoot")
        or os.environ.get("WINDIR")
        or r"C:\Windows"
    )

    values = {
        "SystemRoot": system_root,
        "WINDIR": system_root,
        # CreateProcess requires LOCALAPPDATA when an explicit environment
        # block is supplied to an AppContainer process.
        "LOCALAPPDATA": profile_local_appdata,
        "TEMP": workspace,
        "TMP": workspace,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }

    return "\0".join(
        f"{name}={value}" for name, value in sorted(values.items())
    ) + "\0\0"


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------


class _OutputCapture:
    """Thread-safe combined stdout/stderr budget."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._remaining = self._limit
        self._lock = threading.Lock()
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.exceeded = threading.Event()
        self.stream_truncated = {"stdout": False, "stderr": False}

    def add(self, stream: str, chunk: bytes) -> bool:
        """Consume a chunk. Return False when reading should stop."""

        with self._lock:
            room = self._remaining

            if room <= 0:
                self.stream_truncated[stream] = True
                self.exceeded.set()
                return False

            accepted = chunk[:room]
            target = self._stdout if stream == "stdout" else self._stderr
            target.extend(accepted)
            self._remaining -= len(accepted)

            if len(chunk) > room:
                self.stream_truncated[stream] = True
                self.exceeded.set()
                return False

            return True

    def stdout_text(self) -> str:
        with self._lock:
            return bytes(self._stdout).decode("utf-8", errors="replace")

    def stderr_text(self) -> str:
        with self._lock:
            return bytes(self._stderr).decode("utf-8", errors="replace")


class _OutputReader(threading.Thread):
    """Read a pipe with a bounded budget and stop the job on overflow."""

    def __init__(
        self,
        api: _WindowsApi,
        handle: int,
        capture: _OutputCapture,
        stream: str,
        job: int,
    ) -> None:
        super().__init__(daemon=True)
        self._api = api
        self._handle = handle
        self._capture = capture
        self._stream = stream
        self._job = job
        self._file_descriptor: int | None = None

    def run(self) -> None:
        import msvcrt

        try:
            self._file_descriptor = msvcrt.open_osfhandle(
                int(self._handle),
                os.O_RDONLY,
            )

        except OSError:
            return

        try:
            while True:
                try:
                    chunk = os.read(self._file_descriptor, READ_CHUNK_BYTES)

                except OSError:
                    break

                if not chunk:
                    break

                if not self._capture.add(self._stream, chunk):
                    self._api.kernel32.TerminateJobObject(self._job, 1)
                    break

        finally:
            try:
                os.close(self._file_descriptor)

            except OSError:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_limits(limits: SandboxLimits) -> None:
    if limits.timeout_seconds <= 0:
        raise SandboxUnavailableError(
            "The sandbox timeout must be greater than zero."
        )

    if limits.cpu_seconds <= 0:
        raise SandboxUnavailableError(
            "The sandbox CPU limit must be greater than zero."
        )

    if limits.memory_bytes < MIN_MEMORY_BYTES:
        raise SandboxUnavailableError(
            "The sandbox memory limit is below the supported minimum."
        )

    if limits.max_output_bytes <= 0:
        raise SandboxUnavailableError(
            "The sandbox output limit must be greater than zero."
        )


def _create_workspace(parent: str | None) -> str:
    try:
        if parent:
            resolved = os.path.realpath(parent)

            if not os.path.isdir(resolved):
                raise SandboxUnavailableError(
                    "The configured sandbox workspace directory is invalid."
                )

            parent = resolved

        return tempfile.mkdtemp(prefix="asta_py_", dir=parent)

    except OSError as error:
        raise SandboxUnavailableError(
            "The sandbox workspace could not be created."
        ) from error


def _exit_code(api: _WindowsApi, process: int) -> int | None:
    value = DWORD()

    if not api.kernel32.GetExitCodeProcess(process, ctypes.byref(value)):
        return None

    return int(value.value)


def _close_handle_quietly(api: _WindowsApi, handle: Any) -> None:
    if not handle:
        return

    try:
        api.kernel32.CloseHandle(handle)

    except OSError:
        pass


__all__ = [
    "IS_WINDOWS",
    "SandboxExecutionError",
    "SandboxLimits",
    "SandboxResult",
    "SandboxRuntimeStatus",
    "SandboxUnavailableError",
    "run_code",
    "runtime_status",
    "select_interpreter",
]
