# System Information Tools (Phase 12A)

Phase 12A adds seven read-only tools that answer basic questions about the
machine running ASTA. They are implemented with the existing `BaseTool`,
`ToolRegistry`, `ToolExecutor`, and `PermissionPolicy` architecture. No second
registry and no alternate execution path exists.

## Tools

| Tool | Category | Confirmation | Arguments | Purpose |
| --- | --- | --- | --- | --- |
| `system_os_info` | read-only | none | none | OS name, release, version, Windows build, machine type, architecture, platform string. |
| `system_hostname` | read-only | none | none | Local computer hostname. |
| `system_cpu_info` | read-only | none | none | CPU model text, logical processors, physical cores when available, architecture. |
| `system_memory_info` | read-only | none | none | Total, available, and used physical RAM, plus usage percentage. |
| `system_disk_info` | read-only | none | optional `drive` | Total, used, and available space for approved local drives. |
| `system_python_info` | read-only | none | none | Python version, implementation, build, compiler, executable. |
| `system_summary` | read-only | none | none | Consolidated human-readable summary. |

All tools declare:

```python
category = ToolCategory.READ_ONLY
requires_confirmation = False
allowed_in_private = False
provider_label = "system"
idempotent = True
```

They are disabled unless `TOOL_SYSTEM_INFO_ENABLED=true`. The general
`TOOL_CALLING_ENABLED` path must also be enabled for the model to see them.
They are never `SENSITIVE`, so `TOOL_ALLOW_SENSITIVE` is not consulted.

## Collection sources

Only Python standard-library APIs are used:

- `platform` for OS, machine, architecture, and Python runtime metadata;
- `socket.gethostname()` with `platform.node()` as a local fallback;
- `os.cpu_count()` for logical processors;
- read-only `ctypes` calls to `GetLogicalProcessorInformation` for the Windows
  physical core count and `GlobalMemoryStatusEx` for Windows RAM totals;
- `os.sysconf` for RAM on POSIX hosts;
- `shutil.disk_usage()` for approved disk roots;
- `sys.executable` for the Python executable location.

No tool starts PowerShell, CMD, a shell, a subprocess, or an arbitrary script.
No tool enumerates or returns environment variables, file contents, process
lists, arbitrary registry values, services, browser history, documents, or
private application stores. The standard library's `platform` module may
consult documented OS/hardware identifiers (for example processor
architecture) read-only to produce the bounded facts above; these are not
returned as an environment dump.

## Result shapes

Missing or unavailable values are `null` and the top-level `available` flag is
`false`. Values are never guessed from a different source.

`system_os_info`:

```json
{
  "available": true,
  "system": "Windows",
  "release": "11",
  "version": "10.0.26300",
  "build": "26300",
  "machine": "AMD64",
  "architecture": "64bit",
  "platform": "Windows-11-10.0.26300-SP0"
}
```

`system_hostname`:

```json
{"available": true, "hostname": "EXAMPLE-HOST"}
```

`system_cpu_info`:

```json
{
  "available": true,
  "model": "Intel64 Family 6 Model 191 Stepping 2, GenuineIntel",
  "logical_processors": 20,
  "physical_cores": 14,
  "architecture": "AMD64"
}
```

`system_memory_info`:

```json
{
  "available": true,
  "total_bytes": 16858472448,
  "available_bytes": 4363530240,
  "used_bytes": 12494942208,
  "usage_percent": 74.1,
  "total_gib": 15.7,
  "available_gib": 4.06,
  "used_gib": 11.64
}
```

`system_disk_info`:

```json
{
  "available": true,
  "count": 1,
  "drives": [
    {
      "root": "C:\\",
      "label": "C:",
      "available": true,
      "total_bytes": 510807502848,
      "used_bytes": 416737169408,
      "available_bytes": 94070333440,
      "usage_percent": 81.6,
      "total_gib": 475.73,
      "used_gib": 388.12,
      "available_gib": 87.61
    }
  ]
}
```

If a drive cannot be queried, its entry is:

```json
{
  "root": "C:\\",
  "label": "C:",
  "available": false,
  "error": "Disk usage is unavailable."
}
```

`system_python_info`:

```json
{
  "available": true,
  "version": "3.12.10",
  "implementation": "CPython",
  "executable": "C:\\Asta\\venv\\Scripts\\python.exe",
  "compiler": "MSC v.1943 64 bit (AMD64)",
  "build": "tags/v3.12.10:0cc8128 Apr 8 2025 12:21:36"
}
```

`system_summary` returns the same sections under `sections` and a bounded
`summary` string:

```text
OS: Windows 11 build 26300 (64bit)
Hostname: EXAMPLE-HOST
CPU: Intel64 Family 6 Model 191 Stepping 2, GenuineIntel (20 logical processors, 14 physical cores)
Memory: 15.7 GiB total, 4.07 GiB available, 74.1% used
Disk C: 475.73 GiB total, 87.61 GiB available, 81.6% used
Python: CPython 3.12.10 at C:\Asta\venv\Scripts\python.exe
```

## Disk allowlist

`TOOL_SYSTEM_INFO_DISK_ROOTS` is a comma-separated list of absolute local
roots. When it is empty, only the drive containing the running interpreter is
reported. That is normally the system drive.

Rules:

- relative paths, missing paths, and non-directories are ignored;
- UNC paths (`\\server\share`) are rejected on Windows;
- the drive for every configured Windows path is checked with
  `GetDriveTypeW`; only `DRIVE_FIXED` is accepted, so mapped network drives and
  removable volumes are rejected even when a subdirectory is configured;- duplicate roots are removed after case-normalization;
- at most `TOOL_SYSTEM_INFO_MAX_DISKS` roots are reported (hard maximum 8);
- volumes are never enumerated. A drive appears only because it was explicitly
  configured or derived as the system drive.

The optional `drive` argument of `system_disk_info` accepts a selector such as
`C:`. It must match one of the approved roots exactly; otherwise the call is
rejected with a validation error. If the operator approves a subdirectory such
as `C:\Data`, the `C:` selector does not match it.

## Error mapping

| Condition | Behavior |
| --- | --- |
| Unknown argument or malformed selector | `ToolValidationError`; executor reports `validation`. |
| Selector not in the approved allowlist | `ToolValidationError`; executor reports `validation`. |
| No approved disk root | The disk tool is disabled by configuration; a direct collection call raises `ToolConfigurationError`. `system_summary` reports the disk section as unavailable. |
| One disk query raises `PermissionError`/`OSError` | That drive is marked `available: false`; other drives still run. |
| OS/CPU/memory/hostname source fails | The affected fields are `null` and `available: false`; the tool still returns a successful bounded result. |
| Unexpected collector exception | `ToolExecutionError("System information is unavailable.")`; no traceback or exception text is exposed. |
| Tool disabled | `ToolDisabledError`; executor reports `denied` / `disabled`. |
| Private request | `ToolPrivateRequestError`; executor reports `denied` / `permission`. |

The executor also applies its normal result-size limit and credential
redaction to every output.

## Configuration

```env
# System information tools (Phase 12A). Disabled by default.
TOOL_SYSTEM_INFO_ENABLED=false

# Comma-separated local disk roots. Empty uses the system drive.
# Example: C:\,D:\
TOOL_SYSTEM_INFO_DISK_ROOTS=

# Hard cap on reported drives (maximum 8).
TOOL_SYSTEM_INFO_MAX_DISKS=8
```

The general tool path must also be enabled:

```env
TOOL_CALLING_ENABLED=true
```

## Bounds

- every text field is limited to 200 characters; the executable path is
  limited to 260 characters;
- the consolidated summary is limited to 2000 characters;
- at most 8 drives are reported;
- the executor enforces `TOOL_MAX_RESULT_BYTES` on the serialized result;
- CPU, memory, and disk values are integers or rounded floats, never
  unbounded strings.

## Security properties

- read-only: no file, registry, service, process, setting, or configuration is
  modified;
- no shell, PowerShell, CMD, subprocess, or arbitrary script execution;
- no environment variable, secret, credential, token, file content, browser
  history, document, or private application data is enumerated or returned;
  only documented OS/hardware identifiers used internally by the standard
  library may be consulted;
- no administrator privilege is requested;
- no network lookup is performed for the hostname;
- no arbitrary volume enumeration; only approved roots are queried;
- permission policy, private-mode denial, executor history, result limits, and
  redaction are inherited from the Phase 11 engine;
- tools are disabled by default and never exposed in private mode.

## Tests

Focused tests live in `backend/tests/test_phase12a_system_info.py`. They cover:

- successful collection with deterministic mocked OS values;
- missing/unavailable OS, hostname, CPU, memory, and disk data;
- permission errors on disk queries;
- invalid arguments and unapproved drive selectors;
- text, drive-count, summary, and result-size bounds;
- secret/environment protection and safe error messages;
- registration, builtin ordering, LLM discovery, and provider schemas;
- executor integration, disabled-tool denial, and private-mode denial;
- permission-policy decisions;
- a small live smoke section that checks structure without asserting
  machine-specific values.

Run:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12a_system_info -v
```

## Known limitations

- CPU model text comes from `platform.processor()`/`platform.uname()`. On some
  Windows builds this is a processor family identifier rather than the retail
  marketing name. The tool does not read the registry to obtain a marketing
  name.
- Physical core count is available on Windows through a read-only kernel query.
  On other platforms it is reported as `null` rather than guessed from the
  logical count.
- Memory and physical-core queries can be unavailable in unusual sandboxes;
  the tools report that explicitly instead of failing the whole call.
- Disk usage reflects the filesystem's reported totals and can differ slightly
  from Explorer because of reserved blocks and filesystem metadata.
- No database migration is required for Phase 12A.
