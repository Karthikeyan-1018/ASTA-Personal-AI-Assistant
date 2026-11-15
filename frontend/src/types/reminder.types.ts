export type ReminderCategory = 'System' | 'Maintenance' | 'Personal' | 'Workflow' | 'Work';
export type ReminderFrequency = 'once' | 'daily' | 'weekly' | 'weekdays';

export interface ReminderItem {
  id: string;
  backendId?: number;
  taskId?: number;
  title: string;
  description?: string;
  time: string; // e.g. "14:30" or ISO
  date: string; // e.g. "2026-11-14"
  category: ReminderCategory;
  frequency: ReminderFrequency;
  active: boolean;
  completed: boolean;
  createdAt: string;
  notificationChannel?: 'desktop' | 'sound' | 'silent';
}
