export interface ScheduledReminder {
  id: string;
  taskId: string;
  title: string;
  scheduledTime: string; // ISO
  delivered: boolean;
}

class NotificationService {
  private hasPermission = true;
  private reminders: ScheduledReminder[] = [];

  async requestPermission(): Promise<boolean> {
    // In Android companion apps, permission can be checked and requested
    this.hasPermission = true;
    return this.hasPermission;
  }

  async scheduleTaskReminder(taskId: string, title: string, triggerIso: string): Promise<ScheduledReminder> {
    const reminder: ScheduledReminder = {
      id: `rem-${Date.now()}-${Math.random().toString(36).slice(2, 5)}`,
      taskId,
      title,
      scheduledTime: triggerIso,
      delivered: false,
    };
    this.reminders.push(reminder);
    return reminder;
  }

  async getScheduledReminders(): Promise<ScheduledReminder[]> {
    return [...this.reminders];
  }

  async cancelReminder(id: string): Promise<void> {
    this.reminders = this.reminders.filter((r) => r.id !== id);
  }
}

export const notificationService = new NotificationService();
