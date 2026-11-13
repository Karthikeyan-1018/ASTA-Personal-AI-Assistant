# Tool Registry Architecture

## Overview

Phase 11A adds the central registry that lets ASTA describe, discover, and
manage tools without executing them. A tool is a typed Python capability with a
stable name, a description, argument and result contracts, and permission
metadata. The registry is the single source of truth for those definitions.

Phase 11A deliberately stops at registration and discovery. Argument validation,
policy enforcement, confirmation, and execution belong to Phase 11C (see
[`tool_execution.md`](tool_execution.md)). Provider tool-call parsing and chat
orchestration belong to Phase 11B (see
[`tool_calling.md`](tool_calling.md)).

```text
                    registration (startup / lazy)
 definitions/*.py ─────────────────────────────► ToolRegistry
                                                    │  name -> BaseTool
                                                    │  duplicate prevention
                                                    │  metadata + schema checks
                       definitions()                │
     LLM provider ◄─────────────────────────────────┘
          │
          │ ToolCall(name, arguments)             [Phase 11B]
          ▼
   chat orchestrator ──► registry.get(name) ──► executor   [Phase 11C]
                                                    │
                                                    │ validate + authorize
                                                    ▼
                                               tool.execute()
```

## Package layout

| File | Responsibility |
| --- | --- |
| `app/tools/__init__.py` | Import-light package marker. Concrete modules are imported directly to avoid ORM/service import cycles. |
| `app/tools/enums.py` | Stable string enums, including `ToolCategory`. |
| `app/tools/exceptions.py` | Structured tool errors such as `ToolValidationError` and `ToolNotFoundError`. |
| `app/tools/schemas.py` | `StrictModel`, `ToolCall`, `ToolDefinition`, and the execution result schema. |
| `app/tools/base.py` | `BaseTool`, `ToolContext`, and the shared preview helper. |
| `app/tools/registry.py` | Registration, validation, discovery, enabled/disabled views, and provider definitions. |
| `app/tools/permissions.py` | Server-side category, privacy, enablement, and confirmation policy. |
| `app/tools/executor.py` | Central execution engine: context checks, validation, confirmation, timeout, result bounds, and history. |
| `app/tools/result.py` | Normalized success/error results plus credential redaction and size limits. |
| `app/tools/definitions/` | Built-in tool registrations. |
| `app/tools/implementations/` | Concrete tool implementations, added in later phases. |

## BaseTool contract

Every tool subclasses `BaseTool` and declares class-level metadata:

```python
class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Adds two numbers."
    category = ToolCategory.READ_ONLY
    input_model = CalculatorInput
    output_schema = {
        "type": "object",
        "properties": {"result": {"type": "number"}},
    }
```

Required metadata:

- `name` — lowercase snake case, 2–64 characters, unique across the registry.
- `description` — non-empty; this is what the model reads.
- `category` — `read_only`, `reversible`, or `sensitive`.
- `input_model` — a Pydantic `BaseModel` subclass.

Optional metadata:

- `output_schema` — a JSON-Schema-shaped dictionary for documentation and
  consumers. It is not used to execute the tool in Phase 11A.
- `requires_confirmation` — requests confirmation even when the category would
  not normally require it.
- `allowed_in_private` — whether the tool may participate in private requests.
- `provider_label` — a non-secret audit label; never a credential.

`BaseTool` also provides:

- `enabled` — a property returning `True` by default. Implementations override
  it when configuration should disable the tool.
- `definition()` — projects the tool into a `ToolDefinition`.
- `validate_arguments()` — validates a raw argument dictionary with the input
  model. Invalid input becomes a `ToolValidationError` whose message contains
  field locations and reasons, not submitted values.
- `summarize()` — produces a redacted, length-limited preview for confirmation
  prompts.
- `execute()` — an abstract async method. Phase 11A declares the contract but
  does not run it.

## ToolDefinition and ToolCall

`ToolDefinition` is the public, serializable projection of a tool. It contains
the name, description, category, enabled state, permission metadata, input JSON
Schema, and output schema. `provider_schema()` converts it to the
OpenAI/Ollama-compatible function shape:

```json
{
  "type": "function",
  "function": {
    "name": "calculator",
    "description": "Adds two numbers.",
    "parameters": {
      "type": "object",
      "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"}
      },
      "additionalProperties": false
    }
  }
}
```

`ToolCall` is the inbound provider request: a name plus an argument object. The
registry does not accept `ToolCall` directly. Phase 11B resolves its name:

```python
tool = tool_registry.get(tool_call.name)
```

Phase 11C then validates and executes the resolved tool.

## Registration and discovery

Tools are registered once, either explicitly or through the lazy built-in
loader:

```python
from app.tools.registry import ToolRegistry

registry = ToolRegistry()
registry.register(CalculatorTool())
registry.register_all([TaskListTool(), TaskGetTool()])
```

Registration validates:

- the object implements `BaseTool`;
- the name matches the lowercase snake-case pattern;
- the name is not already registered;
- the description is a non-empty string;
- the category is a `ToolCategory`;
- the input model is a Pydantic `BaseModel` subclass;
- `output_schema` is a dictionary when supplied;
- permission flags are booleans;
- a `ToolDefinition` can be built from the input model.

`register_all()` is atomic: if any member is invalid or duplicates a name, no
member of the batch is added.

Discovery methods:

| Method | Result |
| --- | --- |
| `get(name)` | Tool instance, or `ToolNotFoundError` |
| `maybe_get(name)` | Tool instance or `None` |
| `names()` | Sorted names |
| `all()` | Tools sorted by name |
| `is_enabled(name)` | Current `enabled` state |
| `enabled_tools()` / `enabled_names()` | Enabled subset |
| `disabled_tools()` / `disabled_names()` | Disabled subset |
| `definitions()` | `ToolDefinition` list |
| `llm_definitions()` | Provider-ready function definitions allowed by policy |

`len(registry)`, `name in registry`, and iteration are also supported.

## Enabled and disabled tools

Enablement is configuration-driven. A tool overrides the `enabled` property
when its availability depends on settings:

```python
@property
def enabled(self) -> bool:
    return bool(settings.WEB_SEARCH_ENABLED and settings.WEB_SEARCH_BASE_URL)
```

By default, `definitions()` excludes disabled tools. Pass
`include_disabled=True` for an administrative view. `llm_definitions()` always
excludes disabled tools and additionally applies the existing permission policy
for the current request context. `get()` still resolves a disabled tool so
Phase 11C can return the correct structured `disabled` error rather than
pretending the tool does not exist.

## Permission metadata

Phase 11A records permission metadata but does not enforce it. Enforcement is
centralized in `app/tools/permissions.py` and applied by the executor in Phase
11C and hardened in Phase 11D.

- `read_only` — no stored-data mutation.
- `reversible` — a mutation that may require confirmation.
- `sensitive` — high-impact actions; deployment policy may deny them.
- `allowed_in_private` — controls private-request visibility.
- `requires_confirmation` — forces confirmation for any category, including
  `read_only`.

The model never supplies `user_id`, `private`, or a permission decision. Those
values come from the trusted server-side `ToolContext`.

## Async compatibility

Every tool implementation declares:

```python
async def execute(
    self,
    context: ToolContext,
    arguments: InputModel,
) -> ToolResult:
    ...
```

The registry stores the tool object without wrapping or converting it. Async
metadata and the implementation remain intact for Phase 11B and 11C. Concrete
tool calls are never made during registration.

## Lazy built-in loading

`ensure_builtin_tools()` imports `app.tools.definitions` and registers the
built-in tools exactly once. Lazy loading avoids an import cycle: ORM models
import tool enums, while tool implementations import services that import ORM
models. The loader is protected by a re-entrant lock, so concurrent discovery
cannot partially initialize the global registry.

## Relationship to Phases 11B, 11C, and 11D

Phase 11B:

1. calls `llm_definitions()` for the current request;
2. passes those definitions to Groq or Ollama in the canonical function shape;
3. normalizes the provider response into `NormalizedToolCall` and `ToolCall`
   objects;
4. resolves each valid name with `registry.get(name)` in the orchestrator.

Phase 11C and 11D:

1. validates argument shape and size, then calls `BaseTool.validate_arguments()`;
2. enforces permission, privacy, enablement, and confirmation policy before
   argument details are disclosed;
3. creates or claims a single-use confirmation bound to the owner, conversation,
   tool name, and exact arguments;
4. revalidates the stored payload and reruns policy after a claim;
5. calls `BaseTool.execute()` with a configured timeout;
6. normalizes, redacts, and bounds the result;
7. records metadata-only history and returns a `ToolResult`.

Phase 11D makes `requires_confirmation` effective for every category and adds
conversation binding, replay and duplicate rejection, and post-claim
revalidation and reauthorization.

See [`tool_execution.md`](tool_execution.md) for the complete execution
lifecycle, statuses, security properties, and configuration, and
[`tool_permissions_and_confirmations.md`](tool_permissions_and_confirmations.md)
for the Phase 11D authority and confirmation model.

Phase 11A intentionally contains no provider calls, no database access, no
migrations, and no execution side effects.

## Tests

Focused Phase 11A tests:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11a_base tests.test_phase11a_registry -v
```

Coverage includes registration and discovery, duplicate prevention, batch
atomicity, invalid definitions, JSON Schema projection, disabled filtering,
permission metadata, private filtering, provider definition shape, built-in
integration, and async execution compatibility.

## Known limitations

- Tools are registered per process. There is no distributed registry or
  database-backed tool catalog.
- Enablement is derived from configuration or tool implementation state; there
  is no runtime administrative toggle endpoint in Phase 11A.
- Output schemas are descriptive metadata, not runtime result validation.
- Tool execution, timeouts, confirmations, and history are implemented in Phase
  11C; provider tool-calling is implemented in Phase 11B.
