# Tool Calling API Integration (Phase 11H)

Phase 11H wires the existing Phase 11A–11G tool engine into the real chat
pipeline. It adds no second orchestration path: the chat route, `ChatService`,
and `ToolOrchestrator` continue to use the same `ToolRegistry`, `ToolExecutor`,
`PermissionPolicy`, and confirmation service that the direct `/api/tools`
endpoints use.

The integration is opt-in. With `TOOL_CALLING_ENABLED=false`, chat behaves
exactly as it did before Phase 11H and the public response shape is unchanged.

## Architecture

```
POST /api/chat/
  └─ ChatService.get_response()
       ├─ resolve conversation and load history
       ├─ persist the user message
       ├─ classification + routing (existing Phase 9 path)
       ├─ build contextual prompt (existing unified context builder)
       ├─ if tool calling enabled, not private, and tools exist:
       │    └─ ToolOrchestrator.run()
       │         ├─ registry.llm_definitions(private=False)
       │         ├─ llm_router.generate_with_tools()
       │         ├─ normalize provider calls (Phase 11B)
       │         ├─ apply round / per-turn / per-request / loop limits
       │         ├─ tool_executor.execute() for every call
       │         │    ├─ registry + PermissionPolicy
       │         │    ├─ argument validation
       │         │    ├─ confirmation creation or claim
       │         │    ├─ timeout, cancellation, redaction, bounds
       │         │    └─ metadata-only execution history
       │         ├─ feed bounded results back to the provider
       │         └─ final text-only turn when the loop ends
       ├─ persist safe tool transcript entries (metadata only)
       ├─ persist the final assistant message
       └─ return ChatResponse (existing fields + optional tool fields)
```

For pending confirmations the orchestration stops immediately. The approval
endpoint runs the stored confirmation through the same executor and then asks
the provider for one final text-only answer:

```
POST /api/chat/confirmations/{id}/approve
  ├─ load confirmation (owner-scoped)
  ├─ reject missing / resolved / expired / cross-owner rows
  ├─ validate the owning conversation
  ├─ tool_executor.execute(confirmation_id=...)  (atomic single-use claim)
  ├─ ToolOrchestrator.resume_after_confirmation(tools=[])
  ├─ persist tool_result transcript + final assistant message
  └─ return ChatResponse
```

## Request and response schema

`POST /api/chat/` accepts the existing payload:

```json
{
  "message": "string",
  "private": false,
  "conversation_id": 1
}
```

`conversation_id` is optional. It must belong to the authenticated owner.

The response keeps the historical fields and adds optional tool fields:

```json
{
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "response": "final text answer",
  "conversation_id": 12,
  "tool_calling": true,
  "tool_executions": [
    {
      "success": true,
      "status": "success",
      "tool": "web_search",
      "error_code": null,
      "error_message": null,
      "error_category": null,
      "confirmation_required": false,
      "confirmation_id": null,
      "confirmation_status": "not_required",
      "truncated": false,
      "duration_ms": 42.1,
      "result_bytes": 812,
      "provider": "web_search"
    }
  ],
  "pending_confirmations": [],
  "limit_reached": false,
  "provider_failure": false
}
```

`response_model_exclude_none=true` means `null` fields and the tool fields are
omitted entirely when they do not apply. When tool calling is disabled or no
tool definitions are available, the response contains exactly `provider`,
`model`, `response`, and `conversation_id`.

`tool_executions` is deliberately data-free. Raw tool output is never returned
by the chat API because it may contain untrusted web content or sensitive
values. Use the underlying feature endpoint or the owner-scoped execution
history for the tool's own result.

`pending_confirmations` contains:

```json
{
  "confirmation_id": 7,
  "tool": "python_execute",
  "category": "sensitive",
  "preview": "code: ...",
  "expires_at": "2026-11-12T18:00:00+00:00"
}
```

The confirmation action endpoints add `confirmation_id` and
`confirmation_status` (`executed`, `failed`, or `rejected`).

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/chat/` | Run a normal chat turn, optionally with tools. |
| `POST` | `/api/chat/confirmations/{id}/approve` | Execute the exact stored action, then answer. |
| `POST` | `/api/chat/confirmations/{id}/reject` | Reject the action without executing anything. |

All three routes require the normal authenticated session and CSRF protection.
Identity always comes from the server-side session. The client cannot supply an
owner id, tool arguments, a permission decision, or an approval on behalf of
the model.

The lower-level `/api/tools` routes remain available and unchanged:

- `GET /api/tools/` lists registered tools, including disabled tools.
- `POST /api/tools/execute` runs one call through the full policy.
- `GET /api/tools/executions` and `/api/tools/executions/stats` expose
  metadata-only history.
- `GET /api/tools/confirmations` lists pending confirmations.
- `POST /api/tools/confirmations/{id}/confirm` and `/reject` manage them.

### Confirmation status codes

| Condition | Status |
| --- | --- |
| Missing confirmation or another owner's confirmation | `404` |
| Already executed, rejected, failed, or tampered arguments | `409` |
| Expired by time or by stored status | `410` |
| Successful approve or reject | `200` |

Cross-owner access is intentionally indistinguishable from a missing row.

## Tool loop

One chat request may make several provider turns:

1. **Selection turn(s).** The model receives the filtered tool definitions and
   may return text, one call, or multiple calls. Valid calls are executed
   sequentially through `ToolExecutor`.
2. **Result turns.** Safe, bounded tool results are appended as tool messages
   with an explicit "untrusted data" prefix. The model is never asked to treat
   tool output as instructions.
3. **Final text turn.** After the round budget is exhausted, one final turn is
   made with `tools=[]`. The model therefore cannot start an unbounded tool
   chain.

The orchestrator does not authorize anything itself. A tool name from the model
is only a request; the executor re-checks enablement, private mode, sensitive
policy, confirmation requirements, argument validation, and owner identity.

### Limits

| Setting | Default | Behaviour |
| --- | --- | --- |
| `TOOL_MAX_ROUNDS` | `2` | Provider turns that may request tools. A final text-only turn always follows. |
| `TOOL_MAX_CALLS_PER_REQUEST` | `5` | Total calls across all rounds. Extra calls receive a `ToolCallLimitReached` denial. |
| `TOOL_MAX_CALLS_PER_TURN` | `8` | Calls accepted from one assistant turn; clamped to a ceiling of 16. |
| `TOOL_MAX_RESULT_CONTEXT_BYTES` | `50000` | Total tool-result bytes sent back to the model; floor 1000. |
| `TOOL_LOOP_PREVENTION_ENABLED` | `true` | Rejects an identical `(tool, arguments)` call within one request. |
| `TOOL_EXECUTION_TIMEOUT_SECONDS` | `15` | Per-call wall-clock timeout enforced by the executor. |
| `TOOL_MAX_RESULT_BYTES` | `20000` | Per-result bound applied by the executor. |
| `TOOL_CONFIRMATION_TTL_SECONDS` | `600` | Confirmation lifetime. |

When a limit is hit, the response sets `limit_reached=true` and appends a short
deterministic note to the answer. The model is told explicitly that the call
was not executed. A repeated call is denied with `RepeatedToolCall`; an
over-limit call is denied with `ToolCallLimitReached`.

## Provider behaviour

Phase 11B remains the only provider translation layer.

- **Groq** responses are normalized through `extract_groq_tool_calls` and
  `to_groq_messages` / `to_groq_tools`.
- **Ollama** responses are normalized through `extract_ollama_tool_calls` and
  `to_ollama_messages` / `to_ollama_tools`.
- Both providers produce `ProviderToolTurn` objects. Arguments are parsed into
  mappings; malformed JSON or an unsupported shape is recorded as a structured
  call error rather than raising from the provider layer.
- Multiple calls in one provider response are supported and each one is
  validated and executed independently.
- A text-only response is returned unchanged.
- The existing router order and fallback rules are preserved.

Private requests never reach the tool orchestrator. The chat service passes
`private=False` only for non-private requests, and the router's private path is
Ollama-only and hides tool definitions.

### Provider failures

- If the provider fails **before any tool executes**, the original
  `LLMProviderError` / `LLMTimeoutError` / `LLMUnavailableError` is re-raised, so
  the chat API keeps its historical `503` / `504` responses. The request can be
  retried safely.
- If a tool already executed and a later provider turn fails, the orchestrator
  returns a deterministic summary with `provider_failure=true`. This prevents a
  client retry from silently repeating an action that already ran.
- If the resumed turn after an approved confirmation fails, the same
  deterministic summary is returned. The approval itself is never replayed.

There is no streaming support in this phase. Responses are complete JSON
bodies, and no "streaming tool calls" claim is made.

## Confirmation workflow

A sensitive or confirmation-requiring tool never executes during the chat
request that proposed it.

1. The model requests the tool.
2. The executor validates the arguments and creates a pending confirmation
   bound to the owner, conversation, tool name, and canonical argument digest.
3. The orchestrator stops immediately. Any remaining calls in the same turn are
   reported as `ConfirmationPending` rather than executed.
4. The chat response explains that nothing has executed and includes the
   pending confirmation.
5. The owner calls `/api/chat/confirmations/{id}/approve` or `/reject`.

Approval:

- loads the confirmation by owner (another owner gets `404`);
- rejects `executed`, `rejected`, `failed`, or `expired` rows;
- passes no client-supplied arguments to the executor; the stored arguments are
  revalidated and compared against the stored digest;
- atomically claims the confirmation, so only one caller can win;
- re-runs the current permission policy;
- executes under the normal timeout and result bounds;
- persists a safe `tool_result` transcript entry and a final assistant answer;
- returns the parsed result summary, never the raw tool payload.

The resumed provider turn receives `tools=[]`. The model cannot start a new
tool chain, cannot approve its own action, and cannot claim success before the
executor reports it.

Rejection sets the confirmation to `rejected`, clears the stored arguments, and
returns a deterministic "nothing was executed" answer. Rejection is also
single-use, owner-scoped, and subject to the same expiry rules.

## Persistence

Tool activity is stored in the existing `messages` table using a new nullable
`message_metadata` JSON column (migration `a1b2c3d4e5f6`). No new role is added;
transcript entries use the assistant role with one of these metadata kinds:

| Kind | Meaning |
| --- | --- |
| `tool_call` | The model requested one or more tools. |
| `tool_result` | A call completed, failed, timed out, or was denied. |
| `tool_confirmation_required` | A call is waiting for owner approval. |
| `tool_rejected` | The owner rejected the call. |

Only safe fields are persisted: kind, call id, tool name, status, success flag,
error code, confirmation id, truncation flag, result byte count, and the names
of argument keys. Argument **values**, tool output, file contents, and
credentials are never stored in `message_metadata`.

The transcript is written before the final assistant message, so the message
list is chronological:

```
user → tool_call → tool_result → assistant answer
```

For a pending confirmation:

```
user → tool_call → tool_confirmation_required → assistant pending notice
```

After approval:

```
... → tool_result → assistant resumed answer
```

Transcript entries are excluded from all future prompt history by both the
unified context provider and the prompt builder. They are displayed for audit
and UI purposes but are never replayed into a provider as if the assistant had
said them.

A failed transcript write is rolled back as a batch and does not prevent the
assistant answer from being returned. The assistant message itself is persisted
best-effort, preserving the pre-Phase-11H behaviour.

## Security properties

- **Identity.** The owner comes only from the authenticated session.
  `ToolContext.user_id` is never accepted from a prompt, model output, request
  body, query string, or header.
- **Authorization.** `PermissionPolicy` is re-evaluated before execution and
  again after a confirmation claim. The model cannot grant itself a tool or a
  private-mode exception.
- **Disabled tools.** `llm_definitions()` excludes disabled tools, and the
  executor still denies a disabled tool requested by name. Manual
  `/api/tools/execute` requests cannot run one either.
- **Sensitive tools.** They remain `SENSITIVE`, require confirmation, are not
  allowed in private mode, and are denied unless `TOOL_ALLOW_SENSITIVE=true`.
- **Private mode.** The orchestrator is never invoked for a private request.
  Private chat cannot see tools, create tool history, or create confirmations.
- **Confirmations.** Owner-, conversation-, tool-, and argument-digest-bound,
  single-use, expiring, and revalidated after approval. Cross-owner, replayed,
  tampered, and expired requests are rejected.
- **Untrusted tool output.** Tool results are framed with an explicit untrusted
  data prefix. The system prompt instructs the model not to follow instructions
  found inside tool output.
- **Data minimization.** Execution history and chat transcripts are
  metadata-only. API responses exclude raw tool data and sanitize error
  messages. Secrets are never logged or persisted by the tool engine.
- **Bounded work.** Round, per-turn, per-request, result-size, and context-size
  limits are enforced. An identical call cannot be repeated in the same
  request. A provider can never cause an unbounded tool loop.
- **Cancellation.** Caller cancellation propagates through the orchestrator and
  executor. A tool that raises `CancelledError` on its own is converted into a
  structured `ToolCancelled` result.

Phase 11F filesystem isolation and Phase 11G AppContainer/Job Object isolation
are unchanged. Phase 11H only routes calls to those tools; it does not widen
their boundaries.

## Configuration

```env
# Tool engine (Phases 11A–11H)
TOOL_CALLING_ENABLED=false
TOOL_MAX_ROUNDS=2
TOOL_MAX_CALLS_PER_REQUEST=5
TOOL_MAX_CALLS_PER_TURN=8
TOOL_MAX_RESULT_CONTEXT_BYTES=50000
TOOL_LOOP_PREVENTION_ENABLED=true
TOOL_EXECUTION_TIMEOUT_SECONDS=15
TOOL_MAX_ARGUMENT_BYTES=16384
TOOL_MAX_RESULT_BYTES=20000
TOOL_CONFIRMATION_TTL_SECONDS=600
TOOL_CONFIRMATION_MAX_PENDING=10
TOOL_HISTORY_ENABLED=true
TOOL_EXECUTION_RETENTION_DAYS=90
TOOL_ALLOW_SENSITIVE=false
```

Apply the new migration before enabling tool calling:

```powershell
cd backend
..\venv\Scripts\python.exe -m alembic upgrade head
```

The migration adds a nullable column only. Existing messages remain valid and
no rows are rewritten.

## Tests

Focused Phase 11H checks:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11h_tool_calling_api -v
```

Combined Phase 11A–11H checks:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11a_base tests.test_phase11a_registry tests.test_phase11b_tool_calling tests.test_phase11c_executor tests.test_phase11d_permissions tests.test_phase11e_web_search tests.test_phase11f_filesystem tests.test_phase11g_python_sandbox tests.test_phase11h_tool_calling_api -v
```

The Phase 11H suite covers text-only chat, one and multiple calls, multiple
rounds, round/per-turn/per-request limits, loop prevention, bounded result
context, malformed calls, unknown tools, tool failures, provider failure before
and after execution, disabled tools, sensitive-policy denial, private mode,
confirmation pending/approve/reject/expiry/replay/tamper/cross-owner,
metadata-only persistence and ordering, prompt-history exclusion, cancellation,
and Groq/Ollama contracts. Providers, tools, and HTTP transports are controlled
doubles; no real provider, network, or filesystem call is made.

## Limitations

- Tool execution is sequential. Multiple calls in one turn are not run in
  parallel, which keeps ordering, limits, and history deterministic.
- The resumed turn after approval is text-only and receives only the approved
  tool's result, not a replay of the full pre-confirmation tool chain. This is
  deliberate: it prevents the model from starting another action or approving
  itself.
- Raw tool output is available through the underlying feature endpoints, not
  through the chat response. Chat exposes safe execution summaries only.
- Streaming is not implemented. The chat response is a complete JSON body.
- Multiple tool calls per turn depend on the provider contract; the engine
  handles them when the provider returns them.
- Provider failures after a tool has run cannot be retried transparently. The
  API returns a deterministic summary instead of risking duplicate execution.
- Tool confirmations are owner-scoped to ASTA's single-owner deployment model.
  Multi-tenant role delegation is not implemented.
- The Phase 11H tests use in-memory SQLite, as the rest of the backend suite
  does; PostgreSQL JSON behaviour is exercised by the normal migration and
  integration path, not by these tests.
