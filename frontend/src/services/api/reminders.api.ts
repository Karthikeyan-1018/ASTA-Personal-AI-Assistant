import apiClient from './client';
import type {
  ReminderCreate,
  ReminderResponse,
  ReminderUpdate,
} from '../../types/api.types';

export interface ReminderListQueryParams {
  task_id?: number;
  status?: string;
  limit?: number;
  offset?: number;
}

export const remindersApi = {
  async list(params?: ReminderListQueryParams): Promise<ReminderResponse[]> {
    const response = await apiClient.get<ReminderResponse[]>('/api/reminders', {
      params,
    });
    return response.data;
  },

  async listForTask(
    taskId: number,
    statusFilter?: string
  ): Promise<ReminderResponse[]> {
    const response = await apiClient.get<ReminderResponse[]>(
      `/api/tasks/${taskId}/reminders`,
      {
        params: statusFilter ? { status: statusFilter } : undefined,
      }
    );
    return response.data;
  },

  async create(
    taskId: number,
    payload: ReminderCreate
  ): Promise<ReminderResponse> {
    const response = await apiClient.post<ReminderResponse>(
      `/api/tasks/${taskId}/reminders`,
      payload
    );
    return response.data;
  },

  async reschedule(
    reminderId: number,
    payload: ReminderUpdate
  ): Promise<ReminderResponse> {
    const response = await apiClient.patch<ReminderResponse>(
      `/api/reminders/${reminderId}`,
      payload
    );
    return response.data;
  },

  async cancel(reminderId: number): Promise<ReminderResponse> {
    const response = await apiClient.post<ReminderResponse>(
      `/api/reminders/${reminderId}/cancel`
    );
    return response.data;
  },

  async delete(reminderId: number): Promise<void> {
    await apiClient.delete(`/api/reminders/${reminderId}`);
  },
};

export default remindersApi;
