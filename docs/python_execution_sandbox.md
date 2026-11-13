# Python execution sandbox

Phase 11G adds the `python_execute` tool. It executes submitted Python source
as untrusted code and returns bounded standard output, standard error, and an
exit status. The tool is disabled by default.

This document describes the architecture, the actual isolation boundary,
configuration, limits, restrictions, error handling, platform requirements,
tests, and residual risks.

## Architecture

```
LLM tool call
    |
    v
ToolRegistry  ->  ToolExecutor  ->  permission policy
                                      |  SENSITIVE + TOOL_ALLOW_SENSITIVE
                                      |  private mode denied
                                      |  per-call confirmation bound to code
                                      v
                              PythonExecuteTool
                                      |  source-size check
                                      |  AST denylist (defence in depth)
                                      |  limits from server settings only
                                      v
                     asyncio.to_thread(python_sandbox.run_code)
                                      |
                                      v
                Windows AppContainer token + Job Object
                - fresh profile with no capabilities
                - disposable temp workspace
                - icacls grants for interpreter (RX) and workspace (M)
                - CreateProcessW(CREATE_SUSPENDED, handle list)
                - AssignProcessToJobObject, ResumeThread
                - bounded pipe readers, WaitForSingleObject
                - terminate job on timeout/output overflow
                - revoke ACLs, delete profile, delete workspace
```

There is no alternate execution path. The tool is registered in
`app/tools/definitions/__init__.py`, resolved through `ToolRegistry`, and
invoked only through `ToolExecutor`.

## Execution lifecycle

1. `PythonExecuteTool.execute` checks that the sandbox is enabled, the
   AppContainer runtime is available, network access is not requested, and the
   source is within the configured byte limit.
2. An AST pass rejects a short list of escape-oriented imports and
   `__import__` references. Syntax errors are deliberately passed through so
   the interpreter produces the real traceback.
3. Limits are derived only from server-side settings. Nothing from the LLM can
   change the timeout, memory, CPU, output, workspace, or network policy.
4. `run_code` creates a unique temporary workspace (`asta_py_*`) under the
   system temp directory or the configured parent.
5. A unique AppContainer profile (`asta-py-*`) is created. The profile SID is
   granted read/execute on the interpreter directory and modify on the
   workspace. No other ACL is changed.
6. A Job Object is created with:
   - `ActiveProcessLimit = 1`
   - per-process and per-job user CPU-time limits
   - process and job memory limits
   - `KILL_ON_JOB_CLOSE`
7. The child is created suspended with an AppContainer security capability, a
   minimal environment block, and `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`
   containing only stdin, stdout, and stderr.
8. The process is assigned to the job before it is resumed, so it cannot run
   code outside the limits.
9. Two reader threads consume stdout and stderr into a shared byte budget.
   Exceeding the budget terminates the job and marks `output_limited`.
10. `WaitForSingleObject` enforces the wall-clock timeout. On timeout the whole
    job is terminated, not just the direct child.
11. Results are decoded with replacement, bounded again by the executor, and
    returned as structured data.
12. Cleanup always runs: process/job handles are closed, ACL grants are
    revoked, the AppContainer profile is deleted, and the workspace is removed.

## Actual isolation boundary

The boundary is **Windows OS isolation**, not Python sandboxing:

- **AppContainer token.** The child runs with a per-execution AppContainer SID
  and no capabilities. Access checks apply to every filesystem object. Paths
  that were not granted (ASTA source, `.env`, user documents, drive roots)
  fail with `PermissionError`, regardless of `..`, absolute paths, junctions,
  or symlinks. The AppContainer also blocks network access unless a network
  capability is granted; this implementation grants none.
- **Job Object.** The job enforces memory and CPU limits and permits exactly
  one process. A sandbox program cannot create a child process; attempts fail
  with `WinError 1816` (`ERROR_NOT_ENOUGH_QUOTA`). Timeout and output overflow
  call `TerminateJobObject`, which terminates the entire job.
- **Handle list.** Only stdin, stdout, and stderr are inherited. ASTA database
  connections, sockets, log files, and other open handles are not exposed.
- **Environment block.** The child receives only `SystemRoot`, `WINDIR`,
  `LOCALAPPDATA` (redirected to the disposable profile), `TEMP`, `TMP`, and
  Python UTF/unbuffered flags. `PATH`, `USERPROFILE`, and every ASTA variable
  are absent.
- **Interpreter mode.** The standalone base interpreter runs with
  `-I -B -S -X utf8` and no user site-packages. A virtual-environment launcher
  is not used because it spawns a child process, which the job forbids.

The AST denylist is **not** the boundary. It rejects `ctypes`, `cffi`,
`_ctypes`, `winreg`, `_winreg`, `msvcrt`, `multiprocessing`, `importlib`,
`imp`, `runpy`, `pkgutil`, `site`, and `__import__` as defence in depth. A
determined program can bypass static filtering; it still cannot cross the
AppContainer/Job boundary. `socket` and `subprocess` are intentionally not
blocked so the OS boundary can be exercised and verified.

`PYTHON_EXECUTION_ALLOW_UNSAFE` no longer enables anything. There is no unsafe
fallback runner.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `PYTHON_SANDBOX_ENABLED` | `false` | Enables the tool when the runtime is available. |
| `PYTHON_SANDBOX_TIMEOUT_SECONDS` | `5` | Wall-clock limit. Clamped below `TOOL_EXECUTION_TIMEOUT_SECONDS`. |
| `PYTHON_SANDBOX_CPU_SECONDS` | `5` | Per-process and per-job user CPU-time limit. |
| `PYTHON_SANDBOX_MEMORY_MB` | `256` | Job and process memory limit. Values below 32 are raised to 32. |
| `PYTHON_SANDBOX_MAX_SOURCE_BYTES` | `20000` | Maximum submitted source size. |
| `PYTHON_SANDBOX_MAX_OUTPUT_BYTES` | `20000` | Combined stdout+stderr capture budget. |
| `PYTHON_SANDBOX_ALLOW_NETWORK` | `false` | Must remain false. `true` fails closed because the backend cannot grant network capabilities. |
| `PYTHON_SANDBOX_WORKSPACE_PARENT` | empty | Optional existing directory for per-run workspaces. Empty uses the system temp directory. |

Execution also requires `TOOL_ALLOW_SENSITIVE=true` and an approved
confirmation. Private-mode requests are always denied.

The deprecated `PYTHON_EXECUTION_ENABLED`, `PYTHON_EXECUTION_TIMEOUT_SECONDS`,
`PYTHON_EXECUTION_MAX_CODE_BYTES`, and `PYTHON_EXECUTION_MAX_OUTPUT_BYTES`
environment names are still read as fallbacks. They do not enable an unsafe
runner.

The effective wall timeout is:

```
min(configured_timeout, max(1, TOOL_EXECUTION_TIMEOUT_SECONDS - 2))
```

The two-second reserve lets the sandbox terminate its process tree, revoke
ACLs, and remove the workspace before the executor's own timeout fires.

## Permissions and confirmations

`PythonExecuteTool` is `SENSITIVE`, has `requires_confirmation=True`, and has
`allowed_in_private=False`. The executor:

- denies the tool when it is disabled or the sandbox runtime is unavailable;
- denies it unless `TOOL_ALLOW_SENSITIVE=true`;
- denies it for private requests;
- requires an owner-bound, unexpired, single-use confirmation bound to the
  exact tool name and the exact submitted code;
- revalidates and reauthorizes after approval;
- bounds and redacts the result before it reaches the provider.

The tool never accepts identity, permissions, limits, or settings from the
model. Its input schema contains only `code`.

## Resource limits and failures

| Condition | Behavior |
| --- | --- |
| Syntax error | Interpreter exit is non-zero; `SyntaxError` appears in bounded stderr. |
| Runtime exception | Interpreter exit is non-zero; traceback appears in bounded stderr. |
| Wall-clock timeout | `timed_out=true`; job terminated; process-tree cleanup; captured output retained. |
| CPU quota | Exit status `0xC0000044` (`STATUS_QUOTA_EXCEEDED`); `resource_limited=true`. |
| Memory limit | Allocation fails with `MemoryError`, or the process exits with `0xC000012D`; `resource_limited` is set when the exit status matches. |
| Output overflow | Job terminated; `output_limited=true`; captured bytes never exceed the budget. |
| Subprocess attempt | `OSError` / `WinError 1816` inside the sandbox; no child process exists. |
| Filesystem or network access | `PermissionError` inside the sandbox. |
| Runtime/profile/ACL/interpreter failure | `ToolConfigurationError` (`DISABLED`/configuration category). Fail closed. |
| Process start/observe failure | `ToolExecutionError` (`EXECUTION` category). |
| Source too large | `ToolSizeLimitError`. |
| Blocked import | `ToolExecutionError` from the AST defence-in-depth check. |

Successful executions are returned as a successful `ToolResult` even when the
*submitted program* exits non-zero; the data contains `exit_code`, `stdout`,
`stderr`, `timed_out`, `resource_limited`, `output_limited`, truncation flags,
`elapsed_seconds`, `sandboxed: true`, and `backend:
"windows_appcontainer_job"`. This distinguishes "the sandbox worked and the
program failed" from "the sandbox itself failed".

## Platform requirements and limitations

- **Windows 8 or later** with the AppContainer API (`userenv.dll`,
  `CreateAppContainerProfile`) and Job Objects.
- A **standalone Python 3 interpreter** discoverable through
  `sys._base_executable` or `sys.executable`. The sandbox receives the standard
  library only; ASTA's virtual-environment packages are not available.
- The ASTA process must be able to create an AppContainer profile and change
  ACLs on the interpreter directory and the workspace. A protected interpreter
  directory that cannot be granted read/execute will fail closed. Running ASTA
  as administrator is not required, but the interpreter location must permit
  the grant.
- **Non-Windows platforms are unsupported** and fail closed. Docker, podman,
  wasmtime, and similar dedicated runtimes were not available on the
  development host, so no alternative backend is implemented.
- The AppContainer backend cannot grant network access. The setting exists to
  fail closed explicitly rather than silently ignoring a request.
- Wall-clock timeout cleanup is bounded by the reserve described above.
  Asynchronous cancellation of the executor cannot interrupt a blocked worker
  thread, but the sandbox's own timeout still terminates the process tree.
- Each execution creates and removes an AppContainer profile and applies two
  ACL grants. Startup overhead is roughly one to two seconds on the development
  host.
- A hard crash of the ASTA process (for example, a debugger kill) can leave a
  stale `asta-py-*` profile or an interpreter-directory ACE. Normal execution
  and all tested failure paths clean up. Stale profiles are inert and can be
  removed with `Get-AppxPackage`-independent profile cleanup tools or by
  deleting `%LOCALAPPDATA%\Packages\asta-py-*`; stale ACEs can be removed with
  `icacls <interpreter-dir> /remove *<sid>`.
- If the workspace parent is on a filesystem or ACL configuration that does not
  permit traversal by the AppContainer SID, the child may be unable to read its
  own script and will exit non-zero. This is a fail-closed condition, not a
  boundary bypass.

## Residual risks

- **Shared kernel and Windows vulnerabilities.** AppContainer and Job Objects
  rely on the Windows kernel. A kernel or AppContainer implementation
  vulnerability could escape the boundary. Keeping Windows patched is part of
  the security model.
- **Interpreter and standard-library vulnerabilities.** The sandbox runs a real
  CPython interpreter. A memory-safety or logic vulnerability in CPython or the
  standard library could affect the child. It still runs with an AppContainer
  token and inside the job.
- **Static denylist bypass.** The AST filter can be bypassed by dynamic import
  tricks or obfuscation. It is not relied upon; the OS boundary is.
- **Resource accounting.** CPU limits use user-mode CPU time. A program can
  still consume wall-clock time waiting or sleeping until the wall-clock
  timeout; threads are not separately capped, but the process memory limit and
  the one-process job limit still apply.
- **Output semantics.** Output is capped, not interpreted. A program can emit
  misleading or adversarial text. Results are marked as tool output and should
  be treated as untrusted by the model and UI.
- **ACL side effects.** The sandbox temporarily grants the AppContainer SID
  read/execute on the interpreter directory and modify on the workspace. The
  grants are revoked in `finally`; a hard crash can leave the interpreter ACE
  as described above. The SID is unique per execution and has no capabilities.
- **Workspace parent trust.** The operator controls
  `PYTHON_SANDBOX_WORKSPACE_PARENT`. Pointing it at a sensitive directory does
  not grant the sandbox access to that directory's other files, because only
  the newly created workspace is granted, but the parent must still permit
  temporary-directory traversal for the child to start.
- **Interpreter trust.** The sandbox grants read/execute on the chosen
  interpreter directory. That installation must be trusted and should not be
  writable by an untrusted local account; a replaced interpreter binary would
  run with the sandbox token and limits but would no longer be the expected
  CPython build.
- **No network egress allowlist.** Network access is all-or-nothing and
  currently always denied. There is no supported way to permit a subset of
  destinations.

## Test coverage

`backend/tests/test_phase11g_python_sandbox.py` covers:

- disabled configuration, missing/unsupported runtime, and network fail-closed;
- source-size limits and the memory floor;
- limit derivation from server settings and timeout clamping;
- AST denylist positives and negatives, syntax-error pass-through, and the
  distinction between rejected imports and OS-enforced blocking;
- structured result fields, timeout/output flags, and error mapping;
- registry/definition metadata, sensitive opt-in, private-mode denial, and
  confirmation requirements;
- approved confirmation execution and rejection of changed code;
- on supported Windows hosts, real execution of valid code, syntax and runtime
  errors, workspace writes and cleanup, filesystem confinement (including ASTA
  source and a canary outside the workspace), environment scrubbing, network
  denial, subprocess denial, timeout process-tree termination, output limits,
  memory exhaustion, CPU quota, invalid limits/workspace parents, and
  AppContainer profile cleanup;
- platform gating without touching the OS sandbox.

All real-sandbox tests use isolated temporary directories. No personal file,
ASTA `.env` file, secret, or `agriculture_db` data is read or written.

Run the focused suite:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11g_python_sandbox -v
```

The real integration tests skip when `runtime_status().available` is false
(for example, on a non-Windows host or when the standalone interpreter is
missing).
