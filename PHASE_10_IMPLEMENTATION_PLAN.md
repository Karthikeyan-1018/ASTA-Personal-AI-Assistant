# ASTA – Phase 10 Implementation Plan

**Prepared:** 2026-11-18  
**Status:** Pre-implementation inspection complete

## Repository findings

- Working tree is **clean**; HEAD is `edcaf09e0061f81137542f5e0296e271946fa72c` (`Complete ASTA development through Phase 9`). There are no pre-existing uncommitted source changes to preserve beyond the committed Phase 9 state.
- `.env`, `Reference.txt`, and the previously recorded protected hashes are unchanged.
- The backend already has a substantial long-term memory system:
  - `memories` table with `pending`/`approved`/`rejected`, per-user ownership, timestamps, and nullable 768-dim pgvector embeddings.
  - Existing migrations already enable `vector` and create an approved-only HNSW index; **no migration is needed**.
  - CRUD, approve/reject, secret rejection, user-scoped queries, keyword/semantic/hybrid retrieval, E5 embeddings, and context injection already exist.
  - `ChatService` already builds one prompt for authenticated turns and reuses it for Groq, Ollama, fallback, streaming, and voice delegation.
- Gaps that matter for this phase:
  - Creation does not deduplicate or supersede existing memories.
  - REST retrieval exposes only `keyword`/`semantic`; hybrid retrieval, score threshold, and fallback exist only internally.
  - pgvector semantic retrieval filters secret-bearing rows after the SQL `LIMIT`, so they can consume result slots.
  - The frontend Memory page still seeds fake demo records and fake pending proposals, shows hardcoded/fake statistics and embedding metadata, swallows errors, and mutates local state for records without a backend ID.
  - There are no frontend unit tests and no dedicated Phase 10 backend suite.
  - No live PostgreSQL/pgvector, browser, or real-provider test is currently available.

## Narrow implementation scope

1. **Controlled memory creation**
   - Add normalization/duplicate detection and explicit supersede-by-key behavior inside the existing `MemoryService`.
   - Preserve the existing pending-approval lifecycle and status filtering.
   - Do not save temporary statements automatically and do not invent facts.

2. **Retrieval improvements**
   - Extend the existing retrieval schema with `hybrid`, `min_score`, and keyword fallback options.
   - Apply duplicate suppression and secret filtering before final result limits.
   - Include the memory ID in retrieval results so UI actions can target a real record.

3. **Context integration validation**
   - Use the existing `UnifiedContextBuilder` and `ChatService` paths unchanged where possible.
   - Add tests for ordering, untrusted-memory handling, budgeting, deleted-memory exclusion, and Groq/Ollama/fallback/streaming/voice consistency.

4. **Memory UI cleanup**
   - Remove all demo seeds and fake statistics.
   - Align frontend categories with backend categories and show real status/timestamps.
   - Make every add/edit/delete/approve/reject/refresh/search action call a real backend endpoint and surface loading, empty, and error states.

5. **Tests and validation**
   - Add focused backend tests for creation, update/supersede, duplicates, deletion, retrieval, relevance, isolation, context, caching, auth/CSRF, provider consistency, and UI API contracts.
   - Run targeted tests, the full backend suite, frontend build, and frontend lint.
   - Record measured latency/count/token observations, clearly distinguishing in-memory automated measurements from live PostgreSQL or live provider tests.
   - Recheck hashes and Git status; write `PHASE_10_INTELLIGENT_MEMORY_REPORT.md`.

## Explicitly out of scope

- No new database table, column, migration, vector database, LLM router, or chat service.
- No database reset, destructive migration, user account deletion/recreation, credential change, `.env` change, or `Reference.txt` change.
- No commit, push, Phase 11 work, wake-word detection, or feature removal.
- No live browser or live microphone result will be claimed unless actually executed.
