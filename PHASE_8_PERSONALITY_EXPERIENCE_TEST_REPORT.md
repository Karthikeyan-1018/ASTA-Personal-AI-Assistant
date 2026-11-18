# ASTA – Phase 8 Personality Experience Test Report

**Date:** 2026-11-18  
**Project root:** `C:\Asta`  
**Phase:** Phase 8 – Personality Experience Testing and Validation  
**Final verdict:** **PARTIAL PASS**

---

## 1. Executive summary

Phase 7’s Personality Engine was validated against the actual backend and frontend implementation. Core behavior is sound: all four modes render distinct persona instructions, the shared prompt reaches both Groq and Ollama, proactive motivation decisions are conservative and respect opt-out/Focus/disabled settings, settings are user-scoped, and private conversations stay on Ollama.

One real defect was found: a cached answer generated under one personality mode could be returned after the user switched to another mode. The root cause was that chat cache lookups and stores did not include a personality-derived cache version. A small in-scope fix was applied, a regression test was added, and targeted reruns passed.

Two important real-world checks could not be executed:

- **Authenticated browser UI testing** was blocked by the absence of a login session; the app showed the sign-in page and `/api/auth/me` returned `401`.
- **Live microphone voice testing** was therefore also not executed; authentication is a prerequisite, and no credentials were supplied or bypassed.

Because the core implementation passed and the discovered defect was fixed, but those real-world paths remain unverified, the verdict is **PARTIAL PASS** rather than **PASS** or **FAIL**.

---

## 2. Test environment

| Item | Value |
|---|---|
| OS | Windows |
| Backend | `C:\Asta\backend`, Python 3.12 venv at `C:\Asta\venv` |
| Frontend | `C:\Asta\frontend`, Vite dev server on `http://127.0.0.1:5173` |
| Backend API health | `http://127.0.0.1:8000/health` reachable |
| Browser | OpenCode in-app browser |
| Provider health | Groq `True`; Ollama `True` |
| Groq model | `openai/gpt-oss-20b` |
| Ollama model | `qwen3:8b` |
| Ollama endpoint | `http://127.0.0.1:11434` |
| Cache config observed | `AI_CACHE_ENABLED=True`, `AI_CACHE_STATELESS_ONLY=True`, `AI_CACHE_SEMANTIC_ENABLED=True`, `TOOL_CALLING_ENABLED=False` |
| Test database for API harnesses | In-memory SQLite; no persistent user database was modified |
| Baseline backend suite | 1,953 tests, 6 skipped, `OK` |
| Git HEAD at start and end | `2aaac5f53cd42fd0607b3013ca6a96e4b1436a89` |
| Staged changes | 0 |
| Commit/push performed | No |

No endpoint names, configuration names, or behavior were assumed. They were read from the implementation before testing.

---

## 3. Personality UI results

### 3.1 Browser result — BLOCKED

The frontend dev server was started and opened in the in-app browser. The application reached the sign-in screen:

- Text observed: **“Sign in to your personal assistant”**
- `/api/auth/me` returned **`401 Unauthorized`**
- No authenticated session was available.
- No credentials were provided, and authentication was not bypassed.

Therefore, the following requested browser-level actions were **NOT EXECUTED**:

- Clicking the mode selector in the real UI.
- Toggling proactive motivation in the real UI.
- Moving the intensity slider in the real UI.
- Clicking Save/Discard/Restore defaults in the real UI.
- Refreshing the authenticated page and visually confirming persisted values.

### 3.2 Source-level verification

The Settings page renders `PersonalityPanel` under the Chat tab (`Settings.tsx`, `activeTab === 'chat'`). The panel and API/store were inspected directly:

| Control / behavior | Implementation evidence | Result |
|---|---|---|
| Personality mode selector | `<select id="asta-personality-mode">` with all four modes from `PERSONALITY_MODES` | Present and wired |
| Proactive motivation toggle | Checkbox bound to `draft.proactiveMotivationEnabled` | Present and wired |
| Motivation intensity slider | Range input `min=1`, `max=5`, `step=1` with labels 1–5 | Present and wired |
| Save settings | Disabled unless local draft differs from backend state; sends only changed fields via PATCH | Present and wired |
| Discard changes | Clears local draft overrides and feedback; does not claim backend save | Present and wired |
| Restore defaults | Calls `POST /api/settings/personality/reset` | Present and wired |
| Backend-backed only | API maps snake_case backend response fields; store sets success only after resolved API call | Confirmed |
| Decorative frontend-only controls in personality panel | None found | Confirmed |

### 3.3 API-level settings validation

A temporary harness exercised the real FastAPI endpoints with an in-memory SQLite database and a real authenticated session created locally in that isolated database.

Observed results:

- Initial: `mode=normal`, proactive motivation `true`, intensity `3`, `has_overrides=false`.
- After PATCH: `mode=motivational`, proactive motivation `false`, intensity `5`.
- After fresh GET: values remained `motivational`, `false`, `5`, `has_overrides=true`.
- After reset: values restored to `normal`, `true`, `3`, `has_overrides=false`.
- Two users saved different modes (`focus` and `normal`) without leaking preferences.

This validates persistence and user scoping at the API layer, but it does **not** replace the blocked authenticated browser test.

---

## 4. Results for all four personality modes

### 4.1 Shared prompt contract

A provider double captured the exact prompt passed by the real chat service/router for each mode. All four modes returned HTTP `200`, included the ASTA persona, included the correct mode marker, and included the current user message.

| Mode | Suggested prompt | Captured result |
|---|---|---|
| Normal | “Explain how an API works in simple terms.” | `ANSWER:normal`; persona, `Normal mode:` marker, and message present |
| Motivational | “I'm struggling to understand machine learning. I feel like I'm not making progress.” | `ANSWER:motivational`; persona, `Motivational mode:` marker, and message present |
| Focus | “Explain the difference between supervised and unsupervised learning in a comparison table.” | `ANSWER:focus`; persona, `Focus mode:` marker, and message present |
| Casual | “Hey ASTA, explain Git to me like we're discussing it casually.” | `ANSWER:casual`; persona, `Casual mode:` marker, and message present |

### 4.2 Live provider observations

The same prompt string for each mode was sent to both real providers. Full responses were retained in `C:\Users\karth\AppData\Local\Temp\opencode\phase8_live_providers.json`.

| Mode | Groq observation | Ollama observation | Factual quality / tone |
|---|---|---|---|
| Normal | Calm waiter/client-server analogy with ordered explanation | Calm waiter analogy with request/response framing | Clear, balanced, no unnecessary motivation or jokes |
| Motivational | Acknowledged being stuck, validated effort, then gave a concrete next step | Encouraging and empathetic, then broke the topic down | Positive and realistic; no guilt, shame, or unrealistic promises |
| Focus | Direct comparison table with labeled/unlabeled data, goals, and algorithms | Direct comparison table with categories, objectives, and algorithms | Structured and technically accurate; no unsolicited cheerleading |
| Casual | Relaxed Git explanation using snapshots/commits | Conversational Git explanation using snapshots and project history | Natural and understandable; technically accurate |

No mode changed the underlying factual explanation into unsupported claims. Model wording was nondeterministic, as expected, but the selected persona was reflected consistently.

---

## 5. Proactive motivation results

The shared detector and prompt builder were exercised directly for all seven requested scenarios, plus repeated-encouragement suppression.

| Scenario | Decision kind | Encouragement? | Observed prompt guidance |
|---|---|---|---|
| Ordinary technical question | `none` | No | “Do not add unsolicited motivational language in this turn.” |
| Explicit request for encouragement | `explicit` | Yes | User explicitly asked; give one brief, practical response with a concrete next step |
| Sustained frustration | `struggle` | Yes | Acknowledge effort without assuming emotion; offer one concrete next step |
| Meaningful progress | `progress` | Yes | Celebrate briefly and specifically without inventing achievements; offer practical next step |
| Explicit opt-out | `opt_out` | No | Do not motivate, cheer, or add a pep talk in this turn |
| Proactive motivation disabled | `suppressed_disabled` | No | No unsolicited encouragement |
| Focus mode | `suppressed_focus` | No | Answer technical request directly and practically |
| Repeated recent encouragement | `suppressed_repeated` | No | Do not repeat encouragement; continue with practical help |

### Intensity levels

| Level | Prompt label observed |
|---:|---|
| 1 | Subtle |
| 3 | Balanced |
| 5 | Strong but respectful |

Intensity changes the wording strength of the single guidance line without forcing motivation into neutral/technical replies. Repetition is additionally suppressed by the `suppressed_repeated` decision.

**Limitation:** intensity was verified at the prompt-contract level, not through separate live Groq/Ollama generations at levels 1 and 5, because authenticated UI access was unavailable.

---

## 6. Groq testing results

- Health check: `True`
- Actual model used for direct calls: `openai/gpt-oss-20b`
- All four suggested prompts completed successfully with non-empty answers.
- The exact same persona prompt string was supplied for Groq and Ollama per mode.
- Mode markers were present in the trusted system instructions for all four modes.

Measured direct-call latency:

| Mode | Duration |
|---|---:|
| Normal | 615.1 ms |
| Motivational | 716.1 ms |
| Focus | 1,213.2 ms |
| Casual | 1,634.5 ms |

### Fallback

A simulated Groq timeout was injected while the real Ollama provider remained live.

- Result provider: `ollama`
- Result model: `qwen3:8b`
- `fallback_used=true`
- Duration: 37,018.09 ms
- Persona prompt preserved byte-for-byte
- `Focus mode:` marker preserved
- Response began with a clear supervised/unsupervised comparison table

### Provider errors

When both providers were made to fail, the router raised `LLMUnavailableError` with the message “Both Groq and Ollama providers failed to generate a response.” No fabricated result object was returned.

---

## 7. Ollama testing results

- Health check: `True`
- Endpoint: `http://127.0.0.1:11434`
- Actual model used: `qwen3:8b`
- All four suggested prompts completed successfully with non-empty answers.

Measured direct-call latency:

| Mode | Duration |
|---|---:|
| Normal | 52,444.1 ms |
| Motivational | 23,068.6 ms |
| Focus | 40,394.9 ms |
| Casual | 43,531.9 ms |

Local generation was substantially slower than Groq in this environment; no unexpected response failures occurred.

### Private Ollama-only

A private request was routed through the real router with the real Ollama provider.

- Result provider: `ollama`
- Result model: `qwen3:8b`
- `fallback_used=false`
- Duration: 10,969.67 ms
- Groq was not called.
- Exact persona prompt preserved
- `Casual mode:` marker preserved
- Response identified itself as ASTA naturally

### Provider errors

With both providers forced to fail, no fabricated response was produced; `LLMUnavailableError` was raised.

---

## 8. Voice testing results

**Live voice flow: NOT EXECUTED.**

Exact blocker:

- Authenticated browser access was unavailable (`/api/auth/me` returned `401`; sign-in page displayed).
- No credentials were supplied.
- Authentication was not bypassed.
- Because authentication was unavailable, live microphone permission was not reached or assessed.

Implementation inspection showed that `VoiceConversationManager.submit_turn()` calls the existing `chat_service.get_response()`, so voice turns resolve the same user-specific persona as text chat. Existing backend voice, STT, TTS, and VAD tests passed, and no wake-word detection or manual microphone workflow changes were introduced.

Those backend results support the call path, but they do **not** constitute a live microphone/TTS/STT/VAD end-to-end pass. Voice personality is therefore reported as **NOT EXECUTED**, not PASS.

---

## 9. Regression test results

### Backend

| Run | Result |
|---|---|
| Baseline full suite before Phase 8 code changes | 1,953 tests, 6 skipped, `OK`, exit `0` |
| Phase 7 + cache tests after fix | 28 tests, `OK` |
| Broader relevant set after fix | 189 tests, `OK` |
| Full suite after fix | 1,954 tests, 6 skipped, `OK`, exit `0` (one added regression test versus the 1,953-test baseline) |

The broader post-fix set included personality, cache, chat integration, routing, context, chat memory, voice conversation, VAD, TTS, authentication/CSRF, and health/API tests. Logged expected error-path tracebacks were present, but no test failed.

### Frontend

| Check | Result |
|---|---|
| `npm run build` | Passed, exit `0`; existing chunk-size/dynamic-import warnings remain |
| `npm run lint` | Passed, exit `0`; only the pre-existing warning at `src/pages/Chat.tsx:409:5` |

No new frontend warning or error was introduced.

---

## 10. Bugs discovered

### Cross-mode cached answer leakage

**Severity:** Medium user-visible correctness issue  
**Status:** Fixed and regression-tested

**Reproduction before fix**

1. User with Normal mode asked: “What is the capital of France?”
2. Cached answer returned/generated as `ANSWER:normal`.
3. User switched to Focus mode without restarting the backend.
4. Same user asked the same question again.
5. API returned `ANSWER:normal`; provider call count stayed at `1`.

**Root cause**

`ChatService` called `response_cache_service.lookup()` and `store()` without overriding `config_version`. The cache therefore used the static `AI_CACHE_CONFIG_VERSION` for every persona. `AIResponseCache` is keyed by user, query hash, prompt version, and config version; personality mode, proactive setting, and intensity were absent from that key.

**Impact**

A response generated with one personality could be served after the user changed mode or motivation settings. The user would see the wrong tone and stale motivation behavior, and no provider call would occur to regenerate the answer. User isolation still held; the leak was within one user across persona changes.

---

## 11. Fixes made, if any

The fix was limited, in scope, and did not change the database schema or redesign the engine.

### Files changed for the fix

- `backend/app/personality/engine.py`
  - Added `personality_fingerprint(config)`, a stable SHA-256 fingerprint of the canonical validated `PersonalityConfig`.
- `backend/app/personality/__init__.py`
  - Exported `personality_fingerprint`.
- `backend/app/services/chat_service.py`
  - Resolved the persona config alongside its instructions.
  - Combined the existing base cache version with the persona fingerprint into a bounded `config_version`.
  - Passed that version to both cache lookup and store.
- `backend/tests/test_phase7_personality.py`
  - Added `PersonalityApiTests.test_02_cache_is_isolated_by_personality_mode`.

### Regression test coverage

The new test verifies:

- Normal answer is generated and cached.
- Switching to Focus mode produces a new provider call and `focus answer`.
- A third identical Focus-mode request hits the Focus cache.
- Provider prompts contain the correct mode markers.
- Two distinct `AIResponseCache.config_version` rows are written.

### Post-fix validation

- Focused harness: `stale_cross_mode=false`; first answer `ANSWER:normal`, second answer `ANSWER:focus`; provider calls `1 → 2`.
- Focused unit/API tests: 28 passed.
- Broader relevant tests: 189 passed.
- Full backend suite: 1,954 tests, 6 skipped, `OK`, exit `0` (one added regression test versus the 1,953-test baseline)

---

## 12. Tests not executed and exact blockers

| Test | Status | Exact blocker |
|---|---|---|
| Browser interaction with mode selector, toggle, slider, Save, Discard, Restore defaults | NOT EXECUTED | No authenticated browser session; sign-in page shown; `/api/auth/me` returned `401`; no credentials supplied; bypass forbidden |
| Refresh-based visual confirmation in authenticated UI | NOT EXECUTED | Same authentication blocker |
| All four suggested prompts typed through the actual chat UI | NOT EXECUTED | Same authentication blocker; prompting was instead validated through real API/router paths and live provider calls |
| Live microphone voice personality test | NOT EXECUTED | Authentication unavailable before microphone; mic permission not reached; no bypass attempted |
| Live Groq/Ollama generation at intensity levels 1 and 5 | NOT EXECUTED as live output | Intensity verified at prompt-contract level only; authenticated UI unavailable to change the setting |
| Real Groq outage | NOT EXECUTED | Deliberately simulated provider failure instead of forcing an external service outage; real Ollama fallback path was exercised |

No failed test was hidden, suppressed, or reclassified as a pass.

---

## 13. Security and data-integrity observations

- Authentication derives user identity from the server-side HttpOnly session cookie, not from request body, query, or headers; verified in `app/api/dependencies.py`.
- Personality mutation routes require CSRF; tests confirmed `401` for anonymous GET and `403` for missing CSRF token on PATCH.
- Personality preferences are stored through the existing user-scoped preference service using fixed keys; no schema or migration was added.
- Concurrent persona resolution for two isolated users remained independent (`focus/false/1` vs `casual/true/5`).
- Cache remains user-scoped; private requests are excluded from caching and routed to Ollama only.
- Secret-like prompts are excluded from cache eligibility by the existing `contains_secret` check.
- No database was reset, deleted, recreated, or modified outside isolated in-memory test databases.
- No user account was created in the persistent application database.
- `.env`, credentials, and protected files were not modified. Hashes remained unchanged:
  - `.env` — `E9B23B1AE5146EE491356062ECEEB2A4DFFA46440B047442D4C5B4B130A010D4`
  - `Reference.txt` — `EB2990F7641EA678B6F2BC31BC8259188F517BC12352E9F319A2A3D864BDB34E`
  - `frontend/src/stores/memory.store.ts` — `3FDACBA1ACD7DE718D032C3E5C508A728BE75F271734894F84394EFDD03DFDD1`
  - `frontend/src/pages/Memory.tsx` — `69865BF80F232A0D6512717037271AE6C61F1A276C700CE2EB5304089D558984`
- No API key value was printed or written to a report.
- No second LLM router was introduced.
- No commit, push, staging, reset, clean, stash, or discard operation was performed.
- Unrelated features were not modified by the Phase 8 fix.

---

## 14. Final verdict

**PARTIAL PASS**

Rationale:

- Core personality engine, settings persistence, four-mode prompt delivery, proactive motivation decisions, Groq/Ollama consistency, fallback, private Ollama-only routing, and user isolation were validated.
- The cross-mode cache defect was identified, fixed with a narrow in-scope change, and covered by a regression test.
- Regression suites passed with no new failures.
- Authenticated browser UI control testing and live microphone voice testing were not executed because no authenticated session was available. Those are important real-world tests, so a full **PASS** cannot be claimed.
- No important functionality was found broken after the fix, so **FAIL** is not appropriate.

Phase 9 was not started.
