# Process and Active-Window Inspection (Phase 12D)

Phase 12D adds five read-only tools that let ASTA answer questions about
running applications and the current desktop window:

- "Which applications are running?"
- "Is Chrome running?"
- "Show me the active window."
- "Which process is consuming the most memory?"
- "Is VS Code currently open?"

The tools are implemented with the existing `BaseTool`, `ToolRegistry`,
`ToolExecutor`, and `PermissionPolicy` architecture. There is no second
registry, no second execution path, and no new confirmation workflow. Every
call is still authorized server-side immediately before execution.

This phase is **inspection-only**. No tool can terminate, suspend, resume,
reprioritize, inject into, or debug a process, and no tool can close a window
or send keyboard or mouse input.

## Tools

| Tool | Category | Confirmation | Arguments | Purpose |
| --- | --- | --- | --- | --- |
| `system_process_list` | read-only | none | optional `name_filter`, optional `limit` | List running processes with pid and image name. |
| `system_process_info` | read-only | none | `pid` | Inspect one process: name, status, thread count, CPU time/percent, working-set memory. |
| `system_application_status` | read-only | none | `application` (alias) | Check whether an approved application is running and return bounded matching pids. |
| `system_active_window` | read-only | none | none | Return the foreground window title, owner pid, and process image name. |
| `system_resource_summary` | read-only | none | optional `limit` | Return the top CPU- and memory-consuming processes. |

All five tools declare:

```python
category = ToolCategory.READ_ONLY
requires_confirmation = False
allowed_in_private = False
provider_label = "process_inspection"
idempotent = True
```

They are disabled unless `TOOL_PROCESS_INSPECTION_ENABLED=true`, and the
general tool-calling path (`TOOL_CALLING_ENABLED`) must also be enabled for the
model to see them. They are never `SENSITIVE`, so `TOOL_ALLOW_SENSITIVE` is not
consulted. Private requests are denied by the central permission policy.

## How process inspection works

Only the Python standard library is used. No `psutil` or other third-party
process library is required.

### Enumeration

`system_process_list`, `system_application_status`, and
`system_resource_summary` start from a read-only Toolhelp32 snapshot:

- `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)`
- `Process32FirstW` / `Process32NextW`
- `CloseHandle`

Only the process id, image base name, and thread count are read. The snapshot
is bounded to 2,000 entries so a hostile or unusual machine cannot produce an
unbounded scan. Command lines, executable paths, owners, environment blocks,
and process memory are never requested.

### Per-process measurements

`system_process_info` and `system_resource_summary` open each selected process
with the least-privilege access right
`PROCESS_QUERY_LIMITED_INFORMATION` and read:

- `GetProcessTimes` for kernel and user CPU time (converted from 100 ns units);
- `GetProcessMemoryInfo` (`psapi`) for the working-set size;
- `GetExitCodeProcess` for a running/exited indication.

The handle is closed immediately. No process right that permits modification
is requested. `PROCESS_TERMINATE`, `PROCESS_VM_WRITE`,
`PROCESS_VM_OPERATION`, and `PROCESS_ALL_ACCESS` are never used.

### CPU percentage

CPU percentage is a short, bounded sample rather than a lifetime average:

1. read CPU time for every selected process;
2. wait `TOOL_PROCESS_CPU_SAMPLE_MS` (default 100 ms, maximum 1,000 ms);
3. read CPU time again;
4. divide the delta by the elapsed wall time and the logical processor count,
   then clamp to 0–100 and round to one decimal.

If the sample interval is zero, or either reading is unavailable, `percent` is
reported as `null` instead of being invented. The collection runs in a worker
thread so the event loop stays responsive during the sample.

## How active-window inspection works

`system_active_window` uses three read-only `user32` calls:

- `GetForegroundWindow` for the current foreground window handle;
- `GetWindowTextLengthW` and `GetWindowTextW` for the title, read into a
  bounded buffer;
- `GetWindowThreadProcessId` for the owning process id.

It then resolves the owning process name from the same bounded Toolhelp32
snapshot. It does not capture a screenshot, read window contents, enumerate
other windows, record keystrokes, or interact with the window.

The title is **untrusted data**. It is collapsed to a single line, stripped of
control characters, bounded by `TOOL_PROCESS_MAX_TITLE_CHARS`, and returned
with `"untrusted": true`. The model must treat it as data, never as an
instruction. If the window has no title, `title` is `null` and
`title_present` is `false`.

## Process privacy and filtering

Process lists can contain sensitive information. The tools therefore:

- omit core operating-system processes by name and pid before returning or
  measuring them (`System`, `System Idle Process`, `Registry`,
  `Memory Compression`, `Secure System`, `smss`, `csrss`, `wininit`,
  `winlogon`, `services`, `lsass`, `lsaiso`, and pids 0 and 4);
- refuse to inspect a protected process directly with a permission error;
- never return command-line arguments, environment variables, process memory,
  usernames, personal profile paths, or executable paths;
- bound process names and window titles, collapse whitespace, and remove
  control characters so an untrusted name or title cannot inject extra lines
  into the result;
- bound the number of returned entries and matching pids;
- report access-denied and disappeared processes distinctly from "not running".

### Access denied versus not running

`system_process_info` distinguishes these outcomes:

| `status` | Meaning |
| --- | --- |
| `running` | The process is present and measurable. |
| `not_running` | No process with that pid exists. |
| `process_exited` | The process disappeared between the snapshot and the measurement. |
| `access_denied` | The process exists but its handle could not be opened; it may still be running. |
| `unknown` | The process is present but the status call did not return a value. |

An inaccessible process is never reported as stopped. CPU and memory payloads
use `{"available": false, "reason": ...}` with `access_denied`,
`process_not_found`, `process_exited`, or `unavailable` as appropriate.

## Application allowlist integration

`system_application_status` reuses the Phase 12B server-side allowlist
(`TOOL_APPLICATION_ALLOWLIST`). There is no second application registry.

- The model supplies only an application alias, for example `vscode`.
- The alias is normalized exactly like Phase 12B (case, spaces, underscores,
  hyphens, and punctuation are ignored).
- Path separators, drive syntax, `=`, `..`, and control characters are
  rejected, so an executable path cannot be smuggled through the alias field.
- The alias is resolved at proposal time and again at execution time.
- The configured executable's base name is compared, case-insensitively, with
  the image names in the process snapshot.
- Only the display name, alias, running flag, match count, bounded pids, and a
  truncation flag are returned. Nothing is launched.

If the allowlist is empty or contains no valid executable, the tool reports
itself as disabled and the model cannot call it.

## Result shapes

All results are JSON objects with bounded fields. Examples are abbreviated.

`system_process_list`:

```json
{
  "available": true,
  "count": 2,
  "total_matched": 2,
  "truncated": false,
  "filter": "chrome",
  "processes": [
    {"pid": 1234, "name": "chrome.exe"},
    {"pid": 5678, "name": "chrome.exe"}
  ]
}
```

`system_process_info`:

```json
{
  "pid": 1234,
  "exists": true,
  "accessible": true,
  "name": "chrome.exe",
  "status": "running",
  "thread_count": 32,
  "cpu": {"available": true, "time_seconds": 12.5, "percent": 1.2},
  "memory": {"available": true, "bytes": 104857600, "mib": 100.0}
}
```

`system_application_status`:

```json
{
  "application": "Google Chrome",
  "alias": "chrome",
  "running": true,
  "match_count": 2,
  "pids": [1234, 5678],
  "truncated": false,
  "match_basis": "executable_name"
}
```

`system_active_window`:

```json
{
  "available": true,
  "reason": null,
  "title": "Report - Notepad",
  "title_present": true,
  "title_truncated": false,
  "pid": 4321,
  "process_name": "notepad.exe",
  "untrusted": true
}
```

`system_resource_summary`:

```json
{
  "available": true,
  "count": 2,
  "total_considered": 120,
  "truncated": true,
  "cpu_sample_ms": 100,
  "processes": [
    {
      "pid": 1234,
      "name": "chrome.exe",
      "thread_count": 32,
      "cpu": {"available": true, "time_seconds": 90.0, "percent": 12.5},
      "memory": {"available": true, "bytes": 734003200, "mib": 700.0}
    }
  ]
}
```

## Configuration

```env
# Process and active-window inspection (Phase 12D). Disabled by default.
TOOL_PROCESS_INSPECTION_ENABLED=false

# Maximum entries returned by process list and resource summary (1-100).
TOOL_PROCESS_MAX_RESULTS=25

# Maximum matching pids returned by application status (1-100).
TOOL_PROCESS_MAX_MATCHES=20

# Maximum characters kept for a process name (16-260).
TOOL_PROCESS_MAX_NAME_CHARS=128

# Maximum characters kept for a window title (32-1024).
TOOL_PROCESS_MAX_TITLE_CHARS=300

# CPU sampling interval in milliseconds (0-1000). 0 disables percent.
TOOL_PROCESS_CPU_SAMPLE_MS=100
```

Out-of-range environment values are clamped into the ranges above, so a bad
setting cannot create an unbounded result. No new dependency, database table,
or migration is required.

## Enabling and testing

1. Enable tool calling in the deployment (`TOOL_CALLING_ENABLED=true`).
2. Set `TOOL_PROCESS_INSPECTION_ENABLED=true`.
3. For `system_application_status`, configure at least one valid
   `TOOL_APPLICATION_ALLOWLIST` entry.
4. Restart the backend and confirm the tools appear in the model's tool list.

Focused tests mock every Windows API and never inspect or modify a real
process:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12d_process_inspection -v
```

Related regression suites:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12a_system_info tests.test_phase12b_application_launcher tests.test_phase12c_file_operations -v
```

## Unsupported platforms

Process and window inspection is implemented for Windows only. On any other
platform:

- every Phase 12D tool reports itself as disabled;
- the permission policy denies execution as `disabled`;
- a direct internal call fails closed with a configuration error.

No fallback shell command or platform-specific script is attempted.

## Security properties

- Read-only: no process is terminated, suspended, resumed, reprioritized,
  injected, debugged, or modified, and no window is closed.
- No input or capture: no keyboard/mouse input is generated, no screenshot or
  window content is captured, and no keystroke is recorded.
- No shell: no PowerShell, CMD, shell, `subprocess`, or arbitrary script is
  used.
- Least privilege: only `PROCESS_QUERY_LIMITED_INFORMATION` is requested.
- No administrator rights are requested, and no privilege is adjusted.
- No sensitive process data: command lines, environment variables, memory
  contents, owners, profile paths, and executable paths are never read or
  returned.
- Protected operating-system processes are omitted or refused.
- Results are bounded and structured; raw operating-system exceptions and
  tracebacks are replaced with safe messages.
- Private requests are denied, and the tools are disabled by default.

## Known limitations

- Only the foreground window is inspected. Other windows are not enumerated.
- Process matching uses the image base name, not a full path or product name.
  An application that runs under a differently named helper process may show
  as not running, and an unrelated process with the same image name may match.
- Some protected, elevated, or short-lived processes cannot be opened. They are
  reported as `access_denied` or `process_exited`, never as stopped.
- CPU percentage is a short sample and can differ from Task Manager's
  averaged or per-core display.
- Process and window titles are untrusted strings. Prompt-injection defenses
  rely on the model treating the `untrusted` flag and tool result as data.
- Process ownership is not resolved, so results cannot be attributed to a
  Windows user or session.
- UWP/Store applications and processes that expose only a package identity may
  not be matchable by executable name.
- No history is retained by the tools themselves; normal tool execution history
  still records metadata through the Phase 11 architecture.
- Deletion, termination, window control, screenshots, and input automation are
  intentionally out of scope for this phase.
