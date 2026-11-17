import apiClient from './client';
import type {
  NotificationListQueryParams,
  NotificationPreferenceResponse,
  NotificationPreferenceUpdate,
  NotificationReadAllResponse,
  NotificationResponse,
  NotificationSnoozeRequest,
  NotificationUnreadQueryParams,
} from '../../types/api.types';

/**
 * Backend notification and notification-preference endpoints.
 *
 * The backend worker is the only notification producer; this client never
 * creates notifications.
 */
export const notificationsApi = {
  async list(
    params?: NotificationListQueryParams
  ): Promise<NotificationResponse[]> {
    const response = await apiClient.get<NotificationResponse[]>(
      '/api/notifications',
      { params }
    );
    return Array.isArray(response.data) ? response.data : [];
  },

  async listUnread(
    params?: NotificationUnreadQueryParams
  ): Promise<NotificationResponse[]> {
    const response = await apiClient.get<NotificationResponse[]>(
      '/api/notifications/unread',
      { params }
    );
    return Array.isArray(response.data) ? response.data : [];
  },

  async markRead(notificationId: number): Promise<NotificationResponse> {
    const response = await apiClient.patch<NotificationResponse>(
      `/api/notifications/${notificationId}/read`
    );
    return response.data;
  },

  async markAllRead(): Promise<NotificationReadAllResponse> {
    const response = await apiClient.post<NotificationReadAllResponse>(
      '/api/notifications/read-all'
    );
    return response.data;
  },

  async snooze(
    notificationId: number,
    payload: NotificationSnoozeRequest
  ): Promise<NotificationResponse> {
    const response = await apiClient.post<NotificationResponse>(
      `/api/notifications/${notificationId}/snooze`,
      payload
    );
    return response.data;
  },

  async remove(notificationId: number): Promise<void> {
    await apiClient.delete(`/api/notifications/${notificationId}`);
  },

  async getPreferences(): Promise<NotificationPreferenceResponse> {
    const response = await apiClient.get<NotificationPreferenceResponse>(
      '/api/notifications/preferences'
    );
    return response.data;
  },

  async updatePreferences(
    payload: NotificationPreferenceUpdate
  ): Promise<NotificationPreferenceResponse> {
    const response = await apiClient.patch<NotificationPreferenceResponse>(
      '/api/notifications/preferences',
      payload
    );
    return response.data;
  },
};

export default notificationsApi;
