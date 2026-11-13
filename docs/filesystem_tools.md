# Filesystem tools (Phase 11F)

ASTA exposes six built-in filesystem tools. They are **disabled by default** and
only operate inside explicitly configured workspace roots. The tools never
delete files, never mutate a directory tree recursively, never execute a file,
and never run a shell command.

## Supported operations

| Tool | Category | Confirmation | Purpose |
| --- | --- | --- | --- |
| `fs_list` | read-only | no | List files, directories, and symlinks in one directory. |
| `fs_search` | read-only | no | Find files by a name glob below one directory. |
| `fs_read` | read-only | no | Read a bounded UTF-8 text file. |
| `fs_metadata` | read-only | no | Return type, size, modified time, and suffix. |
| `fs_create` | reversible | yes | Create a text file, optionally replacing an existing one. |
| `fs_update` | reversible | yes | Replace the contents of an existing text file. |

All tools are implemented as `BaseTool` subclasses and registered through
`ToolRegistry`. Every call from chat or the API passes through `ToolExecutor`,
the central permission policy, argument validation, timeout handling, result
bounds, credential redaction, and metadata-only execution history.

The tools are denied in private mode, like every other built-in external tool.

## Configuration

```env
# Filesystem tools (Phase 11F). Disabled by default.
TOOL_FILESYSTEM_ENABLED=false

# Comma-separated absolute directories. Each root must exist and may not be a
# drive root, a Windows system directory, or a parent/child of one.
TOOL_WORKSPACE_ROOTS=

TOOL_FILE_MAX_READ_BYTES=200000
TOOL_FILE_MAX_WRITE_BYTES=100000
TOOL_FILE_MAX_LIST_ENTRIES=500
TOOL_FILE_MAX_SEARCH_RESULTS=100
```

The general tool-calling path must also be enabled (`TOOL_CALLING_ENABLED=true`)
before the model can discover these tools.

Example for a single workspace:

```env
TOOL_FILESYSTEM_ENABLED=true
TOOL_WORKSPACE_ROOTS=C:\Users\karth\Documents\asta-workspace
```

Multiple roots are comma-separated:

```env
TOOL_WORKSPACE_ROOTS=C:\Users\karth\Documents\asta-workspace,D:\projects\notes
```

A relative tool path is resolved against the **first** configured root. An
absolute tool path is accepted when it resolves inside **any** configured root.
Roots that do not exist, are files, are sensitive directories, are filesystem
roots, or overlap a Windows/POSIX system directory are ignored. If no valid
root remains, the filesystem tools report themselves as disabled.

## Security model

### Root containment

Every requested path is resolved with symlinks and junctions followed before
use. The resolved path must be inside a configured root. Drive-relative paths,
root-relative paths without a drive letter, alternate data streams, and paths
that escape through `..` are rejected.

Windows roots such as `C:\`, `C:\Windows`, `C:\Program Files`,
`C:\Program Files (x86)`, and `C:\ProgramData` are rejected. A configured root
may not be inside one of those locations and may not contain one, so a broad
root cannot expose the operating system. On POSIX systems the equivalent
system directories (`/etc`, `/usr`, `/bin`, `/boot`, `/dev`, `/proc`, `/sys`,
and related paths) are rejected.

Symlinks and junctions are resolved before containment is checked. A link
inside a root that points outside the root is rejected. Search skips symlink
and junction directories entirely, so discovery cannot traverse out of the
workspace. Writes to a symlink path are rejected.

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
- sensitive directories such as `.ssh`, `.aws`, `.azure`, `.docker`,
  `.gnupg`, `.git`, and `.kube`.

Listing and search omit these names. Read, metadata, create, and update return
a validation error without exposing file contents.

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

### Safe writes

Creates and updates use safe write behavior:

- a non-overwriting create uses an exclusive create, so an existing file is
  never silently clobbered;
- an overwrite or update writes a temporary file in the destination directory,
  flushes and syncs it, preserves the existing file mode where supported, and
  atomically replaces the destination with `os.replace`;
- a failed write removes its temporary file and leaves the original file
  unchanged.

### Untrusted content and confirmations

File content returned by `fs_read` is marked `untrusted` in both the result
data and metadata. It is still passed through the central result sanitizer and
result-size limit.

`fs_create` and `fs_update` always require explicit confirmation. The approval
is bound to the exact owner, conversation, tool, and argument payload. Approval
never bypasses argument validation, private-mode restrictions, enablement
checks, or the current permission policy. Confirmation previews show the path
and byte count only, never file contents.

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

## Error behavior

Failures are returned as normalized tool results through `ToolExecutor`:

- outside-root, sensitive, malformed, or Windows-alias paths: `validation`;
- missing paths, wrong target type, unsupported file type, read/write failures:
  `execution`;
- oversized content: `size_limit`;
- disabled configuration or private mode: `disabled` or `permission`;
- unapproved create/update: `confirmation_required`.

Error messages do not include file contents, credentials, or stack traces.

## Tests

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11f_filesystem -v
```

The suite uses temporary directories only. It covers valid reads, writes,
listing, discovery, metadata, traversal, absolute paths, symlink and junction
escapes, sensitive names, binary formats, UTF-8 boundaries, oversized content,
invalid paths, Windows aliases, permission denial, private mode, confirmations,
tampered approvals, atomic-write failures, registry and LLM discovery, and a
light regression check for the Phase 11A-11E tools.

## Limitations

- There is no delete, rename, move, copy, chmod, directory-create, or recursive
  mutation operation.
- There is no search by file content; `fs_search` matches names only.
- File content is not executed and no shell command can be requested.
- Paths are resolved before access, but a local process that can modify the
  workspace concurrently can still race the filesystem. Confirmed writes use
  temporary files and atomic replacement to minimize that window.
- The tools are convenience capabilities for a trusted single-owner
  deployment, not a sandbox for hostile local users.
