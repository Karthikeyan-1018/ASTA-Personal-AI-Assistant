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
