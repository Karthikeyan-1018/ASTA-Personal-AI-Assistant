# ASTA Voice Speed and Barge-In Report

**Date:** 2026-11-19  
**Scope:** Voice latency profiling, barge-in implementation, and regression checks.  
**Overall verdict: PARTIAL PASS.** The implementation and focused automated checks pass. Real microphone/speaker interruption accuracy and end-to-end latency have not been validated on the target hardware, so real-time barge-in is not claimed as proven.

## Changes

- Kept the existing voice stack (Silero VAD, faster-whisper, ChatService/provider routing, Piper) and the selected `en_US-joe-medium` voice. No new router or speech service was introduced.
- Reduced VAD upload batches from 16 to 8 frames (512 ms to 256 ms of buffered audio), lowering the maximum batch wait by up to 256 ms. Browser capture requests echo cancellation, noise suppression, and automatic gain control.
- Kept mic/VAD capture active while ASTA processes or plays a response. Speech over configured confidence (`0.65` default) and duration (`0.25 s` default) during an active response triggers barge-in: playback is stopped locally, the old turn is invalidated/cancelled, and the session returns to listening.
- Follow-up diagnosis of the UI's “local speech service is busy” error found that the manual recording lock was released before its STT upload completed. The frontend now holds the shared audio workflow lock through transcription, preventing a voice conversation from starting concurrently with an outstanding manual Whisper request.
- Added interrupted state/event handling, generation guards against stale responses/audio, cancellation-safe handling of native model work, and per-stage server timing fields. Existing authentication, CSRF, session ownership, audio limits and local speech processing are retained.

## Measurements

No trustworthy before/after complete-turn or time-to-first-audio measurement was available: the current endpoint path does not stream LLM tokens or TTS chunks, and browser microphone/playback timing was not measured. The 8-frame batch change reduces the buffering component by at most 256 ms; this is a design bound, not an observed end-to-end gain.

### Local STT profile

The existing `faster-whisper-small.en`, CPU `int8`, 4-thread configuration was profiled on three 22-second synthetic voice comparison samples. Model initialization was about 907 ms. Per-sample transcription times (beam 5 / beam 3 / beam 1) were:

| Sample | Beam 5 | Beam 3 | Beam 1 | WER, beam 5 → beam 1 |
|---|---:|---:|---:|---:|
| Joe | 2923 ms | 3012 ms | 2803 ms | 0.10 → 0.12 |
| John | 3437 ms | 3029 ms | 2744 ms | 0.14 → 0.14 |
| Kristin | 3738 ms | 3169 ms | 2855 ms | 0.12 → 0.12 |

The small and inconsistent speed advantage did not justify reducing beam size: beam 1 worsened Joe's WER, and the sample is too limited to establish a general accuracy/performance tradeoff. The existing beam 5 setting is retained. These are synthetic sample measurements, not live user speech.

### Local VAD profile

On the same three comparison WAVs, 16-frame vs 8-frame local VAD processing took: Joe 488 vs 313 ms; John 334 vs 323 ms; Kristin 346 vs 309 ms. Detected speech-start/segment counts were unchanged per sample (Joe 10/9, John 2/1, Kristin 2/1). A 3-second silence control and low-amplitude synthetic white-noise control produced zero speech starts/segments. This does not test acoustic echo from speakers.

### TTS / resource use

Existing local Piper measurements in the voice reliability run were approximately 1006 ms for a short synthesis and 5.19 MB output for a long response (tracemalloc peak about 7.7 MB). These are prior local test measurements and are not a Joe-specific browser playback-start benchmark. A comparable per-stage CPU/RAM/GPU hardware profile and audio-transfer/playback-start measurement were not obtained in this run.

## Barge-in design and evidence

The frontend retains one microphone stream and VAD session across response playback, requests browser echo/noise controls, and only triggers interruption during an active turn after the configured confidence and duration gates. It stops the current audio immediately, aborts the turn request, and uses a turn generation to ignore stale polling, playback, and callbacks. The backend marks the turn interrupted and invalidates its generation so a late LLM/TTS result cannot restart the old response. Native inference running in worker threads is drained safely to avoid unsafe concurrent use of shared models; the barge-in event response does not wait for that worker to drain.

Automated tests cover interruption while ChatService work is running, while TTS is running, speaking-state transition, cancellation-safe worker completion, API authentication/CSRF, and the new barge-in event. Focused voice/VAD/STT/TTS tests: **58 passed**. These tests establish cancellation/state behavior, not actual speech detection accuracy.

Actual microphone/speaker tests (interrupt at start/middle/end; short/long utterances; own-speaker echo; background noise; consecutive interruptions) were not performed. Browser echo cancellation is requested but support and acoustic echo rejection depend on the browser/device. VAD alone cannot distinguish the user's speech from ASTA's speaker output; acoustic feedback and false-trigger rates remain unmeasured. This is the principal limitation preventing a full PASS.

## Regression checks

- Focused voice, VAD, STT, Piper/TTS, interruption, and auth/CSRF tests: **58 passed**.
- Frontend `npm run build`: passed (existing large-chunk and dynamic-import warnings).
- Frontend `npm run lint`: passed with one existing React set-state-in-effect warning in `Chat.tsx`.
- After the manual STT/voice overlap fix, frontend build and lint were rerun successfully (same existing lint warning).
- Full backend suite: attempted, but **inconclusive**. It advanced through the broad chat/memory/task tests and emitted expected mocked-provider/validation logs, then remained without a test summary for several minutes. I stopped the run; it returned exit 1 on interruption, not on a reported assertion failure. No full-suite pass is claimed.
- The previous reliability report records an older five-turn conversation test and a 1,994-test backend baseline. That is historical half-duplex evidence, not a validation of the new barge-in path.

## Verdict

**PARTIAL PASS.** Local performance profiling is complete for STT and VAD; the VAD batching and manual-STT/voice overlap fixes are implemented, and the focused interruption/security tests and frontend checks pass. Joe Medium remains preferred. The STT semaphore still returns a busy response if an independent backend request holds the single inference slot beyond its configured queue timeout. Live microphone/speaker behavior, actual interruption accuracy/false-trigger rates, complete-turn before/after latency, provider TTFT/fallback timing, target-hardware CPU/RAM/GPU use, and final full-backend regression status remain unverified. Do not interpret this report as proof that ASTA will always stop speaking immediately in a real acoustic environment.
