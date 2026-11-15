import { create } from 'zustand';
import type { UserSettings } from '../types/settings.types';

const STORAGE_KEY = 'asta_desktop_user_settings_v1';

const DEFAULT_SETTINGS: UserSettings = {
  assistantName: 'ASTA',
  userDisplayName: 'Karthik',
  theme: 'obsidian',
  accentColor: 'indigo',
  compactMode: false,
  fontSize: 'md',
  defaultModel: 'Hybrid Router (Groq + Ollama)',
  streamingSpeed: 'fast',
  sendOnEnter: true,
  codeWrap: true,
  soundAlerts: false,
  desktopPush: true,
  retentionDays: 30,
  privateModeDefault: false,
  confirmElevatedTools: true,
  autoApproveReadOnly: true,
  auditLogging: true,
};

function loadStoredSettings(): UserSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

interface SettingsStoreState {
  settings: UserSettings;
  updateSettings: (partial: Partial<UserSettings>) => void;
  resetToDefaults: () => void;
}

export const useSettingsStore = create<SettingsStoreState>((set) => ({
  settings: loadStoredSettings(),
  updateSettings: (partial) =>
    set((state) => {
      const updated = { ...state.settings, ...partial };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      } catch {
        // LocalStorage fallback
      }
      return { settings: updated };
    }),
  resetToDefaults: () => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Ignored
    }
    set({ settings: DEFAULT_SETTINGS });
  },
}));
