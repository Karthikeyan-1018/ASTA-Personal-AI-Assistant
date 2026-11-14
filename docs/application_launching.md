# Application Launching (Phase 12B)

Phase 12B adds one sensitive tool, `system_launch_application`, which starts a
desktop application that the administrator explicitly allowlisted. It is built
on the existing `BaseTool`, `ToolRegistry`, `ToolExecutor`, `PermissionPolicy`,
and Phase 11D/11H confirmation workflow. There is no second registry, no second
confirmation system, and no alternate execution path.

## Tool contract

| Property | Value |
| --- | --- |
| Name | `system_launch_application` |
| Category | `sensitive` |
| Confirmation | required for every launch |
| Private mode | denied |
| Idempotent | no |
| Enabled by default | no |
| Input | `{"application": "<alias>"}` |

The input schema accepts exactly one field. Unknown fields such as `path`,
`command`, or `arguments` are rejected before execution, so the model cannot
smuggle an executable path or command-line arguments into the tool.

A successful result is bounded and contains no executable path:

```json
{
  "launched": true,
  "application": "Visual Studio Code",
  "alias": "visualstudiocode",
  "pid": 4321,
  "message": "Launched Visual Studio Code."
}
```

`pid` is `null` when the operating system does not report one. Failures are
normalized by the executor into a safe error category and never expose an
operating-system exception, traceback, or credential.

## Allowlist configuration

The allowlist is server-side configuration. The model cannot read, modify, or
extend it. Entries use `alias=absolute-path` and may be separated by commas or
newlines:

```env
TOOL_APPLICATION_LAUNCH_ENABLED=false
TOOL_ALLOW_SENSITIVE=false
TOOL_APPLICATION_ALLOWLIST=
```

Add entries only for applications actually installed on the machine:

```env
TOOL_APPLICATION_LAUNCH_ENABLED=true
TOOL_ALLOW_SENSITIVE=true
TOOL_APPLICATION_ALLOWLIST=vscode=C:\Program Files\Microsoft VS Code\Code.exe,notepad=C:\Windows\System32\notepad.exe
```

The allowlist is capped at 32 entries. The first occurrence of a normalized
alias wins; later duplicates are ignored.

### Aliases

An alias is normalized by lowercasing it and removing spaces, underscores,
hyphens, and punctuation. `Visual Studio Code`, `vs-code`, and `vscode` all
resolve to the same entry. Path-like input is rejected before normalization:

- `/` and `\` separators;
- drive syntax such as `C:`;
- `=`;
- `..`;
- control characters;
- empty or overlong values (maximum 64 characters).

This prevents a model from turning a path or traversal attempt into a
configured alias.

### Executable paths

Each configured path must pass all of these checks at proposal and again at
execution time:

- non-empty and at most 260 characters, with no NUL byte;
- absolute, not relative;
- no `..` component;
- `.exe` suffix;
- an existing regular file (not a directory);
- not a symbolic link, junction, or other reparse point;
- located on a fixed local Windows drive (`GetDriveTypeW` returns
  `DRIVE_FIXED`); UNC paths, network shares, removable volumes, CD-ROM drives,
  and RAM disks are rejected.

A malformed or unavailable entry is ignored rather than launched. If no entry
passes validation, the tool remains disabled.

## Enabling the tool

Three conditions are required:

1. `TOOL_CALLING_ENABLED=true` so the model receives tool definitions;
2. `TOOL_APPLICATION_LAUNCH_ENABLED=true`;
3. at least one valid `TOOL_APPLICATION_ALLOWLIST` entry.

Because the tool is `sensitive`, the deployment must also set
`TOOL_ALLOW_SENSITIVE=true`. Without it, the permission policy denies the
request and no confirmation is created. Restart the backend after changing
environment values.

The tool is intentionally disabled by default. Enabling it grants the model the
ability to start only the applications you listed, and only after you approve
each launch.

## Confirmation flow

1. The user asks ASTA to open an approved application.
2. The model calls `system_launch_application` with an alias.
3. `ToolExecutor` validates the arguments and resolves the alias against the
   allowlist. An unknown or unapproved alias fails here, before any
   confirmation row exists.
4. The executor creates an owner-, conversation-, and argument-bound pending
   confirmation and returns `confirmation_required`.
5. The user approves through the existing Phase 11H resume endpoint.
6. The executor re-checks the permission policy, claims the confirmation, and
   re-resolves the stored arguments before starting the process.
7. The result is recorded in tool-execution history.

Rejected, expired, replayed, altered, or already-used confirmations do not
launch anything. An approval cannot be reused for a different alias, by a
different owner, or in a different conversation.

## Launch mechanism

Launching uses the Python standard library only:

```python
subprocess.Popen(
    [executable],
    shell=False,
    close_fds=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
```

The command is a one-element list containing the validated executable path.
There is no shell interpretation, no command string, no extra argument, no
inherited standard stream, and no visible console window. The tool never runs
PowerShell, CMD, a shell, a batch file, a script, or an arbitrary command.

## Security restrictions

- Only Windows is supported. Other platforms fail closed, and the tool reports
  itself disabled.
- No administrator or elevated privileges are requested.
- No environment variable, secret, credential, file content, or process list is
  read or returned.
- No existing process is terminated or modified.
- Nothing is installed, downloaded, or written to the registry or Windows
  settings.
- The executable path is never returned to the model or persisted in execution
  history; only the alias, display name, and process id are returned.
- Argument and result sizes remain bounded by the Phase 11C executor limits.
- The allowlist cannot be changed through tool arguments or chat.

## Troubleshooting

**The tool does not appear in the model's tool list.**

- Confirm `TOOL_CALLING_ENABLED=true`.
- Confirm `TOOL_APPLICATION_LAUNCH_ENABLED=true`.
- Confirm `TOOL_ALLOW_SENSITIVE=true`.
- Confirm at least one allowlist entry is valid. Check the exact `.exe` path,
  including spaces and capitalization.
- Restart the backend after changing environment values.

**The alias is not recognized.**

- Use the alias configured before `=`, not the full application name unless it
  is also the configured alias.
- Remember that case, spaces, underscores, hyphens, and punctuation are
  ignored, so `VS Code` and `vscode` are equivalent.
- Do not pass a path. Path-like values are rejected by design.

**The configured application is rejected as unavailable.**

- The file may be missing, moved, or not an `.exe`.
- The path may point to a directory, a shortcut, a symlink, or a junction.
- The drive may be removable, a network share, or otherwise not a fixed local
  drive.
- The path may contain `..` or be relative.

**The confirmation was approved but nothing launched.**

- The confirmation may have expired
  (`TOOL_CONFIRMATION_TTL_SECONDS`) or already been used.
- The allowlist may have changed between proposal and approval; execution
  re-resolves and fails closed.
- The launch itself may have failed. Inspect the normalized execution result
  and the backend log; raw operating-system errors are intentionally not
  returned.

**The application opens but ASTA reports a failure.**

- The process started but the operating system did not report a process id.
  This is reported as `pid: null`, not as a failure.
- If the application requires elevation, ASTA will not request it. Launch it
  manually or configure a non-elevated entry.

## Tests

Focused tests mock every process launch and use empty temporary `.exe` files as
path fixtures. No real application is started.

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12b_application_launcher -v
```

The suite covers alias normalization, allowlist parsing and bounds, executable
path validation (missing, directory, relative, UNC, traversal, non-`.exe`,
symlink, reparse point, non-fixed drive), unknown and unapproved application
rejection, confirmation required/rejected/expired/replayed/altered, successful
and failed mocked launches, unsupported operating systems, arbitrary argument
and path rejection, private-mode denial, permission policy integration, and
executor integration.

## Limitations

- Windows only. There is no POSIX launcher.
- The tool launches an executable with no arguments. Applications that require
  a document path, profile, or command-line flag cannot be started through this
  tool.
- Each allowlist entry is a single executable. Launcher shortcuts (`.lnk`),
  scripts, batch files, and Microsoft Store application URIs are not supported.
- The tool does not wait for the application to exit, capture its output, or
  manage its lifecycle.
- There is no per-application rate limit beyond the Phase 11 tool-call limits
  and the single-use confirmation requirement.
- A launched application runs with the same user privileges as the backend. Do
  not allowlist applications that perform destructive actions without a
  separate confirmation of their own.
