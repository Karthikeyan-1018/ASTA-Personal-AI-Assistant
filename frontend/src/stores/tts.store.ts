import { create } from 'zustand';
import { ttsApi } from '../services/api/tts.api';
import { useSettingsStore } from './settings.store';

let activeAudio: HTMLAudioElement | null = null;
let activeObjectUrl: string | null = null;
let requestToken = 0;

function releaseAudio(): void {
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
}

interface TTSStoreState {
  speakingId: string | null;
  loadingId: string | null;
  errorId: string | null;
  errorMessage: string | null;
  backendAvailable: boolean | null;
  refreshStatus: () => Promise<void>;
  speak: (messageId: string, text: string) => Promise<void>;
  stop: () => void;
  clearError: () => void;
}

export const useTTSStore = create<TTSStoreState>((set, get) => ({
  speakingId: null,
  loadingId: null,
  errorId: null,
  errorMessage: null,
  backendAvailable: null,

  refreshStatus: async () => {
    try {
      const status = await ttsApi.getStatus();
      const backendAvailable = status.enabled && status.available;
      set({ backendAvailable });
      if (!backendAvailable) get().stop();
    } catch {
      set({ backendAvailable: false });
      get().stop();
    }
  },

  speak: async (messageId, text) => {
    if (!useSettingsStore.getState().settings.ttsEnabled) {
      get().stop();
      return;
    }

    if (get().backendAvailable === false) {
      set({
        errorId: messageId,
        errorMessage: 'Speech is unavailable. The text response is still available.',
      });
      return;
    }

    const cleaned = text.trim();
    if (!cleaned) return;

    // Stopping first invalidates any in-flight request and releases audio so
    // only one message can be generated or played at a time.
    get().stop();
    const token = requestToken;

    set({
      loadingId: messageId,
      errorId: null,
      errorMessage: null,
    });

    try {
      const blob = await ttsApi.synthesize(cleaned);
      if (token !== requestToken) return;
      if (!blob || blob.size === 0) {
        throw new Error('Speech generation produced no audio.');
      }

      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      activeObjectUrl = objectUrl;
      activeAudio = audio;

      audio.onplaying = () => {
        if (token !== requestToken) return;
        set({ speakingId: messageId, loadingId: null });
      };

      audio.onended = () => {
        if (token !== requestToken) return;
        releaseAudio();
        set({ speakingId: null, loadingId: null });
      };

      audio.onerror = () => {
        if (token !== requestToken) return;
        releaseAudio();
        set({
          speakingId: null,
          loadingId: null,
          errorId: messageId,
          errorMessage: 'Audio playback failed. The text response is still available.',
        });
      };

      await audio.play();
      if (token !== requestToken) {
        releaseAudio();
        return;
      }
      set({ speakingId: messageId, loadingId: null });
    } catch (error) {
      if (token !== requestToken) return;
      releaseAudio();
      const detail =
        error instanceof Error && error.message
          ? error.message
          : 'Speech generation failed.';
      set({
        speakingId: null,
        loadingId: null,
        errorId: messageId,
        errorMessage: `${detail} The text response is still available.`,
      });
    }
  },

  stop: () => {
    requestToken += 1;
    releaseAudio();
    set({ speakingId: null, loadingId: null });
  },

  clearError: () => set({ errorId: null, errorMessage: null }),
}));
