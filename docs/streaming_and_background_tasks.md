# Streaming responses and background tasks

Phase 13 adds two additive capabilities:

1. token streaming for chat, exposed as Server-Sent Events (SSE); and
2. durable, in-process background tasks for allowlisted maintenance work.

Neither capability introduces a parallel architecture. Streaming reuses the
existing authentication, conversation ownership, `ChatService`, and LLM router.
Background tasks reuse the existing database, memory embedding service, memory
extractor, and conversation service. The historical `POST /api/chat/` route and
the entire tool pipeline are unchanged.

## Streaming chat

### Endpoint

```http
POST /api/chat/stream
Content-Type: application/json
X-CSRF-Token: <token>

{
  "message": "Summarise my open tasks",
  "conversation_id": 42,
  "private": false
}
```

The request body is the same shape as `POST /api/chat/`. Authentication and
conversation ownership are validated **before** the response starts, so an
unauthenticated request receives `401` and a foreign conversation receives `404`
rather than an in-band SSE error.

The response is `text/event-stream` with `Cache-Control: no-cache` and
`X-Accel-Buffering: no` (to prevent proxy buffering). Each event is a single
`data:` line containing JSON:

```text
event: start
data: {"conversation_id":42,"provider":"groq","model":"..."}

event: delta
data: {"text":"Hello"}

event: done
data: {"conversation_id":42,"provider":"groq","model":"...","finish_reason":"stop","duration_ms":812.4,"usage":{...},"fallback_used":false}
```

### Event protocol

| Event | Meaning | Payload |
| --- | --- | --- |
| `start` | Stream accepted; conversation and selected route are known | `conversation_id`, `provider`, `model` |
| `delta` | One text fragment | `text` |
| `done` | Normal completion | `conversation_id`, actual `provider`/`model`, `finish_reason`, `duration_ms`, `usage`, `fallback_used` |
| `error` | Safe failure | `code`, `message`, `conversation_id` |

Error codes are stable and contain no provider or internal detail:

- `provider_error` — the provider failed mid-stream;
- `timeout` — the provider timed out;
- `unavailable` — no provider could serve the request;
- `empty_response` — the provider returned no usable text;
- `response_too_large` — the response exceeded `STREAM_MAX_RESPONSE_CHARS`;
- `internal_error` — an unexpected local failure;
- `conversation_not_found` — the conversation disappeared after validation.

### Persistence semantics

- The user message is persisted before the provider is called.
- The assistant message is persisted exactly once, only after a normal,
  non-empty completion.
- Partial, cancelled, failed, empty, and oversized answers are never stored.
- A completed turn appends to the existing history; prior messages are never
  rewritten or duplicated.
- Private turns keep the existing private-message behaviour: their history is
  excluded from later cloud prompts, and the assistant reply is marked private.

### Disconnects, cancellation, and resource cleanup

When the client disconnects, Starlette cancels the streaming task. The chat
service's generator closes the provider stream in a `finally` block and skips
assistant persistence. The user message is retained. The process-local
concurrency slot is always released, including on cancellation and failure.

### Provider fallback

The router tries providers in the configured order. Fallback is allowed only
before the first token. Once any text has been emitted, a provider failure ends
the stream with an error instead of concatenating a second provider's answer.
Private requests remain Ollama-only and never consult Groq.

### Concurrency and limits

`STREAM_MAX_CONCURRENT` bounds simultaneous streams in this process. A request
that cannot obtain a slot within `STREAM_QUEUE_TIMEOUT_SECONDS` receives
`429 Too Many Requests` with `Retry-After: 5`. The semaphore is process-local;
a multi-process deployment applies the limit per process.

`STREAM_TIMEOUT_SECONDS` and `STREAM_IDLE_TIMEOUT_SECONDS` bound overall and
inter-token wait time inside the provider adapters. `STREAM_MAX_RESPONSE_CHARS`
bounds accumulation in memory.

### Known streaming limitations

- Streaming bypasses the semantic response cache.
- Tool-enabled requests reuse the bounded orchestrator and emit its final answer
  as delta event(s); tool turns are not token streams. All tool permissions,
  confirmations, and OS security rules still apply.
- The concurrency guard is in-process, not cluster-wide.
- If the process dies mid-stream, the user message may remain without an
  assistant reply. This is deliberate: an incomplete answer is never stored.

## Background tasks

### Design

Tasks run in the application process using `asyncio`. There is no Celery,
Redis, RabbitMQ, or other broker. Task records are persisted in the
`background_tasks` table so status, ownership, and metadata survive a restart.

Only allowlisted task types are accepted:

| Task type | Parameters | Handler |
| --- | --- | --- |
| `memory_embedding` | `memory_id` (positive integer), `force` (boolean) | Existing memory embedding service |
| `memory_extraction` | `conversation_id` (positive integer), `private` (boolean) | Existing memory extractor |

Parameters are strictly validated; unknown keys are rejected. A task can never
submit a shell command, arbitrary tool call, URL, or free-form prompt. Handlers
re-check ownership before touching a memory or conversation.

### Lifecycle

```text
queued -> running -> completed
                  -> failed
                  -> cancelled
```

- `queued`: persisted, waiting for a concurrency slot.
- `running`: claimed atomically from `queued`.
- `completed`: handler returned a small JSON metadata result.
- `failed`: handler raised, timed out, or rejected its input. Only a generic,
  secret-free error code and message are stored.
- `cancelled`: the owner cancelled it, shutdown interrupted it, or startup
  recovery found a stale queued row.

A running task is cancelled cooperatively through asyncio task cancellation.
Cancelling an already terminal task is idempotent and returns the current row.

### Startup recovery and retention

When `BACKGROUND_TASK_RECOVERY_ON_STARTUP` is true:

- stale `running` rows become `failed` with `error_code="interrupted"`;
- stale `queued` rows become `cancelled` with `error_code="restart"`;
- nothing is retried automatically.

Terminal rows older than `BACKGROUND_TASK_RETENTION_DAYS` are deleted at
startup. Only metadata rows are affected.

### Graceful shutdown

The application lifespan calls the service's shutdown hook. In-flight tasks are
cancelled and awaited up to `BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS`; any
remaining incomplete rows are marked cancelled with `error_code="shutdown"`.
Tracked asyncio tasks are cleared.

### API

All routes require a valid session and CSRF protection for state changes.
Identity always comes from the server-side session; the client cannot supply an
owner id.

```http
POST /api/background-tasks/
{
  "task_type": "memory_embedding",
  "parameters": {"memory_id": 12, "force": false}
}
```

```json
{
  "id": 7,
  "task_type": "memory_embedding",
  "status": "queued",
  "result": null,
  "error_code": null,
  "error_message": null,
  "created_at": "2026-11-14T10:00:00Z",
  "started_at": null,
  "completed_at": null
}
```

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/background-tasks/` | Queue an allowlisted task |
| `GET` | `/api/background-tasks/` | List the owner's tasks (`status`, `limit`, `offset`) |
| `GET` | `/api/background-tasks/{id}` | Retrieve one owned task |
| `POST` | `/api/background-tasks/{id}/cancel` | Cancel an owned task |

Responses never include the stored handler parameters, owner id, prompts, or
raw handler output. Cross-owner access returns `404` so the API does not reveal
that another owner's task exists.

Status codes:

- `201` created;
- `400` unsupported type or invalid parameters;
- `404` missing or foreign task;
- `422` malformed request or oversized parameter object;
- `429` per-owner pending-task limit reached;
- `503` background processing disabled.

### Security properties

- Only allowlisted task types and strictly validated parameters are accepted.
- Handlers reuse existing services and never execute arbitrary commands.
- Ownership is enforced on every read, list, cancel, and handler lookup.
- Stored errors are generic and contain no exception text, prompts,
  credentials, or tool output.
- Logs contain task ids and exception class names, not exception messages.
- Cancellation is isolated per user: one owner cannot cancel another owner's
  task, and one task's failure cannot stop the runner.
- Background tasks do not bypass tool permissions, confirmations, or OS
  automation security because they cannot invoke tools.

### Known background-task limitations

- Execution is in-process. A crash loses in-flight work; recovery marks the rows
  rather than resuming them.
- Concurrency and per-user limits are per process.
- No automatic retry. Retrying a non-idempotent side effect is unsafe, so the
  caller must submit a new task.
- Task results are metadata only; large artifacts are not stored.
- The task list is newest-first and paginated; there is no full-text search.

## Configuration

```env
STREAMING_ENABLED=true
STREAM_TIMEOUT_SECONDS=120
STREAM_IDLE_TIMEOUT_SECONDS=30
STREAM_MAX_RESPONSE_CHARS=50000
STREAM_MAX_CONCURRENT=4
STREAM_QUEUE_TIMEOUT_SECONDS=5

BACKGROUND_TASKS_ENABLED=true
BACKGROUND_TASK_MAX_CONCURRENT=2
BACKGROUND_TASK_MAX_QUEUED_PER_USER=10
BACKGROUND_TASK_RETENTION_DAYS=30
BACKGROUND_TASK_SHUTDOWN_TIMEOUT_SECONDS=10
BACKGROUND_TASK_MAX_EXECUTION_SECONDS=300
BACKGROUND_TASK_MAX_CONVERSATION_CHARS=20000
BACKGROUND_TASK_RECOVERY_ON_STARTUP=true
```

## Tests

Focused suites:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase13a_streaming
..\venv\Scripts\python.exe -m unittest tests.test_phase13b_streaming_api
..\venv\Scripts\python.exe -m unittest tests.test_phase13c_streaming_persistence
..\venv\Scripts\python.exe -m unittest tests.test_phase13d_background_tasks
..\venv\Scripts\python.exe -m unittest tests.test_phase13e_background_task_api
..\venv\Scripts\python.exe -m unittest tests.test_phase13f_reliability
```

All provider and handler calls are mocked; no Groq, Ollama, embedding model, or
network access is required.
