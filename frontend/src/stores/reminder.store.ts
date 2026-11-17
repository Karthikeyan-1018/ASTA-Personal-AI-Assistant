import { create } from 'zustand';
import type {
  ReminderItem,
  ReminderCategory,
  ReminderStatus,
} from '../types/reminder.types';
import type {
  BackendReminderStatus,
  ReminderCreate,
  ReminderResponse,
  ReminderUpdate,
} from '../types/api.types';
import type { TaskCategory } from '../types/task.types';
import { remindersApi } from '../services/api/reminders.api';
import {
  backendInstantToLocalParts,
  localDateString,
} from '../utils/reminderTime';
import { getTaskReminderEligibility } from '../utils/reminderEligibility';
import { useTaskStore } from './task.store';

const KNOWN_REMINDER_STATUSES: ReminderStatus[] = [
  'pending',
  'processing',
  'delivered',
  'cancelled',
  'suppressed',
  'failed',
];

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

function mapBackendStatus(status: BackendReminderStatus): ReminderStatus {
  return KNOWN_REMINDER_STATUSES.includes(status as ReminderStatus)
    ? (status as ReminderStatus)
    : 'failed';
}

function mapTaskCategory(category?: TaskCategory): ReminderCategory {
  switch (category) {
    case 'System':
      return 'System';
    case 'Memory':
      return 'Maintenance';
    case 'Security':
    case 'Development':
    case 'Research':
      return 'Workflow';
    default:
      return 'Personal';
  }
}

function mapBackendReminder(r: ReminderResponse): ReminderItem {
  const tasks = useTaskStore.getState().tasks;
  const task = tasks.find(
    (t) => t.backendId === r.task_id || t.id === String(r.task_id)
  );

  let date = localDateString();
  let time = '00:00';

  try {
    const local = backendInstantToLocalParts(r.remind_at);
    date = local.date;
    time = local.time;
  } catch {
    // Keep a readable fallback instead of dropping a confirmed record.
  }

  return {
    id: `rem-backend-${r.id}`,
    backendId: r.id,
    taskId: r.task_id,
    // The backend stores no reminder title; it is resolved from the task.
    title: task?.title || `Task #${r.task_id}`,
    description: task?.description || undefined,
    date,
    time,
    remindAt: r.remind_at,
    category: mapTaskCategory(task?.category),
    frequency: 'once',
    status: mapBackendStatus(r.status),
    offsetMinutes: r.offset_minutes,
    createdAt: r.created_at || new Date().toISOString(),
    notificationChannel: 'desktop',
  };
}

function assertSingleSchedulingSource(
  remindAt?: string,
  offsetMinutes?: number
): void {
  const hasInstant = typeof remindAt === 'string' && remindAt.trim().length > 0;
  const hasOffset =
    typeof offsetMinutes === 'number' && Number.isFinite(offsetMinutes);

  if (hasInstant === hasOffset) {
    throw new Error('Provide exactly one of remind_at or offset_minutes.');
  }

  if (hasInstant && !/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(remindAt!.trim())) {
    throw new Error('remind_at must include a timezone offset.');
  }

  if (hasOffset && (offsetMinutes! < 0 || offsetMinutes! > 525600)) {
    throw new Error('offset_minutes must be between 0 and 525600.');
  }
}

export interface CreateReminderInput {
  taskId: number;
  /** Absolute instant with an explicit offset (produced from local wall time). */
  remindAt?: string;
  /** Minutes before the task deadline. Mutually exclusive with remindAt. */
  offsetMinutes?: number;
}

export interface RescheduleReminderInput {
  remindAt?: string;
  offsetMinutes?: number;
}

interface ReminderStoreState {
  reminders: ReminderItem[];
  isLoading: boolean;
  isCreating: boolean;
  mutatingIds: string[];
  error: string | null;
  isBackendConnected: boolean;
  fetchReminders: () => Promise<void>;
  createReminder: (input: CreateReminderInput) => Promise<boolean>;
  rescheduleReminder: (
    id: string,
    input: RescheduleReminderInput
  ) => Promise<boolean>;
  cancelReminder: (id: string) => Promise<boolean>;
  deleteReminder: (id: string) => Promise<boolean>;
}

export const useReminderStore = create<ReminderStoreState>((set, get) => {
  const finishMutation = (id: string) =>
    set((state) => ({
      mutatingIds: state.mutatingIds.filter((value) => value !== id),
    }));

  return {
    reminders: [],
    isLoading: false,
    isCreating: false,
    mutatingIds: [],
    error: null,
    isBackendConnected: false,

    fetchReminders: async () => {
      set({ isLoading: true, error: null });
      try {
        const backendReminders = await remindersApi.list({ limit: 100 });
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

    createReminder: async (input) => {
      const task = useTaskStore
        .getState()
        .tasks.find((t) => t.backendId === input.taskId);

      if (!task) {
        set({ error: 'Select a saved task before creating a reminder.' });
        return false;
      }

      // Eligibility is derived from the last backend-confirmed task status,
      // not from an optimistic display value.
      const eligibility = getTaskReminderEligibility(task);

      if (!eligibility.eligible) {
        set({
          error:
            eligibility.message ||
            'Reminders can only be scheduled for pending or in-progress tasks.',
        });
        return false;
      }

      let payload: ReminderCreate;

      try {
        assertSingleSchedulingSource(input.remindAt, input.offsetMinutes);
        payload = input.remindAt
          ? { remind_at: input.remindAt.trim() }
          : { offset_minutes: input.offsetMinutes };
      } catch (err: unknown) {
        set({
          error: getErrorMessage(
            err,
            'Reminder schedule is invalid. No changes were saved.'
          ),
        });
        return false;
      }

      // Creation waits for the server because offset-based reminders resolve
      // against the task timezone on the backend. A failed request therefore
      // leaves the last confirmed list untouched.
      const previousReminders = get().reminders;
      set({ isCreating: true, error: null });

      try {
        const created = await remindersApi.create(input.taskId, payload);
        const mapped = mapBackendReminder(created);

        set((state) => ({
          reminders: [mapped, ...state.reminders],
          isCreating: false,
          isBackendConnected: true,
          error: null,
        }));
        return true;
      } catch (err: unknown) {
        set({
          reminders: previousReminders,
          isCreating: false,
          error: getErrorMessage(
            err,
            'Reminder could not be created. No changes were saved.'
          ),
        });
        return false;
      }
    },

    rescheduleReminder: async (id, input) => {
      const previousReminders = get().reminders;
      const target = previousReminders.find((r) => r.id === id);

      if (!target) return false;

      if (!target.backendId) {
        set({
          error:
            'This reminder is not confirmed by the server yet. It was not rescheduled.',
        });
        return false;
      }

      if (target.status !== 'pending') {
        set({
          error: 'Only pending reminders can be rescheduled. Create a new reminder instead.',
        });
        return false;
      }

      // The backend also revalidates the linked task. Checking the confirmed
      // client state prevents an avoidable request and an ineligible update.
      const linkedTask = useTaskStore
        .getState()
        .tasks.find(
          (task) =>
            target.taskId !== undefined &&
            (task.backendId === target.taskId ||
              task.id === String(target.taskId))
        );

      if (!linkedTask) {
        set({
          error:
            'The linked task is not available. Refresh tasks before rescheduling.',
        });
        return false;
      }

      const eligibility = getTaskReminderEligibility(linkedTask);

      if (!eligibility.eligible) {
        set({
          error:
            `This reminder cannot be rescheduled. ${eligibility.message || ''}`.trim(),
        });
        return false;
      }

      let payload: ReminderUpdate;

      try {
        assertSingleSchedulingSource(input.remindAt, input.offsetMinutes);
        payload = input.remindAt
          ? { remind_at: input.remindAt.trim() }
          : { offset_minutes: input.offsetMinutes };
      } catch (err: unknown) {
        set({
          error: getErrorMessage(
            err,
            'Reminder schedule is invalid. Previous values were restored.'
          ),
        });
        return false;
      }

      const optimisticReminders = input.remindAt
        ? previousReminders.map((r) =>
            r.id === id
              ? {
                  ...r,
                  ...backendInstantToLocalParts(input.remindAt!),
                  remindAt: input.remindAt!.trim(),
                }
              : r
          )
        : previousReminders;

      set((state) => ({
        reminders: optimisticReminders,
        mutatingIds: [...state.mutatingIds, id],
        error: null,
      }));

      try {
        const updated = await remindersApi.reschedule(target.backendId, payload);

        set((state) => ({
          reminders: state.reminders.map((r) =>
            r.id === id ? mapBackendReminder(updated) : r
          ),
          isBackendConnected: true,
          error: null,
        }));
        return true;
      } catch (err: unknown) {
        set({
          reminders: previousReminders,
          error: getErrorMessage(
            err,
            'Reminder could not be rescheduled. Previous values were restored.'
          ),
        });
        return false;
      } finally {
        finishMutation(id);
      }
    },

    cancelReminder: async (id) => {
      const previousReminders = get().reminders;
      const target = previousReminders.find((r) => r.id === id);

      if (!target) return false;

      if (!target.backendId) {
        set({
          error:
            'This reminder is not confirmed by the server yet. It was not cancelled.',
        });
        return false;
      }

      if (target.status === 'cancelled') {
        set({ error: 'This reminder is already cancelled. Create a new reminder instead.' });
        return false;
      }

      if (target.status !== 'pending') {
        set({
          error:
            'Only pending reminders can be cancelled. Create a new reminder instead.',
        });
        return false;
      }

      // Cancellation is terminal: there is no reactivation path.
      set((state) => ({
        reminders: state.reminders.map((r) =>
          r.id === id ? { ...r, status: 'cancelled' } : r
        ),
        mutatingIds: [...state.mutatingIds, id],
        error: null,
      }));

      try {
        const cancelled = await remindersApi.cancel(target.backendId);

        set((state) => ({
          reminders: state.reminders.map((r) =>
            r.id === id ? mapBackendReminder(cancelled) : r
          ),
          isBackendConnected: true,
          error: null,
        }));
        return true;
      } catch (err: unknown) {
        set({
          reminders: previousReminders,
          error: getErrorMessage(
            err,
            'Reminder could not be cancelled. It was restored.'
          ),
        });
        return false;
      } finally {
        finishMutation(id);
      }
    },

    deleteReminder: async (id) => {
      const previousReminders = get().reminders;
      const target = previousReminders.find((r) => r.id === id);

      if (!target) return false;

      if (!target.backendId) {
        set({
          error:
            'This reminder is not confirmed by the server yet. It was not deleted.',
        });
        return false;
      }

      set((state) => ({
        reminders: state.reminders.filter((r) => r.id !== id),
        mutatingIds: [...state.mutatingIds, id],
        error: null,
      }));

      try {
        await remindersApi.delete(target.backendId);
        set({ isBackendConnected: true, error: null });
        return true;
      } catch (err: unknown) {
        set({
          reminders: previousReminders,
          error: getErrorMessage(
            err,
            'Reminder could not be deleted. It was restored.'
          ),
        });
        return false;
      } finally {
        finishMutation(id);
      }
    },
  };
});

export default useReminderStore;
