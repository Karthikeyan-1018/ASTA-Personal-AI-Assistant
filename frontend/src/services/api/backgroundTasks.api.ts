import apiClient from './client';
import type {
  BackgroundTaskListQueryParams,
  BackgroundTaskListResponse,
  BackgroundTaskResponse,
} from '../../types/api.types';

/**
 * Owner-scoped background-task monitoring endpoints.
 *
 * The UI only monitors existing tasks. Task creation is deliberately absent:
 * the product flow that queues work owns submission. Mutating calls rely on the
 * shared Axios client for session cookies and CSRF injection.
 */
export const backgroundTasksApi = {
  async list(
    params?: BackgroundTaskListQueryParams
  ): Promise<BackgroundTaskListResponse> {
    const response = await apiClient.get<BackgroundTaskListResponse>(
      '/api/background-tasks/',
      { params }
    );
    return response.data;
  },

  async get(taskId: number): Promise<BackgroundTaskResponse> {
    const response = await apiClient.get<BackgroundTaskResponse>(
      `/api/background-tasks/${taskId}`
    );
    return response.data;
  },

  async cancel(taskId: number): Promise<BackgroundTaskResponse> {
    const response = await apiClient.post<BackgroundTaskResponse>(
      `/api/background-tasks/${taskId}/cancel`
    );
    return response.data;
  },
};

export default backgroundTasksApi;
