import apiClient from './client';
import type {
  MemoryCreate,
  MemoryExtractionRequest,
  MemoryExtractionResponse,
  MemoryResponse,
  MemoryRetrievalRequest,
  MemoryRetrievalResponse,
  MemoryUpdate,
} from '../../types/api.types';

export const memoryApi = {
  async list(): Promise<MemoryResponse[]> {
    const response = await apiClient.get<MemoryResponse[]>('/api/memories/');
    return response.data;
  },

  async create(payload: MemoryCreate): Promise<MemoryResponse> {
    const response = await apiClient.post<MemoryResponse>('/api/memories/', payload);
    return response.data;
  },

  async retrieve(payload: MemoryRetrievalRequest): Promise<MemoryRetrievalResponse> {
    const response = await apiClient.post<MemoryRetrievalResponse>(
      '/api/memories/retrieve',
      payload
    );
    return response.data;
  },

  async get(id: number): Promise<MemoryResponse> {
    const response = await apiClient.get<MemoryResponse>(`/api/memories/${id}`);
    return response.data;
  },

  async update(id: number, payload: MemoryUpdate): Promise<MemoryResponse> {
    const response = await apiClient.put<MemoryResponse>(
      `/api/memories/${id}`,
      payload
    );
    return response.data;
  },

  async delete(id: number): Promise<void> {
    await apiClient.delete(`/api/memories/${id}`);
  },

  async approve(id: number): Promise<MemoryResponse> {
    const response = await apiClient.patch<MemoryResponse>(
      `/api/memories/${id}/approve`
    );
    return response.data;
  },

  async reject(id: number): Promise<MemoryResponse> {
    const response = await apiClient.patch<MemoryResponse>(
      `/api/memories/${id}/reject`
    );
    return response.data;
  },

  async extract(payload: MemoryExtractionRequest): Promise<MemoryExtractionResponse> {
    const response = await apiClient.post<MemoryExtractionResponse>(
      '/api/memories/extract',
      payload
    );
    return response.data;
  },
};

export default memoryApi;
