# Tool History and Reliability (Phase 11I)

Phase 11I makes tool execution observable, recoverable, and retry-safe. It
extends the existing metadata-only history from Phase 11C rather than adding a
parallel execution system. Every call still passes through `ToolExecutor`, the
same registry, permission policy, and confirmation service used by the direct
`/api/tools` routes and the Phase 11H chat integration.

The phase adds:

- a validated execution lifecycle with explicit legal transitions;
- start/completion timestamps, retry counts, truncation flags, and a hashed
  idempotency key on each history row;
- owner-scoped detail, status, and summary endpoints;
- opt-in, bounded retries for eligible idempotent read-only tools;
- duplicate-execution prevention through an optional `Idempotency-Key` header;
- startup recovery of executions left in flight by a previous process;
- process-local counters, structured logs, and a metrics endpoint.

Nothing in this phase weakens confirmation, permission, filesystem, or sandbox
controls. Phase 11D, 11F, and 11G behaviour is unchanged.

## Execution lifecycle

The coarse `status` column is preserved for compatibility. A separate
`lifecycle_status` column models the full state machine:

| Lifecycle | Meaning |
| --- | --- |
| `pending` | Accepted, not yet authorized or started |
| `awaiting_confirmation` | Waiting for the owner to approve or reject |
| `approved` | Confirmation claimed, about to run |
| `running` | Tool implementation is executing |
| `succeeded` | Tool returned a successful result |
| `failed` | Validation, permission, configuration, or tool error |
| `rejected` | Denied by policy, rejected by the owner, or confirmation mismatch |
| `expired` | Confirmation expired before a decision |
| `cancelled` | Caller cancellation or an attempt interrupted by a restart |
| `timed_out` | Execution exceeded `TOOL_EXECUTION_TIMEOUT_SECONDS` |

Legal transitions are enforced by `app/tools/lifecycle.py`:

```
pending ──► awaiting_confirmation ──► approved ──► running ──► succeeded
   │                │                    │            ├──────► failed
   │                ├──► rejected        └──► failed  ├──────► timed_out
   │                ├──► expired                      └──────► cancelled
   ├──► running ──► (terminal outcomes)
   └──► failed / rejected / expired / cancelled
```

Terminal states never transition to a different terminal state. Re-writing the
same terminal state is allowed so idempotent retries and replay reporting remain
safe, but a completed outcome is never overwritten with a contradictory one. An
illegal transition is logged and ignored; it never changes the result of a tool
call.

A confirmation-required call records one `awaiting_confirmation` row. Approval
runs the exact stored arguments as a new attempt and records a second row with
`confirmation_status=confirmed`, preserving Phase 11D semantics. Rejection does
not execute anything.

## Persistence schema

Migration `b2c3d4e5f6a7_add_tool_execution_lifecycle` (revision after
`a1b2c3d4e5f6`) adds these columns to `tool_executions`:

| Column | Type | Notes |
| --- | --- | --- |
| `lifecycle_status` | `VARCHAR(32)` | Non-null, defaults to `pending` |
| `started_at` | timestamptz | Set when the attempt is accepted |
| `completed_at` | timestamptz | Set for every terminal outcome |
| `retry_count` | integer | Non-negative, defaults to `0` |
| `truncated` | boolean | Result was bounded by `TOOL_MAX_RESULT_BYTES` |
| `idempotency_key` | `VARCHAR(128)` | SHA-256 digest only, nullable |

The migration backfills existing rows from `status`:

| Old `status` | Backfilled lifecycle |
| --- | --- |
| `success` | `succeeded` |
| `timeout` | `timed_out` |
| `denied` | `rejected` |
| `confirmation_required` | `awaiting_confirmation` |
| anything else | `failed` |

Existing `started_at`/`completed_at` values are filled from `created_at`.
The status check constraint is widened to include `pending`, `running`, and
`cancelled`; a lifecycle check constraint and three indexes are added, including
a unique `(user_id, idempotency_key)` index. Downgrade coerces in-flight and
cancelled rows to `error` before restoring the original constraint, then drops
the new indexes and columns.

The table continues to store no arguments, results, file contents, prompts,
Python source, credentials, or raw idempotency keys.

## History API

All endpoints require an authenticated session and are owner-scoped. A missing
or cross-owner execution id returns `404`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools/executions` | Paginated metadata history |
| `GET` | `/api/tools/executions/stats` | Aggregate outcomes, lifecycle, retries, confirmations |
| `GET` | `/api/tools/executions/{id}` | Full metadata row for one attempt |
| `GET` | `/api/tools/executions/{id}/status` | Current lifecycle and outcome |
| `GET` | `/api/tools/executions/{id}/summary` | Compact safe summary |
| `GET` | `/api/tools/metrics` | Process-local aggregate counters |

The list endpoint accepts `limit`, `offset`, `tool`, `status`, and `lifecycle`.
Unknown status or lifecycle filters return `400`. Responses never include raw
tool output or argument values. The detail response may include argument field
names, the argument digest, byte counts, and safe error categories.

### Idempotency

`POST /api/tools/execute` accepts an optional `Idempotency-Key` header (maximum
200 characters). Only a SHA-256 digest is stored. If the same owner already used
the key:

- a completed attempt returns a metadata-only replay with
  `idempotent_replay=true` and `data=null`;
- an in-flight attempt returns `status=running` and
  `error_code=ToolExecutionInProgress` without executing the tool;
- a different owner may reuse the same key; keys are never shared across owners.

A replay never returns the original payload because results are not persisted.
Callers that need the value must execute a new request with a new key.

## Reliability and retries

The executor is fail-closed and normalizes every outcome. Timeouts use
`TOOL_EXECUTION_TIMEOUT_SECONDS`; caller cancellation always propagates as
`asyncio.CancelledError`, while a cancellation raised inside a tool becomes a
`ToolCancelled` result. History and metrics failures never change a tool result.

Retries are disabled by default and are deliberately narrow:

- `TOOL_RETRY_ENABLED` must be true;
- the tool must declare `idempotent = True`;
- the tool must be `READ_ONLY` and must not require confirmation;
- only `timeout` and `provider` error categories qualify;
- `TOOL_MAX_RETRIES` is capped at 3 retries per attempt;
- backoff uses `TOOL_RETRY_BACKOFF_MS`, doubling per attempt and capped at 5 s.

Cancellation, validation errors, permission denials, confirmation-required
results, and any tool that might have produced a side effect are never retried.
A tool that sends an email or submits a form must stay non-idempotent (the
default) so an automatic retry cannot duplicate the action. If such an action
needs recovery, it must verify remote state explicitly rather than retry blindly.

The built-in read-only tools `web_search`, `fs_list`, `fs_search`, `fs_read`,
`fs_metadata`, `task_list`, and `task_get` declare `idempotent = True`. Filesystem
writes, Python execution, HTTP requests, and other state-changing tools do not.

## Restart recovery

`ToolHistoryService.begin()` creates the in-flight row before any policy
decision or side effect. On startup, when `TOOL_RECOVERY_ON_STARTUP=true`,
`tool_history_service.recover_interrupted()` marks `pending`, `approved`, and
`running` rows older than `TOOL_STALE_EXECUTION_SECONDS` as `cancelled` with
error code `InterruptedExecution`. It records `completed_at` and increments the
recovery counter.

`awaiting_confirmation` rows are intentionally untouched: they wait for a user
decision, not a worker. Recovery is best effort and wrapped in try/except so a
database problem never prevents the API from starting.

This is a single-process design. In a multi-process deployment, the stale cutoff
must exceed the longest expected execution so one worker does not cancel another
worker's live attempt.

## Logging and metrics

Completed attempts emit one structured line:

```
tool_execution tool=<name> status=<status> lifecycle=<state> duration_ms=<n> \
retry_count=<n> confirmation=<state> error_code=<code>
```

The line contains no argument values, results, prompts, file contents, or
credentials. Provider failures and recovery events are logged as warnings.

`/api/tools/metrics` returns process-local counters that reset on restart:
executions, successes, failures, timeouts, cancellations, provider failures,
retries, confirmation approvals/rejections, duplicate requests prevented,
recoveries, duration samples, average/max duration, and per-tool counts. Durable
per-owner history/stats remain the source of truth in the database.

Application logs should be protected like any other operational data. The
metrics endpoint is authenticated, but the counters are currently global rather
than per-owner.

## Retention

`TOOL_EXECUTION_RETENTION_DAYS` (default 90) controls `purge_expired()`. Only
history rows older than the cutoff are deleted; confirmations have their own
lifecycle and expiry. Set the value to `0` to disable purging.

## Configuration

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

## Tests

`tests/test_phase11i_tool_history_reliability.py` covers lifecycle mapping and
illegal transitions, persistence of every outcome, caller cancellation, timeout,
truncation, owner isolation, retry eligibility and ceilings, confirmation-bound
non-retry, idempotent replay/in-flight duplicates, restart recovery, redaction,
API authentication and filters, metrics, provider-failure counting, and
confirmation approval/rejection regression. Databases are isolated in-memory
SQLite instances; providers and tools are controlled doubles. No network,
filesystem, or model call is made.

## Limitations

- History and metrics are metadata-only. There is no raw output or argument
  replay, by design.
- Idempotency prevents duplicate execution but cannot return the original result
  payload. A replay reports the stored outcome only.
- Metrics are in-memory and process-global; they reset on restart and are not
  per-owner or exportable to Prometheus/OpenTelemetry.
- Recovery is designed for a single process. Multi-process deployments need a
  larger stale cutoff or an external lease.
- PostgreSQL upgrade/downgrade is validated by offline SQL generation only in
  this environment; an actual disposable PostgreSQL run must be performed where
  a server is available. `agriculture_db` is never touched.
- Retry policy is conservative. A transient failure in a non-idempotent tool is
  surfaced to the caller instead of retried automatically.
