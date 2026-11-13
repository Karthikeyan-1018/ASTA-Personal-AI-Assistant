# Tool Execution Engine

## Overview

Phase 11C completes the boundary between a provider's tool request and a real
server-side action. Every execution, whether it comes from chat or the REST API,
passes through `app/tools/executor.py`. The executor is provider-independent: it
accepts a tool name plus an argument mapping, not a Groq or Ollama response
object.

```text
chat orchestrator ──┐
                    ├──► ToolExecutor ──► ToolRegistry.get(name)
POST /api/tools/execute ─┘       │
POST /api/tools/confirmations/{id}/confirm
                                 │
                                 ├── trusted ToolContext (owner, session, privacy)
                                 ├── permission policy
                                 ├── argument size + schema validation
                                 ├── confirmation create / claim / revalidate
                                 ├── BaseTool.execute() with timeout
                                 ├── result redaction + size bound
                                 ├── metadata-only history
                                 └── normalized ToolResult
```

Phase 11A owns registration and `BaseTool`, Phase 11B owns provider parsing and
router translation, Phase 11C owns authorization, execution, confirmation
enforcement, and result normalization, and Phase 11D hardens the permission and
confirmation boundary. Phase 11D reuses this executor and the existing
confirmation service; there is no second approval or execution path. See
[`tool_permissions_and_confirmations.md`](tool_permissions_and_confirmations.md).

## Public interface

```python
result = await tool_executor.execute(
    tool_name="task_list",
    arguments={"limit": 5},
    context=context,
    confirmation_id=None,
    create_confirmation=True,
)
```

`context` is a backend-created `ToolContext` containing the trusted owner id,
database session, optional conversation id, and privacy flag. The LLM never
supplies it.

`execute_call()` is the adapter for the Phase 11B shape:

```python
result = await tool_executor.execute_call(call, context=context)
```

It accepts either `NormalizedToolCall` or the public `ToolCall`. A call carrying
a parser error, a blank name, or a non-object argument payload becomes a
structured `malformed_call` result and is never executed.

## Execution lifecycle

1. **Trusted context check.** `ToolContext.require_user_id()` must produce a
   positive integer and the session must be present. A missing or malformed
   context is a `configuration` error; there is no anonymous fallback owner.
2. **Resolution.** The name is stripped and bounded, then resolved with
   `tool_registry.get(name)`. Unknown tools become `not_found`. A registry
   deployment failure becomes `configuration`, never a permission decision.
3. **Policy check.** The permission policy evaluates enablement, the private
   request flag, category, and confirmation metadata *before* argument
   validation. `requires_confirmation` applies to every category, including
   `read_only`. A disabled or private-only tool therefore cannot disclose
   schema details through validation errors.
4. **Argument checks.** Arguments must be a mapping, must be within
   `TOOL_MAX_ARGUMENT_BYTES`, and must validate against the tool's Pydantic
   input model. Validation errors name fields and reasons but never echo
   submitted values.
5. **Confirmation path.**
   - If confirmation is required and no id is supplied, the executor creates a
     pending confirmation bound to the owner, proposing conversation, tool name,
     and canonical argument digest, then returns `confirmation_required` without
     calling the tool.
   - If an id is supplied, the service atomically claims it, checks the owner,
     tool name, conversation, expiry, and canonical argument digest, and returns
     the exact stored payload. The executor revalidates that payload and re-runs
     the permission policy before executing.
   - The caller cannot approve different arguments from those that were
     previewed, cannot present another owner's or conversation's confirmation,
     and cannot reuse a confirmation. A post-claim failure marks it failed.
6. **Execution.** `BaseTool.execute()` runs under
   `asyncio.wait_for(..., timeout=TOOL_EXECUTION_TIMEOUT_SECONDS)`.
7. **Result normalization.** The executor sets the tool name, measured duration,
   and provider label, redacts credential-shaped metadata and data, removes
   stack traces from messages, and bounds the serialized result to
   `TOOL_MAX_RESULT_BYTES`. Oversized data becomes a compact truncated preview.
8. **History and confirmation bookkeeping.** A metadata-only history row is
   written: tool name, category, status, duration, confirmation state, safe error
   category/code, provider, argument hash, argument keys, and result size. Raw
   arguments, result content, prompts, and credentials are never stored. History
   and confirmation attachment are best effort and never change the tool result.
9. **Return.** Every path returns a `ToolResult`. A fault in one tool cannot
   crash the assistant request. The only exception that deliberately escapes is
   caller cancellation, which propagates so request shutdown and timeout handling
   remain cooperative.

## Statuses and error categories

`ToolExecutionStatus` is the terminal state of one attempt:

| Status | Meaning |
| --- | --- |
| `success` | The tool executed and returned a successful result. |
| `error` | Validation, execution, malformed-call, internal, or provider fault. |
| `timeout` | The configured execution timeout elapsed. |
| `denied` | The policy, owner scope, disabled state, or confirmation refused execution. |
| `confirmation_required` | A pending confirmation was created; nothing executed. |

`ToolErrorCategory` classifies failures without exposing sensitive detail:
`validation`, `not_found`, `disabled`, `permission`, `confirmation`, `timeout`,
`execution`, `provider`, `malformed_call`, `size_limit`, `configuration`, and
`internal`.

The executor also detects contract violations: a tool that returns something
other than a `ToolResult` produces an `internal` / `ToolContractViolation`
result instead of leaking the object to the caller.

## Security properties

- **Trusted identity only.** Owner id, privacy flag, conversation id, and
  session come from `ToolContext`. `StrictModel` rejects unknown fields, so an
  LLM cannot smuggle `user_id`, `private`, or `force` into a tool.
- **Server-side policy.** Enablement, private-request restrictions, sensitive
  tool opt-in, and confirmation requirements are evaluated in the executor, not
  by the model. Policy is rechecked after a confirmation is claimed.
- **Confirmation binding.** A confirmation is owner-scoped, conversation-bound
  when it was proposed inside a conversation, single-use, expiring, and bound to
  the tool name and canonical argument digest. The executor executes the stored,
  revalidated payload rather than trusting a resent payload, and re-runs the
  permission policy after the claim.
- **Sensitive-data protection.** Error messages are bounded and credential-like
  values are redacted. Result metadata and data are recursively redacted for
  credential patterns and obvious credential keys such as `password`, `token`,
  and `api_key`. Internal stack traces are replaced with a generic message in
  the returned result; exception detail remains server-side only.
- **No argument history.** History stores an argument hash and sorted keys, not
  values. Confirmation arguments are cleared when the confirmation is claimed,
  rejected, or expired.
- **Failure isolation.** Unexpected exceptions, structured tool errors,
  timeouts, contract violations, and history failures become normalized results.
  A single failing call does not stop the rest of a multi-call turn.
- **Cancellation distinction.** Caller cancellation is re-raised. A tool that
  raises `CancelledError` on its own becomes a structured `ToolCancelled` error
  so a faulty tool cannot masquerade as request shutdown.

## REST integration

The authenticated tool router exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools/` | List registered tools, including disabled ones. |
| `POST` | `/api/tools/execute` | Execute one call under the full policy. |
| `GET` | `/api/tools/executions` | List owner-scoped metadata-only history. |
| `GET` | `/api/tools/executions/stats` | Aggregate owner-scoped execution statistics. |
| `GET` | `/api/tools/confirmations` | List pending confirmations. |
| `POST` | `/api/tools/confirmations/{id}/confirm` | Claim, revalidate, and execute one confirmation. |
| `POST` | `/api/tools/confirmations/{id}/reject` | Reject a pending confirmation without executing. |

All routes require a session and CSRF protection. Identity is never read from a
request body.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `TOOL_EXECUTION_TIMEOUT_SECONDS` | `15` | Per-call wall-clock timeout. |
| `TOOL_MAX_ARGUMENT_BYTES` | `16384` | Maximum serialized argument size. |
| `TOOL_MAX_RESULT_BYTES` | `20000` | Maximum serialized result size. |
| `TOOL_CONFIRMATION_TTL_SECONDS` | `600` | Confirmation lifetime. |
| `TOOL_CONFIRMATION_MAX_PENDING` | `10` | Pending confirmations per owner. |
| `TOOL_HISTORY_ENABLED` | `true` | Enables metadata-only execution history. |
| `TOOL_EXECUTION_RETENTION_DAYS` | `90` | History retention window. |
| `TOOL_ALLOW_SENSITIVE` | `false` | Deployment opt-in for sensitive tools. |
| `TOOL_CALLING_ENABLED` | `false` | Enables tool orchestration in chat. |
| `TOOL_MAX_CALLS_PER_REQUEST` | `5` | Executed calls across a chat turn. |
| `TOOL_MAX_ROUNDS` | `2` | Provider turns in the chat tool loop. |

Phase 11C adds no settings. All values already exist in `app/core/config.py`.

## Tests

Focused Phase 11C tests:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11c_executor -v
```

Combined Phase 11A, 11B, and 11C tests:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11a_base tests.test_phase11a_registry tests.test_phase11b_tool_calling tests.test_phase11c_executor -v
```

The Phase 11C suite uses a fresh registry, mocked tools, and in-memory SQLite.
No real provider, network, or file-system call is made. Coverage includes
successful execution, unknown and disabled tools, invalid and oversized
arguments, missing trusted context, execution exceptions, structured errors,
timeouts, tool-raised and caller cancellation, result truncation, multiple and
mixed calls, malformed normalized calls, credential redaction, history
bookkeeping, confirmation creation/execution/replay/ownership, and Phase
11A/11B compatibility.

Focused Phase 11D permission and confirmation tests:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11d_permissions -v
```

They cover authorized and unauthorized tools, private-mode and disabled tools,
every confirmation-requiring category, approval and rejection, expiry, replay,
duplicate approval, wrong owner, wrong conversation, modified or tampered
arguments, post-claim validation and authorization failure, API authentication
and CSRF, and Phase 11A–11C regression.

## Known limitations

- Timeout enforcement cancels the awaiting coroutine. A tool that blocks the
  event loop synchronously cannot be interrupted.
- The timeout is global per deployment; there is no per-tool override yet.
- Redaction is heuristic. Tool implementations must still avoid returning
  secrets deliberately; the executor is a safety net, not a data-loss
  prevention system.
- Tool execution is single-process and in-process. There is no distributed
  worker, queue, or cross-process cancellation.
- History is best effort. A database failure is logged and does not fail the
  tool call, so an audit row can be missing during an outage.
- Tool results are data, not instructions. The orchestrator must continue to
  treat retrieved content as untrusted.
- The controlled Python runner remains a convenience runner, not a hardened
  sandbox.

## Relationship to Phase 11D

Phase 11D confirmation workflows reuse the same tables, service, and executor.
The executor does not bypass confirmation or add a second approval path:

- pending confirmations are created with the validated arguments, a safe
  preview, the owning conversation, and a canonical digest;
- approval claims the stored payload once, bound to the owner, tool,
  conversation, and digest, then revalidates the arguments and reruns policy;
- rejection and expiry are handled by the confirmation service and clear the
  stored payload;
- a failed post-claim validation, authorization check, or execution marks the
  confirmation failed;
- a successful execution attaches the history row to the confirmation.

Phase 11D also makes `requires_confirmation` effective for every category,
including `read_only`. No migration is required: the Phase 11 tables introduced
with the tool engine remain unchanged. See
[`tool_permissions_and_confirmations.md`](tool_permissions_and_confirmations.md)
for the full Phase 11D policy and lifecycle.
