import apiClient from './client';
import type {
  TTSSettingsResponse,
  TTSSettingsUpdate,
  TTSStatusResponse,
  TTSSynthesizeRequest,
} from '../../types/api.types';

/**
 * Local Piper text-to-speech endpoints.
 *
 * Synthesis is always explicit: this client is called only from a Speak action
 * on an existing assistant message. The returned WAV blob is never persisted.
 */
export const ttsApi = {
  async getStatus(): Promise<TTSStatusResponse> {
    const response = await apiClient.get<TTSStatusResponse>('/api/tts/status');
    return response.data;
  },

  async getSettings(): Promise<TTSSettingsResponse> {
    const response = await apiClient.get<TTSSettingsResponse>('/api/tts/settings');
    return response.data;
  },

  async updateSettings(payload: TTSSettingsUpdate): Promise<TTSSettingsResponse> {
    const response = await apiClient.put<TTSSettingsResponse>(
      '/api/tts/settings',
      payload,
    );
    return response.data;
  },

  async synthesize(text: string): Promise<Blob> {
    const payload: TTSSynthesizeRequest = { text };
    const response = await apiClient.post<Blob>(
      '/api/tts/synthesize',
      payload,
      { responseType: 'blob' }
    );
    return response.data;
  },
};
