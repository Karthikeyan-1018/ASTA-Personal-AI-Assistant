import { create } from 'zustand';
import { voiceConversationApi } from '../services/api/voiceConversation.api';
import { vadApi } from '../services/api/vad.api';
import {
  isManualMicrophoneActive,
  setVoiceConversationActive,
} from './audioMode';
import { useVADStore, type VADCallbacks } from './vad.store';
import type {
  VADSegmentSummary,
  VoiceConversationState,
  VoiceSessionResponse,
  VoiceStatusResponse,
  VoiceTurnResponse,
} from '../types/api.types';

let activeStream: MediaStream | null = null;
let activeAudio: HTMLAudioElement | null = null;
let activeObjectUrl: string | null = null;
let generation = 0;
let turnGeneration = 0;
let activeTurnAbort: AbortController | null = null;
let cancelActivePlayback: (() => void) | null = null;
let pendingBargeIn: Promise<void> | null = null;
let bargeCandidateVadSessionId: string | null = null;
let bargeTriggeredVadSessionId: string | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
const processedSegments = new Set<string>();

function microphoneError(error: unknown): string {
  const name =
    typeof error === 'object' && error !== null && 'name' in error
      ? String((error as { name?: unknown }).name)
      : '';

  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return 'Microphone permission was denied. Allow access in your browser settings (click the lock/site settings icon in the address bar -> Microphone -> Allow) and reload.';
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return 'No microphone was found on this device.';
    case 'NotReadableError':
    case 'TrackStartError':
      return 'The microphone is already in use by another application.';
    default:
      return readableError(error, 'The voice conversation could not be started.');
  }
}

function readableError(error: unknown, fallback: string): string {
  if (typeof error === 'object' && error !== null) {
    const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (error instanceof Error && error.message.trim()) return error.message.trim();
  }
  return fallback;
}

function remainingSeconds(expiresAt: string | null): number | null {
  if (!expiresAt) return null;
  const milliseconds = Date.parse(expiresAt) - Date.now();
  if (!Number.isFinite(milliseconds)) return null;
  return Math.max(0, Math.ceil(milliseconds / 1000));
}

function decodeAudio(base64: string, mediaType: string): Blob {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mediaType || 'audio/wav' });
}

interface VoiceConversationStoreState {
  enabled: boolean | null;
  available: boolean | null;
  sessionId: string | null;
  state: VoiceConversationState;
  conversationId: number | null;
  followUpExpiresAt: string | null;
  followUpRemainingSeconds: number | null;
  followUpTimeoutSeconds: number;
  bargeInMinSpeechSeconds: number;
  bargeInSpeechThreshold: number;
  busy: boolean;
  micActive: boolean;
  audioPlaying: boolean;
  error: string | null;
  lastTurn: VoiceTurnResponse | null;
  turnSequence: number;

  refreshStatus: () => Promise<void>;
  start: () => Promise<boolean>;
  stop: () => Promise<void>;
  poll: () => Promise<void>;
  reportSpeechStart: (vadSessionId: string) => Promise<void>;
  submitSegment: (
    segment: VADSegmentSummary,
    vadSessionId: string,
  ) => Promise<void>;
  clearError: () => void;
}

export const useVoiceConversationStore = create<VoiceConversationStoreState>(
  (set, get) => {
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

    function releaseAudio(): void {
      cancelActivePlayback = null;
      if (activeAudio) {
        activeAudio.pause();
        activeAudio.onplaying = null;
        activeAudio.onended = null;
        activeAudio.onerror = null;
        activeAudio.removeAttribute('src');
        activeAudio.load();
        activeAudio = null;
      }
      if (activeObjectUrl) {
        URL.revokeObjectURL(activeObjectUrl);
        activeObjectUrl = null;
      }
      if (get().audioPlaying) set({ audioPlaying: false });
    }

    function stopPolling(): void {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function startPolling(): void {
      stopPolling();
      pollTimer = setInterval(() => {
        const state = get();
        if (!state.sessionId) {
          stopPolling();
          return;
        }
        if (state.followUpExpiresAt) {
          const seconds = remainingSeconds(state.followUpExpiresAt);
          if (seconds !== state.followUpRemainingSeconds) {
            set({ followUpRemainingSeconds: seconds });
          }
        }
        void get().poll();
      }, 1000);
    }

    function suspendMicrophone(): void {
      useVADStore.getState().setCallbacks(null);
      void useVADStore.getState().cancel();
      stopTracks(activeStream);
      activeStream = null;
      if (get().micActive) set({ micActive: false });
    }

    /** Release every local resource without asserting a new backend state. */
    function releaseLocalSession(message: string | null = null): void {
      turnGeneration += 1;
      activeTurnAbort?.abort();
      activeTurnAbort = null;
      pendingBargeIn = null;
      bargeCandidateVadSessionId = null;
      bargeTriggeredVadSessionId = null;
      stopPolling();
      suspendMicrophone();
      releaseAudio();
      setVoiceConversationActive(false);
      set({
        sessionId: null,
        state: 'idle',
        busy: false,
        followUpExpiresAt: null,
        followUpRemainingSeconds: null,
        micActive: false,
        audioPlaying: false,
        error: message,
      });
    }

    function applyBackendSession(session: VoiceSessionResponse): void {
      const stopped = session.state === 'idle';
      set({
        state: session.state,
        conversationId:
          session.conversation_id ?? get().conversationId ?? null,
        followUpExpiresAt: session.follow_up_expires_at,
        followUpRemainingSeconds: remainingSeconds(
          session.follow_up_expires_at,
        ),
        error: session.error,
      });

      if (stopped) {
        const message =
          session.stop_reason === 'follow_up_timeout'
            ? 'Follow-up listening ended. Start a new conversation to continue by voice.'
            : null;
        releaseLocalSession(message);
      }
    }

    function installVadCallbacks(): void {
      const callbacks: VADCallbacks = {
        onSpeechStart: (vadSessionId) => {
          const current = get().state;
          if (
            ['transcribing', 'processing', 'speaking'].includes(current) ||
            (get().busy && current !== 'interrupted')
          ) {
            bargeCandidateVadSessionId = vadSessionId;
            return;
          }
          if (current === 'interrupted') return;
          bargeCandidateVadSessionId = null;
          void get().reportSpeechStart(vadSessionId);
        },
        onSpeechActivity: (vadSessionId, active, probability, durationSeconds) => {
          const current = get().state;
          if (
            !active ||
            (!['transcribing', 'processing', 'speaking'].includes(current) &&
              !(get().busy && current !== 'interrupted')) ||
            bargeCandidateVadSessionId !== vadSessionId ||
            bargeTriggeredVadSessionId === vadSessionId ||
            !Number.isFinite(probability) ||
            probability < get().bargeInSpeechThreshold ||
            !Number.isFinite(durationSeconds) ||
            durationSeconds < get().bargeInMinSpeechSeconds
          ) {
            return;
          }
          bargeTriggeredVadSessionId = vadSessionId;
          beginBargeIn(vadSessionId);
        },
        onSegmentComplete: (segment, vadSessionId) => {
          void get().submitSegment(segment, vadSessionId);
        },
      };
      useVADStore.getState().setCallbacks(callbacks);
    }

    function beginBargeIn(vadSessionId: string): void {
      if (pendingBargeIn) return;
      const sessionId = get().sessionId;
      if (!sessionId) return;

      turnGeneration += 1;
      cancelActivePlayback?.();
      set({ state: 'interrupted', audioPlaying: false, busy: false, error: null });

      pendingBargeIn = (async () => {
        try {
          const session = await voiceConversationApi.sendEvent(
            sessionId,
            'barge_in',
            vadSessionId,
          );
          if (get().sessionId === sessionId) applyBackendSession(session);
          activeTurnAbort?.abort();
          activeTurnAbort = null;
        } catch (error) {
          // Playback completion can win a close race with the VAD request.
          // In that case, the user speech is a normal follow-up turn.
          try {
            const session = await voiceConversationApi.getSession(sessionId);
            if (
              session.state === 'follow_up_listening' ||
              session.state === 'listening'
            ) {
              const listening = await voiceConversationApi.sendEvent(
                sessionId,
                'speech_start',
                vadSessionId,
              );
              if (get().sessionId === sessionId) applyBackendSession(listening);
              return;
            }
          } catch {
            // Surface the original state-change error below.
          }
          if (get().sessionId === sessionId) {
            set({
              error: readableError(error, 'The interruption could not be registered.'),
            });
          }
        } finally {
          pendingBargeIn = null;
        }
      })();
    }

    async function acquireMicrophone(token: number): Promise<boolean> {
      if (
        typeof navigator === 'undefined' ||
        !navigator.mediaDevices ||
        typeof navigator.mediaDevices.getUserMedia !== 'function'
      ) {
        set({
          error: 'This browser does not support local microphone capture.',
        });
        return false;
      }

      if (activeStream) {
        installVadCallbacks();
        await useVADStore.getState().start(activeStream);
        const micActive = useVADStore.getState().active;
        if (token === generation) set({ micActive });
        return micActive;
      }

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: { ideal: true },
            noiseSuppression: { ideal: true },
            autoGainControl: { ideal: true },
          },
        });
      } catch (error) {
        if (token === generation) set({ error: microphoneError(error) });
        return false;
      }

      if (
        token !== generation ||
        !get().sessionId ||
        get().state !== 'follow_up_listening'
      ) {
        stopTracks(stream);
        return false;
      }

      activeStream = stream;
      installVadCallbacks();
      await useVADStore.getState().start(stream);

      if (token !== generation) {
        stopTracks(stream);
        return false;
      }

      const micActive = useVADStore.getState().active;
      set({ micActive });
      return micActive;
    }

    async function reportPlaybackEvent(
      event: 'playback_complete' | 'playback_failed',
    ): Promise<void> {
      const sessionId = get().sessionId;
      if (!sessionId) return;

      const token = generation;
      const turnToken = turnGeneration;
      try {
        const session = await voiceConversationApi.sendEvent(
          sessionId,
          event,
        );
        if (token !== generation || turnToken !== turnGeneration) return;
        applyBackendSession(session);

        if (
          event === 'playback_complete' &&
          session.state === 'follow_up_listening'
        ) {
          if (useVADStore.getState().active) {
            set({ micActive: true });
          } else {
            await acquireMicrophone(token);
          }
        } else if (event === 'playback_failed') {
          // The backend keeps the response in the conversation but moves the
          // session to error. Explicitly reclaim it before clearing local state.
          try {
            await voiceConversationApi.stopSession(sessionId);
          } catch {
            // A reclaimed session is already gone; local cleanup still proceeds.
          }
          if (token === generation) {
            releaseLocalSession(
              'Assistant audio playback failed. The text response remains in the conversation.',
            );
          }
        }
      } catch (error) {
        if (token === generation && turnToken === turnGeneration) {
          set({
            error: readableError(
              error,
              'The playback result could not be reported.',
            ),
          });
        }
      }
    }

    async function playTurnAudio(
      payload: VoiceTurnResponse,
      token: number,
    ): Promise<void> {
      if (!payload.audio_base64) return;

      const blob = decodeAudio(
        payload.audio_base64,
        payload.audio_media_type || 'audio/wav',
      );
      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      activeObjectUrl = objectUrl;
      activeAudio = audio;

      await new Promise<void>((resolve) => {
        let settled = false;

        const finish = (
          event?: 'playback_complete' | 'playback_failed',
        ): void => {
          if (settled) return;
          settled = true;
          if (cancelActivePlayback === cancelPlayback) {
            cancelActivePlayback = null;
          }
          releaseAudio();
          resolve();
          if (token === generation && event) void reportPlaybackEvent(event);
        };

        const cancelPlayback = (): void => finish();
        cancelActivePlayback = cancelPlayback;

        audio.onplaying = () => {
          if (token === generation) set({ audioPlaying: true });
        };
        audio.onended = () => finish('playback_complete');
        audio.onerror = () => finish('playback_failed');

        void Promise.resolve(audio.play()).catch(() =>
          finish('playback_failed'),
        );
      });
    }

    return {
      enabled: null,
      available: null,
      sessionId: null,
      state: 'idle',
      conversationId: null,
      followUpExpiresAt: null,
      followUpRemainingSeconds: null,
      followUpTimeoutSeconds: 10,
      bargeInMinSpeechSeconds: 0.25,
      bargeInSpeechThreshold: 0.65,
      busy: false,
      micActive: false,
      audioPlaying: false,
      error: null,
      lastTurn: null,
      turnSequence: 0,

      refreshStatus: async () => {
        try {
          const status = await voiceConversationApi.getStatus();
          set({
            enabled: status.enabled,
            available: status.available,
            followUpTimeoutSeconds: status.follow_up_timeout_seconds,
            bargeInMinSpeechSeconds: status.barge_in_min_speech_seconds,
            bargeInSpeechThreshold: status.barge_in_speech_threshold,
          });
        } catch (error) {
          set({
            enabled: false,
            available: false,
            error: readableError(error, 'Voice conversation status is unavailable.'),
          });
        }
      },

      start: async () => {
        const current = get();
        if (current.sessionId || current.busy) return false;

        if (isManualMicrophoneActive()) {
          set({
            error:
              'Stop the manual microphone recording before starting a voice conversation.',
          });
          return false;
        }

        const token = ++generation;
        let createdSessionId: string | null = null;
        set({ busy: true, error: null });

        try {
          const status: VoiceStatusResponse =
            await voiceConversationApi.getStatus();
          if (token !== generation) return false;

          set({
            enabled: status.enabled,
            available: status.available,
            followUpTimeoutSeconds: status.follow_up_timeout_seconds,
            bargeInMinSpeechSeconds: status.barge_in_min_speech_seconds,
            bargeInSpeechThreshold: status.barge_in_speech_threshold,
          });

          if (!status.enabled || !status.available) {
            throw new Error(
              'Local voice conversation components are unavailable.',
            );
          }
          if (status.active_session_id) {
            throw new Error(
              'A voice conversation is already active in this account.',
            );
          }
          if (isManualMicrophoneActive()) {
            throw new Error(
              'Stop the manual microphone recording before starting a voice conversation.',
            );
          }
          if (
            typeof navigator === 'undefined' ||
            !navigator.mediaDevices ||
            typeof navigator.mediaDevices.getUserMedia !== 'function'
          ) {
            throw new Error(
              'This browser does not support local microphone capture.',
            );
          }

          const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: { ideal: true },
              noiseSuppression: { ideal: true },
              autoGainControl: { ideal: true },
            },
          });
          // Own the stream immediately so every subsequent failure path can
          // release the microphone without a special case.
          activeStream = stream;
          if (token !== generation) {
            stopTracks(stream);
            activeStream = null;
            return false;
          }

          const session = await voiceConversationApi.createSession();
          createdSessionId = session.session_id;
          if (token !== generation) {
            stopTracks(stream);
            void voiceConversationApi.stopSession(session.session_id).catch(
              () => undefined,
            );
            return false;
          }

          activeStream = stream;
          processedSegments.clear();
          turnGeneration += 1;
          pendingBargeIn = null;
          bargeCandidateVadSessionId = null;
          bargeTriggeredVadSessionId = null;
          setVoiceConversationActive(true);
          installVadCallbacks();
          await useVADStore.getState().start(stream);

          if (token !== generation) return false;

          if (!useVADStore.getState().active) {
            throw new Error(
              useVADStore.getState().error ||
                'Local voice activity detection could not be started.',
            );
          }

          set({
            sessionId: session.session_id,
            state: session.state,
            conversationId: session.conversation_id,
            followUpExpiresAt: session.follow_up_expires_at,
            followUpRemainingSeconds: remainingSeconds(
              session.follow_up_expires_at,
            ),
            micActive: useVADStore.getState().active,
            audioPlaying: false,
          });
          startPolling();
          return true;
        } catch (error) {
          if (createdSessionId) {
            await voiceConversationApi
              .stopSession(createdSessionId)
              .catch(() => undefined);
          }
          if (token === generation) {
            releaseLocalSession(microphoneError(error));
          } else {
            stopTracks(activeStream);
            activeStream = null;
          }
          return false;
        } finally {
          if (token === generation) set({ busy: false });
        }
      },

      stop: async () => {
        const sessionId = get().sessionId;
        generation += 1;
        turnGeneration += 1;
        activeTurnAbort?.abort();
        activeTurnAbort = null;
        cancelActivePlayback?.();
        releaseLocalSession();

        if (!sessionId) return;
        try {
          await voiceConversationApi.stopSession(sessionId);
        } catch {
          // A 404 means the backend already reclaimed the session. Other
          // failures still leave the client released and the UI idle.
        }
      },

      poll: async () => {
        const sessionId = get().sessionId;
        if (!sessionId) return;

        const token = generation;
        const turnToken = turnGeneration;
        try {
          const session = await voiceConversationApi.getSession(sessionId);
          if (token !== generation || turnToken !== turnGeneration) return;
          applyBackendSession(session);
        } catch (error) {
          if (token !== generation) return;
          if ((error as { status?: number }).status === 404) {
            generation += 1;
            releaseLocalSession();
          }
        }
      },

      reportSpeechStart: async (vadSessionId) => {
        const sessionId = get().sessionId;
        if (!sessionId) return;

        const token = generation;
        const turnToken = turnGeneration;
        try {
          const session = await voiceConversationApi.sendEvent(
            sessionId,
            'speech_start',
            vadSessionId,
          );
          if (token === generation && turnToken === turnGeneration) {
            applyBackendSession(session);
          }
        } catch (error) {
          if (token === generation && turnToken === turnGeneration) {
            set({
              error: readableError(
                error,
                'The speech event could not be reported.',
              ),
            });
          }
        }
      },

      submitSegment: async (segment, vadSessionId) => {
        const sessionId = get().sessionId;
        if (!sessionId) return;
        if (processedSegments.has(segment.segment_id)) return;
        processedSegments.add(segment.segment_id);

        const token = generation;
        const turnToken = ++turnGeneration;
        set({ busy: true, error: null });

        try {
          if (pendingBargeIn) await pendingBargeIn;
          if (token !== generation || turnToken !== turnGeneration) return;

          if (get().state !== 'speech_detected') {
            const speechSession = await voiceConversationApi.sendEvent(
              sessionId,
              'speech_start',
              vadSessionId,
            );
            if (token !== generation || turnToken !== turnGeneration) return;
            applyBackendSession(speechSession);
            if (speechSession.state !== 'speech_detected') {
              throw new Error(
                'The backend did not accept this speech segment.',
              );
            }
          }

          // The segment exists only while its Stage 3 VAD session is alive.
          // Fetch it first, then release the microphone for half-duplex work.
          const audio = await vadApi.getSegmentWav(
            vadSessionId,
            segment.segment_id,
          );
          if (token !== generation || turnToken !== turnGeneration) return;

          const stream = activeStream;
          if (!stream) throw new Error('The voice microphone is no longer active.');
          const controller = new AbortController();
          activeTurnAbort = controller;
          set({ state: 'transcribing', micActive: true });
          const responsePromise = voiceConversationApi.submitTurn(
            sessionId,
            audio,
            segment.segment_id,
            controller.signal,
          );

          // Rotate the bounded server VAD session after each captured segment,
          // while retaining the same echo-cancelled microphone stream.
          installVadCallbacks();
          await useVADStore.getState().start(stream);
          const micActive = useVADStore.getState().active;
          if (token === generation && turnToken === turnGeneration) {
            set({ micActive });
          }

          const response = await responsePromise;
          if (activeTurnAbort === controller) activeTurnAbort = null;
          if (token !== generation || turnToken !== turnGeneration) return;

          set({
            lastTurn: response,
            turnSequence: get().turnSequence + 1,
          });
          applyBackendSession(response);

          if (response.state === 'speaking' && response.audio_base64) {
            await playTurnAudio(response, token);
          }
        } catch (error) {
          if (token === generation && turnToken === turnGeneration) {
            set({
              error: readableError(
                error,
                'The voice turn could not be completed.',
              ),
            });
          }
        } finally {
          if (token === generation && turnToken === turnGeneration) {
            set({ busy: false });
            activeTurnAbort = null;
          }
        }
      },

      clearError: () => set({ error: null }),
    };
  },
);
