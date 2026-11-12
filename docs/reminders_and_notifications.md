# Reminders, Notifications, and Recurring Tasks

Phase 10 adds task-scoped reminder persistence and CRUD (10A), a database-backed
scheduler (10B), in-app notifications (10C), recurring tasks (10D), reliability
and recovery (10E), notification preferences with quiet hours (10F), end-to-end
regression coverage (10G), and notification snoozing plus reminder rescheduling
(10H).

A reminder always belongs to exactly one existing task and one owner. Creating,
rescheduling, cancelling, and deleting reminders are owner-scoped and reuse the
existing session authentication and CSRF protections. Delivery is in-app only;
email, SMS, and push channels are out of scope.

## Scope and guarantees

- Reminders are task-scoped. There are no standalone, conversation-scoped, or
  calendar-wide reminders.
- Every reminder is created through an explicit API call. ASTA never creates
  reminders automatically, including for existing or newly created tasks.
- A reminder can be expressed as an offset before the task deadline or as an
  explicit timezone-aware instant. The resolved instant is stored in UTC.
- Multiple distinct reminders may belong to the same task. Two reminders for the
  same task that resolve to the same instant are rejected as duplicates.
- Reminders can only be created or rescheduled for `pending` or `in_progress`
  tasks. Completed and cancelled tasks reject new reminders.
- A reminder is never marked delivered before the notification row has been
  successfully committed.
- Deleting a task deletes its reminders but preserves notification history. The
  notification keeps its own title, body, and task-title snapshot.
- Recursive generation is finite and duplicate-safe. Deactivating a rule never
  deletes generated tasks.
- Quiet hours defer reminders; they never silently discard them.
- Snoozing is not a read-status change. It re-arms the reminder that produced the
  notification and reuses the same notification row on re-delivery.
- Rescheduling updates a pending reminder in place; it never creates a second
  notification or bypasses the lifecycle rules.
- No task description, conversation text, memory, credential, or provider
  payload is copied into reminder, notification, or recurrence rows.

## Database architecture

Phase 10 uses additive tables and additive columns only. No existing column,
index, or row is altered or backfilled.

| Table | Purpose |
| --- | --- |
| `task_reminders` | Reminder schedule, lifecycle, claim lease, and retry state |
| `notifications` | In-app notification history and read state |
| `notification_preferences` | Per-owner in-app toggle and quiet-hours settings |
| `task_recurrences` | One recurrence rule per template task |
| `task_recurrence_occurrences` | Link from a rule to each generated task |

### `task_reminders`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `user_id` | integer | FK to `users.id`, `ON DELETE CASCADE` |
| `task_id` | integer | FK to `tasks.id`, `ON DELETE CASCADE` |
| `remind_at` | timestamptz | Resolved scheduling instant, always UTC |
| `offset_minutes` | integer | Minutes before the deadline; `NULL` for an explicit instant |
| `status` | varchar enum | `pending`, `processing`, `delivered`, `cancelled`, `suppressed`, `failed` |
| `dedupe_key` | varchar(200) | Content-free key derived from task id and UTC epoch second |
| `attempt_count` | integer | Delivery attempts, including recovered stale leases |
| `next_attempt_at` | timestamptz | Earliest time the next attempt may run |
| `last_error` | varchar(255) | Safe error class or reason, never raw provider text |
| `claim_token` | varchar(64) | Unique per claim; prevents two workers finalising the same row |
| `processing_started_at` | timestamptz | Lease start for stale-claim recovery |
| `created_at` / `updated_at` | timestamptz | Server-managed |
| `delivered_at` | timestamptz | Set only after successful delivery |

Constraints and indexes:

- `attempt_count >= 0`.
- Unique `(user_id, dedupe_key)` for owner-scoped duplicate prevention.
- Indexes `(status, remind_at)`, `(user_id, remind_at)`, `(task_id)`, a
  due-work index, and a processing-lease index.

### `notifications`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `user_id` | integer | FK to `users.id`, `ON DELETE CASCADE` |
| `task_id` | integer, nullable | FK to `tasks.id`, `ON DELETE SET NULL` |
| `reminder_id` | integer, nullable | FK to `task_reminders.id`, `ON DELETE SET NULL` |
| `notification_type` | varchar enum | `reminder` or `system` |
| `delivery_status` | varchar enum | `pending`, `delivered`, `failed` |
| `title` / `body` | varchar / text | Minimal snapshot, no descriptions or private content |
| `task_title` | varchar(255) | Task-title snapshot at delivery time |
| `reminder_at` | timestamptz | Snapshot of the reminder instant |
| `is_read` / `read_at` | boolean / timestamptz | Owner read state |
| `snoozed_at` | timestamptz, nullable | When the owner last snoozed this notification |
| `snoozed_until` | timestamptz, nullable | Pending re-delivery instant; cleared on re-delivery |
| `snooze_count` | integer | Cumulative snoozes for limit enforcement and audit |
| `dedupe_key` | varchar(200) | `reminder:{reminder_id}` |
| `attempt_count`, `next_attempt_at`, `last_error` | | Delivery metadata |
| `delivered_at` | timestamptz | Set when the row is committed |
| `created_at` / `updated_at` | timestamptz | Server-managed |

Constraints and indexes:

- `attempt_count >= 0`.
- `snooze_count >= 0`.
- Unique `(user_id, dedupe_key)` makes retries idempotent.
- Indexes for owner history, unread history, delivery recovery, and retention.

The non-cascading `SET NULL` references are deliberate: deleting a task must
not erase the user's notification history.

### `notification_preferences`

One row per owner, created lazily on the first PATCH. A missing row means the
deployment defaults apply and is not persisted on read.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `user_id` | integer | FK to `users.id`, `ON DELETE CASCADE`; unique |
| `in_app_enabled` | boolean | Default `true` |
| `quiet_hours_enabled` | boolean | Default `false` |
| `quiet_hours_start` / `quiet_hours_end` | time | Wall-clock values without a timezone |
| `timezone` | varchar(64) | IANA name used to evaluate the window |
| `created_at` / `updated_at` | timestamptz | Server-managed |

### `task_recurrences`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `user_id` | integer | FK to `users.id`, `ON DELETE CASCADE` |
| `task_id` | integer | FK to `tasks.id`, `ON DELETE CASCADE`; unique per task |
| `frequency` | varchar enum | `daily`, `weekly`, `monthly` |
| `interval` | integer | `1`–`365`, checked `>= 1` |
| `weekdays` | varchar(20) | Comma-separated Monday=0 … Sunday=6 for weekly rules |
| `start_date` | date | Defaults to the template's due date |
| `end_date` | date, nullable | Makes generation finite |
| `timezone` | varchar(64), nullable | Defaults to the template's timezone |
| `anchor_time` | time, nullable | Snapshot of the template's due time |
| `is_active` | boolean | Deactivation stops future generation |
| `last_generated_date` | date, nullable | Generation cursor |
| `next_generation_at` | timestamptz, nullable | Earliest next claim time |
| `claim_token`, `processing_started_at`, `last_error` | | Lease and failure state |
| `created_at` / `updated_at` | timestamptz | Server-managed |

### `task_recurrence_occurrences`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `user_id` | integer | FK to `users.id`, `ON DELETE CASCADE` |
| `recurrence_id` | integer, nullable | FK to `task_recurrences.id`, `ON DELETE SET NULL` |
| `task_id` | integer | FK to `tasks.id`, `ON DELETE CASCADE`; unique per task |
| `occurrence_date` | date | Local date of the generated occurrence |
| `created_at` | timestamptz | Server-managed |

Unique `(recurrence_id, occurrence_date)` prevents duplicate generation across
retries, restarts, and concurrent workers.

## Reminder scheduling semantics

Deadlines remain wall-clock values interpreted through the task's IANA
timezone, as documented in
[task_and_deadline_management.md](task_and_deadline_management.md).

### Offset reminders

`offset_minutes` is measured from the task's deadline anchor:

- **Timed task** (`due_date` + `due_time`): the exact deadline instant in the
  task's timezone. `offset_minutes=0` means at the deadline; `60` means one hour
  before it.
- **Date-only task** (`due_date` without `due_time`): the configured local
  anchor time on the due date in the task's timezone. The default anchor is
  `09:00`, controlled by `REMINDER_DEFAULT_LOCAL_TIME`. This is intentionally
  different from overdue classification, which treats a date-only deadline as
  the end of the local day.
- **No due date**: an offset cannot be resolved and is rejected. An explicit
  `remind_at` instant is still allowed.

For example, a date-only task due `2026-11-20` in `America/New_York` with
`offset_minutes=1440` anchors at 09:00 EST on the due date and resolves to
`2026-11-19T14:00:00Z`.

### Explicit instants

`remind_at` must include a timezone offset. The service normalises it to UTC
before storing it. Naive datetimes are rejected. The offset is not stored;
`offset_minutes` is `NULL` for explicit instants.

### Validation

The following are rejected:

- supplying both `offset_minutes` and `remind_at`, or neither;
- a negative offset or one greater than 365 days (`525600` minutes);
- a reminder instant that is not in the future;
- a naive `remind_at`;
- an offset for a task without a due date;
- a reminder for a completed or cancelled task;
- a duplicate instant for the same task and owner;
- rescheduling a reminder that is not `pending`.

A cancelled reminder is idempotently re-cancelled. A delivered, suppressed, or
failed reminder cannot be rescheduled or cancelled through this API.

## Scheduler and delivery lifecycle

The scheduler runs inside the FastAPI process and is started from the
application lifespan when `REMINDER_SCHEDULER_ENABLED=true`. All work state
lives in PostgreSQL, so an API restart resumes pending work without an external
queue.

### Claiming

A worker selects due `pending` reminders where `next_attempt_at` is null or in
the past. PostgreSQL uses
`SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers claim disjoint rows.
Each claim moves the row to `processing`, records a unique `claim_token`, and
sets `processing_started_at`. SQLite test databases serialise writers and
re-check the pending status in the claim update.

### Delivery outcomes

The delivery handler returns one of three outcomes:

| Outcome | Effect |
| --- | --- |
| `delivered` | Reminder becomes `delivered`, `delivered_at` is set, retry fields clear |
| `deferred` | Reminder returns to `pending` with `next_attempt_at` (for example, quiet hours) |
| `suppressed` | Reminder becomes `suppressed` with a safe reason |

The in-app handler first checks preferences:

- `in_app_enabled=false` suppresses the reminder with reason
  `in-app notifications disabled`.
- an active quiet-hours window defers the reminder until the window ends.
- otherwise it persists a notification and returns `delivered`.

A task deleted before delivery suppresses the reminder with
`task no longer exists`. A task completed or cancelled before delivery
suppresses it with `task is no longer open`. The worker never delivers a stale
reminder for a closed task.

### Retries and stale claims

- A handler exception increments `attempt_count` and sets a bounded exponential
  backoff: `base * multiplier^(attempt-1)`, capped by
  `REMINDER_RETRY_BACKOFF_MAX_SECONDS`.
- At `REMINDER_MAX_ATTEMPTS` the reminder becomes `failed` and stops retrying.
- `last_error` stores only a safe error class or reason, never raw exception
  text or provider payloads.
- A claimed reminder whose lease exceeds
  `REMINDER_PROCESSING_LEASE_SECONDS` is recovered by a later cycle. Recovery
  counts as an attempt, clears the claim, and returns the row to `pending` for
  redelivery, or marks it `failed` when the attempt limit is reached.
- One reminder's failure does not block the rest of the batch.

### Idempotency

The notification dedupe key is `reminder:{reminder_id}` with a unique
`(user_id, dedupe_key)` constraint. A retry after a partial failure cannot
create a second notification. If the reminder handler raises after the
notification flush but before commit, the worker rolls back both the
notification and the reminder lifecycle change.

### Snooze re-delivery

Snoozing does not deliver immediately and does not create a second
notification. It re-arms the existing reminder with a new `remind_at` and dedupe
key. The scheduler claims it at the new instant exactly like any other pending
reminder. When delivery runs, the existing notification row is found by
`reminder:{reminder_id}` and reused: `is_read` returns to `false`, `read_at` and
`snoozed_until` clear, `reminder_at` and `delivered_at` update, and the original
`snoozed_at` and `snooze_count` audit values are preserved.

A snoozed reminder is still subject to the normal claim lease, retry, stale-claim
recovery, task-status checks, and quiet-hour deferral. If the process restarts
during the snooze, the pending reminder row is the only state needed to resume.

### Marker recovery and retention

At startup and on each scheduler cycle, notifications left in `pending` or
`failed` are finalised to `delivered`. For the in-app channel, row persistence
is delivery, so no external call is needed.

Old read notifications are purged at most once per hour when older than
`NOTIFICATION_RETENTION_DAYS`. A value of `0` or less disables purging. Unread
notifications are never purged automatically.

### Shutdown

`stop()` signals the loop and waits up to
`REMINDER_SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS` for the in-flight cycle. If the
cycle does not finish, it is cancelled; the claim lease allows another worker
to recover the abandoned work after restart.

## Notification API

All endpoints require the authenticated owner session and are owner-scoped.
State-changing requests require CSRF protection. Nonexistent and foreign
resources both return `404`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/notifications` | List history, newest first |
| `GET` | `/api/notifications/unread` | List unread notifications |
| `GET` | `/api/notifications/{id}` | Retrieve one notification |
| `PATCH` | `/api/notifications/{id}/read` | Mark one notification read (idempotent) |
| `POST` | `/api/notifications/{id}/snooze` | Snooze a delivered notification |
| `POST` | `/api/notifications/read-all` | Mark every unread notification read |
| `DELETE` | `/api/notifications/{id}` | Delete one notification |
| `GET` | `/api/notifications/preferences` | Read effective preferences |
| `PATCH` | `/api/notifications/preferences` | Update preferences |

Listing supports `unread_only`, `limit` (1–100, default 50), and `offset`.
Notifications expose `is_read` and `read_at`. After a task is deleted, the
notification still exists with `task_id=null`, `reminder_id=null`, and its
snapshotted `task_title`.

Marking all read returns `{"updated": N}`.

## Quiet hours and preferences

Preferences are validated and persisted on the first PATCH. Reads never create
a row; they return the effective defaults:

```json
{
  "id": null,
  "in_app_enabled": true,
  "quiet_hours_enabled": false,
  "quiet_hours_start": "22:00:00",
  "quiet_hours_end": "07:00:00",
  "timezone": "UTC",
  "quiet_hours_active": false,
  "quiet_hours_ends_at": null
}
```

Rules:

- `quiet_hours_start` and `quiet_hours_end` are wall-clock times without a
  timezone. Supply the timezone separately.
- Equal start and end are rejected when the window is enabled.
- Windows may cross midnight. `22:00`–`07:00` is active from 22:00 until 07:00
  the next local day.
- The window is evaluated in the owner's IANA timezone, so daylight-saving
  transitions are handled by `zoneinfo`.
- The start boundary is inclusive and the end boundary is exclusive.
- `quiet_hours_active` and `quiet_hours_ends_at` are computed at read time.
- A reminder that becomes due during the window is deferred to the window end.
  It remains `pending` and is delivered on a later cycle.
- Disabling `in_app_enabled` suppresses reminder delivery explicitly with
  reason `in-app notifications disabled`; it is not treated as a transient
  failure.
- If preferences are missing or invalid, delivery falls back to the deployment
  defaults configured by `NOTIFICATION_QUIET_HOURS_DEFAULT_*`.

## Snoozing and rescheduling

Snoozing and rescheduling are related but distinct. Snoozing starts from a
delivered notification and re-arms the reminder behind it. Rescheduling changes
the time of a reminder that has not yet been delivered. Neither operation
creates a duplicate notification.

### Notification lifecycle

| State | Meaning |
| --- | --- |
| delivered, unread | Visible to the owner |
| delivered, read | Seen but still available in history |
| snoozed | Read, with `snoozed_until` set and the reminder re-armed |
| re-delivered | Same row, unread again, `snoozed_until` cleared |
| dismissed | Notification row deleted; a pending snooze is cancelled |

Only delivered notifications can be snoozed. Pending, failed, cancelled, or
deleted notifications return `404` when they no longer exist and `409` when the
current state does not allow snoozing. A notification whose task or reminder was
deleted is rejected with `409`; its history remains readable.

### Snooze API

```http
POST /api/notifications/42/snooze
Content-Type: application/json

{"duration_minutes": 30}
```

```http
POST /api/notifications/42/snooze
Content-Type: application/json

{"snooze_until": "2026-11-12T09:00:00+05:30"}
```

Exactly one of `duration_minutes` and `snooze_until` is required.

- `duration_minutes` accepts any value from 1 to
  `NOTIFICATION_MAX_SNOOZE_MINUTES`. Common presets are `10`, `30`, `60`, and
  `1440` (until the same wall-clock time tomorrow).
- `snooze_until` must include a timezone offset. It is normalised to UTC before
  storage. Naive instants are rejected by schema validation.
- Instants in the past are rejected with `400`.
- A custom instant beyond `NOTIFICATION_MAX_SNOOZE_MINUTES` is rejected.
- Repeated snoozing is allowed until `NOTIFICATION_SNOOZE_MAX_COUNT` is reached.
  A value of `0` or less means unlimited. The counter is per notification.
- Snoozing is rejected while the reminder is `processing`, or when the task is
  deleted, completed, or cancelled.
- Snoozing is rejected when in-app notifications are disabled, because delivery
  would otherwise be silently suppressed.
- If the requested instant falls inside quiet hours, the stored schedule moves
  to the end of the quiet window. This matches normal delivery deferral.

The response is the updated notification with `is_read=true`, `snoozed_at`,
`snoozed_until`, `snooze_count`, and `reminder_at` set to the effective
schedule.

### Dismissal

`DELETE /api/notifications/{id}` deletes the notification and returns `204`.
Deleting a notification that is waiting for snooze re-delivery also cancels the
re-armed reminder, so a dismissed notification cannot reappear. Snoozing a
deleted notification returns `404`.

### Reschedule API

```http
PATCH /api/reminders/7
Content-Type: application/json

{"remind_at": "2026-11-12T09:30:00+05:30"}
```

The existing endpoint accepts either `remind_at` or `offset_minutes`, exactly as
when creating a reminder. Rescheduling:

- requires a `pending` reminder; delivered, processing, cancelled, suppressed,
  and failed reminders return `409`;
- requires an open task and the same owner;
- rejects past instants;
- recalculates `dedupe_key` from the task id and new UTC epoch second;
- rejects another reminder for the same task at the same instant with `409`;
- resets `attempt_count`, `next_attempt_at`, `claim_token`,
  `processing_started_at`, `last_error`, and `delivered_at` without creating a
  notification.

On PostgreSQL, both snoozing and reminder mutations lock the notification and
reminder rows with `SELECT ... FOR UPDATE`. The scheduler claims with
`FOR UPDATE SKIP LOCKED`. If a worker claims first, the mutation waits and then
sees `processing` and returns `409`; if the mutation commits first, the worker
picks up the new schedule. The unique `(user_id, dedupe_key)` constraint is the
final guard against concurrent duplicate schedules, and a failed snooze rolls
back all notification metadata.

## Recurring tasks

A recurrence rule is attached to one owned, open task with a due date. The
template task remains a normal task. Generated occurrences are independent
normal tasks; completing, cancelling, or leaving one overdue does not stop
future generation.

### Frequencies

- **Daily**: every `interval` days from `start_date`.
- **Weekly**: selected weekdays (Monday=0 … Sunday=6) every `interval` weeks,
  anchored to the Monday of `start_date`'s week. If no weekdays are supplied,
  the template's weekday is used.
- **Monthly**: the same day of the month every `interval` months. Months that
  do not contain that day produce no occurrence for that month.

### Bounds and duplicate prevention

- `start_date` defaults to the template's due date and cannot be earlier.
- `end_date` is inclusive and makes generation finite.
- `RECURRENCE_GENERATION_HORIZON_DAYS` limits how far ahead each run generates.
- `RECURRENCE_MAX_GENERATIONS_PER_RUN` caps generated tasks per run.
- A unique `(recurrence_id, occurrence_date)` constraint and a unique
  `task_id` link prevent duplicates across retries and concurrent workers.
- The rule's `last_generated_date` advances transactionally with each generated
  task, so a restart resumes rather than regenerating.
- When the end date is reached, the rule becomes inactive and
  `next_generation_at` is cleared.
- Deleting the template deletes the rule. Existing occurrence tasks remain
  normal tasks; their occurrence links keep the task and set `recurrence_id` to
  `null`.

The API `DELETE /api/recurrences/{id}` deactivates the rule instead of deleting
it, preserving history and occurrence links.

### Recurrence API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/tasks/{task_id}/recurrence` | Create a rule |
| `GET` | `/api/tasks/{task_id}/recurrence` | Read a task's rule |
| `GET` | `/api/recurrences` | List rules (`include_inactive=true` by default) |
| `GET` | `/api/recurrences/{id}` | Retrieve one rule |
| `PATCH` | `/api/recurrences/{id}` | Update cadence, bounds, timezone, or active flag |
| `DELETE` | `/api/recurrences/{id}` | Deactivate while preserving history |
| `GET` | `/api/recurrences/{id}/occurrences` | List generated occurrence links |

Example:

```http
POST /api/tasks/12/recurrence
Content-Type: application/json

{
  "frequency": "weekly",
  "interval": 2,
  "weekdays": [0, 2, 4],
  "start_date": "2026-11-16",
  "end_date": "2027-03-01",
  "timezone": "America/New_York"
}
```

Validation rejects a rule for a missing/closed task, a task without a due date,
a duplicate rule for the same task, a past `start_date`, `end_date` before
`start_date`, weekdays outside 0–6, weekdays on a non-weekly rule, and an
invalid timezone.

Generated tasks copy the template's title, description, category, priority,
due time, and timezone. They start as `pending` with the occurrence date as
`due_date`.

## Reminder API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/tasks/{task_id}/reminders` | Create a reminder for an owned task |
| `GET` | `/api/tasks/{task_id}/reminders` | List one owned task's reminders |
| `GET` | `/api/reminders` | List the owner's reminders across tasks |
| `PATCH` | `/api/reminders/{id}` | Reschedule an owned pending reminder |
| `POST` | `/api/reminders/{id}/cancel` | Cancel an owned pending reminder |
| `DELETE` | `/api/reminders/{id}` | Permanently delete an owned reminder |

Listing supports `status`, `limit` (1–100, default 50), and `offset`. The
user-wide list additionally supports `task_id`. Results are ordered by
`remind_at` ascending, then `id` ascending.

### Create from a deadline offset

```http
POST /api/tasks/12/reminders
Content-Type: application/json

{"offset_minutes": 60}
```

Response (`201 Created`):

```json
{
  "id": 4,
  "user_id": 1,
  "task_id": 12,
  "remind_at": "2026-11-20T16:00:00Z",
  "offset_minutes": 60,
  "status": "pending",
  "created_at": "2026-11-09T12:00:00Z",
  "updated_at": "2026-11-09T12:00:00Z",
  "delivered_at": null
}
```

### Create from an explicit instant

```http
POST /api/tasks/12/reminders
Content-Type: application/json

{"remind_at": "2026-11-20T18:00:00+02:00"}
```

The response reports the normalised UTC instant and `"offset_minutes": null`.

### Reschedule, cancel, and delete

```http
PATCH /api/reminders/4
Content-Type: application/json

{"offset_minutes": 120}
```

Only `pending` reminders can be rescheduled. Rescheduling updates `remind_at`,
`offset_minutes`, and `dedupe_key`, and resets `attempt_count`,
`next_attempt_at`, `last_error`, and `delivered_at`.

```http
POST /api/reminders/4/cancel
DELETE /api/reminders/4
```

Cancelling sets `status=cancelled` and is idempotent. Deleting removes the row
and returns `204 No Content`.

### Status codes

| Status | Meaning |
| --- | --- |
| `201` | Reminder or recurrence created |
| `200` | Resource read, listed, updated, cancelled, or deactivated |
| `204` | Resource deleted or recurrence deactivated |
| `400` | Invalid scheduling data, recurrence rule, or task state |
| `404` | Task, reminder, notification, or recurrence not found for this owner |
| `409` | Duplicate instant, duplicate rule, or invalid lifecycle transition |
| `422` | Request body failed schema validation |

## Privacy and ownership

- Identity always comes from the validated server-side session, never from a
  request body or query parameter.
- Every read and write filters by `user_id`.
- Foreign resources are indistinguishable from missing ones (`404`).
- Notification bodies contain only a minimal task title, due date, and due time.
  Descriptions, conversation text, memories, and credentials are not copied.
- Scheduled work and retry state are database-backed and contain no raw provider
  payloads.
- Recurrence rules store only schedule metadata and copy normal task fields;
  they never include conversation or memory content.

## Configuration

```dotenv
REMINDER_DEFAULT_LOCAL_TIME=09:00
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
```

Notes:

- `REMINDER_DEFAULT_LOCAL_TIME` is parsed as a local time-of-day. A timezone
  suffix is discarded because the task's IANA timezone remains authoritative.
  An invalid value logs a warning and falls back to `09:00`.
- `REMINDER_SCHEDULER_ENABLED=false` disables the in-process worker; existing
  pending rows remain in PostgreSQL and resume when re-enabled.
- `NOTIFICATION_MAX_ATTEMPTS` is reserved for future delivery channels. In-app
  delivery completes on row persistence, so marker recovery always finalises
  pending/failed in-app rows.
- `NOTIFICATION_RETENTION_DAYS=0` disables purging rather than falling back to
  the default.
- `NOTIFICATION_SNOOZE_MAX_COUNT` limits repeated snoozing per notification.
  `0` or less means unlimited.
- `NOTIFICATION_MAX_SNOOZE_MINUTES` bounds a single duration or custom instant.
  The default `525600` is 365 days.
- `RECURRENCE_GENERATION_INTERVAL_SECONDS` controls how soon an active rule is
  eligible for another generation run after a successful pass.
- `RECURRENCE_GENERATION_ENABLED=false` leaves recurrence rows untouched while
  stopping generation.

Automatic default reminders are not implemented. No reminder is ever created
implicitly.

## Migrations

Phase 10 migrations are additive and chain onto the existing head:

| Revision | Purpose |
| --- | --- |
| `e1f2a3b4c5d6` | Add `task_reminders` (Phase 10A) |
| `f1a2b3c4d5e6` | Add reminder processing leases and status (10B) |
| `a2b3c4d5e6f7` | Add in-app notifications (10C) |
| `b3c4d5e6f7a8` | Add notification preferences and quiet hours (10F) |
| `c4d5e6f7a8b9` | Add recurrence rules and occurrence links (10D) |
| `d5e6f7a8b9c0` | Add notification snooze audit fields (10H) |

```powershell
cd backend
..\venv\Scripts\python.exe -m alembic upgrade head
```

Current head: `d5e6f7a8b9c0`.

## Tests

Focused Phase 10 coverage:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase10_reminders -v
..\venv\Scripts\python.exe -m unittest tests.test_phase10_scheduler_notifications -v
..\venv\Scripts\python.exe -m unittest tests.test_phase10_recurrence -v
..\venv\Scripts\python.exe -m unittest tests.test_phase10_reliability -v
..\venv\Scripts\python.exe -m unittest tests.test_phase10_preferences -v
..\venv\Scripts\python.exe -m unittest tests.test_phase10_integration -v
..\venv\Scripts\python.exe -m unittest tests.test_phase10_snooze_reschedule -v
```

Real PostgreSQL locking and concurrency checks are opt-in and create only
temporary rows:

```powershell
$env:ASTA_PHASE10_POSTGRES_TESTS="1"
..\venv\Scripts\python.exe -m unittest tests.test_phase10_snooze_postgres -v
```

The suites cover deadline-relative and absolute creation, date-only anchoring,
UTC normalisation, duplicate prevention, ownership isolation, atomic claims,
restart and stale-lease recovery, bounded retries, partial-delivery rollback,
notification read state, history retention after task deletion, recurrence
cadences and finite generation, quiet-hour windows across midnight and daylight
saving, suppression, snooze presets and custom instants, snooze limits and
repetition, dismissed and deleted-task behaviour, quiet-hour-adjusted snoozes,
restart recovery of snoozed reminders, reschedule lifecycle and duplicate rules,
row-lock waiting, and concurrent dedupe enforcement.

Full regression:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

## Known limitations

- In-app notifications are the only delivery channel. Email, SMS, push, and
  desktop notifications are not implemented.
- The scheduler is an in-process loop. Running multiple API processes can
  execute multiple loops safely because claims are database-backed, but there is
  no separate worker deployment mode or external queue.
- Snooze presets are client-supplied durations or custom instants. ASTA does not
  yet provide notification action buttons, reminder fan-out, or per-task
  notification routing.
- Natural-language reminder scheduling is deferred. Existing “remind me to…”
  handling remains task creation through the task-intent engine.
- Monthly recurrence does not clamp to the last day of shorter months; months
  without the anchor day produce no occurrence.
- Recurrence rules are single per template task and cannot express complex
  calendars such as “last weekday of the month” or “every second Tuesday and
  Thursday except holidays”.
- Notification read state is per owner; there are no multi-device delivery
  receipts beyond the stored row.
