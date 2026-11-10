# Task and Deadline Management

Phase 7B adds structured, user-owned tasks and deadlines to ASTA. Tasks are
stored in PostgreSQL, exposed through a user-scoped REST API, and injected into
chat prompts through the Phase 7A unified context architecture.

The implementation does **not** perform automatic task extraction, reminders,
notifications, JWT authentication, frontend work, or LangGraph orchestration.

## Database architecture

A single `tasks` table stores both the task and its optional deadline. There is
no separate deadline table: `due_date`, `due_time`, and `timezone` describe the
deadline, while the task's lifecycle is captured by `status`, `is_completed`,
and `completed_at`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `user_id` | integer | FK to `users.id`, `ON DELETE CASCADE` |
| `title` | varchar(255) | Required, non-blank |
| `description` | text | Optional, maximum 5000 characters |
| `category` | enum | `college`, `career`, `personal`, `fitness`, `project`, `other` |
| `priority` | enum | `low`, `medium`, `high`, `urgent` |
| `status` | enum | `pending`, `in_progress`, `completed`, `cancelled` |
| `due_date` | date | Optional deadline date |
| `due_time` | time | Optional wall-clock time; requires `due_date` |
| `timezone` | varchar(64) | Optional IANA timezone; requires `due_date` |
| `is_completed` | boolean | Mirrors `status == "completed"` |
| `created_at` | timestamptz | Server-managed |
| `updated_at` | timestamptz | Server-managed |
| `completed_at` | timestamptz | Set on completion, cleared on reopen |

Constraints and indexes:

- `due_time IS NULL OR due_date IS NOT NULL`
- `timezone IS NULL OR due_date IS NOT NULL`
- `(user_id, status)`, `(user_id, category)`, `(user_id, due_date)`,
  `(status, due_date)` indexes for common list and deadline queries.

A task without `due_date` is valid and has no deadline.

## Categories, priorities, and status lifecycle

Categories group tasks by area of life. Priorities are stable and ordered
`low < medium < high < urgent` for sorting and context selection.

A task moves through these states:

```
pending -> in_progress -> completed
pending/in_progress -> cancelled
completed/cancelled -> pending (reopen)
```

Rules:

- `POST /api/tasks/{id}/complete` sets `status=completed`, `is_completed=true`,
  and `completed_at` to the current UTC instant. Completing an already
  completed task is idempotent and preserves the original `completed_at`.
- `POST /api/tasks/{id}/reopen` sets `status=pending`, `is_completed=false`, and
  clears `completed_at`.
- Changing status to `completed` through the status endpoint applies the same
  completion rules. Changing to any other status clears completion metadata.
- `status` is the source of truth; `is_completed` is maintained as a convenient
  mirror for queries and API responses.

## Deadline and timezone handling

Deadlines are user-intended wall-clock values plus an optional IANA timezone:

- **Date-only** (`due_date` without `due_time`): resolves to the final
  microsecond of that date in the task's timezone. A date-only deadline is
  therefore not overdue on its due date and becomes overdue after the local due
  date has passed.
- **Date and time** (`due_date` + `due_time`): resolves to that wall-clock time
  in the task's timezone.
- **Explicit timezone**: for example `America/New_York`, `Asia/Kolkata`, or
  `UTC`. The stored wall-clock values are preserved; comparisons convert to UTC.
- **No timezone**: interpreted as UTC. This is a neutral fallback, not an
  assumed user timezone. Clients should send an IANA timezone when local
  wall-clock behavior matters.

All comparisons use timezone-aware UTC datetimes internally. The API never
accepts or guesses the user's timezone; it validates the supplied IANA name.

Deadline classifications:

- `overdue`: deadline instant is before now. Completed and cancelled tasks are
  excluded.
- `today`: the task's due date equals the current date in that task's timezone.
  This includes timed deadlines later today. Overdue classification takes
  precedence in context selection.
- `tomorrow`: due date equals the next date in the task's timezone.
- `upcoming`: deadline is after now and within a configurable horizon (default
  7 days), excluding overdue tasks.
- `within N days`: used by `GET /api/tasks/upcoming?days=N`.

`tzdata` is required on platforms that do not ship the IANA timezone database
(including typical Windows Python installations). Install it with:

```powershell
..\venv\Scripts\python.exe -m pip install tzdata
```

`UTC` works without `tzdata`; other timezone names are rejected with a clear
validation error when the database is unavailable.

## API endpoints

All endpoints require a trusted authenticated user via the existing
`get_current_user_id` dependency. Every query is scoped to that user. An
inaccessible or nonexistent task id returns `404 Task not found.` without
revealing whether the task belongs to another user.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/tasks/` | Create a task |
| `GET` | `/api/tasks/` | List tasks with filters, pagination, sorting |
| `GET` | `/api/tasks/{id}` | Retrieve one task |
| `PATCH` | `/api/tasks/{id}` | Partially update a task |
| `DELETE` | `/api/tasks/{id}` | Delete a task |
| `POST` | `/api/tasks/{id}/complete` | Complete a task |
| `POST` | `/api/tasks/{id}/reopen` | Reopen a task |
| `POST` | `/api/tasks/{id}/priority` | Change only the priority |
| `POST` | `/api/tasks/{id}/status` | Change only the status |
| `GET` | `/api/tasks/upcoming` | Open tasks due today/tomorrow/within N days |
| `GET` | `/api/tasks/overdue` | Open tasks past their deadline |
| `GET` | `/api/tasks/today` | Open tasks due today |
| `GET` | `/api/tasks/tomorrow` | Open tasks due tomorrow |
| `GET` | `/api/tasks/completed` | Completed tasks, paginated |
| `GET` | `/api/tasks/pending` | Pending tasks |
| `GET` | `/api/tasks/in-progress` | In-progress tasks |

Static paths are declared before `/{id}`, so `/api/tasks/upcoming` is never
interpreted as a task id.

### Create

```http
POST /api/tasks/
Content-Type: application/json

{
  "title": "Submit database assignment",
  "description": "Finish the Phase 7B write-up.",
  "category": "college",
  "priority": "high",
  "status": "pending",
  "due_date": "2026-11-10",
  "due_time": "17:30",
  "timezone": "America/New_York"
}
```

Response (`201 Created`):

```json
{
  "id": 1,
  "user_id": 1,
  "title": "Submit database assignment",
  "description": "Finish the Phase 7B write-up.",
  "category": "college",
  "priority": "high",
  "status": "pending",
  "due_date": "2026-11-10",
  "due_time": "17:30:00",
  "timezone": "America/New_York",
  "is_completed": false,
  "created_at": "2026-11-09T12:00:00Z",
  "updated_at": "2026-11-09T12:00:00Z",
  "completed_at": null
}
```

### List, filters, pagination, sorting

```http
GET /api/tasks/?category=college&priority=high&status=pending&due_from=2026-11-01&due_to=2026-11-30&limit=20&offset=0&sort_by=due_date&sort_order=asc
```

Supported filters: `category`, `priority`, `status`, `due_from`, `due_to`.
Pagination: `limit` (1-100, default 50) and `offset` (default 0).
Sorting: `sort_by` is one of `created_at`, `updated_at`, `due_date`, `title`,
`category`, `priority`, `status`; `sort_order` is `asc` or `desc`.

Response:

```json
{
  "tasks": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

### Update

```http
PATCH /api/tasks/1
Content-Type: application/json

{
  "priority": "urgent",
  "status": "in_progress"
}
```

Only supplied fields are changed. Sending `"due_date": null` clears the due
date and its dependent `due_time` and `timezone`. Sending `due_time` or
`timezone` without a due date is rejected.

### Lifecycle

```http
POST /api/tasks/1/complete
POST /api/tasks/1/reopen
POST /api/tasks/1/priority   {"priority": "urgent"}
POST /api/tasks/1/status     {"status": "cancelled"}
```

### Deadline views

```http
GET /api/tasks/upcoming?days=7
GET /api/tasks/overdue
GET /api/tasks/today
GET /api/tasks/tomorrow
GET /api/tasks/completed?limit=20&offset=0
```

Deadline views return only pending and in-progress tasks, except
`/completed`, which returns completed tasks.

## Validation rules

- Title is required, stripped, and limited to 255 characters.
- Description is optional and limited to 5000 characters.
- Category, priority, and status must be supported enum values.
- `due_date` must be a valid date and `due_time` a valid time.
- `timezone` must be a recognised IANA name and is canonicalised.
- `due_time` requires `due_date`.
- `timezone` requires `due_date`.
- Clients cannot set `user_id`, `created_at`, `updated_at`, `completed_at`, or
  `is_completed`; request schemas forbid unknown fields.

Invalid request bodies return FastAPI's standard `422` validation response.
Service-level conflicts return `400`.

## Context provider integration

`TaskContextProvider` and `DeadlineContextProvider` use the structured
`TaskService`; there is no natural-language task extraction.

- **Deadline section**: open tasks with deadlines that are overdue, due today,
  due tomorrow, or within `TASK_CONTEXT_UPCOMING_DAYS`.
- **Task section**: actionable tasks without a deadline, high/urgent-priority
  tasks, in-progress tasks, and tasks explicitly mentioned in the current
  message. A deadline-bearing task in a relevant deadline state is owned by the
  deadline section and excluded from the task section to prevent duplicates.
- Private requests receive neither task nor deadline context.
- Provider failures are logged and isolated; chat continues without that
  source.
- Task and deadline blocks are escaped, separately delimited, and subject to
  per-section character limits and the overall `CONTEXT_MAX_CHARS` budget.
- Only the authenticated user's tasks are queried.

The existing context priority order remains authoritative:

```dotenv
CONTEXT_PRIORITY_ORDER=history,preference,memory,task,deadline
```

## Configuration

```dotenv
# Task and deadline configuration
TASK_DEFAULT_UPCOMING_DAYS=7
TASK_CONTEXT_UPCOMING_DAYS=7
TASK_CONTEXT_LIMIT=5
DEADLINE_CONTEXT_LIMIT=5

# Unified context section budgets
CONTEXT_TASK_SECTION_CHARS=2000
CONTEXT_DEADLINE_SECTION_CHARS=2000
CONTEXT_MAX_CHARS=12000
```

## Migration

The Phase 7B migration `b7d2e6f4a8c1` revises `a7c2e4f9d1b3` and only creates
the `tasks` table, constraints, and indexes. It does not drop or reset existing
tables. Apply it with:

```powershell
cd backend
..\venv\Scripts\python.exe -m alembic upgrade head
```

## Known limitations

- No recurring tasks, subtasks, attachments, or tags.
- No reminders, notifications, background scheduling, or automatic extraction.
- Each deadline has one timezone; cross-timezone travel is not modelled.
- Deadline queries that depend on per-task timezones are evaluated in Python
  after loading the user's open deadline-bearing tasks. This is appropriate for
  personal task volumes but is not a bulk analytics design.
- Names other than UTC require `tzdata` or system timezone data.
- Authentication remains a trusted dependency hook; JWT authentication is not
  part of this phase.
