import { create } from 'zustand';
import { personalityApi } from '../services/api/personality.api';
import type {
  PersonalitySettings,
  PersonalitySettingsUpdate,
} from '../types/personality.types';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

interface PersonalityStoreState {
  settings: PersonalitySettings | null;
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  successMessage: string | null;
  isBackendConnected: boolean;
  fetchSettings: () => Promise<boolean>;
  saveSettings: (update: PersonalitySettingsUpdate) => Promise<boolean>;
  resetSettings: () => Promise<boolean>;
  clearFeedback: () => void;
}

export const usePersonalityStore = create<PersonalityStoreState>((set, get) => ({
  settings: null,
  isLoading: false,
  isSaving: false,
  error: null,
  successMessage: null,
  isBackendConnected: false,

  fetchSettings: async () => {
    if (get().isLoading) return false;
    set({ isLoading: true, error: null, successMessage: null });

    try {
      const settings = await personalityApi.getSettings();
      set({
        settings,
        isLoading: false,
        isBackendConnected: true,
        error: null,
      });
      return true;

    } catch (err: unknown) {
      set({
        isLoading: false,
        isBackendConnected: false,
        error: getErrorMessage(
          err,
          'Could not load personality settings from the ASTA backend.'
        ),
      });
      return false;
    }
  },

  saveSettings: async (update) => {
    if (get().isSaving) return false;
    set({ isSaving: true, error: null, successMessage: null });

    try {
      const settings = await personalityApi.updateSettings(update);
      set({
        settings,
        isSaving: false,
        isBackendConnected: true,
        error: null,
        successMessage: 'Personality settings saved.',
      });
      return true;

    } catch (err: unknown) {
      set({
        isSaving: false,
        error: getErrorMessage(
          err,
          'Personality settings could not be saved.'
        ),
        successMessage: null,
      });
      return false;
    }
  },

  resetSettings: async () => {
    if (get().isSaving) return false;
    set({ isSaving: true, error: null, successMessage: null });

    try {
      const settings = await personalityApi.resetSettings();
      set({
        settings,
        isSaving: false,
        isBackendConnected: true,
        error: null,
        successMessage: 'Personality settings restored to defaults.',
      });
      return true;

    } catch (err: unknown) {
      set({
        isSaving: false,
        error: getErrorMessage(
          err,
          'Personality settings could not be reset.'
        ),
        successMessage: null,
      });
      return false;
    }
  },

  clearFeedback: () => set({ error: null, successMessage: null }),
}));

export default usePersonalityStore;
