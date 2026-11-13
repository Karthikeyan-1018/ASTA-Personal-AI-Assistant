# Web Search Tool

## Overview

Phase 11E adds `web_search`, a built-in `BaseTool` implementation that returns
short search-result metadata to the model. It does not fetch or summarize page
content. The tool is discoverable through the existing registry and
`llm_definitions()` function-calling path, and every execution goes through
`ToolExecutor`. It never creates a parallel execution path.

The implementation is
[`backend/app/tools/implementations/web_search.py`](../backend/app/tools/implementations/web_search.py).
It is registered with the other built-ins in
[`backend/app/tools/definitions/__init__.py`](../backend/app/tools/definitions/__init__.py).

## Provider choice and credentials

Two providers with documented HTTP APIs are supported. Both are optional and
neither is contacted unless the operator enables the tool.

### SearXNG (default)

SearXNG is an open-source, self-hosted metasearch engine. ASTA calls its
documented search API:

```text
GET {WEB_SEARCH_BASE_URL}/search?q={query}&format=json&safesearch=1
```

The instance administrator must enable JSON output under the `search.formats`
setting. Many public instances disable JSON, so a private instance is the
recommended choice. SearXNG **does not require an API key** in this
integration. Configure only an instance you operate or trust.

Official documentation:
<https://docs.searxng.org/dev/search_api.html>

### Tavily (managed)

Tavily is a hosted search API for LLM applications. ASTA calls the documented
endpoint:

```text
POST https://api.tavily.com/search
Authorization: Bearer <WEB_SEARCH_API_KEY>
```

The API key is **required**. It is sent only in the server-side request header
and is never placed in a JSON body, a tool result, metadata, a prompt, or a log
line.

Tavily is configured with `search_depth=basic`, `include_answer=false`,
`include_raw_content=false`, and `include_images=false`. This keeps latency,
credit use, and response size low. Only the `title`, `url`, and `content`
fields of each result are normalized.

Official documentation:
<https://docs.tavily.com/documentation/api-reference/endpoint/search>

There is no automatic fallback between providers. An unavailable or
misconfigured provider produces a safe structured error; it never silently
switches to a different external service.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `WEB_SEARCH_ENABLED` | `false` | Master switch. The tool is hidden from the LLM when false. |
| `WEB_SEARCH_PROVIDER` | `searxng` | `searxng` or `tavily`. Unknown values leave the tool disabled. |
| `WEB_SEARCH_BASE_URL` | empty | SearXNG base URL, for example `https://search.example.org`. A path prefix and a trailing `/search` are supported. |
| `WEB_SEARCH_API_KEY` | empty | Tavily API key. Environment-only; never commit a real value. |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `10` | Per-request timeout, clamped to 1–60 seconds. |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Deployment cap on returned results, clamped to 1–10. |
| `WEB_SEARCH_MAX_RESPONSE_BYTES` | `262144` | Maximum provider response body, clamped to 1 KiB–5 MiB. |

The model may request fewer results than `WEB_SEARCH_MAX_RESULTS`, but never
more. The general tool-calling path must also be enabled (`TOOL_CALLING_ENABLED`)
before the model sees the function definition.

Example SearXNG configuration:

```env
TOOL_CALLING_ENABLED=true
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PROVIDER=searxng
WEB_SEARCH_BASE_URL=https://search.example.org
```

Example Tavily configuration:

```env
TOOL_CALLING_ENABLED=true
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PROVIDER=tavily
WEB_SEARCH_API_KEY=tvly-your-key
```

## Input contract

The Pydantic input model rejects unknown fields, so a model cannot smuggle in
an owner id, a private flag, a credential, or an unregistered option.

| Field | Type | Rules |
| --- | --- | --- |
| `query` | string | Required, 1–500 characters after trimming, no control characters, not blank. |
| `max_results` | integer | Optional, default `5`, minimum `1`, maximum `10`; further capped by `WEB_SEARCH_MAX_RESULTS`. |

Invalid arguments become `ToolValidationError` through the executor. Validation
messages contain field names and reasons, not submitted values.

## Result contract

A successful result has this shape:

```json
{
  "query": "coffee harvest",
  "provider": "searxng",
  "results": [
    {
      "title": "Coffee harvesting guide",
      "url": "https://example.com/coffee",
      "snippet": "A short excerpt from the search result.",
      "source": "example.com"
    }
  ],
  "count": 1,
  "untrusted": true
}
```

- `title` is whitespace-normalized and limited to 200 characters.
- `snippet` is whitespace-normalized and limited to 500 characters.
- `source` is the lower-case result hostname without a leading `www.`.
- `untrusted` is always `true`, and the result metadata also contains
  `untrusted_content: true`. Search snippets are external data, never
  instructions.
- `provider` and the execution-history `provider` label are `searxng` or
  `tavily`; they never contain a key, URL, query, or provider error body.

Results with a missing or unsafe URL are omitted. A provider response with no
usable items but a valid `results` list is still a successful empty result.

## URL and response safety

- Provider result URLs must use `http` or `https`, include a hostname, contain
  no embedded username/password, contain no whitespace or control characters,
  and be at most 2048 characters. Other schemes, including `javascript:` and
  `data:`, are dropped.
- Redirects are not followed. A 3xx provider response becomes a structured
  provider error, so a redirect cannot move the request to an unexpected host.
- `httpx` environment proxy settings are ignored (`trust_env=False`).
- The response is streamed and read under `WEB_SEARCH_MAX_RESPONSE_BYTES`. An
  oversized body becomes a `ToolSizeLimitError` instead of being parsed or
  buffered without bound.
- Page content is never fetched. The tool returns only the provider's short
  result metadata.
- The SearXNG base URL is operator configuration, not model input, so internal
  or loopback SearXNG deployments are allowed. Only configure an instance you
  trust; arbitrary model-supplied URLs are never used for provider requests.

## Error handling

| Condition | Tool error | Executor category |
| --- | --- | --- |
| Blank/oversized/control-character query or bad `max_results` | `ToolValidationError` | `validation` |
| Unknown provider, missing Tavily key, invalid SearXNG base URL | `ToolConfigurationError` | `configuration` |
| Request timeout | `ToolProviderError` | `provider` |
| DNS/connection/TLS failure | `ToolProviderError` | `provider` |
| HTTP 429 | `ToolProviderError` with a rate-limit message | `provider` |
| HTTP 401/403 | `ToolProviderError` with a rejection message | `provider` |
| HTTP 3xx | `ToolProviderError`; redirect not followed | `provider` |
| HTTP 5xx | `ToolProviderError`; provider unavailable | `provider` |
| Other non-200 status | `ToolProviderError` | `provider` |
| Invalid JSON or unexpected payload shape | `ToolProviderError` | `provider` |
| Response exceeds the byte limit | `ToolSizeLimitError` | `size_limit` |

Provider response bodies and headers are never included in errors. Logging
records only the provider name and HTTP status. `Retry-After` is not surfaced
to the model or stored.

## Permission and private-mode behavior

`web_search` is:

- `ToolCategory.READ_ONLY`;
- `allowed_in_private = False`;
- `requires_confirmation = False`;
- `provider_label = "web_search"`.

Private requests are denied by the central permission policy before arguments
are validated or any network call is made. Because it is read-only, it does not
require confirmation. The tool is hidden from `llm_definitions(private=True)`
and from the model when disabled.

## Registry and function calling

`WebSearchTool()` is part of `BUILTIN_TOOLS`, so `ToolRegistry` registers it
once through the standard lazy loader. When enabled and allowed,
`llm_definitions()` publishes a standard function definition named
`web_search` with `query` and `max_results` properties. Groq and Ollama receive
the same provider-independent schema used by every other tool.

The tool does not bypass `ToolExecutor`: enablement, privacy, argument
validation, timeout, result redaction/size bounds, and metadata-only history
all run in the central executor.

## Tests

Focused tests use `httpx.MockTransport` and never contact a real provider:

```powershell
cd backend
..\venv\Scripts\python.exe -m unittest tests.test_phase11e_web_search -v
```

Coverage includes validation, enablement, SearXNG and Tavily request shapes,
bearer authentication, normalized output, configurable result caps, timeouts,
connection failures, 400/401/403/429/5xx responses, redirects, invalid and
oversized payloads, unsafe URLs, secret non-disclosure in results/errors/logs,
registry and LLM discovery, private-mode denial, disabled behavior, and
executor integration.

## Limitations

- Only two providers are implemented, and there is no automatic failover.
- SearXNG requires an instance with JSON output enabled; public instances often
  disable it.
- Provider result quality, ranking, freshness, and availability are outside
  ASTA's control.
- Search is limited to a query and a result count. Language, country, time
  range, domain filters, pagination, and safe-search level are not exposed to
  the model; SearXNG safe search is fixed at `1`, and Tavily uses its defaults.
- Snippets are not verified, fetched, deduplicated, or summarized by ASTA.
- Tavily usage consumes plan credits; ASTA does not track or expose provider
  credit usage.
- The byte limit protects response parsing, not the total time spent across
  multiple tool calls; the executor's `TOOL_EXECUTION_TIMEOUT_SECONDS` remains
  the outer wall-clock bound.
- The configured SearXNG base URL is trusted operator input and is not resolved
  or restricted to public addresses. Do not point it at an untrusted service.
- No provider response is cached, so repeated identical searches make repeated
  external requests.
