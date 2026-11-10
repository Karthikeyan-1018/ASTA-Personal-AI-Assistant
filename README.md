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

## API overview

| Method | Path | Purpose |
| --- | --- | --- |
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
```

IANA timezone support uses Python's `zoneinfo`. On platforms without system
timezone data (for example Windows), install `tzdata`:

```powershell
..\venv\Scripts\python.exe -m pip install tzdata
```

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
