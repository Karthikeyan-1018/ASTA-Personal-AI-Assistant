import { create } from 'zustand';
import {
  VAD_BATCH_SAMPLES,
  VAD_FRAME_SAMPLES,
  VAD_SAMPLE_RATE,
  vadApi,
} from '../services/api/vad.api';
import type { VADFrameResponse, VADSegmentSummary } from '../types/api.types';

export type VADStatus =
  | 'idle'
  | 'starting'
  | 'listening'
  | 'speech'
  | 'silence'
  | 'completed'
  | 'unavailable';

/** Optional observers used only by an explicit Stage 4 voice conversation. */
export interface VADCallbacks {
  onSpeechStart?: (vadSessionId: string) => void;
  onSegmentComplete?: (
    segment: VADSegmentSummary,
    vadSessionId: string,
  ) => void;
}

interface VADStoreState {
  active: boolean;
  state: VADStatus;
  lastProbability: number | null;
  completedSegments: number;
  sessionId: string | null;
  error: string | null;

  start: (stream: MediaStream) => Promise<void>;
  cancel: () => void;
  reset: () => void;
  setCallbacks: (callbacks: VADCallbacks | null) => void;
}

const MAX_PENDING_SAMPLES = VAD_BATCH_SAMPLES * 4;
const MAX_QUEUED_BATCHES = 4;

let currentSessionId: string | null = null;
let audioContext: AudioContext | null = null;
let sourceNode: MediaStreamAudioSourceNode | null = null;
let processorNode: ScriptProcessorNode | null = null;
let silentGain: GainNode | null = null;
let watchedTracks: MediaStreamTrack[] = [];
let endedHandler: (() => void) | null = null;
let pendingSamples: number[] = [];
let queuedBatches: Int16Array[] = [];
let sending = false;
let generation = 0;
let callbacks: VADCallbacks | null = null;

let resamplerReady = false;
let resamplerPhase = 0;

function audioContextConstructor(): typeof AudioContext | null {
  const scope = globalThis as typeof globalThis & {
    AudioContext?: typeof AudioContext;
    webkitAudioContext?: typeof AudioContext;
  };
  return scope.AudioContext ?? scope.webkitAudioContext ?? null;
}

function vadErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : '';

  if (/401|unauthorized|session/i.test(message)) {
    return 'Your session expired. Sign in again before using local voice activity detection.';
  }

  if (/model|unavailable|404|disabled/i.test(message)) {
    return 'Local voice activity detection is unavailable.';
  }

  return 'Local voice activity detection could not be started.';
}

/**
 * Explicitly resample the browser's native AudioContext rate to the 16 kHz
 * mono rate required by Silero VAD. Linear interpolation preserves short
 * pauses better than dropping samples at non-integer ratios such as 44.1 kHz.
 */
function resampleTo16k(input: Float32Array, inputRate: number): Float32Array {
  if (input.length === 0) return new Float32Array(0);

  const ratio = inputRate / VAD_SAMPLE_RATE;
  if (Math.abs(ratio - 1) < 0.0001) {
    resamplerReady = false;
    return input.slice();
  }

  if (!resamplerReady) {
    resamplerPhase = 0;
    resamplerReady = true;
  }

  const output: number[] = [];
  while (resamplerPhase < input.length) {
    const base = Math.floor(resamplerPhase);
    const fraction = resamplerPhase - base;
    const left = input[base] ?? 0;
    const right = input[base + 1] ?? left;
    output.push(left + (right - left) * fraction);
    resamplerPhase += ratio;
  }

  resamplerPhase -= input.length;
  return Float32Array.from(output);
}

function toPcm16(samples: Float32Array): void {
  for (const sample of samples) {
    const clamped = Math.min(1, Math.max(-1, sample));
    pendingSamples.push(Math.round(clamped * 32767));
  }

  if (pendingSamples.length > MAX_PENDING_SAMPLES) {
    pendingSamples.splice(0, pendingSamples.length - MAX_PENDING_SAMPLES);
  }

  while (pendingSamples.length >= VAD_BATCH_SAMPLES) {
    const batch = Int16Array.from(pendingSamples.splice(0, VAD_BATCH_SAMPLES));
    if (queuedBatches.length >= MAX_QUEUED_BATCHES) queuedBatches.shift();
    queuedBatches.push(batch);
  }
}

function nextDisplayState(
  response: Pick<
    VADFrameResponse,
    'state' | 'speech_detected' | 'completed_segments'
  >,
): VADStatus {
  if (response.completed_segments.length > 0) return 'completed';
  if (response.speech_detected || response.state === 'speaking') return 'speech';
  if (response.state === 'silence') return 'silence';
  return 'listening';
}

async function pump(token: number): Promise<void> {
  if (sending) return;
  sending = true;

  try {
    while (queuedBatches.length > 0 && token === generation && currentSessionId) {
      const batch = queuedBatches.shift();
      if (!batch) break;
      if (batch.length % VAD_FRAME_SAMPLES !== 0) continue;

      try {
        const response = await vadApi.postFrames(currentSessionId, batch);
        if (token !== generation) break;

        const completed = response.completed_segments.length;
        useVADStore.setState((state) => ({
          state: nextDisplayState(response),
          lastProbability: response.speech_probability,
          completedSegments: state.completedSegments + completed,
          error: null,
        }));

        // Real model events only. Stage 4 observers receive the actual VAD
        // session id so they can fetch the real segment WAV before teardown.
        if (response.events.includes('speech_start') && currentSessionId) {
          callbacks?.onSpeechStart?.(currentSessionId);
        }
        for (const segment of response.completed_segments) {
          if (currentSessionId) {
            callbacks?.onSegmentComplete?.(segment, currentSessionId);
          }
        }
      } catch (error) {
        if (token === generation) {
          useVADStore.setState({
            state: 'unavailable',
            error: vadErrorMessage(error),
          });
          void cleanup(token, true);
        }
        break;
      }
    }
  } finally {
    sending = false;
  }
}

async function cleanup(token: number, deleteSession: boolean): Promise<void> {
  const sessionId = currentSessionId;
  currentSessionId = null;
  pendingSamples = [];
  queuedBatches = [];

  for (const track of watchedTracks) {
    if (endedHandler && typeof track.removeEventListener === 'function') {
      track.removeEventListener('ended', endedHandler);
    }
  }
  watchedTracks = [];
  endedHandler = null;

  try {
    processorNode?.disconnect();
  } catch {
    // Cleanup is best effort and must never block the UI.
  }

  try {
    sourceNode?.disconnect();
  } catch {
    // Cleanup is best effort and must never block the UI.
  }

  try {
    silentGain?.disconnect();
  } catch {
    // Cleanup is best effort and must never block the UI.
  }

  processorNode = null;
  sourceNode = null;
  silentGain = null;

  if (audioContext) {
    const context = audioContext;
    audioContext = null;
    try {
      if (context.state !== 'closed') await context.close();
    } catch {
      // Cleanup is best effort and must never block the UI.
    }
  }

  if (deleteSession && sessionId) {
    try {
      await vadApi.deleteSession(sessionId);
    } catch {
      // A stale session expires server-side; cleanup must not block the UI.
    }
  }

  if (token === generation) {
    useVADStore.setState({ active: false, sessionId: null });
  }
}

export const useVADStore = create<VADStoreState>((set, get) => ({
  active: false,
  state: 'idle',
  lastProbability: null,
  completedSegments: 0,
  sessionId: null,
  error: null,

  start: async (stream: MediaStream) => {
    get().cancel();
    const token = generation;

    resamplerReady = false;
    resamplerPhase = 0;
    set({
      active: false,
      state: 'starting',
      lastProbability: null,
      completedSegments: 0,
      sessionId: null,
      error: null,
    });

    const AudioContextClass = audioContextConstructor();
    if (!AudioContextClass) {
      set({
        state: 'unavailable',
        error: 'This browser does not support local voice activity detection.',
      });
      return;
    }

    let session: { session_id: string } | null = null;
    try {
      session = await vadApi.createSession();
    } catch (error) {
      if (token === generation) {
        set({ state: 'unavailable', error: vadErrorMessage(error) });
      }
      return;
    }

    if (token !== generation || !session) {
      await vadApi.deleteSession(session.session_id).catch(() => undefined);
      return;
    }

    try {
      const context = new AudioContextClass();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(VAD_BATCH_SAMPLES, 1, 1);
      const gain = context.createGain();
      gain.gain.value = 0;

      processor.onaudioprocess = (event) => {
        if (token !== generation) return;
        const resampled = resampleTo16k(
          event.inputBuffer.getChannelData(0),
          context.sampleRate || VAD_SAMPLE_RATE,
        );
        toPcm16(resampled);
        if (queuedBatches.length > 0) void pump(token);
      };

      source.connect(processor);
      processor.connect(gain);
      gain.connect(context.destination);

      audioContext = context;
      sourceNode = source;
      processorNode = processor;
      silentGain = gain;
      currentSessionId = session.session_id;

      endedHandler = () => {
        if (token === generation) get().cancel();
      };
      watchedTracks =
        typeof stream.getTracks === 'function' ? stream.getTracks() : [];
      for (const track of watchedTracks) {
        if (typeof track.addEventListener === 'function') {
          track.addEventListener('ended', endedHandler, { once: true });
        }
      }

      set({
        active: true,
        state: 'listening',
        sessionId: session.session_id,
        error: null,
      });
    } catch (error) {
      await cleanup(token, true);
      if (token === generation) {
        set({ state: 'unavailable', error: vadErrorMessage(error) });
      }
    }
  },

  cancel: () => {
    generation += 1;
    set({
      active: false,
      state: 'idle',
      lastProbability: null,
      completedSegments: 0,
      sessionId: null,
      error: null,
    });
    void cleanup(generation, true);
  },

  reset: () => {
    get().cancel();
  },

  setCallbacks: (nextCallbacks) => {
    callbacks = nextCallbacks;
  },
}));
