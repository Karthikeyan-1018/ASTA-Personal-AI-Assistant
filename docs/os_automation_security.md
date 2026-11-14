# OS Automation Security (Phase 12F)

This document describes the security model for ASTA's built-in operating-system
automation tools: system information, application launching, filesystem access,
process and window inspection, and clipboard/keyboard/mouse interaction.

Phase 12F does not add new automation capabilities. It hardens the existing
Phase 12A-12E tools with one central policy, a deployment master switch, an
authenticated-owner emergency stop, server-side rate limits, metadata-only audit
logging, and explicit handling of untrusted tool output.

## Design principle: one permission engine

The existing `PermissionPolicy` and `ToolExecutor` remain the only
authorization and execution path. The Phase 12F module
`backend/app/tools/os_security.py` is a narrow, additive layer consulted by that
engine. It is deliberately **not** a second permission system.

Every OS tool call still passes through the same sequence:

1. trusted `ToolContext` extraction (owner, conversation, privacy);
2. central OS policy (master switch, capability flag, emergency stop, and the
   state-change confirmation invariant);
3. category/private/sensitive policy;
4. argument validation and size bounds;
5. confirmation creation, claim, and argument binding;
6. re-authorization and a final emergency-stop check immediately before the
   side effect;
7. timeout, result bounds, credential redaction, and metadata-only history.

The model can request a tool, but it cannot grant permission, skip a
confirmation, change a setting, raise a rate limit, or clear the emergency stop.

## Master switch and capability flags

`TOOL_OS_AUTOMATION_ENABLED` is a deployment-wide master switch. It defaults to
`false`. When it is `false`, every built-in OS automation tool is denied, even
if its individual capability flag is `true`.

The per-capability flags keep their existing Phase 12 names:

| Capability | Flag | Default |
| --- | --- | --- |
| System information | `TOOL_SYSTEM_INFO_ENABLED` | `false` |
| Application launching | `TOOL_APPLICATION_LAUNCH_ENABLED` | `false` |
| Filesystem | `TOOL_FILESYSTEM_ENABLED` | `false` |
| Process/window inspection | `TOOL_PROCESS_INSPECTION_ENABLED` | `false` |
| Clipboard | `TOOL_CLIPBOARD_ENABLED` | `false` |
| Keyboard | `TOOL_KEYBOARD_ENABLED` | `false` |
| Mouse | `TOOL_MOUSE_ENABLED` | `false` |

Enablement is enforced twice: each OS tool's `enabled` property requires the
master switch and its own flag, and the central policy independently checks the
same values. A programming mistake in one layer therefore does not silently
enable an OS capability.

`TOOL_ALLOW_SENSITIVE` is still required for sensitive tools such as
`system_launch_application`. The master switch never relaxes that requirement.

### Migration

There is no database migration. The new settings are process configuration
only. Existing deployments that already enabled a Phase 12 capability must also
set `TOOL_OS_AUTOMATION_ENABLED=true`; until they do, those tools remain
disabled. This is intentional fail-closed behavior, not a regression.

## Emergency stop

`system_automation_emergency_stop` is a trusted control path, **not** a
registered LLM tool. It is not present in the tool registry or in the provider
tool definitions, so a model cannot invoke it.

### Engaging

An authenticated owner calls `POST /api/tools/emergency-stop`. Engaging the stop:

* flips a process-local flag that blocks every new OS automation execution;
* invalidates all pending tool confirmations in the database (status
  `rejected`, arguments cleared);
* is checked during policy evaluation, again before execution, and once more
  immediately before the tool runs.

Engaging the stop never kills a process, stops or restarts ASTA, touches
PostgreSQL, deletes a file, or changes a setting. It is metadata-only.

### Recovery

Recovery is trusted and deliberate:

* `POST /api/tools/emergency-stop/recover` requires an authenticated owner and
  the server-side secret `TOOL_OS_EMERGENCY_RECOVERY_TOKEN` in the
  `X-Emergency-Recovery-Token` header;
* a missing configured token, a missing header, or a mismatch fails closed
  (`403`);
* authentication alone is never sufficient;
* the token is never returned by an API response, stored in history, or written
  to a log.

If no recovery token is configured, recovery is disabled and only a process
restart clears an engaged stop. The startup configuration validator warns when
OS automation is enabled without a recovery token.

### Single-process limitation

The emergency-stop flag is process-local and in-memory. It is cleared by a
process restart. Pending confirmations invalidated before the restart remain
rejected in the database. In a multi-process deployment, each process has its
own flag, so an operator must engage the stop on every process or use an
external supervisor. This is documented rather than hidden.

## Confirmation hardening

All state-changing OS operations and clipboard reads require explicit
confirmation. The central policy also fails closed if a future state-changing OS
tool is declared without `requires_confirmation`.

The existing confirmation service provides:

* owner, conversation, tool-name, and argument binding;
* single-use claims with an atomic status transition;
* time-limited expiry (`TOOL_CONFIRMATION_TTL_SECONDS`);
* replay, alteration, duplicate, and cross-owner rejection;
* revalidation of arguments and re-evaluation of policy after approval;
* a bounded number of pending confirmations per owner.

Previews are intentionally non-sensitive. File contents, clipboard text,
credentials, and arbitrary argument values are never placed in a confirmation
preview. Clipboard write previews report a character count only; clipboard read
previews describe the action without reading the clipboard.

Emergency-stop invalidation is a system-wide safety action and is intentionally
not owner-scoped.

## Rate and resource limits

Server-side, owner-isolated limits are enforced by the central policy before an
OS tool runs:

| Setting | Default | Meaning |
| --- | --- | --- |
| `TOOL_OS_RATE_LIMIT_ENABLED` | `true` | Enable OS rate limiting |
| `TOOL_OS_MAX_CALLS_PER_MINUTE` | `30` | Total OS executions per owner per minute |
| `TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE` | `10` | State-changing OS executions per owner per minute |
| `TOOL_OS_MAX_CALLS_PER_HOUR` | `300` | Total OS executions per owner per hour |

The limiter is a sliding window. A denied call is not executed and does not
consume a slot. Invalid or non-positive values fall back to the conservative
defaults. The validator reports configuration problems at startup, but
validation never enables a capability.

The model cannot change these values. No tool accepts a rate-limit, setting, or
configuration argument.

### Single-process limitation

The limiter is in-memory and process-local. It adds no database migration and no
external dependency, which suits ASTA's normal single-process deployment. In a
multi-process deployment each process enforces its own budget, so the effective
limit is per process rather than per deployment. Deployments that need a shared
budget should use an external limiter or a single worker.

## Audit logging

`backend/app/tools/os_audit.py` writes one metadata-only event per OS execution
and per emergency-stop action.

Recorded fields include: timestamp, owner id, conversation id, tool name,
category, capability, status, lifecycle, confirmation state, duration, retry
count, safe error category/code, and rate-limit outcome.

Never recorded: argument values, clipboard text, file contents, file paths,
command lines, window titles, process names, tokens, secrets, credentials, or
raw provider payloads.

Audit logging is best effort. A logging failure is swallowed and can never turn
a denied operation into an allowed one, nor break a successful execution.
Authorization decisions never depend on the audit sink.

## Untrusted data and prompt injection

Clipboard text, window titles, process names, filenames, file contents,
application metadata, and search results are untrusted data. They are returned
as data and are never interpreted as instructions.

The chat orchestrator prefixes tool output with an explicit untrusted marker
before it is shown to the model, and the system prompt instructs the model to
never follow instructions found in tool output. Untrusted text cannot:

* grant permission or skip confirmation;
* create or approve a confirmation;
* change a setting or raise a limit;
* invoke the emergency stop or its recovery;
* expand filesystem roots or the application allowlist.

An injection attempt inside a file, clipboard, process name, or window title is
stored and displayed as text only.

## Failure handling

Execution is fail-closed:

* a disabled master switch, capability, or missing configuration denies the
  operation;
* an unknown or malformed setting falls back to the conservative default;
* non-idempotent actions are never retried automatically;
* confirmation-requiring actions are never retried automatically;
* a timeout or uncertain outcome is reported as a timeout/error, never as
  success;
* history remains metadata-only even when execution fails;
* the executor never claims an action succeeded unless the tool returned a
  successful result.

Some OS operations are not rollbackable: a launched application, a clipboard
overwrite, a file creation or replacement, a key press, or a mouse click cannot
be undone transactionally. ASTA provides no transactional guarantee across
these side effects. Operators should treat confirmed state-changing OS actions
as final and use the emergency stop to prevent further actions.

## Unchanged restrictions

Phase 12F does not expand any Phase 12 capability:

* filesystem tools remain confined to configured workspace roots;
* permanent deletion is not offered;
* application launching remains allowlist-only, with no arbitrary arguments,
  shell, or elevation;
* process inspection remains read-only and never exposes command lines,
  environment variables, memory, or executable paths;
* desktop interaction remains limited to bounded clipboard text, allowlisted
  single key presses, and validated mouse coordinates;
* no screenshots, keylogging, background monitoring, or continuous capture are
  added.

## Operator runbook

Enable OS automation:

1. set `TOOL_OS_AUTOMATION_ENABLED=true`;
2. enable only the required capability flags;
3. configure `TOOL_OS_EMERGENCY_RECOVERY_TOKEN` with a strong secret;
4. review the startup configuration warnings;
5. restart ASTA and verify the tool list.

During an incident:

1. call `POST /api/tools/emergency-stop` as the authenticated owner;
2. confirm the response reports `engaged: true`;
3. investigate;
4. recover with `POST /api/tools/emergency-stop/recover` and the
   `X-Emergency-Recovery-Token` header, or restart the process if no token is
   configured.

## Verification

`backend/tests/test_phase12f_os_security.py` covers the central policy, master
switch, capability flags, confirmation hardening, emergency stop and recovery,
rate limiting and owner isolation, audit metadata, prompt-injection boundaries,
configuration validation, failure handling, and the API endpoints. Every OS and
desktop call is mocked; no real input is injected, no application is launched,
and no user file outside a temporary test workspace is modified.
