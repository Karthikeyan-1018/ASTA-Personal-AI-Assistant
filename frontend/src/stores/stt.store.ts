import { create } from 'zustand';
import {
  STT_RECORDING_MIME_CANDIDATES,
  sttApi,
} from '../services/api/stt.api';
import { useVADStore } from './vad.store';
import {
  isVoiceConversationActive,
  setManualMicrophoneActive,
} from './audioMode';

let activeStream: MediaStream | null = null;
let activeRecorder: MediaRecorder | null = null;
let activeMimeType: string | null = null;
let recordingChunks: Blob[] = [];
let recordingStartedAt = 0;
let durationTimer: ReturnType<typeof setInterval> | null = null;
let stopRequested = false;
let cancelRequested = false;
let requestToken = 0;

const DEFAULT_MAX_DURATION_SECONDS = 60;

function recorderConstructor(): typeof MediaRecorder | null {
  const candidate = (
    globalThis as typeof globalThis & { MediaRecorder?: typeof MediaRecorder }
  ).MediaRecorder;
  return typeof candidate === 'undefined' ? null : candidate;
}

function pickMimeType(Recorder: typeof MediaRecorder): string | null {
  if (typeof Recorder.isTypeSupported !== 'function') return null;

  for (const candidate of STT_RECORDING_MIME_CANDIDATES) {
    if (Recorder.isTypeSupported(candidate)) return candidate;
  }

  return null;
}

function microphoneError(error: unknown): string {
  const name =
    typeof error === 'object' && error !== null && 'name' in error
      ? String((error as { name?: unknown }).name)
      : '';

  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return 'Microphone permission was denied. Allow access in your browser settings and try again.';
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return 'No microphone was found on this device.';
    case 'NotReadableError':
    case 'TrackStartError':
      return 'The microphone is already in use by another application.';
    case 'SecurityError':
      return 'Microphone access is blocked by the browser on this page.';
    default:
      return 'Microphone recording could not be started. Please try again.';
  }
}

function transcriptionError(error: unknown): string {
  const message = error instanceof Error ? error.message : '';

  if (/busy/i.test(message)) {
    return 'The local speech service is busy. Please try again shortly.';
  }

  if (/401|unauthorized|session/i.test(message)) {
    return 'Your session expired. Sign in again before using the microphone.';
  }

  return message || 'The recording could not be transcribed. Please try again.';
}

function stopTracks(stream: MediaStream | null): void {
  if (!stream) return;

  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      // Track cleanup is best effort and must never block the UI.
    }
  }
}

function clearDurationTimer(): void {
  if (durationTimer !== null) {
    clearInterval(durationTimer);
    durationTimer = null;
  }
}

interface STTStoreState {
  backendAvailable: boolean | null;
  isRequestingPermission: boolean;
  isRecording: boolean;
  isTranscribing: boolean;
  recordingSeconds: number;
  maxDurationSeconds: number;
  error: string | null;
  lastTranscript: string | null;

  refreshStatus: () => Promise<void>;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  cancelRecording: () => void;
  clearError: () => void;
  clearTranscript: () => void;
}

export const useSTTStore = create<STTStoreState>((set, get) => {
  function cleanupAudio(): void {
    clearDurationTimer();
    // Stage 3 VAD is optional telemetry for the current explicit recording.
    // It must never delay or interrupt the Stage 2 upload workflow.
    void useVADStore.getState().cancel();
    setManualMicrophoneActive(false);
    stopTracks(activeStream);
    activeStream = null;
    activeRecorder = null;
    activeMimeType = null;
    recordingChunks = [];
  }

  async function uploadRecording(blob: Blob, mimeType: string, token: number) {
    if (get().isTranscribing) return;

    set({ isTranscribing: true, error: null });

    try {
      const response = await sttApi.transcribe(blob, mimeType);

      if (token !== requestToken) return;

      const text = response.text?.trim();
      if (!response.success || !text) {
        throw new Error('The local speech service returned no transcription.');
      }

      set({ lastTranscript: text });
    } catch (error) {
      if (token === requestToken) {
        set({ error: transcriptionError(error) });
      }
    } finally {
      if (token === requestToken) {
        set({ isTranscribing: false });
      }
    }
  }

  return {
    backendAvailable: null,
    isRequestingPermission: false,
    isRecording: false,
    isTranscribing: false,
    recordingSeconds: 0,
    maxDurationSeconds: DEFAULT_MAX_DURATION_SECONDS,
    error: null,
    lastTranscript: null,

    refreshStatus: async () => {
      try {
        const status = await sttApi.getStatus();
        set({
          backendAvailable: status.enabled && status.available,
          maxDurationSeconds:
            status.max_duration_seconds > 0
              ? status.max_duration_seconds
              : DEFAULT_MAX_DURATION_SECONDS,
        });
      } catch {
        set({ backendAvailable: false });
      }
    },

    startRecording: async () => {
      const state = get();
      if (
        state.isRequestingPermission ||
        state.isRecording ||
        state.isTranscribing
      ) {
        return;
      }

      if (isVoiceConversationActive()) {
        set({
          error:
            'Stop the voice conversation before using the manual microphone.',
        });
        return;
      }

      const Recorder = recorderConstructor();
      if (
        !Recorder ||
        typeof navigator === 'undefined' ||
        !navigator.mediaDevices ||
        typeof navigator.mediaDevices.getUserMedia !== 'function'
      ) {
        set({
          error:
            'This browser does not support local microphone recording. Try a current version of Chrome, Edge, Firefox, or Safari.',
        });
        return;
      }

      const token = ++requestToken;
      stopRequested = false;
      cancelRequested = false;
      cleanupAudio();
      set({
        error: null,
        lastTranscript: null,
        recordingSeconds: 0,
        isRequestingPermission: true,
      });

      let stream: MediaStream | null = null;

      try {
        // Permission is requested only from this explicit user action.
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        if (token !== requestToken || cancelRequested) {
          stopTracks(stream);
          return;
        }

        const mimeType = pickMimeType(Recorder);
        const recorder = new Recorder(
          stream,
          mimeType ? { mimeType } : undefined,
        );

        activeStream = stream;
        activeRecorder = recorder;
        activeMimeType = recorder.mimeType || mimeType || 'audio/webm';
        recordingChunks = [];
        recordingStartedAt = performance.now();

        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            recordingChunks.push(event.data);
          }
        };

        recorder.onerror = () => {
          if (token !== requestToken) return;
          cleanupAudio();
          set({
            isRecording: false,
            isRequestingPermission: false,
            recordingSeconds: 0,
            error: 'Microphone recording failed. Please try again.',
          });
        };

        recorder.onstop = () => {
          if (token !== requestToken) return;

          const shouldUpload = stopRequested && !cancelRequested;
          const blob = new Blob(recordingChunks, {
            type: activeMimeType || 'audio/webm',
          });
          const uploadMimeType = activeMimeType || 'audio/webm';
          const hasAudio = blob.size > 0;

          cleanupAudio();
          stopRequested = false;
          set({
            isRecording: false,
            isRequestingPermission: false,
            recordingSeconds: 0,
          });

          if (!shouldUpload || !hasAudio) return;

          void uploadRecording(blob, uploadMimeType, token);
        };

        // Use the already-permissioned microphone stream. VAD observes the
        // same stream for real status only; it never uploads or sends text.
        void useVADStore.getState().start(stream);

        recorder.start(250);
        setManualMicrophoneActive(true);
        set({ isRequestingPermission: false, isRecording: true });

        durationTimer = setInterval(() => {
          if (token !== requestToken || !activeRecorder) return;

          const seconds = (performance.now() - recordingStartedAt) / 1000;
          set({ recordingSeconds: seconds });

          if (seconds >= get().maxDurationSeconds) {
            get().stopRecording();
          }
        }, 250);
      } catch (error) {
        stopTracks(stream);
        cleanupAudio();
        stopRequested = false;
        set({
          isRequestingPermission: false,
          isRecording: false,
          recordingSeconds: 0,
          error: microphoneError(error),
        });
      }
    },

    stopRecording: () => {
      if (stopRequested || !get().isRecording) return;

      if (!activeRecorder || activeRecorder.state === 'inactive') {
        cleanupAudio();
        set({ isRecording: false, isRequestingPermission: false });
        return;
      }

      stopRequested = true;
      activeRecorder.stop();
    },

    cancelRecording: () => {
      requestToken += 1;
      stopRequested = false;
      cancelRequested = true;

      if (activeRecorder && activeRecorder.state !== 'inactive') {
        try {
          activeRecorder.onstop = null;
          activeRecorder.stop();
        } catch {
          // Cleanup below still releases tracks and timer resources.
        }
      }

      cleanupAudio();
      set({
        isRequestingPermission: false,
        isRecording: false,
        isTranscribing: false,
        recordingSeconds: 0,
        error: null,
      });
    },

    clearError: () => set({ error: null }),
    clearTranscript: () => set({ lastTranscript: null }),
  };
});
