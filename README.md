# ASTA – Personal AI Assistant

ASTA is a context-aware personal AI assistant with a FastAPI backend,
PostgreSQL storage, hybrid Groq/Ollama LLM routing, and pgvector-backed
long-term memory.

## Current capabilities

- Hybrid LLM routing: Groq first, Ollama fallback, with optional complexity-aware and cost-aware policies.
- Private requests are routed to local Ollama only, regardless of routing policy.
- Approved long-term memories with keyword, semantic, or hybrid retrieval.
- Memory-aware chat prompts.
- Token-aware context budgeting that trims low-priority sources before a prompt exceeds a model's window.
- Deterministic query classification for routing and cache eligibility, with an optional disabled-by-default LLM fallback.
- Conservative semantic response cache for stateless, non-private, non-sensitive requests.
- Metadata-only AI usage and performance tracking (tokens, latency, fallback, cache hits, optional cost).
- Persistent conversations and messages in PostgreSQL.
- Conversation CRUD, paginated message history, and resumable chats.
- Structured user preferences with explicit CRUD and inferred suggestions.
- Structured tasks with categories, priorities, lifecycle status, and deadlines.
- Task-scoped reminders with deadline-relative or absolute times, owner-scoped CRUD, and duplicate prevention.
- Database-backed reminder scheduler with atomic claims, bounded retries, restart recovery, and graceful shutdown.
- In-app notification history with read state that survives task and reminder deletion.
- Notification snoozing that re-arms the existing reminder without duplicating notifications.
- Reminder rescheduling with duplicate prevention and concurrency-safe lifecycle checks.
- Notification preferences with timezone-aware quiet hours that defer rather than discard reminders.
- Finite recurring tasks (daily, weekly, monthly) with custom weekdays, intervals, occurrence links, and duplicate prevention.
- Natural-language task management with confirmation-gated mutations.
- Central tool registry with validated metadata, input/output schemas, duplicate prevention, discovery, and enabled/disabled views (Phase 11A).
- Provider-specific LLM function calling for Groq and Ollama, with normalized tool-call parsing, safe malformed-response handling, and text-only fallback (Phase 11B).
- Central tool execution engine with trusted-context checks, argument validation, permission and confirmation enforcement, timeouts, result bounds, credential redaction, and metadata-only execution history (Phase 11C).
- Hardened tool permissions and confirmations with category, enablement, and private-mode policy; owner-, conversation-, and argument-bound single-use approvals; expiry, replay, duplicate, and tamper handling; and post-approval revalidation and reauthorization (Phase 11D).
- Configurable web search through a self-hosted SearXNG instance or the managed Tavily API, returning normalized, size-bounded, untrusted result metadata through the central tool executor (Phase 11E).
- Root-confined filesystem tools for directory listing, name search, bounded UTF-8 reads, metadata, confirmed file creation, and confirmed atomic updates, with symlink/junction, sensitive-file, binary-format, and Windows path protections (Phase 11F).
- Resource-limited Python execution inside a real Windows AppContainer and Job Object boundary, with a disposable workspace, a scrubbed environment, blocked subprocesses, denied network access, bounded output, and confirmed, disabled-by-default tool integration (Phase 11G).
- End-to-end tool calling in the real chat pipeline, with bounded multi-round execution, owner- and argument-bound confirmation approvals, metadata-only transcript persistence, safe provider-failure recovery, and unchanged text-only behaviour when disabled (Phase 11H).
- Tool execution history and reliability with a validated lifecycle, start/completion timestamps, retry and truncation metadata, owner-scoped status/summary APIs, opt-in bounded retries for idempotent read-only tools, idempotency-key duplicate prevention, restart recovery, and structured logs/metrics (Phase 11I).
- Read-only system information tools for OS, hostname, CPU, memory, approved local disk usage, Python runtime, and a consolidated summary, using only Python standard-library APIs (Phase 12A).
- Secure, allowlisted desktop-application launching by alias, with server-side executable paths, confirmation-gated sensitive execution, private-mode denial, and no shell or arbitrary argument execution (Phase 12B).
- Read-only process and active-window inspection with bounded process listings, least-privilege CPU and memory measurements, protected-process filtering, allowlist-backed application status, and no process or window modification (Phase 12D).
- Tightly restricted, confirmation-gated clipboard, keyboard, and mouse interaction with plain-text-only bounded clipboard access, a fixed single-key allowlist, validated pointer coordinates, and one left click per confirmed action (Phase 12E).
- Timezone-aware deadline views (overdue, today, tomorrow, upcoming).
- Unified context assembly from preferences, memories, tasks, deadlines, and bounded history.
- Per-user ownership checks on every conversation, message, preference, task, usage, and AI cache query.
- Single-owner authentication with Argon2id passwords, revocable cookie sessions, and CSRF protection.
- Database-backed, progressive login rate limiting that survives backend restarts.
- Configurable trusted hosts and explicit CORS origins for laptop and phone access on a private LAN.

## Dependencies and environment setup

ASTA targets Python 3.12. Dependencies are pinned in two files:

- `backend/requirements.txt` - runtime dependencies, including the PostgreSQL
  driver, Argon2id support, timezone data, and the ML stack used by semantic
  memory.
- `backend/requirements-dev.txt` - includes the runtime file and adds the
  test-only async SQLite driver.

```powershell
cd C:\Asta\backend
..\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

For a production or runtime-only install, use `requirements.txt` instead.

The semantic-memory stack (`sentence-transformers`, PyTorch, Transformers,
NumPy, SciPy, scikit-learn) is large and is genuinely required for embedding
generation. Model weights are downloaded by `sentence-transformers` on first
semantic use and are **not** committed to the repository. Keyword retrieval
remains available if semantic storage is unavailable.

`tzdata` is included because Windows does not ship the IANA timezone database
used for deadline handling.

To validate installation in a fresh Python 3.12 environment without touching
`C:\Asta\venv`:

```powershell
cd C:\Asta\backend
..\venv\Scripts\python.exe scripts\verify_phase81_dependencies.py
```

The script creates and removes a throwaway virtual environment, runs
`pip check`, imports every direct dependency, and verifies that
`app.main:app` imports.

## Conversation persistence

Conversations and messages are stored in PostgreSQL. Sending a chat message
without a `conversation_id` creates a conversation; sending one with an owned
`conversation_id` resumes it and adds recent messages as short-term context.
Long-term memory retrieval remains a separate step.

See [docs/conversation_persistence.md](docs/conversation_persistence.md) for
the architecture, configuration, and request/response examples.

## Preferences and unified context

Structured preferences are stored separately from long-term memories. Explicit
preferences can be created through the API; preferences inferred from
conversation are stored as pending suggestions and only become active after an
explicit approval. Chat combines active preferences, approved memories, and
bounded conversation history into source-separated, untrusted context blocks.

See [docs/preferences_and_context.md](docs/preferences_and_context.md) for the
preference lifecycle, context prioritization, configuration, and examples.

## Tasks and deadlines

Tasks are stored in PostgreSQL with categories, priorities, lifecycle status,
and optional deadlines. A deadline is a date, an optional wall-clock time, and
an optional IANA timezone. Deadline comparisons are timezone-aware and use UTC
internally; date-only deadlines are not overdue before their due date passes.
Completed and cancelled tasks are excluded from overdue results.

Chat receives only relevant task and deadline context: overdue items, tasks due
today/tomorrow, upcoming deadlines, high-priority or in-progress tasks, and
tasks explicitly mentioned by the user. Task and deadline prompt sections remain
separate and are subject to the unified context budget.

See [docs/task_and_deadline_management.md](docs/task_and_deadline_management.md)
for the model, lifecycle, deadline semantics, API examples, and configuration.

### Task reminders, notifications, and recurrence

Phase 10A added task-scoped reminders. A reminder always belongs to an existing
task and owner. It can be expressed as a positive offset before the deadline or
as an explicit timezone-aware instant; the resolved scheduling time is stored
in UTC. Multiple distinct reminders may belong to one task.

For timed tasks, an offset is measured from the exact deadline. For date-only
tasks, the anchor is `REMINDER_DEFAULT_LOCAL_TIME` (09:00 by default) on the
due date in the task's IANA timezone, so a one-day reminder means 09:00 the
previous day rather than the end of the due date. Creating, rescheduling,
cancelling, and deleting reminders all enforce task ownership.

Phase 10B–10H complete the delivery pipeline:

- **Scheduler.** An in-process background loop claims due reminders from
  PostgreSQL with `SELECT FOR UPDATE SKIP LOCKED` where available, records a
  claim token and lease, and only marks a reminder delivered after the delivery
  handler commits successfully. Abandoned claims are recovered after the lease
  expires; failures use bounded exponential backoff up to
  `REMINDER_MAX_ATTEMPTS`. Work is database-backed, so restarts resume safely,
  and shutdown waits for the in-flight cycle.
- **In-app notifications.** Delivery persists one notification per reminder
  using a unique `(user_id, dedupe_key)` key. Notifications snapshot the title
  and body and reference the task and reminder with `ON DELETE SET NULL`, so
  history survives task deletion. Read/unread state is owner-scoped.
- **Recurrence.** A rule on an owned open task generates independent occurrence
  tasks for daily, weekly (with custom weekdays), or monthly cadences, with
  intervals, optional start/end dates, and the task's timezone and wall-clock
  time. Generation is finite and bounded by the end date, horizon, and per-run
  cap. A unique `(recurrence_id, occurrence_date)` constraint prevents
  duplicates. Deactivating a rule stops future generation and preserves all
  existing tasks and history.
- **Preferences and quiet hours.** Owners can disable in-app notifications or
  set quiet hours. Quiet hours use wall-clock start/end times plus an IANA
  timezone, support windows that cross midnight, and handle daylight-saving
  transitions. A reminder that falls inside the window is deferred until the
  window ends, never silently discarded.
- **Snooze and reschedule.** A delivered notification can be snoozed for a
  duration (for example 10, 30, or 60 minutes, or 1440 for "tomorrow") or until a
  custom timezone-aware instant. Snoozing marks the notification read, records
  audit metadata, and re-arms the existing reminder; the same notification row
  becomes unread again on re-delivery. Pending reminders can be rescheduled.
  Both operations are owner-scoped, reject past or duplicate instants, respect
  quiet hours, and use row locking so a scheduler claim cannot race a change.

Notification history is retained for `NOTIFICATION_RETENTION_DAYS`; only read
notifications older than the cutoff are purged, and `0` disables purging.
Email, SMS, and push channels are out of scope. Natural-language reminder
scheduling is intentionally deferred; the existing task-intent engine still
treats “remind me to…” as a task request.

See [docs/reminders_and_notifications.md](docs/reminders_and_notifications.md)
for the schema, scheduling and retry semantics, recurrence rules, quiet hours,
API examples, configuration, and limitations.

## Natural-language task management

Chat can understand task requests such as "Remind me to call mom tomorrow",
"Show my overdue tasks", "Mark my Java assignment as completed", or "Move my
exam to next Monday". Intent detection and relative-date resolution are
deterministic and use an injected clock. The LLM is only consulted for
hard-to-classify task-like messages, and its structured output is strictly
validated; it cannot supply task IDs, user IDs, or database operations.

No mutation happens from the language model. Create, update, complete, reopen,
cancel, and delete requests become user-scoped pending actions that require an
explicit "yes" in a later turn (or through the confirmation API). Confirmations
expire, are single-use, are superseded by unrelated messages, and revalidate
ownership and task state immediately before execution. Private requests stay on
local Ollama.

See [docs/natural_language_task_management.md](docs/natural_language_task_management.md)
for intents, extraction and date rules, the confirmation lifecycle, storage,
API examples, privacy guarantees, and configuration.

## Unified context architecture

Chat assembles one prompt from six sources: the current message, bounded
conversation history, active preferences, approved memories, relevant open
tasks, and relevant deadlines. Sources are filtered for relevance and ownership,
de-duplicated, kept in separate untrusted blocks, and trimmed against per-section
and global character budgets. Phase 9 also enforces a per-model token budget, so
the lowest-priority sources are removed before the prompt can exceed the selected
provider's context window. Private requests use Ollama only and exclude stored
task/deadline context.

See [docs/context_architecture.md](docs/context_architecture.md) for the full
retrieval flow, relevance rules, priority ordering, budgets, privacy guarantees,
failure handling, and example assembly.

## AI optimization and cost control

Phase 9 adds a conservative optimization layer in front of the existing
provider router and context builder:

- Deterministic query classification (`simple`, `moderate`, `complex`,
  `dynamic_information`, `task_action`, `private`) drives routing and cache
  eligibility. Classification is an optimization hint only; it never authorizes
  a mutation or changes ownership checks.
- Routing policies (`groq_first`, `local_first`, `cost_aware`) select the primary
  provider and fallback. Private requests are always Ollama-only and never touch
  a cloud provider, including on health checks and failure paths.
- The semantic response cache only considers stateless, non-private,
  non-sensitive, non-dynamic eligible requests with no rendered context. It is
  user-scoped, TTL-bound, and versioned; PostgreSQL/pgvector adds cosine
  similarity, while SQLite uses exact query-hash matching.
- Usage records store tokens, latency, provider/model, fallback and cache flags,
  and optional configured cost. They never contain prompt text, response text,
  credentials, or provider error details.
- Hybrid memory retrieval blends semantic and keyword scores, de-duplicates
  results, keeps the approved-only rule, and falls back to keyword retrieval when
  semantic storage is unavailable.

See [docs/ai_optimization.md](docs/ai_optimization.md) for routing policies,
classification rules, cache eligibility, usage tracking, hybrid retrieval,
privacy guarantees, configuration, and the Phase 9 migration.

## Tool registry

Phase 11A adds a central, in-process registry for function-calling tools. A
tool declares a unique name, description, category, Pydantic input model,
optional output schema, and permission metadata. The registry validates every
definition, prevents duplicates, supports atomic batch registration, and
exposes discovery APIs for listing tools, resolving one by name, and viewing
enabled or disabled tools.

Definitions are provider-independent until `llm_definitions()` converts the
currently allowed set into OpenAI/Ollama-compatible function schemas. Phase 11A
does not execute tools or add database tables. Phase 11B parses provider
tool-call responses; execution and confirmations remain in Phase 11C.

See [docs/tool_registry.md](docs/tool_registry.md) for the architecture,
`BaseTool` contract, schema and permission metadata, discovery flow, lazy
built-in loading, and Phase 11B/11C integration points.

## LLM function calling

Phase 11B connects the tool registry to the hybrid Groq + Ollama router. The
model can request a registered tool by name and JSON arguments, but nothing is
executed at this layer. Registry definitions are converted at the provider
boundary into the canonical function schema both providers accept: Groq uses
`tools` with `tool_choice="auto"`, while Ollama uses its native `tools`
parameter.

Responses are normalized into a `ProviderToolTurn` with assistant text and
`NormalizedToolCall` entries. Malformed JSON, non-object arguments, missing
names, duplicate ids, unsupported response shapes, and oversized payloads are
reported as structured errors instead of raising, so the assistant can recover.
Valid calls also convert to the public `ToolCall` schema for Phase 11C.

Routing behavior is unchanged: non-private requests follow the configured
policy and fallback order, private requests stay Ollama-only, and text-only
responses are returned exactly as before. Phase 11B does not execute tools,
authorize actions, or touch the database.

See [docs/tool_calling.md](docs/tool_calling.md) for provider formats, parsing
rules, message translation, router behavior, limits, and the Phase 11C
boundary.

## Tool execution

Phase 11C adds the single execution path for every tool call. The executor takes
a normalized tool name and argument mapping plus a backend-created
`ToolContext`, then:

1. rejects a missing or malformed trusted context, so there is no anonymous
   owner fallback;
2. resolves the tool through the registry and returns structured `not_found` or
   `configuration` errors;
3. applies enablement, privacy, sensitive-tool, and confirmation policy before
   argument details are disclosed;
4. checks argument size and validates the payload against the tool's Pydantic
   model without echoing submitted values;
5. creates a pending confirmation or claims an existing one, revalidates the
   stored arguments, and rechecks policy;
6. executes under a wall-clock timeout and distinguishes caller cancellation
   from a tool that raises `CancelledError` itself;
7. redacts credential-shaped result data and error messages, removes stack
   traces from returned errors, and bounds the serialized result size;
8. records metadata-only history and attaches or fails the confirmation as
   best-effort bookkeeping.

Every outcome is a normalized `ToolResult` with a stable status
(`success`, `error`, `timeout`, `denied`, or `confirmation_required`) and a safe
error category. One failing tool never crashes the assistant request. Owner
identity and permissions are never accepted from provider output or request
arguments.

See [docs/tool_execution.md](docs/tool_execution.md) for the execution
lifecycle, status and error mapping, security properties, REST endpoints,
configuration, tests, known limitations, and Phase 11D hardening.

## Tool permissions and confirmations

Phase 11D centralizes authorization in `app/tools/permissions.py` and hardens
the existing confirmation service. It does not add a parallel execution path.

Authorization is decided from the trusted `ToolContext`, never from model output
or request arguments. The policy evaluates, in order:

1. tool enablement (`disabled` denial);
2. private-mode restrictions;
3. the sensitive-tool deployment policy;
4. `requires_confirmation`, which now applies to every category, including
   `read_only`.

A tool that needs approval returns `confirmation_required` without executing.
The pending confirmation is bound to the owner, the proposing conversation, the
tool name, and the canonical argument digest. Approval is an atomic single-use
claim, so replay, duplicate approval, a different owner, a different
conversation, and modified arguments are all rejected. Rejection and expiry are
terminal and clear the stored payload.

After a claim, the executor revalidates the stored arguments and re-runs the
permission policy before executing. An approval therefore cannot bypass
validation or current authorization. A post-claim failure marks the
confirmation failed, a successful run attaches the metadata-only history row,
and the existing authentication, CSRF, and owner-scoping protections are
unchanged.

See
[docs/tool_permissions_and_confirmations.md](docs/tool_permissions_and_confirmations.md)
for the policy matrix, confirmation lifecycle and binding rules, executor
integration, failure mapping, REST status codes, configuration, tests, and
limitations.

## Web search tool

Phase 11E adds the built-in `web_search` read-only tool. It is disabled by
default and is only offered to the model when `WEB_SEARCH_ENABLED=true`, the
selected provider is configured, and the general tool-calling path is enabled.

Two documented providers are supported:

- **SearXNG** (`WEB_SEARCH_PROVIDER=searxng`, the default) is a self-hosted
  metasearch instance whose administrator has enabled `format=json`. It needs
  `WEB_SEARCH_BASE_URL` and **does not require an API key**. This is the
  preferred option when search should stay on infrastructure you control.
- **Tavily** (`WEB_SEARCH_PROVIDER=tavily`) is a managed search API reached at
  `https://api.tavily.com/search`. It **requires** `WEB_SEARCH_API_KEY`, sent as
  a server-side bearer credential.

The tool validates a non-blank, control-character-free query of at most 500
characters and `max_results` between 1 and 10. The configured
`WEB_SEARCH_MAX_RESULTS` value further caps the effective count. It returns
only normalized `title`, `url`, `snippet`, and `source` fields, always marked
`untrusted`. Result URLs must be HTTP(S), contain no embedded credentials or
unsafe characters, and the response body is read under a configured byte
limit. Redirects are not followed, page content is never fetched, and provider
error bodies, credentials, and search queries are never logged.

Provider failures, timeouts, rate limits, invalid JSON, unexpected payload
shapes, redirects, and oversized responses become safe structured tool errors.
Web search is denied in private mode and, as a read-only tool, does not require
confirmation; every call still goes through `ToolExecutor` and the central
permission policy.

```env
# Web search (Phase 11E). Disabled by default.
WEB_SEARCH_ENABLED=false
WEB_SEARCH_PROVIDER=searxng
WEB_SEARCH_BASE_URL=
WEB_SEARCH_API_KEY=
WEB_SEARCH_TIMEOUT_SECONDS=10
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_MAX_RESPONSE_BYTES=262144
```

See [docs/web_search.md](docs/web_search.md) for provider setup, the request
and result contract, error mapping, security properties, tests, and
limitations.

## Filesystem tools

Phase 11F introduced six built-in filesystem tools. Phase 12C adds existence
checks plus approved-root folder creation, rename, and move. They are disabled
by default and only operate inside explicitly configured workspace roots. The
tools never delete files, mutate a directory tree recursively, execute a file,
or run a shell command.

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

There is no delete tool. Permanent deletion is intentionally deferred.

Every call goes through `ToolExecutor` and the central permission policy. The
filesystem tools are denied in private mode. A relative path resolves against
the first configured root; an absolute path is accepted only when it resolves
inside one of the configured roots.

Security is fail-closed:

- paths are resolved (following symlinks and junctions) and must remain inside
  an approved root; `..` traversal, absolute escapes, UNC/network paths,
  Windows device namespaces, and link escapes are rejected;
- drive roots and Windows system directories cannot be configured as roots,
  nor can a root contain or live inside one; the user profile and its ancestors
  are rejected, as are roots inside or containing a browser profile or
  credential store;
- `.env` files, credentials, secrets, private keys, key stores, Terraform
  state, browser credential files, sensitive directories such as
  `.ssh`/`.aws`/`.git`/`agriculture_db`, and common credential files are denied
  and omitted from listings and search;
- Windows alternate data streams, reserved device names, trailing dots or
  spaces, drive-relative paths, and rooted paths without a drive letter are
  rejected;
- reads are bounded, must decode as UTF-8, and reject NUL bytes, control-heavy
  binary content, and known binary formats; writes are bounded and text-only;
- creates, updates, folder creation, rename, and move always require explicit
  confirmation bound to the exact owner, conversation, tool, and arguments;
  approvals are single-use, expiring, and revalidated immediately before
  execution;
- non-overwriting creates use exclusive creation, while updates and overwrites
  write a same-directory temporary file and atomically replace the destination;
  a failed write leaves the original unchanged;
- rename and move never overwrite an existing destination, refuse
  links/junctions/reparse points, reject a workspace root, and refuse a folder
  moved into itself or a descendant; cross-volume moves fail closed;
- read content is marked `untrusted` and is never written to execution history.

```env
# Filesystem tools (Phase 11F + 12C). Disabled by default.
TOOL_FILESYSTEM_ENABLED=false
# Comma-separated absolute directories that may be accessed.
TOOL_WORKSPACE_ROOTS=
TOOL_FILE_MAX_READ_BYTES=200000
TOOL_FILE_MAX_WRITE_BYTES=100000
TOOL_FILE_MAX_LIST_ENTRIES=500
TOOL_FILE_MAX_SEARCH_RESULTS=100
```

See [docs/filesystem_tools.md](docs/filesystem_tools.md) for operation
contracts, root rules, Windows handling, sensitive-file policy, atomic-write
behavior, rename/move rules, confirmation binding, error mapping, tests, and
limitations.

## Python execution sandbox

Phase 11G adds the `python_execute` sensitive tool. It remains disabled unless
`PYTHON_SANDBOX_ENABLED=true`, `TOOL_ALLOW_SENSITIVE=true`, the Windows
AppContainer runtime is available, and the owner approves a per-call
confirmation. Private-mode requests are always denied.

The tool is not an `exec()` wrapper and not a plain subprocess. Each call gets a
fresh AppContainer profile with no capabilities and a Job Object:

- the child token cannot read ASTA source, `.env` files, user documents, or any
  path that was not explicitly granted; only the disposable workspace is
  writable and only the interpreter installation is readable;
- network access is blocked by the AppContainer token and cannot be enabled by
  configuration; asking for it fails closed;
- child process creation is blocked by the job's one-process limit;
- memory and CPU quotas are enforced by the job, and the wall-clock timeout
  terminates the whole job;
- stdin, stdout, and stderr are the only inherited handles, and the environment
  block contains no `PATH`, `USERPROFILE`, or ASTA variables;
- combined output is bounded, overflow terminates the job, and the workspace,
  profile, ACL grants, job, and handles are cleaned up in `finally` paths.

An AST denylist rejects escape-oriented imports such as `ctypes`, `winreg`,
`importlib`, `msvcrt`, and `multiprocessing`. It is defence in depth only; the
AppContainer/Job Object boundary is the security control. Unsupported platforms
and missing runtimes fail closed.

```env
# Python execution sandbox (Phase 11G). Disabled by default.
PYTHON_SANDBOX_ENABLED=false
PYTHON_SANDBOX_TIMEOUT_SECONDS=5
PYTHON_SANDBOX_CPU_SECONDS=5
PYTHON_SANDBOX_MEMORY_MB=256
PYTHON_SANDBOX_MAX_SOURCE_BYTES=20000
PYTHON_SANDBOX_MAX_OUTPUT_BYTES=20000
PYTHON_SANDBOX_ALLOW_NETWORK=false
PYTHON_SANDBOX_WORKSPACE_PARENT=
```

See [docs/python_execution_sandbox.md](docs/python_execution_sandbox.md) for
the execution lifecycle, actual isolation boundary, limits, error handling,
Windows requirements, test coverage, and residual risks.

## Chat tool-calling integration

Phase 11H connects the existing tool engine to `POST /api/chat/`. It adds no
parallel orchestrator: `ToolOrchestrator` calls the same `ToolExecutor` and
confirmation service used by the direct `/api/tools` routes. The path is opt-in
through `TOOL_CALLING_ENABLED`; when it is disabled, chat and its public
response shape are unchanged.

A request can make several bounded provider turns. The model may return text,
one call, or multiple calls. Every call is validated, authorized, executed, and
redacted by the executor. Safe results are fed back as explicitly untrusted
tool messages, and one final text-only turn is always allowed so the model
cannot loop indefinitely. Round, per-turn, per-request, result-size, and
result-context limits are enforced, and an identical call is rejected within
the same request.

If a tool requires confirmation, the request stops immediately and nothing is
executed. The response returns a deterministic pending notice plus the
confirmation id. The owner can then call:

- `POST /api/chat/confirmations/{id}/approve`
- `POST /api/chat/confirmations/{id}/reject`

Approval executes the exact stored arguments, atomically claims the
confirmation, and then asks the provider for a final text-only answer. The model
cannot start another tool chain or approve its own action. Missing and
cross-owner confirmations return `404`, resolved or tampered confirmations
return `409`, and expired confirmations return `410`.

Tool activity is persisted as ordered assistant-role transcript entries with
`messages.message_metadata.kind` (`tool_call`, `tool_result`,
`tool_confirmation_required`, `tool_rejected`). Only metadata is stored:
argument values, tool output, file contents, and credentials are never written
to the transcript, and transcript entries are excluded from future prompts.

If a provider fails before any tool runs, the existing `503`/`504` contract is
preserved. If a tool already ran and a later turn fails, a deterministic summary
is returned so a retry cannot silently execute the action again. Streaming is
not implemented.

```env
# Tool loop (Phase 11H)
TOOL_MAX_ROUNDS=2
TOOL_MAX_CALLS_PER_REQUEST=5
TOOL_MAX_CALLS_PER_TURN=8
TOOL_MAX_RESULT_CONTEXT_BYTES=50000
TOOL_LOOP_PREVENTION_ENABLED=true
```

See [docs/tool_calling_api_integration.md](docs/tool_calling_api_integration.md)
for the architecture, lifecycle, response schema, limits, provider behaviour,
confirmation workflow, persistence, security properties, tests, and
limitations.

## Tool history and reliability

Phase 11I extends the existing metadata-only history with a validated lifecycle
(`pending`, `awaiting_confirmation`, `approved`, `running`, `succeeded`,
`failed`, `rejected`, `expired`, `cancelled`, `timed_out`), start/completion
timestamps, retry counts, truncation flags, and hashed idempotency keys. Legal
transitions are enforced, illegal or duplicate terminal writes are ignored, and
Phase 11D confirmation handling is unchanged.

History is written in two phases. An in-flight row is created before any policy
decision or side effect, then finalized with a terminal outcome. On startup,
stale `pending`, `approved`, and `running` rows are marked `cancelled` with
`InterruptedExecution`; `awaiting_confirmation` rows are left for the owner.
History and metrics failures never change a tool result.

Retries are opt-in and narrow: only read-only tools that declare
`idempotent = True`, do not require confirmation, and fail with a transient
timeout/provider error. Confirmation-bound and state-changing tools are never
retried automatically. `POST /api/tools/execute` accepts an optional
`Idempotency-Key` header; only its SHA-256 digest is stored. A repeated request
returns metadata-only stored state with `idempotent_replay=true` and never the
original payload.

The owner-scoped endpoints `GET /api/tools/executions/{id}`, `/status`, and
`/summary`, plus `GET /api/tools/metrics`, expose lifecycle, safe metadata, and
process-local counters. Structured logs contain tool name, outcome, duration,
retry count, confirmation state, and error code only — never arguments, output,
files, prompts, or credentials.

```env
# Tool history and reliability (Phase 11I)
TOOL_HISTORY_ENABLED=true
TOOL_RETRY_ENABLED=false
TOOL_MAX_RETRIES=1
TOOL_RETRY_BACKOFF_MS=250
TOOL_RECOVERY_ON_STARTUP=true
TOOL_STALE_EXECUTION_SECONDS=300
TOOL_EXECUTION_RETENTION_DAYS=90
```

See [docs/tool_history_and_reliability.md](docs/tool_history_and_reliability.md)
for the state machine, schema and migration, endpoints, retry policy,
idempotency semantics, restart recovery, observability, retention, security
properties, tests, and limitations.

## System information tools

Phase 12A adds seven read-only system information tools. They are disabled by
default and use only the Python standard library. No tool runs PowerShell, CMD,
a shell, or an arbitrary script.

| Tool | Category | Confirmation | Purpose |
| --- | --- | --- | --- |
| `system_os_info` | read-only | no | OS name, release, version, Windows build, and architecture. |
| `system_hostname` | read-only | no | Local computer hostname (no DNS lookup). |
| `system_cpu_info` | read-only | no | CPU model text, logical processors, and physical cores when available. |
| `system_memory_info` | read-only | no | Total, available, and used RAM plus usage percentage. |
| `system_disk_info` | read-only | no | Total, used, and available space for approved local drives. |
| `system_python_info` | read-only | no | Python version, implementation, build, compiler, and executable. |
| `system_summary` | read-only | no | Consolidated human-readable summary of the other sections. |

Every call goes through `ToolExecutor` and the central permission policy. The
tools are read-only and idempotent, do not require confirmation, and are denied
in private mode. They are never registered as sensitive tools, so
`TOOL_ALLOW_SENSITIVE` is not involved.

Security is fail-closed:

- values are collected with `platform`, `socket`, `os`, `shutil`, `sys`, and
  read-only `ctypes` kernel queries; no shell or subprocess is started;
- environment variables, file contents, process lists, arbitrary registry
  values, browser history, documents, and other user data are never enumerated
  or returned as data; the standard library may consult documented OS/hardware
  identifiers read-only;
- disk usage is limited to `TOOL_SYSTEM_INFO_DISK_ROOTS` or, when that is
  empty, the single drive containing the running interpreter; UNC paths and
  mapped network or removable volumes are rejected for every configured path;
- strings and result sizes are bounded; unavailable fields are reported as
  `null`/`available: false` rather than invented;
- OS and permission errors become safe structured results, and raw tracebacks
  are never exposed.

```env
# System information tools (Phase 12A). Disabled by default.
TOOL_SYSTEM_INFO_ENABLED=false
# Comma-separated local disk roots; empty uses the system drive.
TOOL_SYSTEM_INFO_DISK_ROOTS=
TOOL_SYSTEM_INFO_MAX_DISKS=8
```

See [docs/system_information_tools.md](docs/system_information_tools.md) for
the operation contracts, disk allowlist rules, result shapes, error mapping,
security properties, tests, and limitations.

## Application launching

Phase 12B adds one sensitive tool, `system_launch_application`, which starts an
application that the administrator explicitly allowlisted. The model may ask
for an application by alias, but it can never supply an executable path or
command-line arguments.

| Tool | Category | Confirmation | Purpose |
| --- | --- | --- | --- |
| `system_launch_application` | sensitive | yes (every launch) | Launch one allowlisted desktop application by alias. |

Every call goes through `ToolExecutor` and the central permission policy. The
tool is disabled by default, requires `TOOL_APPLICATION_LAUNCH_ENABLED=true`,
requires `TOOL_ALLOW_SENSITIVE=true`, and is denied in private mode. A pending
confirmation is created only after the alias resolves to a valid approved
executable, and the stored arguments are re-resolved immediately before the
process starts.

Allowlist configuration uses `alias=absolute-path` entries:

```env
# Application launching (Phase 12B). Disabled by default.
TOOL_APPLICATION_LAUNCH_ENABLED=false
TOOL_ALLOW_SENSITIVE=false
# Comma- or newline-separated alias=absolute-path entries (maximum 32).
TOOL_APPLICATION_ALLOWLIST=
```

Security is fail-closed:

- only aliases present in `TOOL_APPLICATION_ALLOWLIST` can be launched; the
  executable path comes exclusively from configuration;
- aliases are normalized (case, spaces, underscores, hyphens, and punctuation
  are ignored), while path separators, drive syntax, `=`, `..`, and control
  characters are rejected;
- each configured path must be an absolute `.exe` file on a fixed local
  Windows drive; directories, missing files, relative paths, UNC/network
  paths, traversal, symlinks, junctions, and reparse points are rejected;
- launching uses `subprocess.Popen([executable], shell=False)` with no shell
  interpretation, no extra arguments, no inherited standard streams, and a
  hidden window;
- unsupported operating systems fail closed, and no administrator privileges
  are requested;
- the result exposes the alias, display name, and process id, but never the
  executable path, environment variables, or secrets;
- no existing process is terminated or modified, and nothing is installed,
  downloaded, or written to the registry or Windows settings.

See [docs/application_launching.md](docs/application_launching.md) for the
allowlist rules, confirmation flow, security properties, troubleshooting, and
limitations.

## Process and window inspection

Phase 12D adds five read-only tools for inspecting running processes and the
foreground desktop window. They use only the Python standard library and
read-only Windows APIs; no `psutil` dependency is required. No tool can
terminate, suspend, reprioritize, inject into, or debug a process, and no tool
can close a window, capture a screenshot, or send keyboard or mouse input.

| Tool | Category | Confirmation | Purpose |
| --- | --- | --- | --- |
| `system_process_list` | read-only | no | List running processes with pid and image name. |
| `system_process_info` | read-only | no | Inspect one process by pid, including bounded CPU and memory data. |
| `system_application_status` | read-only | no | Check whether an allowlisted application is running. |
| `system_active_window` | read-only | no | Return the foreground window title, owner pid, and process name. |
| `system_resource_summary` | read-only | no | Return the top CPU- and memory-consuming processes. |

The tools are disabled by default and denied in private mode. Process
enumeration uses a bounded Toolhelp32 snapshot; per-process measurements use
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`, `GetProcessTimes`,
`GetProcessMemoryInfo`, and `GetExitCodeProcess`. The foreground window is read
with `GetForegroundWindow`, `GetWindowTextW`, and
`GetWindowThreadProcessId`.

Privacy protections:

- core operating-system processes are omitted from listings and refused by
  direct inspection;
- command lines, environment variables, process memory, owners, profile paths,
  and executable paths are never read or returned;
- process names and window titles are bounded and stripped of control
  characters, and window titles are marked `untrusted`;
- access-denied and exited processes are reported distinctly, and an
  inaccessible process is never reported as stopped;
- `system_application_status` reuses the Phase 12B server-side allowlist and
  accepts only an alias, never an executable path;
- unsupported operating systems fail closed.

```env
# Process and active-window inspection (Phase 12D). Disabled by default.
TOOL_PROCESS_INSPECTION_ENABLED=false
TOOL_PROCESS_MAX_RESULTS=25
TOOL_PROCESS_MAX_MATCHES=20
TOOL_PROCESS_MAX_NAME_CHARS=128
TOOL_PROCESS_MAX_TITLE_CHARS=300
TOOL_PROCESS_CPU_SAMPLE_MS=100
```

See [docs/process_inspection.md](docs/process_inspection.md) for the API
details, result shapes, privacy strategy, configuration, tests, and known
limitations.

## Clipboard, keyboard, and mouse interaction

Phase 12E adds six narrowly scoped desktop-interaction tools. They use only the
Python standard library `ctypes` module and a small set of Windows APIs; no
`pyautogui`, `pyperclip`, or other automation dependency is required.

| Tool | Category | Confirmation | Purpose |
| --- | --- | --- | --- |
| `system_clipboard_read` | read-only | yes | Read bounded plain Unicode text from the clipboard. |
| `system_clipboard_write` | reversible | yes | Replace the clipboard with bounded plain Unicode text. |
| `system_keyboard_press` | reversible | yes | Press one allowlisted key once. |
| `system_mouse_position` | read-only | no | Return the current pointer coordinates. |
| `system_mouse_move` | reversible | yes | Move the pointer to validated screen coordinates. |
| `system_mouse_click` | reversible | yes | Move to validated coordinates and perform one left click. |

The tools are disabled by default, enabled by separate clipboard, keyboard, and
mouse flags, and denied in private mode. Clipboard reads and every state change
require a single-use confirmation bound to the owner, conversation, tool, and
arguments; expired, replayed, altered, rejected, and mismatched confirmations
are denied without acting.

Restrictions:

- only plain `CF_UNICODETEXT` clipboard content is read or written, bounded in
  both directions; images, files, HTML, and rich text are ignored;
- clipboard writes replace the previous contents, disclose the replacement in
  the confirmation preview, and never store the text in history;
- clipboard content is marked `untrusted` and is never interpreted as an
  instruction;
- only one of fourteen allowlisted keys can be pressed per execution
  (`ENTER`, `ESC`, `TAB`, `BACKSPACE`, `DELETE`, `SPACE`, `UP`, `DOWN`, `LEFT`,
  `RIGHT`, `HOME`, `END`, `PAGE_UP`, `PAGE_DOWN`);
- no arbitrary typing, key combinations, holding, keylogging, or background
  monitoring;
- the pointer can be read, moved to validated virtual-screen coordinates, or
  clicked once with the left button;
- no double-click, right-click, middle-click, drag, scroll, or continuous
  movement;
- no screenshot, screen capture, secure-desktop access, elevation request,
  privilege adjustment, or other-session control;
- no PowerShell, CMD, shell, `subprocess`, input hook, or arbitrary script;
- unsupported operating systems fail closed.

```env
# Clipboard, keyboard, and mouse interaction (Phase 12E). Disabled by default.
TOOL_CLIPBOARD_ENABLED=false
TOOL_CLIPBOARD_MAX_READ_CHARS=4000
TOOL_CLIPBOARD_MAX_WRITE_CHARS=4000

TOOL_KEYBOARD_ENABLED=false

TOOL_MOUSE_ENABLED=false
```

See [docs/desktop_interaction.md](docs/desktop_interaction.md) for the API
details, result shapes, confirmation flow, security properties, configuration,
tests, and known limitations.

## OS automation security

Phase 12F hardens every Phase 12 OS automation tool without adding new
capabilities. A deployment-wide master switch (`TOOL_OS_AUTOMATION_ENABLED`)
must be true in addition to each capability flag; when it is false, all OS tools
are denied. The central policy is layered on the existing permission engine and
cannot be changed by the model.

- **Emergency stop:** an authenticated owner can call
  `POST /api/tools/emergency-stop` to block new OS operations and invalidate
  pending confirmations. Recovery requires the server-side
  `TOOL_OS_EMERGENCY_RECOVERY_TOKEN`; authentication alone is not enough. The
  stop never kills processes, touches ASTA/PostgreSQL, deletes files, or changes
  settings. It is process-local and clears on restart.
- **Rate limits:** owner-isolated, server-side sliding-window limits are applied
  before every OS execution. The model cannot raise them. The limiter is
  in-memory and per process.
- **Audit logging:** one metadata-only event per OS execution and emergency-stop
  action (timestamp, owner, conversation, tool, category, status, confirmation,
  duration, safe error, rate-limit outcome). Argument values, clipboard text,
  file contents and paths, command lines, window titles, process names, and
  tokens are never logged.
- **Untrusted data:** clipboard text, window titles, process names, filenames,
  file contents, application metadata, and search results are data only. They
  cannot grant permission, skip confirmation, change settings, or invoke the
  emergency stop.
- **Failure handling:** execution fails closed, non-idempotent and
  confirmation-requiring actions are never retried automatically, and an
  uncertain outcome is never reported as success. Confirmed OS side effects are
  not transactional and may not be rollbackable.

All existing restrictions remain: workspace-confined filesystem access, no
permanent deletion, allowlist-only application launching with no arguments,
shell, or elevation, read-only process inspection, and bounded clipboard,
keyboard, and mouse interaction.

```env
# OS automation security (Phase 12F). Master switch plus per-capability flags.
TOOL_OS_AUTOMATION_ENABLED=false
TOOL_OS_RATE_LIMIT_ENABLED=true
TOOL_OS_MAX_CALLS_PER_MINUTE=30
TOOL_OS_MAX_STATE_CHANGES_PER_MINUTE=10
TOOL_OS_MAX_CALLS_PER_HOUR=300
# Leave empty to disable emergency-stop recovery.
TOOL_OS_EMERGENCY_RECOVERY_TOKEN=
```

See [docs/os_automation_security.md](docs/os_automation_security.md) for the full
model, operator runbook, and known single-process limitations.

## Authentication and local-network access

ASTA has one owner account. Passwords are hashed with Argon2id, and login issues
an opaque, revocable session cookie (`HttpOnly`, `Secure` by default,
`SameSite=Lax`) plus a CSRF token. Chat, conversations, memories, preferences,
tasks, and task-action confirmations all require a valid session; identity is
never accepted from request bodies, query strings, or headers.

Failed login and owner-setup attempts are counted per source IP and normalised
account identifier in PostgreSQL. Five failures start a 15-minute cooldown, and
repeated abuse doubles the cooldown up to 24 hours. Blocked requests return
`429 Too Many Requests` with `Retry-After`. Cooldowns always expire, a
successful login clears the pair's state, and a restart does not reset the
counter.

Create the owner with the local CLI (`scripts/create_owner.py`) or the one-time
`POST /api/auth/setup` endpoint guarded by `AUTH_OWNER_SETUP_TOKEN`. For phone
access, use HTTPS on a private LAN port, restrict Windows Firewall to the Private
profile and `LocalSubnet`, and keep PostgreSQL and Ollama on loopback only.
`X-Forwarded-For` is ignored unless the direct peer is listed in
`AUTH_TRUSTED_PROXIES`.

See [docs/authentication_and_lan_security.md](docs/authentication_and_lan_security.md)
for owner setup, login/logout, session lifecycle, CSRF, LAN/HTTPS configuration,
Windows Firewall rules, the future frontend contract, and security limitations.

## API overview

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/auth/setup` | Create the single owner (one-time, setup token required) |
| `POST` | `/api/auth/login` | Authenticate and start a session |
| `POST` | `/api/auth/logout` | Revoke the current session |
| `GET` | `/api/auth/me` | Return the authenticated owner and expiry |
| `POST` | `/api/chat/` | Send a message, optionally continuing a conversation |
| `POST` | `/api/conversations/` | Create a conversation |
| `GET` | `/api/conversations/` | List the current user's conversations |
| `GET` | `/api/conversations/{id}` | Retrieve one conversation |
| `PATCH` | `/api/conversations/{id}` | Rename a conversation |
| `DELETE` | `/api/conversations/{id}` | Delete a conversation and its messages |
| `GET` | `/api/conversations/{id}/messages` | List messages (paginated, chronological) |
| `POST` | `/api/preferences/` | Explicitly create an active preference |
| `GET` | `/api/preferences/` | List preferences (filter by category/status) |
| `GET` | `/api/preferences/{id}` | Retrieve one preference |
| `PATCH` | `/api/preferences/{id}` | Update a preference |
| `DELETE` | `/api/preferences/{id}` | Delete a preference |
| `POST` | `/api/preferences/{id}/deactivate` | Deactivate a preference |
| `POST` | `/api/preferences/extract` | Return inferred candidates without saving |
| `GET` | `/api/preferences/suggestions/` | List pending suggestions |
| `POST` | `/api/preferences/suggestions/` | Extract and store pending suggestions |
| `POST` | `/api/preferences/suggestions/{id}/approve` | Approve a suggestion |
| `POST` | `/api/preferences/suggestions/{id}/reject` | Reject a suggestion |
| `POST` | `/api/tasks/` | Create a task |
| `GET` | `/api/tasks/` | List tasks (filters, pagination, sorting) |
| `GET` | `/api/tasks/{id}` | Retrieve one task |
| `PATCH` | `/api/tasks/{id}` | Partially update a task |
| `DELETE` | `/api/tasks/{id}` | Delete a task |
| `POST` | `/api/tasks/{id}/complete` | Complete a task |
| `POST` | `/api/tasks/{id}/reopen` | Reopen a task |
| `POST` | `/api/tasks/{id}/priority` | Change task priority |
| `POST` | `/api/tasks/{id}/status` | Change task status |
| `GET` | `/api/tasks/upcoming` | Upcoming deadlines |
| `GET` | `/api/tasks/overdue` | Overdue tasks |
| `GET` | `/api/tasks/today` | Tasks due today |
| `GET` | `/api/tasks/tomorrow` | Tasks due tomorrow |
| `GET` | `/api/tasks/completed` | Completed tasks |
| `GET` | `/api/tasks/pending` | Pending tasks |
| `GET` | `/api/tasks/in-progress` | In-progress tasks |
| `POST` | `/api/tasks/{id}/reminders` | Create a task reminder |
| `GET` | `/api/tasks/{id}/reminders` | List reminders for one task |
| `GET` | `/api/reminders` | List the owner's reminders across tasks |
| `PATCH` | `/api/reminders/{id}` | Reschedule a pending reminder |
| `POST` | `/api/reminders/{id}/cancel` | Cancel a pending reminder |
| `DELETE` | `/api/reminders/{id}` | Delete a reminder |
| `GET` | `/api/notifications` | List notification history (newest first) |
| `GET` | `/api/notifications/unread` | List unread notifications |
| `GET` | `/api/notifications/{id}` | Retrieve one notification |
| `PATCH` | `/api/notifications/{id}/read` | Mark one notification as read |
| `POST` | `/api/notifications/{id}/snooze` | Snooze a delivered notification |
| `POST` | `/api/notifications/read-all` | Mark every unread notification as read |
| `DELETE` | `/api/notifications/{id}` | Delete one notification |
| `GET` | `/api/notifications/preferences` | Read effective notification preferences |
| `PATCH` | `/api/notifications/preferences` | Update notifications and quiet hours |
| `POST` | `/api/tasks/{id}/recurrence` | Create a recurrence rule for an owned open task |
| `GET` | `/api/tasks/{id}/recurrence` | Read one task's recurrence rule |
| `GET` | `/api/recurrences` | List the owner's recurrence rules |
| `GET` | `/api/recurrences/{id}` | Retrieve one recurrence rule |
| `PATCH` | `/api/recurrences/{id}` | Update a recurrence rule |
| `DELETE` | `/api/recurrences/{id}` | Deactivate a rule (history is preserved) |
| `GET` | `/api/recurrences/{id}/occurrences` | List generated occurrence links |
| `GET` | `/api/task-actions/pending` | List active task-action confirmations |
| `POST` | `/api/task-actions/{id}/confirm` | Confirm and execute a pending action |
| `POST` | `/api/task-actions/{id}/reject` | Reject a pending action |
| `GET` | `/api/ai/usage/summary` | Aggregated AI usage and performance for the owner |
| `GET` | `/api/ai/usage/recent` | Recent metadata-only AI usage rows for the owner |
| `GET` | `/api/tools/` | List registered tools, including disabled ones |
| `POST` | `/api/tools/execute` | Execute a tool under full server-side policy |
| `GET` | `/api/tools/executions` | List metadata-only tool execution history |
| `GET` | `/api/tools/executions/stats` | Aggregate tool execution statistics |
| `GET` | `/api/tools/executions/{id}` | Retrieve one owner-scoped execution record |
| `GET` | `/api/tools/executions/{id}/status` | Read one execution's lifecycle and outcome |
| `GET` | `/api/tools/executions/{id}/summary` | Read a compact, payload-free execution summary |
| `GET` | `/api/tools/metrics` | Process-local aggregate tool metrics |
| `GET` | `/api/tools/confirmations` | List pending tool confirmations |
| `POST` | `/api/tools/confirmations/{id}/confirm` | Confirm and execute a pending tool call |
| `POST` | `/api/tools/confirmations/{id}/reject` | Reject a pending tool call |

All endpoints above except `POST /api/auth/login` and the one-time
`POST /api/auth/setup` require a valid session. State-changing requests must
also send `X-CSRF-Token`. `/`, `/health`, and `/api/health/providers` remain
public because they expose no private data.

## Configuration

Context and persistence limits are configured through environment variables:

```dotenv
CONVERSATION_HISTORY_LIMIT=20
CONVERSATION_MESSAGE_PAGE_SIZE=50
PREFERENCE_RETRIEVAL_LIMIT=8
PREFERENCE_RELEVANCE_THRESHOLD=0.15
PREFERENCE_MAX_VALUE_CHARS=1000
CONTEXT_MAX_CHARS=12000
CONTEXT_PREFERENCE_SECTION_CHARS=2500
CONTEXT_MEMORY_SECTION_CHARS=5000
CONTEXT_HISTORY_SECTION_CHARS=6000
CONTEXT_TASK_SECTION_CHARS=2000
CONTEXT_DEADLINE_SECTION_CHARS=2000
CONTEXT_PRIORITY_ORDER=history,task,deadline,preference,memory
TASK_DEFAULT_UPCOMING_DAYS=7
TASK_CONTEXT_UPCOMING_DAYS=7
TASK_CONTEXT_LIMIT=5
DEADLINE_CONTEXT_LIMIT=5
TASK_INTENT_ENABLED=true
TASK_INTENT_LLM_ENABLED=true
TASK_ACTION_CONFIRMATION_TTL_SECONDS=600
TASK_DEFAULT_TIMEZONE=
TASK_INTENT_MAX_RESULTS=20
REMINDER_DEFAULT_LOCAL_TIME=09:00

# Reminder scheduler, notifications, and recurrence (Phase 10)
REMINDER_SCHEDULER_ENABLED=true
REMINDER_SCHEDULER_POLL_SECONDS=30
REMINDER_SCHEDULER_BATCH_SIZE=25
REMINDER_PROCESSING_LEASE_SECONDS=300
REMINDER_MAX_ATTEMPTS=5
REMINDER_RETRY_BACKOFF_SECONDS=60
REMINDER_RETRY_BACKOFF_MULTIPLIER=2
REMINDER_RETRY_BACKOFF_MAX_SECONDS=3600
REMINDER_SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS=10
NOTIFICATION_RETENTION_DAYS=365
NOTIFICATION_MAX_ATTEMPTS=5
NOTIFICATION_SNOOZE_MAX_COUNT=10
NOTIFICATION_MAX_SNOOZE_MINUTES=525600
NOTIFICATION_QUIET_HOURS_DEFAULT_ENABLED=false
NOTIFICATION_QUIET_HOURS_DEFAULT_START=22:00
NOTIFICATION_QUIET_HOURS_DEFAULT_END=07:00
NOTIFICATION_QUIET_HOURS_DEFAULT_TIMEZONE=UTC
RECURRENCE_GENERATION_ENABLED=true
RECURRENCE_GENERATION_HORIZON_DAYS=30
RECURRENCE_GENERATION_BATCH_SIZE=25
RECURRENCE_MAX_GENERATIONS_PER_RUN=10
RECURRENCE_GENERATION_INTERVAL_SECONDS=3600
RECURRENCE_RETRY_BACKOFF_SECONDS=300
RECURRENCE_PROCESSING_LEASE_SECONDS=300

# Single-owner authentication
AUTH_OWNER_SETUP_TOKEN=
AUTH_SESSION_LIFETIME_HOURS=168
AUTH_SESSION_COOKIE_NAME=asta_session
AUTH_CSRF_COOKIE_NAME=asta_csrf
AUTH_COOKIE_PATH=/api
AUTH_CSRF_COOKIE_PATH=/
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
AUTH_SESSION_LAST_USED_UPDATE_SECONDS=300
AUTH_PASSWORD_MIN_LENGTH=12
AUTH_PASSWORD_MAX_LENGTH=128
AUTH_ARGON2_TIME_COST=3
AUTH_ARGON2_MEMORY_COST=65536
AUTH_ARGON2_PARALLELISM=4

# Database-backed login rate limiting
AUTH_LOGIN_RATE_LIMIT_ENABLED=true
AUTH_LOGIN_MAX_FAILED_ATTEMPTS=5
AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES=15
AUTH_LOGIN_COOLDOWN_MULTIPLIER=2
AUTH_LOGIN_MAX_COOLDOWN_MINUTES=1440
AUTH_LOGIN_ATTEMPT_RETENTION_HOURS=48
AUTH_TRUSTED_PROXIES=

# AI optimization (Phase 9)
AI_TOKEN_BUDGET_ENABLED=true
AI_CHARS_PER_TOKEN=4.0
AI_DEFAULT_CONTEXT_WINDOW_TOKENS=8192
AI_GROQ_CONTEXT_WINDOW_TOKENS=131072
AI_OLLAMA_CONTEXT_WINDOW_TOKENS=40960
AI_MODEL_CONTEXT_WINDOWS=
AI_RESPONSE_RESERVE_TOKENS=1024
AI_CONTEXT_SAFETY_TOKENS=256
AI_QUERY_CLASSIFIER_ENABLED=true
AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED=false
AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD=0.6
AI_ROUTING_POLICY=groq_first
AI_ROUTING_SIMPLE_PROVIDER=ollama
AI_ROUTING_MODERATE_PROVIDER=groq
AI_ROUTING_COMPLEX_PROVIDER=groq
AI_ROUTING_DYNAMIC_PROVIDER=groq
AI_ROUTING_MODEL_OVERRIDES=
AI_GROQ_INPUT_COST_PER_MILLION=0
AI_GROQ_OUTPUT_COST_PER_MILLION=0
AI_OLLAMA_INPUT_COST_PER_MILLION=0
AI_OLLAMA_OUTPUT_COST_PER_MILLION=0
AI_CACHE_ENABLED=true
AI_CACHE_SIMILARITY_THRESHOLD=0.95
AI_CACHE_TTL_SECONDS=3600
AI_CACHE_MAX_ENTRIES_PER_USER=500
AI_CACHE_PROMPT_VERSION=phase9-v1
AI_CACHE_CONFIG_VERSION=1
AI_CACHE_ALLOWED_COMPLEXITIES=simple,moderate
AI_CACHE_STATELESS_ONLY=true
AI_CACHE_SEMANTIC_ENABLED=true
AI_USAGE_TRACKING_ENABLED=true
AI_USAGE_RETENTION_DAYS=90
AI_USAGE_RECENT_LIMIT=50
AI_MEMORY_HYBRID_ENABLED=true
AI_MEMORY_SEMANTIC_WEIGHT=0.7
AI_MEMORY_KEYWORD_WEIGHT=0.3
AI_MEMORY_CANDIDATE_LIMIT=20
AI_MEMORY_MIN_HYBRID_SCORE=0.05

# Tool engine (Phases 11A-11I)
TOOL_CALLING_ENABLED=false
TOOL_MAX_CALLS_PER_REQUEST=5
TOOL_MAX_CALLS_PER_TURN=8
TOOL_MAX_ROUNDS=2
TOOL_MAX_RESULT_CONTEXT_BYTES=50000
TOOL_LOOP_PREVENTION_ENABLED=true
TOOL_EXECUTION_TIMEOUT_SECONDS=15
TOOL_MAX_ARGUMENT_BYTES=16384
TOOL_MAX_RESULT_BYTES=20000
TOOL_CONFIRMATION_TTL_SECONDS=600
TOOL_CONFIRMATION_MAX_PENDING=10
TOOL_HISTORY_ENABLED=true
TOOL_EXECUTION_RETENTION_DAYS=90
TOOL_ALLOW_SENSITIVE=false
TOOL_RETRY_ENABLED=false
TOOL_MAX_RETRIES=1
TOOL_RETRY_BACKOFF_MS=250
TOOL_RECOVERY_ON_STARTUP=true
TOOL_STALE_EXECUTION_SECONDS=300

# Web search (Phase 11E). Disabled by default.
# searxng: self-hosted, no API key, requires format=json on the instance.
# tavily: managed API, requires WEB_SEARCH_API_KEY.
WEB_SEARCH_ENABLED=false
WEB_SEARCH_PROVIDER=searxng
WEB_SEARCH_BASE_URL=
WEB_SEARCH_API_KEY=
WEB_SEARCH_TIMEOUT_SECONDS=10
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_MAX_RESPONSE_BYTES=262144

# Filesystem tools (Phase 11F). Disabled by default.
# Comma-separated absolute directories that may be accessed.
TOOL_FILESYSTEM_ENABLED=false
TOOL_WORKSPACE_ROOTS=
TOOL_FILE_MAX_READ_BYTES=200000
TOOL_FILE_MAX_WRITE_BYTES=100000
TOOL_FILE_MAX_LIST_ENTRIES=500
TOOL_FILE_MAX_SEARCH_RESULTS=100

# System information tools (Phase 12A). Disabled by default.
# Empty disk roots uses the drive containing the running interpreter.
TOOL_SYSTEM_INFO_ENABLED=false
TOOL_SYSTEM_INFO_DISK_ROOTS=
TOOL_SYSTEM_INFO_MAX_DISKS=8

# Process and active-window inspection (Phase 12D). Disabled by default.
TOOL_PROCESS_INSPECTION_ENABLED=false
TOOL_PROCESS_MAX_RESULTS=25
TOOL_PROCESS_MAX_MATCHES=20
TOOL_PROCESS_MAX_NAME_CHARS=128
TOOL_PROCESS_MAX_TITLE_CHARS=300
TOOL_PROCESS_CPU_SAMPLE_MS=100

# Clipboard, keyboard, and mouse interaction (Phase 12E). Disabled by default.
TOOL_CLIPBOARD_ENABLED=false
TOOL_CLIPBOARD_MAX_READ_CHARS=4000
TOOL_CLIPBOARD_MAX_WRITE_CHARS=4000
TOOL_KEYBOARD_ENABLED=false
TOOL_MOUSE_ENABLED=false

# Python execution sandbox (Phase 11G). Disabled by default.
# Requires Windows AppContainer, a standalone Python 3 interpreter, and
# TOOL_ALLOW_SENSITIVE=true plus per-call confirmation.
PYTHON_SANDBOX_ENABLED=false
PYTHON_SANDBOX_TIMEOUT_SECONDS=5
PYTHON_SANDBOX_CPU_SECONDS=5
PYTHON_SANDBOX_MEMORY_MB=256
PYTHON_SANDBOX_MAX_SOURCE_BYTES=20000
PYTHON_SANDBOX_MAX_OUTPUT_BYTES=20000
# Network access is not supported by the backend. Keep false.
PYTHON_SANDBOX_ALLOW_NETWORK=false
# Empty uses the system temporary directory for disposable workspaces.
PYTHON_SANDBOX_WORKSPACE_PARENT=

# Explicit frontend origins (empty for same-origin) and Host allowlist
CORS_ALLOWED_ORIGINS=
TRUSTED_HOSTS=localhost,127.0.0.1,::1,testserver,test
```

IANA timezone support uses Python's `zoneinfo`. `tzdata` is included in
`requirements.txt` for platforms such as Windows that do not ship system
timezone data.

Apply database migrations with:

```powershell
cd backend
..\venv\Scripts\python.exe -m alembic upgrade head
```

## Tests

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Real-PostgreSQL integration checks (Phase 7E, `asta_db` only) run separately:

```powershell
cd backend
..\venv\Scripts\python.exe scripts\verify_phase7e_integration.py
```

Phase 8 authentication and session checks on real PostgreSQL (`asta_db` only,
throwaway rows, automatic cleanup):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase8_auth -v
..\venv\Scripts\python.exe scripts\verify_phase8_auth.py
```

Phase 8.1 dependency and rate-limit checks:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase81_dependencies tests.test_phase81_rate_limit -v
..\venv\Scripts\python.exe scripts\verify_phase81_dependencies.py
..\venv\Scripts\python.exe scripts\verify_phase81_rate_limit.py
```

Phase 9 focused AI optimization checks (token budgets, classification, routing,
cache, usage tracking, hybrid memory retrieval, and chat cache integration):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase9_token_budget tests.test_phase9_query_classifier tests.test_phase9_routing tests.test_phase9_cache tests.test_phase9_usage tests.test_phase9_usage_api tests.test_phase9_hybrid_retrieval tests.test_phase9_chat_integration -v
```

All Phase 9 tests mock Groq and Ollama; no real provider call is made.

Phase 10A reminder checks (model, CRUD, ownership, scheduling, timezone
anchoring, duplicate prevention, and cascade deletion):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase10_reminders -v
```

Phase 11A tool registry and `BaseTool` checks:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11a_base tests.test_phase11a_registry -v
```

Phase 11B provider function-calling and router checks (mocked SDK responses; no
real provider call):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11b_tool_calling -v
```

Phase 11C tool execution engine checks (mocked tools and in-memory SQLite):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11c_executor -v
```

Phase 11D permission and confirmation checks (mocked tools, in-memory SQLite,
real authentication and CSRF):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11d_permissions -v
```

Phase 11E web search checks (mocked HTTP transport; no real search request):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11e_web_search -v
```

Phase 11F filesystem checks (temporary directories only; no personal files):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11f_filesystem -v
```

Phase 11G Python sandbox checks (config/unit/executor tests everywhere; real
AppContainer integration tests on supported Windows hosts):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11g_python_sandbox -v
```

Phase 11H chat/API tool-calling checks (mocked providers and tools; in-memory
SQLite):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11h_tool_calling_api -v
```

Phase 12A system information checks (mocked OS values; temporary disk roots):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12a_system_info -v
```

Phase 12B application launching checks (mocked process launches; temporary
executable fixtures):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12b_application_launcher -v
```

Phase 12D process and window inspection checks (mocked Windows APIs; no real
process is inspected or modified):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12d_process_inspection -v
```

Phase 12E clipboard, keyboard, and mouse checks (mocked Windows APIs; no real
clipboard access or input is performed):

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase12e_desktop_interaction -v
```

Combined Phase 11A–11H checks:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11a_base tests.test_phase11a_registry tests.test_phase11b_tool_calling tests.test_phase11c_executor tests.test_phase11d_permissions tests.test_phase11e_web_search tests.test_phase11f_filesystem tests.test_phase11g_python_sandbox tests.test_phase11h_tool_calling_api -v
```

Phase 10B–10H scheduler, notification, recurrence, preference, integration,
snooze, and reschedule checks:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase10_scheduler_notifications tests.test_phase10_recurrence tests.test_phase10_reliability tests.test_phase10_preferences tests.test_phase10_integration tests.test_phase10_snooze_reschedule -v
```

Real PostgreSQL locking and concurrency checks are opt-in and use only
temporary rows in `asta_db`:

```powershell
cd backend
$env:ASTA_PHASE10_POSTGRES_TESTS="1"
..\venv\Scripts\python.exe -m unittest tests.test_phase10_snooze_postgres -v
```
