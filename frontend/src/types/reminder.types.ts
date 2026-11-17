export type ReminderCategory = 'System' | 'Maintenance' | 'Personal' | 'Workflow' | 'Work';

/** Backend reminder lifecycle. Cancellation is terminal. */
export type ReminderStatus =
  | 'pending'
  | 'processing'
  | 'delivered'
  | 'cancelled'
  | 'suppressed'
  | 'failed';

export type ReminderFrequency = 'once';

export interface ReminderItem {
  id: string;
  backendId?: number;
  taskId?: number;
  /** Resolved from the associated task; the backend stores no reminder title. */
  title: string;
  description?: string;
  /** Local wall-clock time, e.g. "14:30". */
  time: string;
  /** Local calendar date, e.g. "2026-11-14". */
  date: string;
  /** Backend scheduling instant as returned by the API (UTC). */
  remindAt: string;
  category: ReminderCategory;
  frequency: ReminderFrequency;
  status: ReminderStatus;
  /** Present when the reminder was created from a task-deadline offset. */
  offsetMinutes?: number | null;
  createdAt: string;
  notificationChannel?: 'desktop' | 'sound' | 'silent';
}

/** Reminder states that can still fire, i.e. are not terminal. */
export const LIVE_REMINDER_STATUSES: ReminderStatus[] = [
  'pending',
  'processing',
];

export function isLiveReminder(status: ReminderStatus): boolean {
  return LIVE_REMINDER_STATUSES.includes(status);
}
