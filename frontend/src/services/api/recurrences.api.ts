import apiClient from './client';
import type {
  RecurrenceCreate,
  RecurrenceListQueryParams,
  RecurrenceOccurrenceQueryParams,
  RecurrenceOccurrenceResponse,
  RecurrenceResponse,
  RecurrenceUpdate,
} from '../../types/api.types';

/**
 * Owner-scoped recurrence endpoints.
 *
 * `DELETE` deactivates the rule; it never removes the rule, the template task,
 * generated tasks, or occurrence history. All mutating calls rely on the shared
 * Axios client for session cookies and CSRF injection.
 */
export const recurrencesApi = {
  async createForTask(
    taskId: number,
    payload: RecurrenceCreate
  ): Promise<RecurrenceResponse> {
    const response = await apiClient.post<RecurrenceResponse>(
      `/api/tasks/${taskId}/recurrence`,
      payload
    );
    return response.data;
  },

  async getForTask(taskId: number): Promise<RecurrenceResponse> {
    const response = await apiClient.get<RecurrenceResponse>(
      `/api/tasks/${taskId}/recurrence`
    );
    return response.data;
  },

  async list(
    params?: RecurrenceListQueryParams
  ): Promise<RecurrenceResponse[]> {
    const response = await apiClient.get<RecurrenceResponse[]>(
      '/api/recurrences',
      { params }
    );
    return response.data;
  },

  async get(recurrenceId: number): Promise<RecurrenceResponse> {
    const response = await apiClient.get<RecurrenceResponse>(
      `/api/recurrences/${recurrenceId}`
    );
    return response.data;
  },

  async update(
    recurrenceId: number,
    payload: RecurrenceUpdate
  ): Promise<RecurrenceResponse> {
    const response = await apiClient.patch<RecurrenceResponse>(
      `/api/recurrences/${recurrenceId}`,
      payload
    );
    return response.data;
  },

  async deactivate(recurrenceId: number): Promise<void> {
    await apiClient.delete(`/api/recurrences/${recurrenceId}`);
  },

  async listOccurrences(
    recurrenceId: number,
    params?: RecurrenceOccurrenceQueryParams
  ): Promise<RecurrenceOccurrenceResponse[]> {
    const response = await apiClient.get<RecurrenceOccurrenceResponse[]>(
      `/api/recurrences/${recurrenceId}/occurrences`,
      { params }
    );
    return response.data;
  },
};

export default recurrencesApi;
