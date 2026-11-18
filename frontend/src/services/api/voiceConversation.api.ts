import apiClient from './client';
import type {
  VoiceConversationEvent,
  VoiceEventRequest,
  VoiceSessionResponse,
  VoiceStatusResponse,
  VoiceStopResponse,
  VoiceTurnResponse,
} from '../../types/api.types';

/**
 * Protected local voice conversation endpoints.
 *
 * Stage 4 submits only real VAD segment audio. The client observes the
 * backend-owned state and never supplies a transcript, an assistant response,
 * a conversation id, or a tool approval.
 */
export const VOICE_REQUEST_TIMEOUT_MS = 120000;

export const voiceConversationApi = {
  async getStatus(): Promise<VoiceStatusResponse> {
    const response = await apiClient.get<VoiceStatusResponse>(
      '/api/voice-conversations/status',
    );
    return response.data;
  },

  async createSession(): Promise<VoiceSessionResponse> {
    const response = await apiClient.post<VoiceSessionResponse>(
      '/api/voice-conversations/sessions',
      null,
      { timeout: VOICE_REQUEST_TIMEOUT_MS },
    );
    return response.data;
  },

  async getSession(sessionId: string): Promise<VoiceSessionResponse> {
    const response = await apiClient.get<VoiceSessionResponse>(
      `/api/voice-conversations/sessions/${encodeURIComponent(sessionId)}`,
    );
    return response.data;
  },

  async sendEvent(
    sessionId: string,
    event: VoiceConversationEvent,
    vadSessionId?: string | null,
  ): Promise<VoiceSessionResponse> {
    const payload: VoiceEventRequest = {
      event,
      vad_session_id: vadSessionId ?? null,
    };
    const response = await apiClient.post<VoiceSessionResponse>(
      `/api/voice-conversations/sessions/${encodeURIComponent(sessionId)}/events`,
      payload,
      { timeout: VOICE_REQUEST_TIMEOUT_MS },
    );
    return response.data;
  },

  /**
   * Hand one real Stage 3 VAD segment WAV to Stage 4. The audio is fetched
   * from VAD first because its in-memory segment is released with the session.
   */
  async submitTurn(
    sessionId: string,
    audio: ArrayBuffer,
    segmentId: string,
  ): Promise<VoiceTurnResponse> {
    const form = new FormData();
    form.append('file', new Blob([audio], { type: 'audio/wav' }), 'segment.wav');
    form.append('segment_id', segmentId);

    const response = await apiClient.post<VoiceTurnResponse>(
      `/api/voice-conversations/sessions/${encodeURIComponent(sessionId)}/turns`,
      form,
      {
        headers: { 'Content-Type': undefined },
        timeout: VOICE_REQUEST_TIMEOUT_MS,
      },
    );
    return response.data;
  },

  async stopSession(sessionId: string): Promise<VoiceStopResponse> {
    const response = await apiClient.post<VoiceStopResponse>(
      `/api/voice-conversations/sessions/${encodeURIComponent(sessionId)}/stop`,
      null,
      { timeout: VOICE_REQUEST_TIMEOUT_MS },
    );
    return response.data;
  },
};
