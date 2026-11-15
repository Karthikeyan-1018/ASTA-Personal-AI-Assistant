import apiClient from './client';
import type {
  ConversationCreate,
  ConversationResponse,
  ConversationUpdate,
  MessageListResponse,
} from '../../types/api.types';

export const conversationsApi = {
  async list(): Promise<ConversationResponse[]> {
    const response = await apiClient.get<ConversationResponse[]>('/api/conversations/');
    return response.data;
  },

  async create(payload: ConversationCreate = {}): Promise<ConversationResponse> {
    const response = await apiClient.post<ConversationResponse>('/api/conversations/', payload);
    return response.data;
  },

  async get(id: number): Promise<ConversationResponse> {
    const response = await apiClient.get<ConversationResponse>(`/api/conversations/${id}`);
    return response.data;
  },

  async updateTitle(id: number, title: string): Promise<ConversationResponse> {
    const payload: ConversationUpdate = { title };
    const response = await apiClient.patch<ConversationResponse>(
      `/api/conversations/${id}`,
      payload
    );
    return response.data;
  },

  async delete(id: number): Promise<void> {
    await apiClient.delete(`/api/conversations/${id}`);
  },

  async listMessages(
    conversationId: number,
    limit = 50,
    offset = 0
  ): Promise<MessageListResponse> {
    const response = await apiClient.get<MessageListResponse>(
      `/api/conversations/${conversationId}/messages`,
      {
        params: { limit, offset },
      }
    );
    return response.data;
  },
};

export default conversationsApi;
