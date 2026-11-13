# Tool Permissions and Confirmations

## Overview

Phase 11D hardens the authorization and approval boundary that Phase 11C
introduced. It does not add a second execution path. Every tool call still goes
through `app/tools/executor.py`, and every authorization decision still comes
from `app/tools/permissions.py`.

Phase 11D closes the remaining gaps:

- `requires_confirmation` is honored for every category, including `read_only`;
- confirmation claims are bound to the authenticated owner, the conversation
  that created them, the tool name, and the canonical argument digest;
- approvals are single-use and atomically claimed, so replay and duplicate
  approval are impossible;
- a claim is followed by argument revalidation and a fresh permission
  decision, so a stale approval can never bypass current policy;
- expired, rejected, missing, tampered, and failed confirmations are resolved
  safely and cannot execute the tool.

```text
tool request
    │
    ▼
ToolExecutor
    │
    ├── trusted ToolContext (owner, session, conversation, privacy)
    ├── PermissionPolicy.require_allowed()
    │       ├── enabled / disabled
    │       ├── private-mode restriction
    │       ├── sensitive-tool deployment policy
    │       └── requires_confirmation
    │
    ├── argument size + schema validation
    │
    ├── confirmation required?
    │       ├── no  ─────────────────────────────► execute
    │       └── yes
    │             ├── create pending (owner, conversation, digest, preview)
    │             └── claim supplied id
    │                   ├── owner lookup
    │                   ├── tool-name check
    │                   ├── conversation check
    │                   ├── expiry / resolved check
    │                   ├── argument-digest check
    │                   └── atomic pending -> executed
    │
    ├── revalidate the stored approved arguments
    ├── re-run PermissionPolicy.require_allowed()
    ├── execute
    └── record history + attach/fail the confirmation
```

## Central authorization policy

`PermissionPolicy` is the only component that decides whether a tool may run.
Tools and provider responses cannot authorize themselves. The policy receives
the registered `BaseTool` and the trusted `private` flag, and returns a
`PermissionDecision` containing `allowed`, `requires_confirmation`, the
category, a human-readable reason, and a stable error category.

The decision order is deliberate:

1. **Enablement.** A tool whose `enabled` property is false is denied with the
   `disabled` category before argument details are disclosed.
2. **Private mode.** A private request may only use tools that declare
   `allowed_in_private = True`. Everything else is denied with the `permission`
   category.
3. **Sensitive policy.** A `sensitive` tool is denied unless the deployment sets
   `TOOL_ALLOW_SENSITIVE=true`. When allowed, it always requires confirmation,
   regardless of its `requires_confirmation` flag.
4. **Confirmation metadata.** `requires_confirmation = True` is honored for
   `read_only`, `reversible`, and `sensitive` tools.

The registry uses the same policy through `llm_definitions()`, so the model
never sees a tool that the current request context would reject. `get()` still
resolves disabled tools so the executor can return a structured `disabled`
error instead of pretending the tool does not exist.

## Confirmation model

A confirmation is a durable `tool_confirmations` row. It stores:

- the owning `user_id`;
- the optional `conversation_id` where the proposal was created;
- the tool name and category;
- the exact validated argument payload and its canonical digest;
- a safe, length-limited preview;
- creation, expiry, and resolution timestamps;
- the linked execution id after a successful run.

The raw arguments exist only between proposal and resolution. The service clears
them atomically when the confirmation is claimed, rejected, or expired. History
rows never contain argument values.

### Binding rules

A confirmation can only authorize the exact call it was created for:

| Binding | Rule |
| --- | --- |
| Owner | The row is looked up by `user_id`; another owner gets `not_found`. |
| Tool | The claimed tool name must match the stored tool name. |
| Conversation | If the row has a `conversation_id`, the caller must present the same conversation. A different or missing conversation is rejected. |
| Arguments | The caller's expected arguments and the stored payload must both match the stored canonical digest. |
| Expiry | The row must still be pending and unexpired at claim time. |
| Use count | Only one caller can win the atomic `pending -> executed` update. |

The executor passes the backend-created `ToolContext.conversation_id` as the
expected conversation. A caller cannot supply or override it through tool
arguments.

### Lifecycle

```text
pending ──claim──► executed ──success──► execution attached
   │                   │
   │                   └──failure──► failed
   ├──reject──────────► rejected
   ├──expiry──────────► expired
   └──stale sweep─────► expired
```

- **Create.** `create_pending()` expires stale rows, enforces
  `TOOL_CONFIRMATION_MAX_PENDING`, validates nothing itself (the executor has
  already validated), and returns a `confirmation_required` result without
  calling the tool.
- **Claim.** `claim()` verifies ownership, state, expiry, tool, conversation,
  and digest, then performs a conditional `UPDATE ... WHERE status = pending`.
  A rowcount other than one means another caller won the race; the loser gets a
  resolved or expired error.
- **Execute.** The executor revalidates the stored payload and re-runs the
  permission policy before calling the tool. A failure in either step marks the
  confirmation `failed`.
- **Reject.** `reject()` transitions a pending row to `rejected` and clears the
  arguments. Nothing executes.
- **Expire.** Expired rows are transitioned on read/claim/reject and by a stale
  sweep. Expired confirmations cannot be claimed or rejected as if still valid.
- **Fail.** A tool failure, timeout, or unexpected post-claim fault marks an
  executed confirmation `failed` so it cannot be reused.

## Executor integration

The executor remains the only integration point:

1. Extract the trusted owner, session, privacy flag, and conversation id from
   `ToolContext`.
2. Resolve the tool and run `permission_policy.require_allowed()`.
3. Enforce the argument object shape, size limit, and Pydantic schema.
4. If confirmation is required and no id is supplied, create a pending
   confirmation and return `confirmation_required`.
5. If an id is supplied:
   - reject it when the tool does not use confirmations;
   - claim it with the expected tool name, arguments, and conversation;
   - revalidate the stored arguments;
   - re-evaluate the permission policy;
   - execute only when both checks still pass.
6. Execute under the configured timeout and normalize the result.
7. Record metadata-only history.
8. Attach the execution id on success, or mark the confirmation failed.

Approval can never skip validation or authorization. The two post-claim checks
are intentionally independent: a confirmation proves user intent, not current
permission to act.

## Failure mapping

| Failure | Result status | Error category | Confirmation row |
| --- | --- | --- | --- |
| Disabled tool | `denied` | `disabled` | `not_required` |
| Private-mode denial | `denied` | `permission` | `not_required` |
| Sensitive policy denial | `denied` | `permission` | `not_required` |
| Approval needed | `confirmation_required` | `confirmation` | `pending` |
| Wrong owner / missing row | `denied` | `confirmation` | unchanged |
| Wrong tool, conversation, or digest | `denied` | `confirmation` | unchanged (still pending) |
| Expired | `denied` | `confirmation` | `expired` |
| Already resolved / replay | `denied` | `confirmation` | unchanged (terminal) |
| Post-claim validation failure | `error` | `validation` | `failed` |
| Post-claim policy failure | `denied` | `disabled` or `permission` | `failed` |
| Tool execution failure | `error` | `execution` | `failed` |

Rejected and expired confirmations are terminal. A caller cannot retry the same
row; a new proposal must be created and approved.

## REST integration

The existing authenticated router is unchanged:

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/api/tools/execute` | Validate and authorize; creates a pending confirmation when needed. |
| `POST` | `/api/tools/confirmations/{id}/confirm` | Claim, revalidate, reauthorize, and execute one exact confirmation. |
| `POST` | `/api/tools/confirmations/{id}/reject` | Reject without executing. |
| `GET` | `/api/tools/confirmations` | List owner-scoped pending confirmations, optionally by conversation. |

The confirm endpoint builds its `ToolContext` from the authenticated session and
the stored conversation id, so the conversation binding is enforced on the API
path as well as in chat. HTTP mapping:

- `404` — row missing or owned by another user;
- `409` — already resolved, replayed, wrong tool, wrong conversation, or
  tampered arguments;
- `410` — expired confirmation;
- `200` — the structured `ToolResult`, which may itself report a tool failure.

CSRF protection and owner scoping are unchanged. The client never supplies an
owner id or an approved argument payload.

## Configuration

| Setting | Default | Effect |
| --- | --- | --- |
| `TOOL_ALLOW_SENSITIVE` | `false` | Allows sensitive tools; they still require confirmation. |
| `TOOL_CONFIRMATION_TTL_SECONDS` | `600` | Confirmation lifetime. |
| `TOOL_CONFIRMATION_MAX_PENDING` | `10` | Maximum pending confirmations per owner. |
| `TOOL_HISTORY_ENABLED` | `true` | Enables metadata-only execution history. |

Enablement remains configuration-driven through each tool's `enabled` property.
There is no runtime admin toggle and no per-request override.

## Tests

Focused Phase 11D tests:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11d_permissions -v
```

Combined Phase 11A–11D tests:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11a_base tests.test_phase11a_registry tests.test_phase11b_tool_calling tests.test_phase11c_executor tests.test_phase11d_permissions -v
```

The Phase 11D suite uses mocked tools, a fresh registry, in-memory SQLite, and
the real FastAPI authentication and CSRF dependencies. Coverage includes:

- authorized and unauthorized tools;
- category, enablement, and private-mode policy;
- read-only, reversible, and sensitive confirmation requirements;
- approval, rejection, and execution failure;
- expiry, replay, duplicate approval, and pending limits;
- wrong owner, wrong tool, wrong conversation, and modified arguments;
- tampered stored arguments and post-claim validation or policy failure;
- API authentication, CSRF, `404`/`409`/`410` mapping, and owner scoping;
- regression coverage for Phases 11A–11C.

No real provider, network, file-system, or Python-execution tool is called.

## Limitations

- Confirmation approval is user intent, not a capability grant. Current policy
  is always reapplied.
- A tampered confirmation that fails its digest check remains pending; the user
  can still approve the original stored payload. The tampered request never
  executes.
- Timeout and cancellation behavior is inherited from Phase 11C. In-process
  execution cannot interrupt a tool that blocks the event loop synchronously.
- Confirmation and history cleanup is bounded but not a full archival system.
- The controlled Python runner remains a convenience runner, not a hardened
  sandbox.
