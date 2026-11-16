import { create } from 'zustand';
import type { ReminderItem } from '../types/reminder.types';
import type { ReminderResponse } from '../types/api.types';
import { remindersApi } from '../services/api/reminders.api';
import { useTaskStore } from './task.store';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

function mapBackendReminder(r: ReminderResponse): ReminderItem {
  const tasks = useTaskStore.getState().tasks;
  const task = tasks.find(
    (t) => t.backendId === r.task_id || t.id === String(r.task_id)
  );

  let dateStr = new Date().toISOString().split('T')[0];
  let timeStr = '12:00';

  if (r.remind_at) {
    try {
      const d = new Date(r.remind_at);
      if (!isNaN(d.getTime())) {
        dateStr = d.toISOString().split('T')[0];
        timeStr = d.toTimeString().substring(0, 5);
      }
    } catch {
      // Keep defaults
    }
  }

  return {
    id: `rem-backend-${r.id}`,
    backendId: r.id,
    taskId: r.task_id,
    title: task?.title || `Task #${r.task_id} Reminder`,
    description: task?.description || undefined,
    date: dateStr,
    time: timeStr,
    category: (task?.category as any) || 'Personal',
    frequency: 'once',
    active: r.status === 'pending',
    completed: r.status === 'delivered',
    createdAt: r.created_at || new Date().toISOString(),
    notificationChannel: 'desktop',
  };
}

interface ReminderStoreState {
  reminders: ReminderItem[];
  isLoading: boolean;
  error: string | null;
  isBackendConnected: boolean;
  fetchReminders: () => Promise<void>;
  addReminder: (reminder: Omit<ReminderItem, 'id' | 'createdAt' | 'completed'>) => Promise<void>;
  updateReminder: (id: string, updates: Partial<ReminderItem>) => Promise<void>;
  deleteReminder: (id: string) => Promise<void>;
  toggleReminderActive: (id: string) => Promise<void>;
  toggleReminderCompleted: (id: string) => Promise<void>;
}

export const useReminderStore = create<ReminderStoreState>((set, get) => ({
  reminders: [],
  isLoading: false,
  error: null,
  isBackendConnected: false,

  fetchReminders: async () => {
    set({ isLoading: true, error: null });
    try {
      const backendReminders = await remindersApi.list();
      const mapped = Array.isArray(backendReminders)
        ? backendReminders.map(mapBackendReminder)
        : [];
      // A successful empty response is authoritative: clear any previously
      // displayed records instead of falling back to stale or mock data.
      set({
        reminders: mapped,
        isBackendConnected: true,
        isLoading: false,
        error: null,
      });
    } catch (err: unknown) {
      // Keep the last confirmed reminder list intact when the request fails.
      set({
        isBackendConnected: false,
        isLoading: false,
        error: getErrorMessage(
          err,
          'Could not load reminders. Showing the last confirmed data.'
        ),
      });
    }
  },

  addReminder: async (data) => {
    const previousReminders = get().reminders;
    const tempId = `rem-${Date.now()}`;
    const optimisticReminder: ReminderItem = {
      ...data,
      id: tempId,
      createdAt: new Date().toISOString(),
      completed: false,
    };

    set((state) => ({
      reminders: [optimisticReminder, ...state.reminders],
      error: null,
    }));

    try {
      const tasks = useTaskStore.getState().tasks;
      const targetTask =
        tasks.find((t) => t.title.toLowerCase() === data.title.toLowerCase()) ||
        tasks.find((t) => t.backendId !== undefined) ||
        tasks[0];

      if (!targetTask?.backendId) {
        throw new Error('No saved task is available to attach this reminder to.');
      }

      const remindAtIso = `${data.date}T${data.time}:00Z`;
      const res = await remindersApi.create(targetTask.backendId, {
        remind_at: remindAtIso,
      });

      set((state) => ({
        reminders: state.reminders.map((r) =>
          r.id === tempId
            ? {
                ...r,
                id: `rem-backend-${res.id}`,
                backendId: res.id,
                taskId: res.task_id,
              }
            : r
        ),
        error: null,
      }));
    } catch (err: unknown) {
      // The optimistic entry was never confirmed by the server, so remove it
      // and restore the last confirmed list.
      set({
        reminders: previousReminders,
        error: getErrorMessage(err, 'Reminder could not be created. No changes were saved.'),
      });
    }
  },

  updateReminder: async (id, updates) => {
    const previousReminders = get().reminders;
    const current = get().reminders.find((r) => r.id === id);
    set((state) => ({
      reminders: state.reminders.map((r) => (r.id === id ? { ...r, ...updates } : r)),
      error: null,
    }));

    if (current?.backendId && (updates.date || updates.time)) {
      try {
        const nextDate = updates.date || current.date;
        const nextTime = updates.time || current.time;
        const remindAtIso = `${nextDate}T${nextTime}:00Z`;
        await remindersApi.reschedule(current.backendId, {
          remind_at: remindAtIso,
        });
      } catch (err: unknown) {
        set({
          reminders: previousReminders,
          error: getErrorMessage(
            err,
            'Reminder could not be rescheduled. Previous values were restored.'
          ),
        });
      }
    }
  },

  deleteReminder: async (id) => {
    const previousReminders = get().reminders;
    const target = get().reminders.find((r) => r.id === id);

    if (!target?.backendId) {
      set({ error: 'This reminder is not confirmed by the server yet. It was not deleted.' });
      return;
    }

    set((state) => ({
      reminders: state.reminders.filter((r) => r.id !== id),
      error: null,
    }));

    try {
      await remindersApi.delete(target.backendId);
    } catch (err: unknown) {
      set({
        reminders: previousReminders,
        error: getErrorMessage(err, 'Reminder could not be deleted. It was restored.'),
      });
    }
  },

  toggleReminderActive: async (id) => {
    const target = get().reminders.find((r) => r.id === id);
    if (!target) return;

    const previousReminders = get().reminders;
    if (!target.backendId) {
      set({ error: 'This reminder is not confirmed by the server yet. It was not changed.' });
      return;
    }

    const nextActive = !target.active;
    set((state) => ({
      reminders: state.reminders.map((r) =>
        r.id === id ? { ...r, active: nextActive } : r
      ),
      error: null,
    }));

    try {
      if (!nextActive) {
        await remindersApi.cancel(target.backendId);
      } else {
        const remindAtIso = `${target.date}T${target.time}:00Z`;
        await remindersApi.reschedule(target.backendId, {
          remind_at: remindAtIso,
        });
      }
    } catch (err: unknown) {
      set({
        reminders: previousReminders,
        error: getErrorMessage(
          err,
          'Reminder status could not be changed. It was restored.'
        ),
      });
    }
  },

  toggleReminderCompleted: async (id) => {
    set((state) => ({
      reminders: state.reminders.map((r) =>
        r.id === id ? { ...r, completed: !r.completed } : r
      ),
    }));
  },
}));
