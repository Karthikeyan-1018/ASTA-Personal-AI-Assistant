# ASTA System Monitor and Automation Report

**Date:** 2026-11-19  
**Verdict: PARTIAL PASS** — the live monitor implementation and focused regression checks are in place. The deployment's existing safety switches are all disabled, so the dashboard will clearly report policy-disabled sections until an operator enables the required read-only capabilities. No switches were changed in `.env`.

## 1. Repository findings

- Phase 12A already provides seven read-only system information tools: OS, hostname, CPU identity/counts, memory capacity/use, approved local disk capacity, Python runtime, and a consolidated summary.
- Phase 12D already provides five read-only process/window tools: process list, process detail, approved-app status, active window, and top CPU/RAM resource summary. It uses Windows Toolhelp and limited-query process handles. Command lines, environment variables, user names, and executable paths are not returned.
- Phase 12B already supports launching applications only by server-configured alias and executable allowlist.
- Phase 12C filesystem tools are confined to configured workspace roots. File-changing operations require confirmation. Permanent deletion is not offered.
- Phase 12E clipboard, keyboard, and mouse tools are bounded, separately flagged, and confirmed where required.
- Phase 12F provides the deployment master switch, capability gates, confirmation enforcement, emergency stop, rate limits, metadata-only audit, and fail-closed behavior.
- The former System Monitor page showed real RAM/disk capacity and a small process sample, but had no host CPU usage, disk activity, network rates, GPU telemetry, or uptime. It polled every 20 seconds and only charted session RAM/disk/process samples.
- Process termination is not implemented. Arbitrary shell execution, arbitrary executable paths, arbitrary websites, and unrestricted file access are not supported.

## 2. Implemented

- Added authenticated `GET /api/system-monitor/snapshot`, with the existing authentication and CSRF dependencies. It is read-only and returns a validated top-level response; every metric section has an explicit availability flag/reason.
- The endpoint checks the existing `PermissionPolicy` for the System Information and Process Inspection capability tools before collecting those sections. It does not route monitoring through the LLM or create another tool executor.
- Added `psutil==7.2.2` for real CPU, physical-memory, disk-I/O, and per-interface network counters. CPU, disk, and network rates are computed from counter deltas and elapsed time. Their first sample is null because there is no earlier counter to compare.
- Disk capacity continues to use the existing allowlisted local roots. When none are configured, the existing collector reports only the application/system drive; it does not enumerate all volumes.
- GPU telemetry uses a fixed `nvidia-smi` query with no shell, a two-second timeout, and explicit unavailable values when the command or a metric is unsupported.
- The actual processor model is read from the Windows processor identification key, with the existing platform text as fallback. OS, hostname, architecture, boot time, and uptime use local host sources.
- Process data reuses the existing read-only Windows resource summary and is cached for 10 seconds. The dashboard can search/sort the returned bounded process sample by name/PID, CPU, and memory. Paths and command-line arguments remain hidden.
- The dashboard refreshes at the backend-configured cadence (default two seconds, clamped to 1–10 seconds), stops polling while the page is hidden, and refreshes provider/tool counters less often. History contains only backend measurements received during the current page session; it does not invent earlier data.
- No process termination control was added. Existing automation tools and their confirmation flows were not weakened or broadened.

## 3. Capability and permission matrix

| Capability | Required switches/configuration | Permission and confirmation | Current `.env` state |
|---|---|---|---|
| System information and dashboard host metrics | `TOOL_OS_AUTOMATION_ENABLED=true` and `TOOL_SYSTEM_INFO_ENABLED=true` | Authenticated owner; read-only; no confirmation; policy and emergency-stop checks | Disabled |
| Process inspection | `TOOL_OS_AUTOMATION_ENABLED=true` and `TOOL_PROCESS_INSPECTION_ENABLED=true` | Authenticated owner; read-only; no confirmation; Windows only | Disabled |
| Application launch | Master switch, `TOOL_APPLICATION_LAUNCH_ENABLED=true`, `TOOL_ALLOW_SENSITIVE=true`, and an explicit `TOOL_APPLICATION_ALLOWLIST` entry | Sensitive action; explicit per-call confirmation; alias only, no arbitrary command/arguments | Disabled by capability flag |
| File modification | Master switch, `TOOL_FILESYSTEM_ENABLED=true`, and configured `TOOL_WORKSPACE_ROOTS` | Restricted to approved roots; state-changing operations require confirmation; permanent delete is unavailable | Disabled; roots not enabled |
| Process termination | No supporting capability or endpoint exists | Not available; no termination action can be approved or run | Not implemented |
| Clipboard / keyboard / mouse | Master switch plus each relevant capability flag | Bounded actions and explicit confirmation where defined; private-mode restrictions apply | Disabled |

`TOOL_CALLING_ENABLED` is also false in the current `.env`, so natural-language tool calls are not currently enabled. Turning that global setting on may expose other eligible tool categories; audit those separately. The master OS switch is currently false and all inspected per-capability switches are false. The emergency recovery token is not configured. **I left all `.env` values unchanged.** Before enabling the master switch, configure a strong emergency recovery token and enable only the capabilities you intend to use.

Suggested minimum for the dashboard only (after setting an emergency recovery token):

```dotenv
TOOL_OS_AUTOMATION_ENABLED=true
TOOL_SYSTEM_INFO_ENABLED=true
TOOL_PROCESS_INSPECTION_ENABLED=true
SYSTEM_MONITOR_REFRESH_SECONDS=2
```

These are recommendations only; this change did not write them into `.env`.

## 4. Live Windows sample and performance

The read-only collector was run directly against the current Windows host. Values below are observations from that run, not hard-coded dashboard defaults:

- CPU: Intel Core i5-13500HX, 14 physical cores, 20 logical processors; CPU usage computed from Windows CPU-time counter deltas (one observed sample was 19.6%); frequency was reported by the local telemetry source.
- RAM: 16.86 GB physical total (about 15.7 GiB); one observed sample reported 75.7% used.
- C: drive: 510.8 GB total (about 475.7 GiB), 69.7% used. Other volumes are intentionally omitted unless included in the existing local-drive allowlist.
- Network: Wi-Fi was up; throughput was calculated from per-interface byte-counter deltas. One observed interval reported about 142 B/s upload and 274 B/s download. No packet contents or addresses are collected.
- Disk activity: `psutil.disk_io_counters()` was unsupported in this runtime, so disk read/write throughput is displayed as “Not supported”; capacity remains available. The dashboard does not substitute disk capacity for disk activity.
- GPU, cross-checked with the same direct `nvidia-smi` command: NVIDIA GeForce RTX 3050 6GB Laptop GPU, 6144 MiB total VRAM; the collector observed about 946 MiB used, 2% utilization, 42 °C, and 6.63 W in one sample. Values vary with load.
- Uptime and boot time came from the local OS boot-time source.
- Collector latency including a first bounded process sample was about 551 ms; a subsequent call using the 10-second process cache took about 84 ms. This is a local smoke measurement, not a controlled laptop benchmark or a promise for every deployment.

The current execution environment denied `Get-Counter` and `Get-CimInstance`, so CPU/RAM/disk/process values could not be independently compared to Task Manager/CIM here. The reported GPU model and VRAM capacity matched a direct `nvidia-smi` query; utilization, temperature, and power naturally differed between samples. The process snapshot is bounded and this environment exposed only a small visible process set; validate process visibility on the actual ASTA deployment.

## 5. Security and automation behavior

- The new route is authenticated and read-only. A missing user session receives 401; the API response schema validates section availability/reasons and refresh bounds.
- Monitoring honors the same master/capability switches and emergency stop. A disabled capability is returned as unavailable with its policy reason, not as a fabricated zero.
- Process identity and resource use are shown only from the existing Windows inspection collector. No path, command line, environment, or process memory is exposed.
- Application launch remains allowlist-only and confirmation-gated. File operations stay within configured workspace roots and destructive operations remain guarded. No unrestricted shell or process termination was added.
- There are no database migrations and no microphone/system telemetry history is persisted by this feature. Dashboard history is in-memory in the current frontend session.

## 6. Regression checks

- System information, process inspection, registry, executor, and permission suites: **252 tests passed** in the focused run.
- New System Monitor suite: **5 tests passed**, including live Windows collection/shape, interval bounds, disabled-section behavior, authentication rejection, and authenticated response validation.
- Frontend `npm run build`: passed. Existing Vite warnings remain for a large JavaScript chunk and an ineffective dynamic import.
- Frontend `npm run lint`: passed with one existing React set-state-in-effect warning in `Chat.tsx`.
- Full backend suite: attempted, reached Phase 11F filesystem isolation tests, then stopped advancing after a nested Python sandbox timeout message. I interrupted the run after several minutes with no final summary; this is inconclusive, not a pass or an assertion failure.

## 7. Limitations and verdict

**PARTIAL PASS.** Real Windows metric collection, GPU querying, process visibility, authentication, permission gating, and frontend rendering paths are implemented and focused checks pass. In the current deployment, every OS capability and tool calling are disabled, so the dashboard intentionally reports the required policy switches until configured. Independent Task Manager comparisons were blocked by the restricted execution environment. Process termination remains intentionally unsupported, and the process list is bounded to the existing tool's configured sample.
