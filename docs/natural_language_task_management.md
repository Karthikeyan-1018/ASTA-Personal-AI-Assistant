# Natural-Language Task Management

Phase 7D adds a deterministic, confirmation-gated natural-language layer on top
of the structured task system from Phase 7B. Users can create, find, update,
complete, reopen, cancel, and delete tasks by chatting, but the assistant never
mutates the database directly. Every mutation is first stored as a server-side
pending action and only executes after an explicit confirmation.

## Supported intents

| Intent | Typical request | Action |
| --- | --- | --- |
| `create_task` | "Remind me to call mom tomorrow." | Proposes a new task |
| `list_tasks` | "Show my high-priority college tasks." | Lists matching open tasks |
| `get_task` | "Find my Python project." | Shows one matching task |
| `update_task` | "Change my project deadline to next Monday." | Proposes field changes |
| `complete_task` | "Mark my ML assignment as completed." | Proposes completion |
| `reopen_task` | "Reopen my database assignment." | Proposes reopening |
| `cancel_task` | "Cancel my old project task." | Proposes cancellation |
| `delete_task` | "Delete my old task." | Proposes permanent deletion |
| `upcoming_tasks` | "What are my upcoming deadlines?" | Lists upcoming tasks |
| `overdue_tasks` | "What is overdue?" | Lists overdue tasks |
| `today_tasks` | "What do I have to finish today?" | Lists today's tasks and overdue items |
| `tomorrow_tasks` | "What is due tomorrow?" | Lists tomorrow's tasks |
| `task_status_query` | "Did I complete my Java assignment?" | Reports status |
| `no_task_action` | Ordinary conversation | Falls through to normal chat |

## Pipeline

1. The current user message is persisted as usual.
2. If an active pending action exists in the conversation, a strict yes/no
   confirmation or rejection is handled first. Any other message supersedes the
   stale proposal.
3. Deterministic keyword, pattern, and date rules classify the message. They
   never call an LLM.
4. Only if the message looks task-related but the deterministic rules cannot
   classify it may the hybrid LLM router be consulted. The LLM receives no task
   IDs, user IDs, database fields, or stored history, and its output is parsed
   into the strict `LLMTaskIntent` Pydantic schema. Unknown keys are rejected.
5. All dates, timezones, enum values, ownership, and task state are validated
   deterministically before anything is proposed.
6. Mutations become rows in `task_pending_actions`. Queries and clarification
   replies never write task data.
7. The chat handler returns a normal `ChatResponse`-compatible result with
   `provider="task_engine"` and `model="deterministic"`, so clients and
   conversation persistence behave exactly as before.

A task-intent failure is isolated: the exception is logged without message
content and the turn continues through the normal context/LLM pipeline.

## Structured extraction

The engine extracts only what the message states:

- title (required for creation)
- optional description
- category (`college`, `career`, `personal`, `fitness`, `project`, `other`)
- priority (`low`, `medium`, `high`, `urgent`)
- optional due date
- optional due time
- optional IANA timezone

Missing optional values stay `null`. The stored defaults (`other` category and
`medium` priority) match the structured task API. Categories are inferred only
from explicit vocabulary; a task without a category signal remains `other` and
is not silently narrowed during list queries.

## Relative dates

All resolution receives an injected clock, so behaviour is deterministic and
testable. Supported forms include:

- `today`, `tomorrow`, `day after tomorrow`
- `this <weekday>`, `next <weekday>`, bare `<weekday>`
- `in N days`, `in N weeks` (digits or simple number words)
- ISO dates (`2026-11-15`)
- named dates (`15 November 2026`, `November 20, 2026`)
- times (`18:00`, `6 PM`, `at 6 o'clock`)
- explicit timezones (`Asia/Kolkata`, `UTC`)

Documented rules:

- `this`/`coming`/bare weekdays mean the next occurrence including today.
- `next <weekday>` means that weekday in the following ISO week. For example,
  on Monday 9 November 2026, `next Monday` is 16 November 2026.
- A date without a year resolves to its next occurrence.
- A time with no date applies to today.
- Date-only deadlines are stored without a time or timezone.
- A timed deadline requires an explicit timezone or `TASK_DEFAULT_TIMEZONE`.
- Slash dates (`11/15/2026`) are locale-dependent and always require
  clarification.
- Vague phrases (`sometime`, `soon`, `next weekend`, `end of the month`) require
  clarification.
- Past dates, impossible calendar dates, invalid times, and times that already
  passed today are rejected with a clarification instead of being guessed.

## Confirmation safety

Every create, update, complete, reopen, cancel, and delete is a pending action.

- **Specific target**: the proposal contains the exact task ID selected after a
  user-scoped title match. IDs from the LLM are never accepted.
- **Strict confirmation**: only short phrases such as `yes`, `confirm`, `do it`,
  `no`, or `cancel it` count. Unrelated sentences are never treated as consent.
- **Single use**: an atomic status transition from `pending` to `executed`
  ensures concurrent or repeated confirmations cannot execute twice.
- **Expiry**: actions expire after `TASK_ACTION_CONFIRMATION_TTL_SECONDS`
  (default 600), after which confirmation returns an expiry response.
- **Supersede**: a new unrelated message in the same conversation marks the
  previous proposal `superseded`, so a later "yes" cannot revive it.
- **Revalidation**: immediately before execution the target is reloaded with
  user scoping and its current status is checked. A deleted, foreign, cancelled,
  or otherwise invalid target fails safely and the action becomes `failed`.
- **User scoping**: pending actions are queried and resolved by `user_id`; one
  user can never see or confirm another user's action.

Statuses are `pending`, `executed`, `rejected`, `expired`, `superseded`, and
`failed`. One active pending action per user and conversation is maintained.

## Storage

Phase 7D adds one non-destructive table through migration
`c8e1f3a5d7b9_add_task_pending_actions.py` (after `b7d2e6f4a8c1_add_tasks.py`).
No existing table, row, or pgvector embedding is changed.

`task_pending_actions` stores:

- `user_id`, `conversation_id`, `action_type`, `status`
- `target_task_id` and optional `result_task_id`
- a JSON payload containing only validated task fields
- a human-readable `summary` for confirmation
- `created_at`, `expires_at`, and `resolved_at`

Raw LLM output, credentials, tokens, and private conversation history are never
stored in this table.

## Chat examples

```
User: Remind me to call mom tomorrow.
ASTA: I understood the following task:
      Title: Call mom
      Category: Personal
      Priority: Medium
      Deadline: 2026-11-10
      Would you like me to create this task? Reply "yes" to confirm or "no" to cancel.

User: yes
ASTA: Done. I created "Call mom".
```

```
User: Delete my old task.
ASTA: Target task:
      - Old task (id 4); status pending; medium priority; other
      Would you like me to permanently delete this task? This cannot be undone.
      Reply "yes" to confirm or "no" to cancel.

User: no
ASTA: Okay, I won't make any changes.
```

## API

The existing `/api/tasks/` API remains the source of truth for direct task
operations. Three small endpoints expose the confirmation workflow:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/task-actions/pending` | List the current user's active pending actions |
| `POST` | `/api/task-actions/{id}/confirm` | Atomically claim and execute one action |
| `POST` | `/api/task-actions/{id}/reject` | Reject one action without executing it |

Chat confirmation is the primary path; these endpoints are optional and useful
for a future UI. They return `404` for missing/foreign actions, `409` for
already-handled actions, `410` for expired actions, and `400` when the target
state no longer permits the change.

## Privacy

- Private chat requests pass `private=True` through to the existing hybrid
  router, which keeps them on local Ollama. No private content is sent to Groq.
- Deterministic task handling never calls an LLM at all.
- The optional LLM interpreter receives only the current message and the current
  UTC time, never stored history, task IDs, user IDs, or private memory.
- Task-intent logs never include message content, extracted values, task titles,
  or summaries.

## Configuration

```dotenv
TASK_INTENT_ENABLED=true
TASK_INTENT_LLM_ENABLED=true
TASK_ACTION_CONFIRMATION_TTL_SECONDS=600
# Leave empty to always ask for a timezone on timed deadlines.
TASK_DEFAULT_TIMEZONE=
TASK_INTENT_MAX_RESULTS=20
```

`TASK_INTENT_ENABLED=false` makes every message fall through to normal chat.
`TASK_INTENT_LLM_ENABLED=false` keeps deterministic handling only.

## Tests

`backend/tests/test_natural_language_tasks.py` covers all supported intents,
extraction, every relative-date form, ambiguity and invalid input, LLM fallback
validation and injection rejection, privacy routing, confirmation and rejection,
supersession, expiry, duplicate execution, ownership, state revalidation, task
queries, feature-flag fallthrough, and the confirmation API. External LLM
providers are mocked; the pending-action lifecycle is additionally verified
against real PostgreSQL with
`backend/scripts/verify_task_pending_actions.py`.

## Scope

Phase 7D intentionally excludes recurring tasks, reminders and notifications,
authentication/JWT changes, LangGraph, frontend work, and any rewrite of the
Phase 5–7C memory, preference, context, or task systems.
