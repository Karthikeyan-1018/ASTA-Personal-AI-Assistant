import apiClient from './client';
import type {
  PersonalityMode,
  PersonalitySettings,
  PersonalitySettingsUpdate,
} from '../../types/personality.types';

interface BackendPersonalitySettings {
  assistant_name: string;
  mode: PersonalityMode;
  personality_traits: string[];
  motivational_philosophy: string;
  proactive_motivation_enabled: boolean;
  motivation_intensity: number;
  response_style: string;
  humor_enabled: boolean;
  has_overrides: boolean;
}

interface BackendPersonalityUpdate {
  mode?: PersonalityMode;
  proactive_motivation_enabled?: boolean;
  motivation_intensity?: number;
}

function mapSettings(
  response: BackendPersonalitySettings
): PersonalitySettings {
  return {
    assistantName: response.assistant_name,
    mode: response.mode,
    personalityTraits: Array.isArray(response.personality_traits)
      ? response.personality_traits
      : [],
    motivationalPhilosophy: response.motivational_philosophy,
    proactiveMotivationEnabled: response.proactive_motivation_enabled,
    motivationIntensity: response.motivation_intensity,
    responseStyle: response.response_style,
    humorEnabled: response.humor_enabled,
    hasOverrides: response.has_overrides,
  };
}

function mapUpdate(
  update: PersonalitySettingsUpdate
): BackendPersonalityUpdate {
  const payload: BackendPersonalityUpdate = {};

  if (update.mode !== undefined) payload.mode = update.mode;
  if (update.proactiveMotivationEnabled !== undefined) {
    payload.proactive_motivation_enabled = update.proactiveMotivationEnabled;
  }
  if (update.motivationIntensity !== undefined) {
    payload.motivation_intensity = update.motivationIntensity;
  }

  return payload;
}

/** Backend-backed personality controls; no local-only decorative settings. */
export const personalityApi = {
  async getSettings(): Promise<PersonalitySettings> {
    const response = await apiClient.get<BackendPersonalitySettings>(
      '/api/settings/personality'
    );
    return mapSettings(response.data);
  },

  async updateSettings(
    update: PersonalitySettingsUpdate
  ): Promise<PersonalitySettings> {
    const response = await apiClient.patch<BackendPersonalitySettings>(
      '/api/settings/personality',
      mapUpdate(update)
    );
    return mapSettings(response.data);
  },

  async resetSettings(): Promise<PersonalitySettings> {
    const response = await apiClient.post<BackendPersonalitySettings>(
      '/api/settings/personality/reset'
    );
    return mapSettings(response.data);
  },
};

export default personalityApi;
