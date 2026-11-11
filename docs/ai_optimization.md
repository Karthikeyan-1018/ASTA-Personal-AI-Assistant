# AI Optimization, Routing, Cache, and Usage Tracking

Phase 9 adds a conservative optimization layer to ASTA's existing chat
pipeline. It does not replace the unified context builder, the LLM router
contract, or the authentication model. It makes four additive improvements:

1. **Token-aware context budgeting** keeps a prompt inside the selected
   model's context window.
2. **Deterministic query classification** supplies an optimization hint for
   routing and cache eligibility.
3. **A conservative semantic response cache** serves repeated stateless
   requests without calling a provider.
4. **Metadata-only usage tracking** records tokens, latency, routing, fallback,
   and cache outcomes.

Hybrid memory retrieval is included as a related context optimization.

Private requests remain local-only throughout. A private prompt is resolved to
Ollama before any routing policy, cache lookup, or classifier fallback runs.

## Pipeline

```text
POST /api/chat/
    |
    v
ChatService
    |-- resolve/create conversation, persist user message
    |-- classify_query(prompt, private)
    |-- select_route(private, complexity)
    |-- retrieve_hybrid(memories)  -> keyword fallback
    |-- UnifiedContextBuilder.inspect(provider, model)
    |       char budget + token budget
    |
    v
cache eligible? -- no --> LLMRouter -> persist -> usage record
    |
   yes
    |
    v
response_cache.lookup -- hit --> cached answer -> persist -> usage record
    |
   miss
    |
    v
LLMRouter -> persist -> cache.store (if still eligible) -> usage record
```

Every step after routing is best-effort except the provider call itself. A cache
or analytics failure never turns a successful answer into an error.

## Query classification

`app/ai/query_classifier.py` classifies a request into one of six labels:

| Label | Meaning | Eligible for cache |
| --- | --- | --- |
| `simple` | Short, direct request | Yes |
| `moderate` | Explanatory or multi-sentence request | Yes |
| `complex` | Long, multi-part, or reasoning-heavy request | No |
| `dynamic_information` | Time-sensitive or current-state request | No |
| `task_action` | Task mutation or confirmation wording | No |
| `private` | Private request | No |

Rule order is security-relevant:

1. `private=True` always returns `private` before any other check. The optional
   LLM fallback is never called for it.
2. Empty input returns `simple` with low confidence.
3. Task mutation or confirmation wording returns `task_action`.
4. Time-sensitive, current-state, agenda, or "what should I do next" wording
   returns `dynamic_information`.
5. Long or multi-part requests (length, word count, multiple question marks,
   code fences, or multiple complex signals) return `complex`.
6. Explanatory requests return `moderate`.
7. Everything else returns `simple`.

Classification is **only** an optimization hint. It cannot:

- authorize a task mutation or task-action confirmation;
- change ownership or session checks;
- make a private request cloud-eligible;
- bypass the approved-only memory rule.

### Optional LLM fallback

The fallback is disabled by default. When enabled, it runs only after a
deterministic result below `AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD`
(default `0.6`), never for private requests, and accepts only
`simple`, `moderate`, `complex`, `dynamic_information`, or `dynamic`.
Unknown labels, prompt-injection text, `PRIVATE`, and `TASK_ACTION` are rejected
and the deterministic result is kept. A fallback failure also keeps the
deterministic result.

Set `AI_QUERY_CLASSIFIER_ENABLED=false` to skip classification entirely; normal
requests then behave as `moderate` and private requests remain `private`.

## Routing policies

`app/ai/routing_policy.py` decides the ordered providers for a request.

| Policy | Primary (non-private) | Fallback |
| --- | --- | --- |
| `groq_first` (default) | Groq | Ollama |
| `local_first` | Ollama | Groq |
| `cost_aware` | Configured per complexity | The other provider |

`cost_aware` uses:

| Complexity | Setting | Default |
| --- | --- | --- |
| `simple` | `AI_ROUTING_SIMPLE_PROVIDER` | `ollama` |
| `moderate` | `AI_ROUTING_MODERATE_PROVIDER` | `groq` |
| `complex` | `AI_ROUTING_COMPLEX_PROVIDER` | `groq` |
| `dynamic_information` | `AI_ROUTING_DYNAMIC_PROVIDER` | `groq` |
| `task_action` | `AI_ROUTING_COMPLEX_PROVIDER` | `groq` |

Unknown policy names fall back to `groq_first` with a warning. Unknown
cost-aware provider values fall back to Groq. A provider typo is never silently
sent to an unintended destination.

### Private requests

Private routing is resolved before `AI_ROUTING_POLICY` is read:

- primary: `ollama`;
- fallback: none;
- policy: `private`.

This applies to every policy. The private execution path never checks Groq's
health, never calls Groq, and never falls back to the cloud. An unavailable or
failing Ollama returns `LLMUnavailableError` with a message confirming the
private request was not sent to the cloud.

### Model selection

Model names come from provider configuration (`GROQ_MODEL`, `OLLAMA_MODEL`).
Optional overrides use a comma-separated `complexity:provider:model` format:

```dotenv
AI_ROUTING_MODEL_OVERRIDES=simple:ollama:qwen3:8b,default:groq:openai/gpt-oss-20b
```

Resolution order for a provider is: `(complexity, provider)`, then
`(default, provider)`, then `(*, provider)`, then the provider default model.

### Router metadata

`LLMRouter.generate_response()` keeps its historical return contract
(`provider`, `model`, `response`) and adds internal metadata:
`routing_policy`, `complexity`, `provider_order`, `fallback_used`,
`duration_ms`, `usage`, and `request_type`. `ChatResponse` exposes only
`provider`, `model`, `response`, and `conversation_id`, so the public API remains
backward-compatible.

## Token-aware context budgeting

`app/ai/tokenization.py` resolves a conservative input budget.

Context window resolution order:

1. `AI_MODEL_CONTEXT_WINDOWS` entry for the exact model name.
2. `AI_GROQ_CONTEXT_WINDOW_TOKENS` or `AI_OLLAMA_CONTEXT_WINDOW_TOKENS`.
3. `AI_DEFAULT_CONTEXT_WINDOW_TOKENS` for any other provider.

The available input budget is:

```text
max(128, context_window - AI_RESPONSE_RESERVE_TOKENS - AI_CONTEXT_SAFETY_TOKENS)
```

A prompt is over budget when it exceeds either the existing character limit or
the token limit. Trimming follows the configured priority order from lowest to
highest. History drops its oldest messages first. The current user message is
never removed.

Token counting is a deterministic heuristic: `ceil(characters /
AI_CHARS_PER_TOKEN)` plus a word-based estimate, with an additional count for CJK
characters. It never returns zero for non-empty text and does not download a
tokenizer at request time. Provider-reported usage is preferred for analytics
when available. If counting or budget resolution fails, the builder uses the
character budget instead of failing the request.

Disable token budgeting with `AI_TOKEN_BUDGET_ENABLED=false`.

## Semantic response cache

`app/services/response_cache_service.py` stores and retrieves eligible answers.
The cache is intentionally narrow.

### Eligibility

A request may use the cache only when **all** of these are true:

- the request is authenticated;
- it is not private;
- `AI_CACHE_ENABLED=true`;
- the classifier returns `simple` or `moderate`;
- the complexity is listed in `AI_CACHE_ALLOWED_COMPLEXITIES`;
- the prompt contains no credential-like secret;
- when `AI_CACHE_STATELESS_ONLY=true`, there is no conversation history and no
  rendered context section.

Task actions, dynamic/current-state requests, context-bearing prompts, private
prompts, and prompts that look like they contain secrets are never cached.

### Matching

- Queries are normalised with collapsed whitespace and `casefold()`, then hashed
  with SHA-256. Raw prompts are not stored.
- The cache key includes `user_id`, `query_hash`, `prompt_version`, and
  `config_version`.
- PostgreSQL with pgvector also performs cosine-similarity lookup against stored
  embeddings. Matches below `AI_CACHE_SIMILARITY_THRESHOLD` (default `0.95`) are
  rejected.
- SQLite and other portable databases use exact query-hash matching only and do
  not load the embedding model.
- `AI_CACHE_SEMANTIC_ENABLED=false` disables semantic matching while keeping
  exact matching.

### Lifecycle

- Entries expire after `AI_CACHE_TTL_SECONDS` (default one hour).
- Each user is capped at `AI_CACHE_MAX_ENTRIES_PER_USER`; the oldest entries by
  creation time are evicted when the cap is exceeded.
- Exact and semantic hits increment `hit_count` and set `last_used_at`.
- A lookup does not commit; the chat flow's normal persistence commits the
  updated hit metadata together with the assistant message.
- `purge_expired()` deletes expired rows.
- Change `AI_CACHE_PROMPT_VERSION` or `AI_CACHE_CONFIG_VERSION` to invalidate
  entries after a prompt or configuration change.

### Privacy

- Private requests are never looked up in or written to the cache.
- Entries are user-scoped; another user's row is never returned.
- The table stores a query hash and an optional embedding, not the raw prompt.
- Responses are stored so a later identical request can avoid a provider call.
  Because only stateless, context-free, non-sensitive requests are eligible,
  cached answers cannot contain another user's context or a private turn.

## Hybrid memory retrieval

`MemoryRetriever.retrieve_hybrid()` blends semantic and keyword candidates:

- semantic candidates contribute `AI_MEMORY_SEMANTIC_WEIGHT` (default `0.7`);
- keyword candidates contribute `AI_MEMORY_KEYWORD_WEIGHT` (default `0.3`);
- results are deduplicated by normalised content and category;
- a candidate survives when its blended score is at least
  `AI_MEMORY_MIN_HYBRID_SCORE` (default `0.05`);
- `AI_MEMORY_CANDIDATE_LIMIT` controls how many candidates each path may return.

Both retrieval paths enforce `MemoryStatus.APPROVED` and user ownership. If
semantic retrieval is unavailable, retrieval falls back to keyword search. If
keyword retrieval fails during a hybrid request, semantic results are still
used. Unexpected database failures propagate so `ChatService` can log a failure
and continue without memory context.

Disable hybrid retrieval with `AI_MEMORY_HYBRID_ENABLED=false`; chat then uses
the existing semantic-or-keyword path.

## Usage and performance tracking

`app/services/usage_tracking_service.py` records one metadata-only row per
chat outcome.

Recorded fields include:

- request type, provider, model, complexity, routing policy;
- cache-hit, fallback, and success flags;
- an error class name (never an error message);
- input, output, and total token counts;
- token source (`provider` or `estimated`);
- optional configured cost;
- duration in milliseconds.

Records never contain prompt text, response text, credentials, or provider error
details. Provider-reported counts are distinguished from estimated counts.
`cost_usd` is populated only when a positive configured price exists; zero means
"unknown". Failed chat calls record a failure row before the error is re-raised.
Cache hits record a row with no token usage.

### Endpoints

Both endpoints require the authenticated owner and are scoped to that user:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/ai/usage/summary?days=90` | Aggregate totals, rates, providers, models, latency, optional cost |
| `GET` | `/api/ai/usage/recent?limit=50` | Recent rows, newest first |

Summary fields include `total_requests`, `successful_requests`,
`failed_requests`, `cache_hits`, `cache_hit_rate`, `fallback_requests`,
`total_input_tokens`, `total_output_tokens`, `total_tokens`,
`provider_reported_usage`, `estimated_usage`, `estimated_cost_usd`,
`average_duration_ms`, `providers`, `models`, and `request_types`.
`days` is bounded to 1–3650 and defaults to `AI_USAGE_RETENTION_DAYS`.
`limit` is bounded to 1–500 and defaults to `AI_USAGE_RECENT_LIMIT`.

### Retention

`purge_older_than()` deletes rows older than the configured retention window
(default 90 days). It is not scheduled automatically; call it from an operator
job or maintenance command. Disable tracking with
`AI_USAGE_TRACKING_ENABLED=false`.

## Privacy guarantees

- Private requests always use Ollama only, including fallback and
  classification paths.
- Private requests are never classified by an LLM, cached, or sent to Groq.
- Identity comes from the validated server-side session; usage and cache rows are
  always filtered by the authenticated owner.
- Only approved memories enter normal context.
- Classification and LLM output never authorize a mutation. Task changes still
  go through `TaskService` with explicit confirmation.
- Usage records are metadata-only.
- Cache rows store a hash and optional embedding, not raw prompts.
- Logs do not include prompt, response, or memory content.

## Failure handling

| Failure | Behavior |
| --- | --- |
| Classification fails | Use a safe default (`moderate`, or `private` for private requests) |
| LLM fallback labels are invalid | Keep the deterministic result |
| Token counting fails | Use the character budget |
| Budget resolution fails | Use the character budget |
| Cache lookup fails | Continue to the provider |
| Cache store fails | Keep the generated answer |
| Cache embedding fails | Store or match by exact hash only |
| Usage record fails | Keep the chat response |
| Semantic memory retrieval unavailable | Use keyword retrieval |
| Keyword retrieval fails during hybrid | Use semantic results |
| Memory retrieval fails entirely | Continue without memory context |
| Provider fails | Existing 503/504 behavior is preserved |

## Configuration

```dotenv
# Token-aware context
AI_TOKEN_BUDGET_ENABLED=true
AI_CHARS_PER_TOKEN=4.0
AI_DEFAULT_CONTEXT_WINDOW_TOKENS=8192
AI_GROQ_CONTEXT_WINDOW_TOKENS=131072
AI_OLLAMA_CONTEXT_WINDOW_TOKENS=40960
AI_MODEL_CONTEXT_WINDOWS=
AI_RESPONSE_RESERVE_TOKENS=1024
AI_CONTEXT_SAFETY_TOKENS=256

# Classification
AI_QUERY_CLASSIFIER_ENABLED=true
AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED=false
AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD=0.6

# Routing
AI_ROUTING_POLICY=groq_first
AI_ROUTING_SIMPLE_PROVIDER=ollama
AI_ROUTING_MODERATE_PROVIDER=groq
AI_ROUTING_COMPLEX_PROVIDER=groq
AI_ROUTING_DYNAMIC_PROVIDER=groq
AI_ROUTING_MODEL_OVERRIDES=

# Optional prices (USD per million tokens); zero means unknown
AI_GROQ_INPUT_COST_PER_MILLION=0
AI_GROQ_OUTPUT_COST_PER_MILLION=0
AI_OLLAMA_INPUT_COST_PER_MILLION=0
AI_OLLAMA_OUTPUT_COST_PER_MILLION=0

# Response cache
AI_CACHE_ENABLED=true
AI_CACHE_SIMILARITY_THRESHOLD=0.95
AI_CACHE_TTL_SECONDS=3600
AI_CACHE_MAX_ENTRIES_PER_USER=500
AI_CACHE_PROMPT_VERSION=phase9-v1
AI_CACHE_CONFIG_VERSION=1
AI_CACHE_ALLOWED_COMPLEXITIES=simple,moderate
AI_CACHE_STATELESS_ONLY=true
AI_CACHE_SEMANTIC_ENABLED=true

# Usage tracking
AI_USAGE_TRACKING_ENABLED=true
AI_USAGE_RETENTION_DAYS=90
AI_USAGE_RECENT_LIMIT=50

# Hybrid memory retrieval
AI_MEMORY_HYBRID_ENABLED=true
AI_MEMORY_SEMANTIC_WEIGHT=0.7
AI_MEMORY_KEYWORD_WEIGHT=0.3
AI_MEMORY_CANDIDATE_LIMIT=20
AI_MEMORY_MIN_HYBRID_SCORE=0.05
```

The Phase 9 defaults are safe for existing deployments. The one behavioral
default change is `CONTEXT_PRIORITY_ORDER`, now
`history,task,deadline,preference,memory`. Set it explicitly to preserve an
older ordering.

## Database and migration

Migration `d8e3f5a2b7c1` (`add Phase 9 response cache and AI usage tracking`)
revises `c7d2e8f4a1b6` and is additive:

- `ai_response_cache` with a user foreign key, query hash, optional
  `vector(768)` embedding, answer metadata, hit counters, and TTL columns;
- `ai_usage_records` with a nullable user foreign key and metadata/token columns.

It does not alter, drop, or recreate any existing table. The pgvector extension
is already enabled by the earlier semantic-memory migrations. The downgrade
drops only the two Phase 9 tables and their indexes.

Apply it with:

```powershell
cd backend
..\venv\Scripts\python.exe -m alembic upgrade head
```

The pre-existing `ix_memories_embedding_hnsw` drift reported by
`alembic check` is unrelated to Phase 9 and is not modified.

## Tests

Focused tests use SQLite for unit coverage and mock Groq and Ollama; no real
provider call is made:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase9_token_budget tests.test_phase9_query_classifier tests.test_phase9_routing tests.test_phase9_cache tests.test_phase9_usage tests.test_phase9_usage_api tests.test_phase9_hybrid_retrieval tests.test_phase9_chat_integration -v
```

The broader suite remains the source of truth:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```
