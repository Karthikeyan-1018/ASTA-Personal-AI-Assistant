import { create } from 'zustand';
import type {
  NotificationPreferenceDraft,
  NotificationPreferences,
} from '../types/notification.types';
import { notificationsApi } from '../services/api/notifications.api';
import {
  buildNotificationPreferencePatch,
  mapNotificationPreferences,
} from '../utils/notificationPreferences';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

interface NotificationPreferenceStoreState {
  preferences: NotificationPreferences | null;
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  successMessage: string | null;
  isBackendConnected: boolean;
  fetchPreferences: () => Promise<boolean>;
  savePreferences: (draft: NotificationPreferenceDraft) => Promise<boolean>;
  clearFeedback: () => void;
}

export const useNotificationPreferenceStore =
  create<NotificationPreferenceStoreState>((set, get) => ({
    preferences: null,
    isLoading: false,
    isSaving: false,
    error: null,
    successMessage: null,
    isBackendConnected: false,

    fetchPreferences: async () => {
      if (get().isLoading) return false;

      set({ isLoading: true, error: null });

      try {
        const response = await notificationsApi.getPreferences();
        set({
          preferences: mapNotificationPreferences(response),
          isLoading: false,
          isBackendConnected: true,
          error: null,
        });
        return true;
      } catch (err: unknown) {
        // Last confirmed preferences remain displayed.
        set({
          isLoading: false,
          isBackendConnected: false,
          error: getErrorMessage(
            err,
            'Could not load notification preferences. Showing the last confirmed settings.'
          ),
        });
        return false;
      }
    },

    savePreferences: async (draft) => {
      const state = get();

      if (state.isSaving) return false;

      if (!state.preferences) {
        set({
          error:
            'Notification preferences are not loaded yet. Retry before saving.',
        });
        return false;
      }

      const { patch, error } = buildNotificationPreferencePatch(
        state.preferences,
        draft
      );

      if (error) {
        set({ error, successMessage: null });
        return false;
      }

      if (Object.keys(patch).length === 0) {
        set({ error: null, successMessage: 'No changes to save.' });
        return true;
      }

      set({ isSaving: true, error: null, successMessage: null });

      try {
        const response = await notificationsApi.updatePreferences(patch);
        set({
          preferences: mapNotificationPreferences(response),
          isSaving: false,
          isBackendConnected: true,
          error: null,
          successMessage: 'Notification preferences saved.',
        });
        return true;
      } catch (err: unknown) {
        // Preserve the last confirmed preferences on failure.
        set({
          isSaving: false,
          error: getErrorMessage(
            err,
            'Notification preferences could not be saved. The last confirmed settings were kept.'
          ),
          successMessage: null,
        });
        return false;
      }
    },

    clearFeedback: () => set({ error: null, successMessage: null }),
  }));

export default useNotificationPreferenceStore;
