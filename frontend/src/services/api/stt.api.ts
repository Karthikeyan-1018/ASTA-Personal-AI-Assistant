import apiClient from './client';
import type { STTStatusResponse, STTTranscribeResponse } from '../../types/api.types';

/** Preferred MediaRecorder containers, in descending compatibility order. */
export const STT_RECORDING_MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
] as const;

export const STT_UPLOAD_TIMEOUT_MS = 60000;

function extensionForMime(mimeType: string): string {
  const normalized = mimeType.toLowerCase();
  if (normalized.includes('ogg')) return 'ogg';
  if (normalized.includes('mp4') || normalized.includes('m4a')) return 'm4a';
  if (normalized.includes('webm')) return 'webm';
  return 'wav';
}

/**
 * Local speech-to-text endpoints.
 *
 * Audio is sent only after the user explicitly stops a recording. The blob is
 * held in memory for that one request and is never persisted by this client.
 */
export const sttApi = {
  async getStatus(): Promise<STTStatusResponse> {
    const response = await apiClient.get<STTStatusResponse>('/api/stt/status');
    return response.data;
  },

  async transcribe(blob: Blob, mimeType: string): Promise<STTTranscribeResponse> {
    const formData = new FormData();
    formData.append('file', blob, `recording.${extensionForMime(mimeType)}`);

    // Let the browser attach the multipart boundary instead of reusing the
    // JSON Content-Type configured on the shared axios client.
    const response = await apiClient.post<STTTranscribeResponse>(
      '/api/stt/transcribe',
      formData,
      {
        headers: { 'Content-Type': undefined },
        timeout: STT_UPLOAD_TIMEOUT_MS,
      },
    );

    return response.data;
  },
};
