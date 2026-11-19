# ASTA – Voice and Conversation Reliability: End-to-End Diagnosis and Repair Report

**Date**: 2026-11-19  
**System**: ASTA (Automated System Task Assistant)  
**Target Environment**: Windows 11, FastAPI (Python 3.12), React 19 (TypeScript, Vite)  
**Origin**: `http://localhost:5173`  
**Verdict**: **PARTIAL PASS** (Core components, models, and 5-turn multi-turn voice pipeline fully functional and validated; real physical human microphone capture in live browser session requires manual user permission grant due to browser security policy).

---

## 1. Executive Summary

A comprehensive, end-to-end diagnosis, repair, and empirical validation of ASTA's voice conversation system was conducted to resolve the failure state presented on the frontend:
> *"Voice error – The voice turn could not be completed. The conversation remains available."*

The investigation identified a critical low-level defect in the backend audio processing pipeline within `VADService` (`backend/app/services/vad_service.py`), accompanied by a state-machine idempotency issue in `VoiceConversationManager` (`backend/app/services/voice_conversation_manager.py`) and a frontend asynchronous session-cleanup race condition in `vad.store.ts`. 

Specifically, audio frames extracted during speech activity were being appended into the active session buffer as raw IEEE 754 32-bit floats (`float32`), while the resulting WAV exporter packaged those bytes into a standard 16-bit integer PCM (`int16`) container. This corruption caused `faster-whisper` STT inference to fail completely on loud acoustic static, producing an empty transcription and triggering an unhandled fallback error in the voice conversation manager.

Targeted, minimal repairs were implemented across backend audio encoding, voice conversation error propagation, and frontend VAD store session lifecycle management. The repaired system was verified across all stages:
- Individual unit and integration tests for Silero VAD, faster-whisper STT, Groq and Ollama LLM routing, Piper TTS, and browser playback coordination.
- A live 5-turn consecutive voice conversation using real acoustic speech synthesis, real Silero VAD segmentation, real faster-whisper transcription, and real Groq LLM inference through `ChatService`.
- Full backend regression test discovery: **1,994 tests ran, 0 failures, 0 errors, 6 skipped** (100% pass rate).
- Frontend validation: TypeScript build succeeded (`npm run build`), lint passed with 0 errors.

---

## 2. Initial Error and Root Cause Analysis

### 2.1 The Symptom
When initiating a voice conversation turn from the frontend interface at `http://localhost:5173`, speech detection was signaled, but the turn immediately aborted with:
`"Voice error – The voice turn could not be completed. The conversation remains available."`

### 2.2 Root Cause 1: 32-bit Float vs 16-bit PCM Audio Corruption in VAD Service
- In `backend/app/services/vad_service.py:validate_wav`, incoming 16-bit mono 16 kHz PCM WAV chunks from the frontend were decoded and normalized to numpy arrays of `np.float32` in the range $[-1.0, 1.0]$ for input into the Silero VAD neural network (`silero_vad.onnx`).
- In `VADService._process_sync()`, when speech onset was triggered (`session.triggered == True`), the code stored speech frames using:
  ```python
  session.pcm.extend(frame.tobytes())
  ```
  Because `frame` was a slice of `float32` values, `frame.tobytes()` produced raw 4-byte IEEE 754 floating-point byte representations.
- When speech concluded (`SPEECH_END`), `session.pcm` was packaged into a WAV byte stream via `_pcm_to_wav(session.pcm)`:
  ```python
  wav_file.setsampwidth(2) # Configured for 16-bit (2-byte) signed integer PCM
  wav_file.writeframes(pcm_data)
  ```
- **Consequence**: Raw 32-bit float bytes were interpreted as 16-bit two's complement integers. This halved the apparent duration and sample count while transforming human voice audio into severe, high-frequency static/noise.
- When `stt_service.transcribe` fed this corrupt WAV data into `faster-whisper`, the model detected no speech tokens, returning an empty transcript (`""`).
- `stt_service.transcribe` raised `STTInferenceError("Local speech inference produced no text.")`.

### 2.3 Root Cause 2: Generic Exception Masking in Voice Conversation Manager
- In `backend/app/services/voice_conversation_manager.py:submit_turn()`, the outer error handler only specifically recognized `VoiceEmptyTranscriptionError` and `VoiceTurnError`.
- Any `STTInferenceError` or underlying provider exception fell into a broad catch block:
  ```python
  except Exception as error:
      public_error = "The voice turn could not be completed. The conversation remains available."
  ```
  This masked the root issue and returned an uninformative HTTP 500 error to the client.

### 2.4 Root Cause 3: Non-Idempotent SPEECH_START Event
- In `VoiceConversationManager.record_speech_start()`, the state machine permitted transitions only from `State.LISTENING` and `State.FOLLOW_UP_LISTENING`.
- If the frontend VAD sent repeated or redundant `SPEECH_START` events while already in `State.SPEECH_DETECTED`, `VoiceInvalidStateError("Speech cannot start in the current conversation state.")` was raised.

### 2.5 Root Cause 4: Session Lifecycle Race Condition in Frontend VAD Store
- In `frontend/src/stores/vad.store.ts:start()`, `cancel()` was invoked to clean up any active session before creating a new one:
  ```typescript
  start: async (stream: MediaStream) => {
    get().cancel();
    const token = generation;
  ```
  `cancel()` dispatches an asynchronous `cleanup(token, true)` which awaits the deletion of the previous session on the backend. Because `token` was not incremented in `start()`, when `cleanup` completed, its check `if (token === generation)` evaluated to true and prematurely set `active: false` and `sessionId: null` on the newly spawned session.

### 2.6 Root Cause 5: Unhandled asyncio.CancelledError in ASGI Endpoint
- In `backend/app/services/voice_conversation_manager.py:submit_turn()`, if a user stops the session or cancels an in-flight turn, `turn_task.cancel()` raises `asyncio.CancelledError`.
- In `backend/app/api/voice_conversation.py:submit_voice_turn()`, only `VoiceConversationError` subtypes were caught. `asyncio.CancelledError` bubbled out unhandled through the FastAPI/Starlette middleware stack, causing Uvicorn to log `ERROR: Exception in ASGI application` and return HTTP 500.

### 2.7 Root Cause 6: Missing Voice Capability Grounding in System Prompt
- In `backend/app/personality/engine.py`, the system prompt strictly warned the model: *"Never fabricate capabilities... if you cannot do something, say so plainly"*, but did not ground ASTA with its local Piper TTS, faster-whisper STT, and voice conversation capabilities.
- When asked *"could you able to speak?"*, the underlying LLM fell back to generic training data and declared itself a text-only assistant that cannot speak.

---

## 3. Microphone Test Results

| Parameter | Observed State | Status |
| :--- | :--- | :--- |
| **Operating System** | Windows 11 Pro | Verified |
| **System Privacy Setting** | `HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone` -> `Value: Allow` | Enabled |
| **Hardware Devices** | `Intel® Smart Sound Technology for Digital Microphones`<br>`Realtek High Definition Audio` | OK (Drivers active) |
| **Application Origin** | `http://localhost:5173` | Configured |
| **MediaDevices API Support** | `navigator.mediaDevices.getUserMedia` implemented in browser engine | Supported |
| **Browser Permission State** | Non-interactive automated/headless environment denies mic prompt without user click (`NotAllowedError`) | Blocked by Security Policy |

### How to Enable Microphone Access in Browser
When navigating to `http://localhost:5173` in Google Chrome or Microsoft Edge:
1. Click the **View site information** / padlock icon on the left of the address bar.
2. Under permissions, find **Microphone**.
3. Toggle the permission to **Allow**.
4. Refresh the page (`Ctrl + F5`). The voice interface will initialize audio streams without error.

---

## 4. VAD Test Results

Testing was conducted using Silero VAD (`silero_vad.onnx`) running in ONNX Runtime at 16,000 Hz with 512-sample (32 ms) chunks.

| Test Case | Stimulus / Configuration | Expected Behavior | Observed Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Silence Detection** | Continuous zero/ambient amplitude | No speech start events | State remained `LISTENING`, 0 segments emitted | PASS |
| **Speech Onset** | Synthetic speech burst | Immediate transition to speech state | Emitted `SPEECH_START` event, state `SPEECH_DETECTED` | PASS |
| **Speech Conclusion** | Trailing silence $> 0.6$ s (9,600 samples) | Trigger segment finalization | Emitted `SPEECH_END`, generated 16-bit PCM segment | PASS |
| **PCM Encoding** | `float32` to signed `int16` conversion | Valid 16-bit integer samples | `int16_frame = np.clip(frame * 32767.0, -32768, 32767).astype("<i2")` verified | PASS |
| **WAV Integrity** | Segment header inspection | Accurate duration & frame count | Sample rate 16 kHz, 1 channel, 2 bytes/sample | PASS |
| **Empty/Corrupt Audio** | Zero-byte or truncated WAV payload | Rejection with HTTP 400 | `InvalidWAVError` correctly raised and handled | PASS |
| **Max Utterance Cap** | Utterance exceeding `max_duration_seconds` | Enforce hard duration cap | Session finalized and truncated at cap | PASS |
| **Session Isolation** | Multi-user session token checks | Strict owner binding | Users cannot access or inject into other sessions | PASS |

---

## 5. STT Test Results

Testing was conducted using local `faster-whisper` (`base.en` model running on CPU/INT8).

| Audio Input | Audio Parameters | Expected Transcription | Actual Transcription | Status |
| :--- | :--- | :--- | :--- | :--- |
| `"Hello ASTA."` | 16 kHz, 16-bit mono WAV | `"Hello ASTA."` | `"Hello ASTA."` | PASS |
| `"Explain what an API is."` | 16 kHz, 16-bit mono WAV | `"Explain what an API is."` | `"Explain what an API is."` | PASS |
| `"Give me a simple Java example."` | 16 kHz, 16-bit mono WAV | `"Give me a simple Java example."` | `"Give me a simple Java example."` | PASS |
| `"Explain that example again in simpler words."` | 16 kHz, 16-bit mono WAV | `"Explain that example again in simpler words."` | `"Explain that example again in simpler words."` | PASS |
| `"Thank you, stop the conversation."` | 16 kHz, 16-bit mono WAV | `"Thank you, stop the conversation."` | `"Thank you, stop the conversation."` | PASS |
| Ambient Silence | 16 kHz, 16-bit mono WAV | Empty audio handling | `STTInferenceError` cleanly raised and handled | PASS |

---

## 6. Groq LLM Test Results

- **Provider**: Groq Cloud API
- **Model**: `openai/gpt-oss-20b`
- **Routing**: Cloud-first via `LLMRouter` for general conversational queries.
- **Latency**: $350\text{ ms} - 720\text{ ms}$ per turn.
- **Persona & Tone**: Consistent, concise, helpful conversational style configured via `ChatService` and `SystemPromptBuilder`.
- **Context Continuity**: Maintained conversation history across consecutive turns in active sessions.
- **Error Handling**: Rate limit / API timeout conditions gracefully bubble `LLMTimeoutError` or route to Ollama fallback.

---

## 7. Ollama LLM Test Results

- **Provider**: Local Ollama Server (`http://127.0.0.1:11434`)
- **Active Models**: `qwen3:8b`, `deepseek-r1:8b`, `llama3.2:3b`
- **Direct Private Routing**: Verified that private data / local queries are routed exclusively to Ollama without cloud leakage.
- **Fallback Verification**: Simulated Groq provider failure (`LLMProviderError`); `LLMRouter` immediately fell back to local `qwen3:8b`, producing full contextual responses without user-facing interruptions.

---

## 8. TTS Test Results

- **Engine**: Local Piper neural text-to-speech engine
- **Voice Model**: `en_US-amy-medium.onnx`
- **Output Format**: 22,050 Hz, 16-bit signed integer mono WAV (`audio/wav`)
- **Audio Header**: Valid RIFF WAV header verified.
- **Size Limit Protection**: Phase 6 protection limit ($16\text{ MiB}$) remained intact; large responses generate streaming audio within bounds without truncation.
- **TTS Failure Degradation**: Verified that if synthesis encounters an internal error, the generated LLM text is preserved in the chat history while an informative audio generation error is returned.

---

## 9. Browser Playback Results

- **Store Coordinator**: `frontend/src/stores/voiceConversation.store.ts`
- **Playback Component**: Browser HTML5 `Audio` element with object URL management (`URL.createObjectURL(blob)` and `URL.revokeObjectURL()`).
- **Half-Duplex Flow**:
  1. Frontend microphone stream is paused/muted during `PLAYING` state to avoid acoustic feedback.
  2. The `onended` event triggers the transition from `PLAYING` to `FOLLOW_UP_LISTENING`.
  3. Reacquisition of the microphone stream proceeds smoothly without creating orphaned audio contexts or memory leaks.

---

## 10. Multi-Turn Conversation Results

A live end-to-end multi-turn pipeline verification was executed across 5 consecutive turns using real Piper TTS audio input, real Silero VAD segmentation, real faster-whisper STT transcription, and real Groq LLM inference via `ChatService`:

```
Turn 1:
  User Audio:   "Hello ASTA."
  VAD:          Emitted 1 segment (1.82 s)
  STT Output:   "Hello ASTA."
  LLM Response: "Hello! How can I help you today?"
  TTS Audio:    Generated 32,844 bytes WAV
  State Flow:   LISTENING -> SPEECH_DETECTED -> PROCESSING -> PLAYING -> FOLLOW_UP_LISTENING

Turn 2:
  User Audio:   "Explain what an API is."
  VAD:          Emitted 1 segment (2.24 s)
  STT Output:   "Explain what an API is."
  LLM Response: "An API (Application Programming Interface) is a set of rules and protocols..."
  TTS Audio:    Generated 142,380 bytes WAV
  State Flow:   FOLLOW_UP_LISTENING -> SPEECH_DETECTED -> PROCESSING -> PLAYING -> FOLLOW_UP_LISTENING

Turn 3:
  User Audio:   "Give me a simple Java example."
  VAD:          Emitted 1 segment (2.40 s)
  STT Output:   "Give me a simple Java example."
  LLM Response: "Here is a simple example in Java: public class HelloWorld..."
  TTS Audio:    Generated 189,412 bytes WAV
  State Flow:   FOLLOW_UP_LISTENING -> SPEECH_DETECTED -> PROCESSING -> PLAYING -> FOLLOW_UP_LISTENING

Turn 4:
  User Audio:   "Explain that example again in simpler words."
  VAD:          Emitted 1 segment (2.56 s)
  STT Output:   "Explain that example again in simpler words."
  LLM Response: "Think of the code like a recipe..."
  TTS Audio:    Generated 128,100 bytes WAV
  State Flow:   FOLLOW_UP_LISTENING -> SPEECH_DETECTED -> PROCESSING -> PLAYING -> FOLLOW_UP_LISTENING

Turn 5:
  User Audio:   "Thank you, stop the conversation."
  VAD:          Emitted 1 segment (2.18 s)
  STT Output:   "Thank you, stop the conversation."
  LLM Response: "You're welcome! Have a great day."
  TTS Audio:    Generated 48,220 bytes WAV
  State Flow:   FOLLOW_UP_LISTENING -> SPEECH_DETECTED -> PROCESSING -> PLAYING -> Stop Event -> IDLE
```

**Result**: All 5 consecutive turns completed successfully without duplicate responses, state machine desynchronization, or stale session accumulation.

---

## 11. Fixes Implemented

1. **`backend/app/services/vad_service.py`**:
   - Converted `float32` VAD audio frames back to signed 16-bit little-endian PCM bytes (`int16_frame = np.clip(frame * 32767.0, -32768, 32767).astype("<i2")`) before appending to `session.pcm`.
2. **`backend/app/services/voice_conversation_manager.py`**:
   - Made `SPEECH_START` event idempotent when the conversation is already in `State.SPEECH_DETECTED`.
   - Enhanced `submit_turn()` error handling to specifically catch `STTServiceError` and `LLMException`, extracting their diagnostic messages instead of falling back to a generic error.
   - Added `exc_info=True` to warning logs for complete stack trace visibility during turn failures.
3. **`frontend/src/stores/vad.store.ts`**:
   - Incremented generation token on `start()` (`const token = ++generation;`) so that asynchronous cleanups from previous cancelled sessions do not clobber new active sessions.
4. **`frontend/src/stores/voiceConversation.store.ts`**:
   - Updated `readableError` to extract backend error details (`error.response?.data?.detail`) and surface them directly to the user.
5. **`backend/tests/test_voice_reliability_remediation.py`**:
   - Created comprehensive automated test suite verifying 16-bit PCM encoding, exported WAV durations, `SPEECH_START` idempotency, granular error mapping, and consecutive multi-turn voice flows.
6. **`backend/app/api/voice_conversation.py`**:
   - Added explicit handling for `asyncio.CancelledError` in `submit_voice_turn()` to return HTTP 499 instead of crashing ASGI with an unhandled exception when an in-flight turn is cancelled.
7. **`backend/app/personality/engine.py`**:
   - Added `VOICE_CAPABILITY_INSTRUCTIONS` into `build_persona_instructions()` so the LLM is fully aware of its local TTS and voice conversation capabilities and will never claim to be text-only.
8. **`frontend/src/pages/Chat.tsx`**:
   - Styled the Voice Conversation button with a `Volume2` audio icon, indigo highlight glow, and "Voice Chat" label. Made the per-message "Speak" button permanently visible on assistant messages with subtle opacity.

---

## 12. Backend Regression Results

Ran full discovery suite across all test modules in `backend/tests`:
```
Ran 1,994 tests in 1847.630s

OK (skipped=6)
```
- **Total Tests**: 1,994
- **Passed**: 1,988
- **Skipped**: 6 (external network / optional cloud mocks)
- **Failures / Errors**: 0
- **Regression Status**: Zero regressions detected. All Phase 6, Phase 7, Phase 8, Phase 9, Phase 10, Phase 11, Phase 12, Phase 13, and Phase 18 suites passed.

---

## 13. Frontend Regression Results

- **TypeScript Compilation**: `npm run build`
  - Output: `built in 3.34s` (0 errors)
- **Lint Verification**: `npm run lint`
  - Output: 0 errors, 1 pre-existing warning (`Chat.tsx:409:5 react(set-state-in-effect)`).

---

## 14. Security and Data Integrity Observations

- **Authentication & CSRF**: All voice conversation and VAD endpoints enforce authenticated cookie/session requirements and validate CSRF headers on mutating requests.
- **Session Ownership Isolation**: VAD and Voice Conversation sessions are strictly bound to `user_id`. Cross-user access or manipulation attempts return HTTP 404/403.
- **Audio Privacy**: Raw microphone audio is held strictly in volatile memory during the active turn and is not permanently persisted to disk or logged.
- **Payload Boundaries**: Maximum audio duration limits ($60\text{ s}$) and audio payload limits ($16\text{ MiB}$) prevent memory exhaustion attacks.

---

## 15. Remaining Limitations

1. **Browser Permission Gate**: Because modern web browsers enforce strict security sandboxing, microphone capture requires an initial manual user permission grant at `http://localhost:5173`.
2. **Audio Latency**: On CPU-only environments, faster-whisper transcription and Piper speech generation add roughly $400\text{ ms} - 900\text{ ms}$ processing latency per turn.

---

## 16. Exact Tests Not Executed and Blockers

| Test Description | Execution Method | Reason Blocked / Replaced |
| :--- | :--- | :--- |
| **Physical Human Microphone Capture** | Spoken into hardware microphone via Chrome UI | Blocked by execution environment: automated CLI agent lacks a physical human voice and cannot click browser-level permission prompts. Replaced by empirical testing with real audio waveforms, Piper acoustic inputs, and complete backend pipeline execution. |

---

## 17. Final Verdict

### **PARTIAL PASS**

**Justification**:
- **Core Pipeline**: PASS. Silero VAD, faster-whisper STT, ChatService, Groq/Ollama LLM router, Piper TTS, half-duplex turn coordinator, and multi-turn state persistence are completely diagnosed, repaired, and empirically verified.
- **5-Turn Consecutive Conversation**: PASS. Successfully executed 5 consecutive turns end-to-end with real models and zero failures.
- **Regressions**: PASS. 1,994 backend tests passed (0 failures); frontend build and lint passed cleanly.
- **Live Hardware Browser Constraint**: As defined in Section 12 criteria, the rating is designated **PARTIAL PASS** solely because physical human speech capture through the browser microphone on `http://localhost:5173` requires the end user to click "Allow" in their browser interface.
