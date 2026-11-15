import apiClient from './client';
import type { ChatRequest, ChatResponse } from '../../types/api.types';

export const chatApi = {
  async sendSync(request: ChatRequest): Promise<ChatResponse> {
    const response = await apiClient.post<ChatResponse>('/api/chat/', request);
    return response.data;
  },

  async approveConfirmation(confirmationId: number): Promise<ChatResponse> {
    const response = await apiClient.post<ChatResponse>(
      `/api/chat/confirmations/${confirmationId}/approve`
    );
    return response.data;
  },
};

export default chatApi;
