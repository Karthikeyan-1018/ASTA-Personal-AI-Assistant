/**
 * Frontend notification models.
 *
 * Backend notifications are the only source of truth. The panel renders
 * confirmed records returned by `/api/notifications`, and the unread badge is
 * derived from `/api/notifications/unread` because the backend exposes no
 * unread-count endpoint.
 */

export type NotificationDeliveryStatus = 'pending' | 'delivered' | 'failed';
export type NotificationType = 'reminder' | 'system';

export interface NotificationItem {
  /** Backend notification id. */
  id: number;
  type: NotificationType;
  deliveryStatus: NotificationDeliveryStatus;
  title: string;
  message: string;
  taskId: number | null;
  reminderId: number | null;
  taskTitle: string | null;
  reminderAt: string | null;
  isRead: boolean;
  readAt: string | null;
  snoozedAt: string | null;
  snoozedUntil: string | null;
  snoozeCount: number;
  deliveredAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface NotificationToast {
  /** Dedupe key: notification id plus delivery marker. */
  key: string;
  notificationId: number;
  type: NotificationType;
  title: string;
  message: string;
  createdAt: string;
}

/** Maximum page size supported by the backend notification routes. */
export const NOTIFICATION_PAGE_LIMIT = 100;

/** Default polling interval for new notifications. */
export const NOTIFICATION_POLL_INTERVAL_MS = 30_000;

export interface NotificationPreferences {
  id: number | null;
  userId: number;
  inAppEnabled: boolean;
  quietHoursEnabled: boolean;
  /** Local wall-clock HH:MM, never an instant. */
  quietHoursStart: string;
  /** Local wall-clock HH:MM, never an instant. */
  quietHoursEnd: string;
  timezone: string;
  quietHoursActive: boolean;
  quietHoursEndsAt: string | null;
}

/** Editable subset of notification preferences. */
export interface NotificationPreferenceDraft {
  inAppEnabled: boolean;
  quietHoursEnabled: boolean;
  quietHoursStart: string;
  quietHoursEnd: string;
  timezone: string;
}
