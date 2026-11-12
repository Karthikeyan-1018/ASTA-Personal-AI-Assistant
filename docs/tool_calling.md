# LLM Function Calling

## Overview

Phase 11B connects ASTA's hybrid Groq + Ollama router to the tool registry from
Phase 11A. It lets the model request a registered tool by name and arguments,
without executing anything. Execution, authorization, confirmations, and history
remain in Phase 11C.

The boundary is deliberately narrow:

```text
ToolRegistry.llm_definitions()
        │  provider-independent function schemas
        ▼
LLMRouter.generate_with_tools()  ── routing, health checks, fallback
        │
        ├── GroqProvider.generate_with_tools()
        │       tools + tool_choice="auto"
        │
        └── OllamaProvider.generate_with_tools()
                tools
        │
        ▼
ProviderToolTurn
        │  content + NormalizedToolCall list
        ▼
ToolOrchestrator (Phase 11C)
        │
        ▼
ToolExecutor validates, authorizes, confirms, executes, records
```

Nothing in Phase 11B imports or calls a tool implementation.

## Provider formats

Both providers accept tool definitions in the same canonical function shape:

```json
{
  "type": "function",
  "function": {
    "name": "task_list",
    "description": "List the owner's tasks.",
    "parameters": {
      "type": "object",
      "properties": {"limit": {"type": "integer"}},
      "additionalProperties": false
    }
  }
}
```

- **Groq** receives `tools=[...]` plus `tool_choice="auto"`. Function arguments
  come back as a JSON-encoded string.
- **Ollama** receives `tools=[...]`. Arguments normally come back as an object,
  but older builds may return a string; both are accepted.

`to_groq_tools()` and `to_ollama_tools()` sanitize definitions at the provider
boundary. They keep only `type`, `function.name`, `function.description`, and a
deep-copied `function.parameters`. Invalid entries, duplicate names, missing
names, and non-list inputs are dropped rather than sent to a provider.

## Parsing and normalization

Provider responses become a `ProviderToolTurn`:

| Field | Meaning |
| --- | --- |
| `content` | Assistant text, or an empty string |
| `tool_calls` | `NormalizedToolCall` entries |
| `usage` | Provider-reported token counts, when available |
| `model` | The model that produced the turn |

Each `NormalizedToolCall` has `id`, `name`, `arguments`, and an optional
`error`. `ProviderToolTurn.tool_call_schemas` returns only valid calls converted
to the public `ToolCall` schema.

The parser is defensive because provider output is untrusted:

- **Malformed JSON** is recorded as a value-free error. Raw arguments are never
  echoed into logs or error messages.
- **Non-object arguments** are rejected; a tool call must carry an object.
- **Oversized payloads** beyond `MAX_TOOL_ARGUMENT_CHARS` are rejected.
- **Missing or blank names** produce an invalid call with an error, never an
  empty tool name.
- **Oversized names** beyond 100 characters are marked invalid.
- **Missing, duplicate, or oversized call ids** are replaced with generated
  unique ids so multi-turn replay stays consistent.
- **Unsupported `tool_calls` shapes** (for example a string) produce one
  structured error instead of raising or iterating characters.
- **Excess calls** beyond `MAX_TOOL_CALLS_PER_TURN` are truncated and reported
  as one overflow error.
- **Partial garbage** is preserved per call: one malformed call does not discard
  valid calls in the same turn.

A malformed call is returned to the orchestrator, which turns it into a
`MalformedToolCall` result. It is not raised as a provider failure, so the
assistant can recover and explain the problem. If a malformed call still has an
id, replay keeps it paired with its tool result using a safe placeholder name;
a malformed call without an id is dropped from replay.

## Message translation

Multi-turn tool calling needs the previous assistant request and tool result to
be replayable. Neutral messages use only these shapes:

```python
# assistant requested calls
{"role": "assistant", "content": "...", "tool_calls": [
    {"id": "call_1", "name": "task_list", "arguments": {"limit": 5}}
]}

# trusted tool result
{"role": "tool", "tool_call_id": "call_1", "name": "task_list",
 "content": "..."}
```

`to_groq_messages()` serializes arguments to JSON and adds
`type="function"`. `to_ollama_messages()` keeps arguments as a mapping and uses
`tool_name`. Unknown or nameless replayed calls are filtered out, and empty
`tool_calls` lists are omitted rather than sent.

## Router behavior

`LLMRouter.generate_with_tools()` reuses the Phase 9 routing policy:

- Non-private requests follow the configured policy and fallback order.
- A provider that reports itself unhealthy is skipped.
- A timeout or provider error falls through to the next provider.
- If both providers fail, `LLMUnavailableError` preserves the existing chat API
  503 contract; timeouts still map to 504.
- An empty response with no tool calls is treated as a provider failure.

Private requests keep the Phase 9 guarantee:

- Ollama is the only provider contacted. Groq is never asked for a health check.
- Tool definitions are withheld (`tools=[]`) on the private path. The chat layer
  already declines to orchestrate tools for private requests; withholding the
  schemas is defence in depth.

Text-only chat is unchanged. `generate_response()` is untouched, the new method
adds `tool_calls` and `tool_call_schemas` keys, and a text-only
`generate_with_tools()` turn returns the same `response` content with empty
call lists.

## Relationship to Phase 11C

Phase 11C owns:

1. `ToolCall` argument validation against the tool's Pydantic model.
2. Permission and confirmation policy.
3. `BaseTool.execute()` with timeout and result bounds.
4. Execution history and pending confirmations.

Phase 11B only resolves provider responses into validated call objects. It
cannot authorize, confirm, or execute anything, and it does not touch the
database.

## Configuration

Phase 11B reuses existing settings:

| Setting | Purpose |
| --- | --- |
| `TOOL_CALLING_ENABLED` | Enables tool orchestration in chat (default `false`). |
| `TOOL_MAX_ROUNDS` | Maximum provider turns in the execution loop. |
| `TOOL_MAX_CALLS_PER_REQUEST` | Maximum executed calls across all rounds. |
| `GROQ_TIMEOUT_SECONDS` | Groq request timeout. |
| `OLLAMA_TIMEOUT_SECONDS` | Ollama request timeout. |
| `OLLAMA_NUM_CTX` | Ollama context window passed with each request. |

No new configuration or database migration is required for Phase 11B.

## Tests

Focused provider and router tests use mocked SDK responses; no real provider is
contacted:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11b_tool_calling -v
```

Coverage includes argument parsing, Groq and Ollama extraction, missing and
malformed calls, call-id normalization, call-count and size limits, public
`ToolCall` conversion, registry-to-provider schema conversion, message replay
translation, text-only preservation, routing fallback, private Ollama-only
behavior, empty-response handling, and the guarantee that Phase 11B never
executes a tool.

## Known limitations

- Tool choice is always `auto`; there is no forced-tool or per-tool choice
  policy yet.
- Streaming responses are not supported.
- Provider-specific argument quirks beyond JSON strings and mappings are not
  normalized.
- Token usage during multi-turn tool loops is summed by the orchestrator, while
  Phase 11B reports one provider turn at a time.
- No retry is attempted for a malformed provider call; the error is returned to
  the model for a recovery turn.
