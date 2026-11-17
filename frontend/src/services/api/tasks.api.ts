import apiClient from './client';
import type {
  TaskCreate,
  TaskListResponse,
  TaskResponse,
  TaskUpdate,
} from '../../types/api.types';

export interface TaskListQueryParams {
  category?: string;
  priority?: string;
  status?: string;
  due_from?: string;
  due_to?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export const tasksApi = {
  async list(params?: TaskListQueryParams): Promise<TaskListResponse> {
    const response = await apiClient.get<TaskListResponse>('/api/tasks/', {
      params,
    });
    return response.data;
  },

  async create(payload: TaskCreate): Promise<TaskResponse> {
    const response = await apiClient.post<TaskResponse>('/api/tasks/', payload);
    return response.data;
  },

  async get(taskId: number): Promise<TaskResponse> {
    const response = await apiClient.get<TaskResponse>(`/api/tasks/${taskId}`);
    return response.data;
  },

  async update(taskId: number, payload: TaskUpdate): Promise<TaskResponse> {
    const response = await apiClient.patch<TaskResponse>(
      `/api/tasks/${taskId}`,
      payload
    );
    return response.data;
  },

  async delete(taskId: number): Promise<void> {
    await apiClient.delete(`/api/tasks/${taskId}`);
  },

  async complete(taskId: number): Promise<TaskResponse> {
    const response = await apiClient.post<TaskResponse>(
      `/api/tasks/${taskId}/complete`
    );
    return response.data;
  },

  async reopen(taskId: number): Promise<TaskResponse> {
    const response = await apiClient.post<TaskResponse>(
      `/api/tasks/${taskId}/reopen`
    );
    return response.data;
  },

  /**
   * Cancel through the backend's existing status-only endpoint. There is no
   * dedicated `/cancel` route; the backend task-intent executor uses the same
   * `change_status` service path for cancellation. Sending only `status` keeps
   * every unrelated task field unchanged.
   */
  async cancel(taskId: number): Promise<TaskResponse> {
    const response = await apiClient.post<TaskResponse>(
      `/api/tasks/${taskId}/status`,
      { status: 'cancelled' }
    );
    return response.data;
  },

  async getUpcoming(days?: number): Promise<TaskResponse[]> {
    const response = await apiClient.get<TaskResponse[]>('/api/tasks/upcoming', {
      params: days ? { days } : undefined,
    });
    return response.data;
  },

  async getToday(): Promise<TaskResponse[]> {
    const response = await apiClient.get<TaskResponse[]>('/api/tasks/today');
    return response.data;
  },

  async getOverdue(): Promise<TaskResponse[]> {
    const response = await apiClient.get<TaskResponse[]>('/api/tasks/overdue');
    return response.data;
  },

  async getCompleted(limit = 50, offset = 0): Promise<TaskListResponse> {
    const response = await apiClient.get<TaskListResponse>(
      '/api/tasks/completed',
      {
        params: { limit, offset },
      }
    );
    return response.data;
  },
};

export default tasksApi;
