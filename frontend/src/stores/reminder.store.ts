import { create } from 'zustand';
import type { ReminderItem } from '../types/reminder.types';
import type { ReminderResponse } from '../types/api.types';
import { remindersApi } from '../services/api/reminders.api';
import { useTaskStore } from './task.store';

const INITIAL_REMINDERS: ReminderItem[] = [
  {
    id: 'rem-1',
    title: 'Daily ASTA System Diagnostic Snapshot',
    description: 'Verify CPU thread load, memory usage, and background queue health.',
    time: '20:00',
    date: new Date().toISOString().split('T')[0], // Today
    category: 'System',
    frequency: 'daily',
    active: true,
    completed: false,
    createdAt: '2026-11-14T08:00:00Z',
    notificationChannel: 'desktop',
  },
  {
    id: 'rem-2',
    title: 'Sync pgvector Semantic Embedding Index',
    description: 'Ensure newly drafted project notes have up-to-date vector representations.',
    time: '22:30',
    date: new Date().toISOString().split('T')[0], // Today
    category: 'Maintenance',
    frequency: 'daily',
    active: true,
    completed: false,
    createdAt: '2026-11-14T08:30:00Z',
    notificationChannel: 'desktop',
  },
  {
    id: 'rem-3',
    title: 'Weekly Knowledge Graph Integrity Check',
    description: 'Audit semantic memory associations and prune conflicting entity relations.',
    time: '09:00',
    date: '2026-11-16', // Upcoming
    category: 'Workflow',
    frequency: 'weekly',
    active: true,
    completed: false,
    createdAt: '2026-11-13T10:00:00Z',
    notificationChannel: 'sound',
  },
  {
    id: 'rem-4',
    title: 'Review Ollama Model Weights Update',
    description: 'Check for new quantized checkpoints of DeepSeek and Qwen coder models.',
    time: '15:00',
    date: '2026-11-18', // Upcoming
    category: 'Work',
    frequency: 'once',
    active: false,
    completed: false,
    createdAt: '2026-11-12T16:00:00Z',
    notificationChannel: 'desktop',
  },
  {
    id: 'rem-5',
    title: 'Morning Workspace Sync & Briefing',
    description: 'Review morning agenda and active desktop co-pilot priorities.',
    time: '08:30',
    date: new Date().toISOString().split('T')[0],
    category: 'Personal',
    frequency: 'weekdays',
    active: true,
    completed: true,
    createdAt: '2026-11-14T07:00:00Z',
    notificationChannel: 'desktop',
  },
];

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
  reminders: INITIAL_REMINDERS,
  isLoading: false,
  error: null,
  isBackendConnected: false,

  fetchReminders: async () => {
    set({ isLoading: true, error: null });
    try {
      const backendReminders = await remindersApi.list();
      if (Array.isArray(backendReminders)) {
        if (backendReminders.length > 0) {
          const mapped = backendReminders.map(mapBackendReminder);
          set({ reminders: mapped, isBackendConnected: true, isLoading: false });
        } else {
          set({ isBackendConnected: true, isLoading: false });
        }
      }
    } catch (err: any) {
      // Backend not yet populated or offline; keep local samples
      set({
        isBackendConnected: false,
        isLoading: false,
        error: err?.message || 'Could not connect to reminders service',
      });
    }
  },

  addReminder: async (data) => {
    const tempId = `rem-${Date.now()}`;
    const optimisticReminder: ReminderItem = {
      ...data,
      id: tempId,
      createdAt: new Date().toISOString(),
      completed: false,
    };

    set((state) => ({
      reminders: [optimisticReminder, ...state.reminders],
    }));

    try {
      const tasks = useTaskStore.getState().tasks;
      const targetTask =
        tasks.find((t) => t.title.toLowerCase() === data.title.toLowerCase()) ||
        tasks.find((t) => t.backendId !== undefined) ||
        tasks[0];

      if (targetTask?.backendId) {
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
        }));
      }
    } catch {
      // Keep optimistic entry on error
    }
  },

  updateReminder: async (id, updates) => {
    const current = get().reminders.find((r) => r.id === id);
    set((state) => ({
      reminders: state.reminders.map((r) => (r.id === id ? { ...r, ...updates } : r)),
    }));

    if (current?.backendId && (updates.date || updates.time)) {
      try {
        const nextDate = updates.date || current.date;
        const nextTime = updates.time || current.time;
        const remindAtIso = `${nextDate}T${nextTime}:00Z`;
        await remindersApi.reschedule(current.backendId, {
          remind_at: remindAtIso,
        });
      } catch {
        // Keep updated state
      }
    }
  },

  deleteReminder: async (id) => {
    const target = get().reminders.find((r) => r.id === id);
    set((state) => ({
      reminders: state.reminders.filter((r) => r.id !== id),
    }));

    if (target?.backendId) {
      try {
        await remindersApi.delete(target.backendId);
      } catch {
        // Handled
      }
    }
  },

  toggleReminderActive: async (id) => {
    const target = get().reminders.find((r) => r.id === id);
    if (!target) return;

    const nextActive = !target.active;
    set((state) => ({
      reminders: state.reminders.map((r) =>
        r.id === id ? { ...r, active: nextActive } : r
      ),
    }));

    if (target.backendId) {
      try {
        if (!nextActive) {
          await remindersApi.cancel(target.backendId);
        } else {
          const remindAtIso = `${target.date}T${target.time}:00Z`;
          await remindersApi.reschedule(target.backendId, {
            remind_at: remindAtIso,
          });
        }
      } catch {
        // Handled
      }
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
