import apiClient from './client';
import type {
  VADFrameResponse,
  VADSessionResponse,
  VADStatusResponse,
} from '../../types/api.types';

/** Silero VAD requires exactly 512 samples per 16 kHz frame. */
export const VAD_SAMPLE_RATE = 16000;
export const VAD_FRAME_SAMPLES = 512;

/** One HTTP batch is 8 frames (0.256 s) for responsive speech and barge-in. */
export const VAD_BATCH_FRAMES = 8;
export const VAD_BATCH_SAMPLES = VAD_FRAME_SAMPLES * VAD_BATCH_FRAMES;
export const VAD_TIMEOUT_MS = 15000;

/**
 * Encode signed 16-bit mono PCM as an in-memory WAV buffer.
 *
 * The backend validates this explicit 16 kHz PCM contract. Compressed
 * MediaRecorder containers are never passed to the VAD model.
 */
export function encodeVadWav(samples: Int16Array): ArrayBuffer {
  const bytes = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(bytes);

  const writeAscii = (offset: number, value: string): void => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeAscii(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, 'WAVE');
  writeAscii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, VAD_SAMPLE_RATE, true);
  view.setUint32(28, VAD_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(44 + index * 2, samples[index] ?? 0, true);
  }

  return bytes;
}

/**
 * Local VAD endpoints. Audio is sent only while an explicit recording is
 * active; no audio or transcript is persisted by this client.
 */
export const vadApi = {
  async getStatus(): Promise<VADStatusResponse> {
    const response = await apiClient.get<VADStatusResponse>('/api/vad/status');
    return response.data;
  },

  async createSession(): Promise<VADSessionResponse> {
    const response = await apiClient.post<VADSessionResponse>(
      '/api/vad/sessions',
      null,
      { timeout: VAD_TIMEOUT_MS },
    );
    return response.data;
  },

  async postFrames(
    sessionId: string,
    samples: Int16Array,
  ): Promise<VADFrameResponse> {
    const wav = encodeVadWav(samples);
    const form = new FormData();
    form.append(
      'file',
      new Blob([wav], { type: 'audio/wav' }),
      'vad-frame.wav',
    );

    // Let the browser attach the multipart boundary instead of reusing the
    // JSON Content-Type configured on the shared axios client.
    const response = await apiClient.post<VADFrameResponse>(
      `/api/vad/sessions/${encodeURIComponent(sessionId)}/frames`,
      form,
      {
        headers: { 'Content-Type': undefined },
        timeout: VAD_TIMEOUT_MS,
      },
    );
    return response.data;
  },

  async deleteSession(sessionId: string): Promise<void> {
    await apiClient.delete(
      `/api/vad/sessions/${encodeURIComponent(sessionId)}`,
      { timeout: VAD_TIMEOUT_MS },
    );
  },

  /**
   * Explicit Stage 2 integration boundary. This returns the segment as a
   * 16 kHz mono WAV; the UI never calls it automatically.
   */
  async getSegmentWav(sessionId: string, segmentId: string): Promise<ArrayBuffer> {
    const response = await apiClient.get<ArrayBuffer>(
      `/api/vad/sessions/${encodeURIComponent(sessionId)}/segments/${encodeURIComponent(segmentId)}/wav`,
      { responseType: 'arraybuffer', timeout: VAD_TIMEOUT_MS },
    );
    return response.data;
  },
};
