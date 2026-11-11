# Preferences and Unified Context

## Overview

Phase 7A adds two related capabilities:

1. A structured, user-owned preference store with explicit preferences and
   approval-gated inferred suggestions.
2. A unified context builder that combines active preferences, approved
   long-term memories, bounded conversation history, and relevant task/deadline
   context. See [context_architecture.md](context_architecture.md) for the full
   six-source architecture.

Preferences are deliberately **not** stored as ordinary memories. Memories are
free-form durable facts without a stable key, uniqueness constraint, source, or
active/inactive lifecycle. Preferences need all four, so they live in the
dedicated `user_preferences` table.

## Architecture

```text
User message
    |
    v
UnifiedContextBuilder
    |-- HistoryContextProvider       (bounded recent messages)
    |-- PreferenceContextProvider    (active, relevant preferences)
    |-- MemoryContextProvider        (approved long-term memories)
    |-- TaskContextProvider          (relevant open tasks)
    |-- DeadlineContextProvider      (overdue/today/tomorrow/upcoming)
    |
    v
Deduplication -> priority order -> character budget
    |
    v
Source-separated prompt -> existing Groq/Ollama router -> response
```

Each provider implements a small `ContextProvider` protocol. Adding a new source
means implementing `retrieve()` and adding the provider to
`UnifiedContextBuilder`; `ChatService` does not need to change.

The existing Phase 5 memory retriever remains responsible for semantic/keyword
memory retrieval. The existing Phase 6 conversation service remains responsible
for persistence and bounded history. Preference retrieval is deterministic and
keyword-based because preferences are short structured records.

## Preference model

Table: `user_preferences`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | primary key |
| `user_id` | integer | FK `users.id`, cascade delete |
| `key` | varchar(100) | normalized lowercase snake_case |
| `value` | text | self-contained preference statement |
| `category` | varchar(50) | communication, learning, programming, career, productivity, general |
| `status` | enum | pending, active, inactive, rejected |
| `source` | enum | explicit, inferred |
| `created_at` | timestamptz | server default `now()` |
| `updated_at` | timestamptz | server default `now()` |

Constraints and indexes:

- Unique `(user_id, key)` prevents duplicate preferences.
- Index `(user_id, status)` supports active-context retrieval.
- Index `(user_id, category)` supports filtered listing.

## Preference lifecycle

```text
Explicit create ──────────────────────────> active
                                               |
                                     deactivate|
                                               v
                                           inactive

Extraction ──> pending (inferred) ──approve──> active
                     |
                 reject
                     v
                 rejected
```

- **Explicit** preferences are created through `POST /api/preferences/` and are
  active immediately.
- **Inferred** preferences are created only as pending suggestions. They are
  never active until the user approves them.
- Rejected suggestions are retained for audit but are never retrieved as
  context.
- Only `active` preferences are retrieved for chat.
- Temporary or one-off statements are rejected by extraction rules and a
  conservative temporary-marker filter.
- Values that look like credentials are rejected at write time and filtered
  again at retrieval/render time.

## API endpoints

All endpoints enforce ownership through `get_current_user_id`. A preference
owned by another user returns `404`, never another user's data.

### Create an explicit preference

```http
POST /api/preferences/
Content-Type: application/json

{
  "key": "explanation_style",
  "value": "Prefer beginner-friendly explanations.",
  "category": "communication"
}
```

Response `201`:

```json
{
  "id": 1,
  "user_id": 1,
  "key": "explanation_style",
  "value": "Prefer beginner-friendly explanations.",
  "category": "communication",
  "status": "active",
  "source": "explicit",
  "created_at": "2026-11-09T10:00:00Z",
  "updated_at": "2026-11-09T10:00:00Z"
}
```

Duplicate keys return `409`. Secret-like values return `400`.

### List and retrieve

```http
GET /api/preferences/
GET /api/preferences/?category=programming
GET /api/preferences/?status=active
GET /api/preferences/{id}
```

### Update, deactivate, delete

```http
PATCH /api/preferences/{id}
Content-Type: application/json

{
  "value": "Prefer Python and PyTorch for AI projects.",
  "category": "programming"
}
```

```http
POST /api/preferences/{id}/deactivate
DELETE /api/preferences/{id}
```

`PATCH` accepts `key`, `value`, `category`, and `status` (`active` or
`inactive` only). Pending/rejected transitions belong to the suggestion
workflow.

### Extract candidates without saving

```http
POST /api/preferences/extract
Content-Type: application/json

{
  "conversation": "Please explain programming concepts like I am a beginner.",
  "private": false
}
```

Response:

```json
{
  "candidates": [
    {
      "key": "explanation_style",
      "value": "Prefer beginner-friendly explanations.",
      "category": "communication",
      "explanation": "The user explicitly requested beginner-level explanations."
    }
  ]
}
```

Nothing is persisted by this endpoint.

### Create, list, approve, and reject suggestions

```http
POST /api/preferences/suggestions/
Content-Type: application/json

{
  "conversation": "Please explain programming concepts like I am a beginner.",
  "private": false
}
```

Response `201`:

```json
{
  "suggestions": [
    {
      "id": 9,
      "user_id": 1,
      "key": "explanation_style",
      "value": "Prefer beginner-friendly explanations.",
      "category": "communication",
      "status": "pending",
      "source": "inferred",
      "created_at": "2026-11-09T10:05:00Z",
      "updated_at": "2026-11-09T10:05:00Z"
    }
  ]
}
```

```http
GET  /api/preferences/suggestions/
POST /api/preferences/suggestions/{id}/approve
POST /api/preferences/suggestions/{id}/reject
```

Approval makes the preference active while retaining `source: "inferred"`.
Resolving the same suggestion twice returns `400`.

## Unified context

### Retrieval

- **Preferences:** active preferences owned by the user are ranked by keyword
  overlap with the query and category hints. Irrelevant preferences are
  excluded. Pending, inactive, and rejected preferences are never retrieved.
- **Memories:** existing Phase 5 retrieval is unchanged: semantic mode with
  keyword fallback when enabled, otherwise keyword mode.
- **History:** the existing bounded recent-message retrieval from Phase 6 is
  unchanged. Private messages are excluded from later non-private prompts.
- **Tasks/deadlines:** providers exist but return no entries in Phase 7A.

### Prioritization

The current user message always has the highest priority and is never trimmed.
Configurable source priority defaults to:

```text
history > preference > memory > task > deadline
```

`CONTEXT_PRIORITY_ORDER` changes this ordering. Priority controls both
deduplication (higher-priority duplicate context wins) and which sections are
trimmed first when the total character budget is exceeded.

### Deduplication

- Exact normalized duplicates within and across sources are removed.
- A long-term memory that simply repeats an active preference value is dropped
  so the same fact is not sent twice.
- Each source still has its own per-section limits from Phases 5 and 6.

### Rendering and untrusted data

The final prompt contains separate blocks:

```text
SYSTEM INSTRUCTIONS
USER PREFERENCES
RETRIEVED MEMORIES
CONVERSATION HISTORY
TASK CONTEXT        (future)
DEADLINE CONTEXT    (future)
CURRENT USER MESSAGE
```

Every block is delimited, escaped (`<`, `>`, `&`), and explicitly marked as
untrusted data. Retrieved content cannot close its block or override system
instructions.

### Budget

`CONTEXT_MAX_CHARS` bounds the total prompt. When it is exceeded, entries are
removed from the lowest-priority source first. For history, the oldest messages
are removed first so the newest remain. The current user message is never
removed.

## Privacy behavior

- Private requests continue to use **Ollama only**; the existing router is
  unchanged.
- Private conversation messages are excluded from later non-private prompts.
- Preference and memory retrieval remain user-scoped.
- No credentials are stored in preferences; secret-like values are rejected.
- Prompt construction never logs message, memory, or preference content.

## Configuration

```dotenv
CONVERSATION_HISTORY_LIMIT=20
CONVERSATION_MESSAGE_PAGE_SIZE=50

PREFERENCE_RETRIEVAL_LIMIT=8
PREFERENCE_RELEVANCE_THRESHOLD=0.15
PREFERENCE_MAX_VALUE_CHARS=1000

CONTEXT_MAX_CHARS=12000
CONTEXT_PREFERENCE_SECTION_CHARS=2500
CONTEXT_PRIORITY_ORDER=history,task,deadline,preference,memory
```

## Limitations

- No JWT authentication yet; trusted user identity is still supplied by the
  existing dependency convention.
- Preference extraction is advisory and depends on the configured LLM.
- Preference matching is deterministic keyword/category scoring, not semantic
  embedding matching.
- Tasks and deadlines are extension interfaces only; they are not implemented
  in Phase 7A.
- There is no automatic preference lifetime decay; users deactivate or delete
  preferences explicitly.
