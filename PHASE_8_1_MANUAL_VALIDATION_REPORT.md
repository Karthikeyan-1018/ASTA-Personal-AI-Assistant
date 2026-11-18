# ASTA – Phase 8.1 Manual Browser UI and Voice Validation Report

**Date:** 2026-11-18  
**Project root:** `C:\Asta`  
**Phase:** Phase 8.1 – Manual Browser UI and Voice Validation  
**Final verdict:** **PARTIAL PASS**

---

## 1. Browser UI results

**Result: PASS (authenticated Personality Settings UI executed successfully)**

### Environment and access

- Backend already running on port `8000`; `GET http://127.0.0.1:8000/health` returned `200` with `{"application":"ASTA","status":"Healthy"}`.
- Frontend already running on Vite port `5173`.
- The app was opened in the shared browser tab and authenticated as the **existing owner account** `Karthikeyan` (owner id `50`). No account was created, reset, or recreated.
- Navigated to **Settings → Chat Preferences → Personality & Motivation**.

### Controls observed in the live UI

- `Behaviour mode` select (`#asta-personality-mode`) with exactly four options: `normal`, `motivational`, `focus`, `casual`.
- `Proactive motivation` checkbox, initially checked.
- `Motivation intensity` range (`#asta-motivation-intensity`) with values `1`–`5`; label showed `Balanced` for `3`.
- Buttons: `Restore defaults`, `Save personality`, `Discard changes`.
- A `Custom` badge appears when backend overrides exist and disappears after restoring defaults.
- `Save personality` and `Discard changes` start disabled when there are no unsaved edits.

### All four modes tested through the UI

For each mode, the value was changed in the real select control, `Save personality` became enabled, the button was clicked, the success message `Personality settings saved.` appeared, and `GET /api/settings/personality` confirmed the backend value:

| Mode selected in UI | Backend `mode` after save | Proactive | Intensity | `has_overrides` |
|---|---:|---:|---:|---:|
| `motivational` | `motivational` | `true` | `3` | `true` |
| `focus` | `focus` | `true` | `3` | `true` |
| `casual` | `casual` | `true` | `3` | `true` |
| `normal` | `normal` | `true` | `3` | `true` |

### Proactive motivation and intensity

- Changed mode to `casual`, unchecked proactive motivation, and moved intensity to `5`.
- UI label changed to `Motivation intensity: Strong but respectful`.
- `Save personality` became enabled and was clicked.
- `Personality settings saved.` was displayed.
- Backend confirmed: `mode=casual`, `proactive_motivation_enabled=false`, `motivation_intensity=5`, `has_overrides=true`.

### Persistence after browser refresh

- The browser tab was reloaded, then Settings → Chat Preferences was reopened.
- UI showed: mode `casual`, proactive motivation unchecked, intensity `5`, `Custom` badge present, `Save personality` disabled.
- `GET /api/auth/me` after refresh returned `200`, confirming the authenticated session survived refresh.
- This verifies saved settings persisted in the backend, not just in local component state.

### Discard changes

- Changed the draft to mode `focus` and intensity `2`; both `Save personality` and `Discard changes` became enabled.
- Clicked `Discard changes`.
- UI immediately reverted to the saved values (`casual`, unchecked, intensity `5`), and both buttons became disabled again.
- Backend remained unchanged (`casual`, `false`, `5`), confirming no accidental write.

### Restore defaults

- Clicked `Restore defaults`.
- Success message `Personality settings restored to defaults.` appeared.
- UI showed mode `normal`, proactive motivation checked, intensity `3` (`Balanced`), no `Custom` badge, and `Save personality` disabled.
- Backend confirmed `mode=normal`, `proactive_motivation_enabled=true`, `motivation_intensity=3`, `has_overrides=false`.

### Screenshots

Screenshots were captured from the live browser session and saved to:

- `C:\Asta\PHASE_8_1_SCREENSHOTS\01_personality_baseline.png`
- `C:\Asta\PHASE_8_1_SCREENSHOTS\02_personality_unsaved_changes.png`
- `C:\Asta\PHASE_8_1_SCREENSHOTS\03_personality_after_refresh.png`
- `C:\Asta\PHASE_8_1_SCREENSHOTS\04_personality_defaults_restored.png`

The assistant could not visually render screenshot images in this session; UI observations above were taken directly from the live DOM, accessibility tree where available, and authenticated API responses.

---

## 2. Authentication results

**Result: PASS after correcting the browser origin**

- Initial browser state was unauthenticated: `GET /api/auth/me` returned `401 {"detail":"Authentication required."}`.
- The first sign-in attempts were made from `http://127.0.0.1:5173`. Network inspection showed `POST http://localhost:8000/api/auth/login` returned **200** with a valid owner response for `Karthikeyan`, but subsequent `/api/auth/me` calls still returned **401**.
- Cause identified from response headers: the backend sets host-only cookies for `localhost` with `SameSite=Lax`; Chromium treats `http://127.0.0.1:5173` and `http://localhost:8000` as different sites, so the session cookie was not sent back on cross-site XHR. CORS itself was correctly configured (`Access-Control-Allow-Credentials: true`, `Access-Control-Allow-Origin: http://127.0.0.1:5173`).
- The tab was navigated to the matching origin `http://localhost:5173/`. The existing account was used to sign in there; no setup flow, account recreation, credential modification, or auth bypass was used.
- Authenticated state confirmed:
  - `GET /api/auth/me` → `200`
  - Body: `{"owner":{"id":50,"username":"Karthikeyan",...},"session_expires_at":"2026-11-25T15:05:18.078055Z"}`
- No `.env`, credentials, or protected files were modified.

---

## 3. Personality settings persistence results

**Result: PASS**

- Save, refresh, and re-open of the panel preserved all three writable fields: mode, proactive motivation, and motivation intensity.
- Backend `GET /api/settings/personality` and UI values agreed before and after refresh.
- `has_overrides` moved from `false` to `true` on save and back to `false` after `Restore defaults`.
- Unsaved edits were discardable without changing the backend.
- Defaults were restorable from the UI with a success message and backend confirmation.

---

## 4. Live microphone results

**Result: BLOCKED / NOT EXECUTED**

- Voice status endpoint returned:
  - `enabled: true`
  - `available: true`
  - components: `vad=true`, `stt=true`, `tts=true`, `chat=true`
  - `active_session_id: null`
  - `state: idle`
- Microphone permission for the exact ASTA browser origin was queried with `navigator.permissions.query({name:"microphone"})`:
  - Origin: `http://localhost:5173`
  - State: **`denied`**
- One normal UI attempt was made by clicking `Start voice conversation`.
- The app displayed the exact error:
  > `Microphone permission was denied. Allow access in your browser settings and try again.`
- Backend voice status after the failed attempt remained `active_session_id: null`, `state: idle`; no session leaked or needed cleanup.
- Permission was **not** bypassed, reset, or granted by any alternate mechanism.

Screenshot:

- `C:\Asta\PHASE_8_1_SCREENSHOTS\05_microphone_permission_denied.png`

---

## 5. Speech recognition results

**Result: NOT EXECUTED**

No spoken input could be captured because microphone permission was denied. No speech was detected, no audio segment was submitted, and no transcript was produced. Live STT is therefore unverified in this phase.

The backend STT component was reported available by `/api/voice-conversations/status`, and STT tests passed in the regression suite, but that is not a substitute for a live microphone test.

---

## 6. Voice response playback results

**Result: NOT EXECUTED**

No voice turn completed, so no TTS audio was generated or played in a live conversation. The `Speak` buttons on existing text messages were not used as a substitute for the manual voice-conversation playback path. Live TTS playback remains unverified in this phase.

---

## 7. Multi-turn conversation results

**Result: NOT EXECUTED**

Because the first microphone acquisition failed, there were zero voice turns. The following were therefore not performed:

- two consecutive live voice turns;
- changing personality mode and testing another voice response;
- stopping an active voice conversation.

No active session was created, so clean session termination was observed only as the expected idle/no-session state after the failed start, not after a completed multi-turn conversation.

Wake-word detection was not enabled, and the half-duplex manual voice architecture was left unchanged.

---

## 8. Regression test results

| Check | Command / evidence | Result |
|---|---|---|
| Full backend suite | `..\venv\Scripts\python.exe -m unittest discover -s tests -q` | **OK** — `Ran 1954 tests in 709.009s`, `OK (skipped=6)` |
| Targeted personality / voice / auth tests | `..\venv\Scripts\python.exe -m unittest tests.test_phase7_personality tests.test_phase18_stage4_voice_conversation tests.test_phase8_auth -q` | **OK** — `Ran 73 tests`, `OK` |
| Frontend build | `npm run build` | **PASS** — built successfully in 782 ms |
| Frontend lint | `npm run lint` | **PASS with pre-existing warning only** — `src/pages/Chat.tsx:409:5 react(set-state-in-effect)`; no new errors |
| Cross-user personality leakage | Phase 7 personality tests in full and targeted suites; no second account created because account recreation is forbidden | **PASS at automated-test level**; no live second-user check possible |
| Audio / transcript persistence | `voice_conversation_manager.py` documents in-memory-only session state; Phase 18 Stage 4 tests passed; no live audio was captured | **PASS at code/test level**; no live audio path exercised |
| Auth / CSRF protections | Phase 8 auth tests passed; no auth source files modified; protected hashes unchanged | **PASS** |

### Protected file hashes after validation

- `.env` — `E9B23B1AE5146EE491356062ECEEB2A4DFFA46440B047442D4C5B4B130A010D4`
- `Reference.txt` — `EB2990F7641EA678B6F2BC31BC8259188F517BC12352E9F319A2A3D864BDB34E`
- `frontend/src/stores/memory.store.ts` — `3FDACBA1ACD7DE718D032C3E5C508A728BE75F271734894F84394EFDD03DFDD1`
- `frontend/src/pages/Memory.tsx` — `69865BF80F232A0D6512717037271AE6C61F1A276C700CE2EB5304089D558984`

All match the Phase 8 baseline.

### Repository safety

- No source code was modified during Phase 8.1.
- No commit or push was performed.
- No database was reset and no owner account was deleted or recreated.
- No credentials, API keys, `.env`, or protected files were modified.
- Phase 9 was not started.

---

## 9. Blockers and exact errors

### Blocked: authenticated access on the initial origin

- Browser origin: `http://127.0.0.1:5173`
- `POST /api/auth/login` returned `200` with a valid owner response.
- Subsequent `GET /api/auth/me` returned:
  - HTTP `401`
  - Body: `{"detail":"Authentication required."}`
- Cause: host-only `localhost` cookies with `SameSite=Lax` were not sent from the cross-site `127.0.0.1` frontend origin.
- Resolution: signed in from `http://localhost:5173` using the existing account. No bypass or account change was used.

### Blocked: live microphone permission

- Browser origin: `http://localhost:5173`
- `navigator.permissions.query({name:"microphone"})` → `state: "denied"`
- Application error after clicking `Start voice conversation`:
  > `Microphone permission was denied. Allow access in your browser settings and try again.`
- No voice session was created; status remained `idle` with `active_session_id: null`.

### Not failures, only pre-existing warnings

- Frontend build warns about a >1600 kB chunk and an ineffective dynamic import of `src/stores/reminder.store.ts`; the build succeeds.
- Lint reports the already-known `Chat.tsx:409:5` `react(set-state-in-effect)` warning; no new lint errors were introduced.

---

## 10. Final verdict

**PARTIAL PASS**

The authenticated browser Personality Settings test **fully passed**: all four modes were exercised through the UI, settings saved and persisted after refresh, unsaved changes were discarded correctly, and defaults were restored with matching backend state.

The live microphone voice test did **not** run because microphone permission for `http://localhost:5173` is denied. Speech recognition, live voice response playback, multi-turn voice conversation, and voice-personality changes are therefore unverified in this phase. Regression checks passed, but they cannot substitute for the missing live voice execution.

Because the requirement for **PASS** is that both browser UI and live voice tests actually succeed, this phase cannot be marked PASS. No Phase 9 work was started; explicit approval is required to continue.
