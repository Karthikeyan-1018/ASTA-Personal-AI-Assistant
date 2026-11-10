# ASTA – Personal AI Assistant

ASTA is a context-aware personal AI assistant with a FastAPI backend,
PostgreSQL storage, hybrid Groq/Ollama LLM routing, and pgvector-backed
long-term memory.

## Current capabilities

- Hybrid LLM routing: Groq first, Ollama fallback.
- Private requests are routed to local Ollama only.
- Approved long-term memories with keyword or semantic retrieval.
- Memory-aware chat prompts.
- Persistent conversations and messages in PostgreSQL.
- Conversation CRUD, paginated message history, and resumable chats.
- Structured user preferences with explicit CRUD and inferred suggestions.
- Structured tasks with categories, priorities, lifecycle status, and deadlines.
- Natural-language task management with confirmation-gated mutations.
- Timezone-aware deadline views (overdue, today, tomorrow, upcoming).
- Unified context assembly from preferences, memories, tasks, deadlines, and bounded history.
- Per-user ownership checks on every conversation, message, preference, and task query.
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
and global character budgets. Private requests use Ollama only and exclude stored
task/deadline context.

See [docs/context_architecture.md](docs/context_architecture.md) for the full
retrieval flow, relevance rules, priority ordering, budgets, privacy guarantees,
failure handling, and example assembly.

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
| `GET` | `/api/task-actions/pending` | List active task-action confirmations |
| `POST` | `/api/task-actions/{id}/confirm` | Confirm and execute a pending action |
| `POST` | `/api/task-actions/{id}/reject` | Reject a pending action |

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
CONTEXT_PRIORITY_ORDER=history,preference,memory,task,deadline
TASK_DEFAULT_UPCOMING_DAYS=7
TASK_CONTEXT_UPCOMING_DAYS=7
TASK_CONTEXT_LIMIT=5
DEADLINE_CONTEXT_LIMIT=5
TASK_INTENT_ENABLED=true
TASK_INTENT_LLM_ENABLED=true
TASK_ACTION_CONFIRMATION_TTL_SECONDS=600
TASK_DEFAULT_TIMEZONE=
TASK_INTENT_MAX_RESULTS=20

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
