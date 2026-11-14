# Filesystem tools (Phase 11F + 12C)

ASTA exposes ten built-in filesystem tools. They are **disabled by default** and
only operate inside explicitly configured workspace roots. The tools never
delete files, never mutate a directory tree recursively, never execute a file,
and never run a shell command.

Phase 11F introduced the original six tools. Phase 12C adds read-only existence
checks plus approved-root folder creation, file creation/update, rename, and
move. The original six-tool tuple (`FS_TOOLS`) is unchanged; the new tools are
exported separately as `FS_12C_TOOLS` and together as `FS_ALL_TOOLS`.

## Supported operations

| Tool | Category | Confirmation | Purpose |
| --- | --- | --- | --- |
| `fs_list` | read-only | no | List files, directories, and symlinks in one directory. |
| `fs_search` | read-only | no | Find files by a name glob below one directory. |
| `fs_read` | read-only | no | Read a bounded UTF-8 text file. |
| `fs_metadata` | read-only | no | Return type, size, modified time, and suffix. |
| `fs_exists` | read-only | no | Report whether a path exists and what type it is. |
| `fs_create` | reversible | yes | Create a text file, optionally replacing an existing one. |
| `fs_update` | reversible | yes | Replace the contents of an existing text file. |
| `fs_mkdir` | reversible | yes | Create one new folder; the parent must exist. |
| `fs_rename` | reversible | yes | Rename an entry in place; never overwrites. |
| `fs_move` | reversible | yes | Move an entry to a new path; never overwrites. |

There is deliberately **no delete tool**. Permanent deletion remains out of
scope for Phase 12C.

All tools are implemented as `BaseTool` subclasses and registered through
`ToolRegistry`. Every call from chat or the API passes through `ToolExecutor`,
the central permission policy, argument validation, timeout handling, result
bounds, credential redaction, and metadata-only execution history.

The tools are denied in private mode, like every other built-in external tool.

## Configuration

```env
# Filesystem tools (Phase 11F + 12C). Disabled by default.
TOOL_FILESYSTEM_ENABLED=false

# Comma-separated absolute directories. Each root must exist and may not be a
# drive root, a Windows system directory, a user profile, a browser profile, a
# credential store, or a parent/child of one.
TOOL_WORKSPACE_ROOTS=

TOOL_FILE_MAX_READ_BYTES=200000
TOOL_FILE_MAX_WRITE_BYTES=100000
TOOL_FILE_MAX_LIST_ENTRIES=500
TOOL_FILE_MAX_SEARCH_RESULTS=100
```

The general tool-calling path must also be enabled (`TOOL_CALLING_ENABLED=true`)
before the model can discover these tools.

Example for a single workspace (use your own path, never a personal path from a
shared document):

```env
TOOL_FILESYSTEM_ENABLED=true
TOOL_WORKSPACE_ROOTS=C:\path\to\asta-workspace
```

Multiple roots are comma-separated:

```env
TOOL_WORKSPACE_ROOTS=C:\path\to\asta-workspace,D:\path\to\notes
```

A relative tool path is resolved against the **first** configured root. An
absolute tool path is accepted when it resolves inside **any** configured root.
Roots that do not exist, are files, are sensitive directories, are filesystem
roots, are the user profile or an ancestor of it, live inside or contain a
browser profile or credential store, or overlap a Windows/POSIX system
directory are ignored. If no valid root remains, the filesystem tools report
themselves as disabled.

## Security model

### Root containment

Every requested path is resolved with symlinks and junctions followed before
use. The resolved path must be inside a configured root. Drive-relative paths,
root-relative paths without a drive letter, alternate data streams, UNC and
network paths, Windows device namespaces (`\\?\`, `\\.\`), and every `..`
segment are rejected. A `..` segment is rejected even when the final resolved
path would remain inside a root.

Windows roots such as `C:\`, `C:\Windows`, `C:\Program Files`,
`C:\Program Files (x86)`, and `C:\ProgramData` are rejected. A configured root
may not be inside one of those locations and may not contain one, so a broad
root cannot expose the operating system. On POSIX systems the equivalent
system directories (`/etc`, `/usr`, `/bin`, `/boot`, `/dev`, `/proc`, `/sys`,
and related paths) are rejected.

The user profile and every ancestor of it are rejected as roots, as are
configured roots that live inside or contain a browser profile or a credential
store (Chrome, Edge, Brave, Chromium, Firefox, Windows Credentials/Vault,
`.ssh`, `.aws`, `.azure`, `.gnupg`, `.kube`, and `.password-store`).

Symlinks and junctions are resolved before containment is checked. A link
inside a root that points outside the root is rejected. Search skips symlink
and junction directories entirely, so discovery cannot traverse out of the
workspace. Writes to a symlink path are rejected. Rename and move refuse a
symbolic link, junction, or any other reparse point as either source or
destination.

### Sensitive files

The following are denied regardless of the configured root:

- `.env`, `.env.*`, and files whose suffix is `.env`;
- `credentials`, `secrets`, and names containing a clear
  `credential`/`credential(s)` or `secret`/`secret(s)` token;
- private-key files such as `id_rsa`, `id_dsa`, `id_ecdsa`, and
  `id_ed25519`, including backups with those prefixes;
- key stores and certificates such as `.pem`, `.key`, `.pfx`, `.p12`,
  `.keystore`, `.jks`, `.ppk`, `.p8`, `.gpg`, and `.asc`;
- Terraform state and variables (`.tfstate`, `.tfvars`);
- ordinary credential files such as `.netrc`, `.pgpass`, `.npmrc`, `.pypirc`,
  `.git-credentials`, `.htpasswd`, and `.vault-token`;
- browser credential stores such as `Login Data`, `Cookies`, `Local State`,
  `logins.json`, `key3.db`, and `key4.db`;
- sensitive directories such as `.ssh`, `.aws`, `.azure`, `.docker`,
  `.gnupg`, `.git`, `.kube`, and `agriculture_db`.

Listing and search omit these names. Read, metadata, exists, create, update,
mkdir, rename, and move return a validation or execution error without exposing
file contents.

### Windows path handling

On Windows the tools reject:

- control characters in paths;
- alternate data streams such as `notes.txt:hidden`;
- reserved device names such as `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, and
  `LPT1`-`LPT9`, including names with an extension;
- names ending in a dot or space, which Windows can silently alias to another
  file;
- drive-relative paths such as `C:notes.txt` and rooted paths without a drive
  letter.

### File types and size limits

Reads and writes are bounded by `TOOL_FILE_MAX_READ_BYTES` and
`TOOL_FILE_MAX_WRITE_BYTES`. Reads must decode as UTF-8 text. A NUL byte, a high
proportion of control bytes, invalid UTF-8, and known binary formats (executables,
archives, images, audio/video, office documents, databases, compiled files, and
fonts) are rejected. A multibyte UTF-8 character split exactly at the read limit
is handled without reporting the file as invalid.

Writes are text-only and reject NUL content, oversized content, sensitive paths,
and binary extensions.

### Safe writes, rename, and move

Creates and updates use safe write behavior:

- a non-overwriting create uses an exclusive create, so an existing file is
  never silently clobbered;
- an overwrite or update writes a temporary file in the destination directory,
  flushes and syncs it, preserves the existing file mode where supported, and
  atomically replaces the destination with `os.replace`;
- a failed write removes its temporary file and leaves the original file
  unchanged.

`fs_mkdir` creates exactly one folder with `os.mkdir`; it never creates missing
parents and never overwrites an existing entry.

`fs_rename` changes only the final name and keeps the entry in its current
directory. `fs_move` takes the full destination path, including the new name.
Both use a no-overwrite rename:

- an approved workspace root cannot be renamed or moved;
- an existing destination, a destination that contains a configured root, and a
  folder moved into itself or a descendant are rejected;
- symbolic links, junctions, and reparse points are refused as source or
  destination;
- cross-volume moves fail closed instead of copying and deleting;
- on Windows `os.rename` refuses an existing destination. On POSIX the
  destination existence check is repeated immediately before the call; the
  remaining race is documented under limitations.

### Untrusted content and confirmations

File content returned by `fs_read` is marked `untrusted` in both the result
data and metadata. It is still passed through the central result sanitizer and
result-size limit.

Every state-changing operation (`fs_create`, `fs_update`, `fs_mkdir`,
`fs_rename`, and `fs_move`) always requires explicit confirmation. The approval
is bound to the exact owner, conversation, tool, and argument payload, is
single-use, and expires. Rejected, expired, replayed, altered, wrong-owner,
wrong-conversation, and wrong-tool confirmations are refused. Approval never
bypasses argument validation, private-mode restrictions, enablement checks, or
the current permission policy.

Paths are validated when the confirmation is proposed and again immediately
before execution. `execute` re-resolves each path once more right before
touching the filesystem. Confirmation previews show the path and byte count
only, never file contents.

## Input contracts

### `fs_list`

```json
{"path": "."}
```

Returns `path`, `entries`, and `truncated`. Each entry contains `name`, `type`
(`file`, `directory`, `symlink`, or `unknown`), `size`, `modified`, and
`is_symlink`.

### `fs_search`

```json
{"pattern": "*.py", "path": "."}
```

The pattern is a file-name glob of at most 128 characters. Path separators and
`.`/`..` are rejected. Returns `pattern`, `matches`, and `truncated`. Each match
contains `path`, `size`, `modified`, and `type`. The search walks recursively
below the supplied directory.

### `fs_read`

```json
{"path": "notes/todo.txt"}
```

Returns `path`, `content`, `size`, `truncated`, and `untrusted`. The content is
the first `TOOL_FILE_MAX_READ_BYTES` bytes, trimmed to a complete UTF-8
character when necessary.

### `fs_create`

```json
{"path": "notes/todo.txt", "content": "Buy coffee\n", "overwrite": false}
```

Returns `path`, `bytes_written`, `created`, and `overwritten`. The parent
directory must already exist. `overwrite=false` never replaces an existing
file.

### `fs_update`

```json
{"path": "notes/todo.txt", "content": "Buy tea\n"}
```

Returns `path`, `bytes_written`, and `updated`. The target must already exist
and be a supported text file.

### `fs_metadata`

```json
{"path": "notes/todo.txt"}
```

Returns `path`, `name`, `type`, `size`, `modified`, `suffix`, and `is_symlink`.
The path is resolved before inspection.

### `fs_exists`

```json
{"path": "notes/todo.txt"}
```

Returns `path`, `exists`, `type` (`file`, `directory`, `other`, or `null`), and
`is_symlink`. A missing path is a successful result, not an error. This tool is
read-only and does not require confirmation.

### `fs_mkdir`

```json
{"path": "notes/archive"}
```

Returns `path` and `created`. Exactly one folder is created; the parent
directory must already exist and an existing entry is never replaced.

### `fs_rename`

```json
{"path": "notes/todo.txt", "new_name": "todo-done.txt"}
```

Returns `path`, `new_path`, and `renamed`. `new_name` is a single name without
path separators; the entry stays in the same directory. An existing destination
is never overwritten.

### `fs_move`

```json
{"source": "notes/todo.txt", "destination": "archive/todo.txt"}
```

Returns `source`, `destination`, and `moved`. `destination` is the full new path,
including the new name. An existing destination is never overwritten and
cross-volume moves are refused.

## Error behavior

Failures are returned as normalized tool results through `ToolExecutor`:

- outside-root, sensitive, UNC/device, traversal, malformed, or Windows-alias
  paths: `validation`;
- missing paths, wrong target type, unsupported file type, existing
  destination, missing parent, cross-volume move, and read/write failures:
  `execution`; a destination conflict detected while a confirmation is being
  proposed is reported as `validation`;
- oversized content: `size_limit`;
- disabled configuration or private mode: `disabled` or `permission`;
- an unapproved state-changing call: `confirmation_required`;
- a rejected, expired, replayed, altered, or mismatched confirmation:
  `confirmation`.

Error messages do not include file contents, credentials, or stack traces.

## Tests

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11f_filesystem -v
..\venv\Scripts\python.exe -m unittest tests.test_phase12c_file_operations -v
```

The suites use temporary directories only. Phase 11F covers valid reads,
writes, listing, discovery, metadata, traversal, absolute paths, symlink and
junction escapes, sensitive names, binary formats, UTF-8 boundaries, oversized
content, invalid paths, Windows aliases, permission denial, private mode,
confirmations, tampered approvals, atomic-write failures, registry and LLM
discovery, and a light regression check for the Phase 11A-11E tools. Phase 12C
covers approved-root hardening (user profile, browser profiles, credential
stores, `agriculture_db`), UNC/device/traversal rejection, existence checks,
folder creation, rename, move, destination conflicts, cross-volume failure,
result bounds, absolute-path redaction, confirmation binding/expiry/replay,
post-approval path and permission revalidation, private mode, and disabled
tools.

## Limitations

- There is no delete, copy, chmod, or recursive mutation operation. Permanent
  deletion is intentionally deferred.
- `fs_mkdir` creates one folder at a time and never creates missing parents.
- `fs_move` does not copy across volumes; a cross-volume move fails closed.
- On POSIX, `os.rename` replaces an existing destination. The existence check is
  repeated immediately before the call, but a local process could still race
  that window. Windows refuses an existing destination.
- There is no search by file content; `fs_search` matches names only.
- File content is not executed and no shell command can be requested.
- Paths are resolved before access, but a local process that can modify the
  workspace concurrently can still race the filesystem. Confirmed writes use
  temporary files and atomic replacement to minimize that window.
- The tools are convenience capabilities for a trusted single-owner
  deployment, not a sandbox for hostile local users.
