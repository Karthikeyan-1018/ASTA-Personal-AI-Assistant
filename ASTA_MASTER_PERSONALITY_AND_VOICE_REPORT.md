# ASTA Master Personality and Voice Report

**Date:** 2026-11-19  
**Verdict:** **PARTIAL PASS**

## Summary

ASTA has an explicit spoken-response style in its existing shared personality engine, speech-only text normalization, Piper sentence pauses, speed control, and per-user voice settings. John medium remains the system fallback, while Joe medium is saved as the current user's preference. The settings page offers only voices whose `.onnx` and matching `.onnx.json` files are installed locally.

The implementation preserves the displayed and persisted assistant response: speech cleanup runs on a separate copy immediately before synthesis. It keeps Piper local, serializes inference, releases the prior model before loading another, retains WAV output and the existing audio-size bound, and fails clearly when text or audio is over limit.

Three local voice models are installed and have generated comparable WAV samples. The user listened to the Joe medium sample and said that voice is good, then selected and saved Joe as the account preference. This is a positive listening confirmation for Joe; the user has not provided a full comparative ranking of all three voices or a long-session comfort evaluation.

## 1. Personality architecture

ASTA keeps its current `PersonalityService`, `build_persona_instructions`, `ChatService`, `VoiceConversationManager`, and Groq/Ollama router. Voice turns pass a spoken-style flag into the same ChatService call, which adds conversational delivery guidance to the shared system prompt. Text and streaming continue to use their existing style. Spoken turns skip the context-free answer cache so a cached written-style answer is not reused for speech.

The four existing modes remain: Normal, Motivational, Focus, and Casual. Accuracy and honesty remain higher priority than brevity, playfulness, or encouragement. Capability instructions now require ASTA to describe speech as available only when the application confirms it.

## 2. Master identity and mode behavior

The engine already describes ASTA as a personal assistant for learning, programming, engineering, project work, planning, tasks, deadlines, troubleshooting, research, and everyday conversation. Existing mode behavior remains provider-independent. Spoken turns add direct, concise sentences, natural contractions, no routine greeting repetition, and no headings or Markdown unless they are needed to answer.

## 3. Current voice and alternatives

The local installation includes `en_US-john-medium.onnx` (63,531,379 bytes, 22,050 Hz), `en_US-joe-medium.onnx`, and `en_US-kristin-medium.onnx`, each with a matching JSON configuration and retained model card. The John card identifies a single-speaker US English model trained on public-domain LibriVox recordings. Piper engine code is GPL-3.0-or-later; each voice has separate dataset/model provenance, and the upstream project advises checking each voice's model card and training chain. [John medium model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/john/medium/MODEL_CARD), [Piper licensing guidance](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md).

| Candidate | Model size (upstream medium files) | License/provenance note | Local listening result |
| --- | ---: | --- | --- |
| John medium | 63.5 MB | Public-domain LibriVox data; fine-tuned from Kristin medium | Sample generated; user has not stated preference |
| Kristin medium | 63.5 MB | Public-domain LibriVox data; trained from scratch | Sample generated; user has not stated preference |
| Joe medium | 63.2 MB | CC0 data and CC0 rdh base, per current model card | User listened and said this voice is good; saved as account preference |
| Lessac medium | 63.2 MB | Dataset license limits use to speech-technology research/exploration; exclude from any default recommendation | Not installed |
| Amy medium | 63.2 MB | Current card says dataset license “See URL”; fine-tuned from a CC BY 4.0 base, so attribution/provenance needs review | Not installed |

Model sizes and the John, Kristin, Joe, Lessac, and Amy licensing details come from the upstream [Piper voice cards](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US) and [voice licensing guidance](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md). These are paper comparisons of format, size, and licensing, not perceived-quality rankings. Medium models are all about 63 MB; CPU and memory footprints should be similar, but actual latency for these uninstalled models was not measured.

**Selection:** The system fallback remains John medium for continuity. The current user has selected and saved Joe medium at 1.00× in the account-scoped Voice Settings. The user described the Joe sample as good. This confirms the user's preference for Joe, but is not a claim that the other samples were comparatively ranked or that long-session listening comfort was evaluated. John, Kristin, and Joe are installed locally; the settings selector offers all three. Model cards are retained beside the model files.

## 4. TTS configuration and delivery changes

- Voice selection is restricted to locally installed, complete model/config pairs; no model downloads happen at runtime.
- User-selected voice and speed (0.8× to 1.2×) are saved in the authenticated user's existing preference store.
- Piper `length_scale` controls speed; the existing calm baseline is retained.
- Piper yields sentence audio chunks. ASTA adds a configurable 0.18-second silence between chunks by default (`TTS_SENTENCE_PAUSE_SECONDS`). This is application-level silence insertion, not a fabricated Piper style parameter. [Piper Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).
- Text normalization removes Markdown wrappers, links/URLs, and code blocks from the spoken copy; it expands a small set of technical initialisms and common numeric units/percentages. The persisted/displayed assistant response is untouched.
- Piper exposes speed and synthesis variability controls, but no general emotional speaking-style control in the Python configuration used here. No style selector is presented.
- Long speech is not silently truncated. The existing 2,000-character input validation and configured maximum WAV size remain enforced with explicit errors.

## 5. Voice Settings UI

Settings now has a Voice section with an installed-voice selector, speaking-speed slider, save action, actual Piper preview, and restore-default action. The preview uses the saved per-user settings and the same authenticated local synthesis endpoint as message playback. If no complete voice is installed, the UI explains that state rather than offering decorative choices.

The current local installation has three selectable voices: Joe, John, and Kristin medium. The saved Joe choice persists per authenticated user; restoring defaults returns to the system fallback.

## 6. Performance and hardware

The measured local John model file is 63,531,379 bytes (about 60.6 MiB); the app serializes Piper inference and keeps one model loaded at a time. Across two real Piper reliability runs, first short synthesis (`Short response works.`, 21 characters) took **921–983 ms** and produced **82–87 KB WAV**. A **1,957-character** answer produced **5.13–5.15 MB WAV** under the 16 MiB limit. `tracemalloc` reported a **7.36 MiB peak Python allocation** for that long synthesis; this is not total process RSS and excludes native ONNX allocations. Measurements are from the current Windows test environment; its CPU model was not independently queried, so the result is not asserted as a benchmark for a particular i5/RTX configuration.

## 7. Tests and integration

- New focused unit tests cover markdown/link/code cleanup, technical initialisms, numeric units, spoken persona instructions, complete local model discovery, Piper speed mapping, sentence pause bytes, and WAV output shape.
- **128 backend tests passed** across the selected Piper reliability, personality/provider, voice-conversation, voice-settings API, streaming, VAD ownership, and STT/VAD compatibility suites. The TTS tests used real local Piper synthesis; API/LLM/network behavior was exercised through controlled test doubles and in-memory SQLite where appropriate.
- New test coverage verifies authenticated voice preference persistence and isolation between two users. Existing tests verify all four personality modes, Groq/Ollama persona parity, provider fallback, stream paths, multi-turn voice state transitions, STT/VAD compatibility, and TTS size handling.
- Frontend TypeScript/Vite production build succeeded. Frontend lint succeeded with one pre-existing warning at `Chat.tsx:409` (`react(set-state-in-effect)`).
- The project's earlier reliability report recorded a five-turn audio conversation, but that historical report is not a listening evaluation and does not establish the quality of the current spoken style.

## 8. Audio listening comparison

The same normalization-aware sample was synthesized with all three local voices at 1.0× speed. These are actual WAV files produced by ASTA's Piper service. The user listened to Joe and said it is good, and the app preference was saved as Joe. No full comparative ranking or long-session comfort evaluation was reported, and this report does not make objective claims about perceived naturalness.

| Voice | Synthesis time | WAV size | Duration | Sample |
| --- | ---: | ---: | ---: | --- |
| John medium | 1,228.0 ms | 903,732 bytes | 20.49 s | [Play John sample](/C:/Asta/voice_comparison_samples/asta_voice_comparison_en_US-john-medium.wav) |
| Joe medium | 890.0 ms | 812,596 bytes | 18.43 s | [Play Joe sample](/C:/Asta/voice_comparison_samples/asta_voice_comparison_en_US-joe-medium.wav) |
| Kristin medium | 1,081.6 ms | 768,564 bytes | 17.43 s | [Play Kristin sample](/C:/Asta/voice_comparison_samples/asta_voice_comparison_en_US-kristin-medium.wav) |

Sample text: “Sure. Think of an API as a messenger between two applications. One application sends a request, and the other sends back a response. That's the basic idea. A CPU can process ordinary application work while a graphics processor handles visual tasks.” Synthesis times include loading/switching voices; they are not steady-state comparisons. The user selected Joe after listening to its sample. The John and Kristin samples remain available for optional comparison; no additional choice is required to use Joe.


### Standard five-sentence audition set

A reusable local generator is available at [generate_standard_comparison.py](/C:/Asta/voice_comparison_samples/generate_standard_comparison.py). It uses ASTA's speech normalizer and the installed Piper CLI, applies the same 1.05 baseline length scale and 0.18-second sentence silence for every model, and runs one voice at a time. The requested source sentences are in [asta_standard_comparison_sample.txt](/C:/Asta/voice_comparison_samples/asta_standard_comparison_sample.txt); the normalized synthesis input is also retained locally.

| Voice | Synthesis time | WAV size | Duration | Standard sample |
| --- | ---: | ---: | ---: | --- |
| Joe medium | 1,666.6 ms | 980,286 bytes | 22.23 s | [Play Joe standard sample](/C:/Asta/voice_comparison_samples/asta_standard_comparison_en_US-joe-medium.wav) |
| John medium | 1,859.8 ms | 993,598 bytes | 22.53 s | [Play John standard sample](/C:/Asta/voice_comparison_samples/asta_standard_comparison_en_US-john-medium.wav) |
| Kristin medium | 1,899.3 ms | 958,782 bytes | 21.74 s | [Play Kristin standard sample](/C:/Asta/voice_comparison_samples/asta_standard_comparison_en_US-kristin-medium.wav) |

These measurements include Piper process/model startup and are not steady-state latency rankings. The user-selected Joe preference followed the earlier Joe sample; the newly standardized five-sentence set has not yet been separately evaluated by the user.
## 9. Security and privacy

Voice configuration is keyed to the authenticated user through the existing preference system. Existing authentication and CSRF protection remain on the TTS API. Text normalization and speech synthesis execute locally; no audio or text is sent to a cloud TTS provider. Model paths are not returned to the browser, and the selector only accepts names discovered from complete local model/config pairs.

## 10. Known limitations and next steps

1. Joe is the current user's saved voice; John remains the system fallback.
2. The user confirmed Joe sounds good. A full comparative ranking and long-session comfort assessment remain unevaluated.
3. CPU latency and memory use need a timed real-model benchmark on the target machine.
4. Numeric normalization handles common units and percentages; arbitrary identifiers, dates, mathematical notation, and uncommon technical names remain dependent on Piper's phonemizer.
5. Piper model cards should be retained with model files, especially if voices are redistributed.

**Final verdict: PARTIAL PASS.** Personality and configurable local speech implementation are in place, three voice samples were generated, and the user selected Joe as good and saved it for their account. A comparative ranking of all voices, long-session comfort review, and target-hardware performance validation remain incomplete.


