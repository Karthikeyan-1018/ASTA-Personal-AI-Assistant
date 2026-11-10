# Conversation Persistence

## Architecture

Conversation persistence uses the existing `conversations` and `messages`
tables created during the PostgreSQL foundation. No table is recreated and no
existing record is deleted by the Phase 6 migration.

```
users
  └── conversations (user_id, ON DELETE CASCADE)
        └── messages (conversation_id, ON DELETE CASCADE)
```

`app/services/conversation_service.py` owns all conversation database
operations:

- create, list, read, rename, and delete conversations;
- append user and assistant messages;
- list messages with pagination;
- load the most recent messages for short-term context.

Every query is scoped by `user_id`. A conversation owned by another user is
reported as `404 Conversation not found.`, so no ownership information leaks.

`app/memory/context_builder.py` keeps two independent context blocks:

1. `RETRIEVED MEMORIES` – approved long-term memories from pgvector/keyword
   retrieval.
2. `CONVERSATION HISTORY` – recent short-term messages from the current
   conversation.

Both blocks are delimited and treated as untrusted data. The current user
message is always emitted separately and last.

## Schema change

Migration `f3a1c9d7e5b2` (`add_message_metadata`) adds two non-destructive
columns to `messages`:

| Column | Type | Rules |
| --- | --- | --- |
| `provider` | `varchar(50)` | Nullable. Assistant response provider. |
| `is_private` | `boolean` | Not null, server default `false`. |

No credentials, API keys, or provider secrets are stored.

## Chat flow

1. Resolve the owned conversation, or create one from the first message.
2. Load the most recent messages **before** saving the new user message.
3. Save the user message.
4. Retrieve approved long-term memories separately.
5. Build the prompt with memory and history blocks.
6. Call the hybrid LLM router.
7. Save the assistant message with provider metadata only after a successful
   response.
8. Return the answer and `conversation_id`.

If the LLM fails, the user message stays persisted and no assistant message is
written. A private message is never included in a later non-private prompt, so
private content is not sent to Groq through history.

## Configuration

```dotenv
# Number of recent messages loaded for short-term context (default 20).
CONVERSATION_HISTORY_LIMIT=20

# Default page size for message listings (default 50, maximum 100).
CONVERSATION_MESSAGE_PAGE_SIZE=50
```

History is additionally bounded by `MAX_HISTORY_MESSAGES` (50) and
`MAX_HISTORY_CHARS` (6000) in `app/memory/context_builder.py`.

## Endpoints

All conversation endpoints require the authenticated user dependency. Tests
override `get_current_user_id`; JWT authentication can replace the dependency
without changing the service or API contracts.

### Create a conversation

```http
POST /api/conversations/
Content-Type: application/json

{"title": "Trip planning"}
```

`title` is optional; the default is `New conversation`.

```json
{
  "id": 1,
  "user_id": 1,
  "title": "Trip planning",
  "created_at": "2026-11-09T12:00:00+00:00",
  "updated_at": "2026-11-09T12:00:00+00:00"
}
```

### List conversations

```http
GET /api/conversations/
```

Returns conversations owned by the current user, most recently updated first.

### Retrieve, rename, delete

```http
GET    /api/conversations/{conversation_id}
PATCH  /api/conversations/{conversation_id}
DELETE /api/conversations/{conversation_id}
```

Rename body:

```json
{"title": "Renamed conversation"}
```

Delete returns `204 No Content` and cascades to all messages.

### List messages

```http
GET /api/conversations/{conversation_id}/messages?limit=50&offset=0
```

Messages are always returned in chronological order (`created_at`, then `id`).
`limit` must be between 1 and 100; `offset` must be non-negative.

```json
{
  "messages": [
    {
      "id": 1,
      "conversation_id": 1,
      "role": "user",
      "content": "Hello",
      "provider": null,
      "is_private": false,
      "created_at": "2026-11-09T12:00:01+00:00"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

## Chat examples

### Start a new conversation

```http
POST /api/chat/
Content-Type: application/json

{"message": "Hello, my name is Karthik."}
```

```json
{
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "response": "Hello Karthik!",
  "conversation_id": 1
}
```

### Continue an existing conversation

```http
POST /api/chat/
Content-Type: application/json

{
  "message": "What is my name?",
  "conversation_id": 1
}
```

Recent messages from conversation `1` are supplied as short-term context. If
the conversation does not exist or belongs to another user, the API returns
`404`.

### Private chat

```http
POST /api/chat/
Content-Type: application/json

{
  "message": "A private question",
  "private": true
}
```

Private requests use Ollama only. Both persisted messages are marked
`is_private: true`; assistant messages record `provider: "ollama"`.

Unauthenticated chat still works but is stateless: no messages are stored, no
history is loaded, and `conversation_id` is omitted from the response.

## Error handling

| Status | Meaning |
| --- | --- |
| `400` | Empty prompt or invalid conversation data |
| `404` | Conversation not found or not owned by the current user |
| `422` | Invalid pagination or request body |
| `503` | LLM provider unavailable |
| `504` | LLM provider timeout |

LLM failures preserve the user message. No partial assistant response is
persisted.

## Limitations

- Authentication is not implemented yet; identity comes from the trusted
  `get_current_user_id` / `get_optional_user_id` dependency.
- History is limited by message count and characters, not model-token counts.
- Titles are derived from the first user message unless supplied explicitly.
- Context extraction, tasks, and reminders belong to later phases.
