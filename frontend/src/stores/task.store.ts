import { create } from 'zustand';
import tasksApi from '../services/api/tasks.api';
import type { TaskItem, TaskFilterOptions, TaskPriority, TaskCategory } from '../types/task.types';
import type { BackendTaskCategory, BackendTaskPriority, TaskResponse } from '../types/api.types';
import {
  confirmedTaskStatus,
  isTaskCancellable,
  mapBackendTaskStatus,
  toBackendTaskStatus,
} from '../utils/taskStatus';

function mapBackendCategory(cat: BackendTaskCategory): TaskCategory {
  switch (cat) {
    case 'college':
    case 'career':
      return 'Development';
    case 'project':
      return 'Memory';
    case 'personal':
    case 'fitness':
      return 'General';
    default:
      return 'General';
  }
}

function mapFrontendCategory(cat: TaskCategory): BackendTaskCategory {
  switch (cat) {
    case 'Development':
      return 'career';
    case 'Memory':
      return 'project';
    case 'Security':
    case 'System':
      return 'project';
    case 'Research':
      return 'college';
    default:
      return 'other';
  }
}

function mapBackendPriority(p: BackendTaskPriority): TaskPriority {
  if (p === 'urgent') return 'critical';
  return p as TaskPriority;
}

function mapFrontendPriority(p: TaskPriority): BackendTaskPriority {
  if (p === 'critical') return 'urgent';
  return p as BackendTaskPriority;
}

function mapBackendTaskToItem(t: TaskResponse): TaskItem {
  // Exact, explicit mapping. Neither completed nor cancelled is ever coerced
  // into pending, and an unsupported value becomes an explicit unknown state.
  const status = mapBackendTaskStatus(t.status);

  return {
    id: String(t.id),
    backendId: t.id,
    backendStatus: status,
    title: t.title,
    description: t.description || undefined,
    category: mapBackendCategory(t.category),
    priority: mapBackendPriority(t.priority),
    status,
    completed: t.is_completed,
    dueDate: t.due_date || undefined,
    dueTime: t.due_time || undefined,
    createdAt: t.created_at,
    completedAt: t.completed_at || undefined,
  };
}

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

function toBackendTaskId(id: string): number | null {
  const parsed = Number(id);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function cancellationBlockReason(task: TaskItem): string {
  const status = confirmedTaskStatus(task);

  if (status === 'completed') return 'A completed task cannot be cancelled.';
  if (status === 'cancelled') return 'This task is already cancelled.';
  if (status === 'unknown') {
    return 'This task has an unrecognised status. Refresh tasks before cancelling.';
  }
  return 'Only a saved pending or in-progress task can be cancelled.';
}

interface TaskStoreState {
  tasks: TaskItem[];
  filter: TaskFilterOptions;
  isLoading: boolean;
  error: string | null;
  /** Task ids with an in-flight mutation. Prevents duplicate/conflicting writes. */
  mutatingIds: string[];
  /** Incremented on each confirmed mutation so stale list fetches are ignored. */
  revision: number;
  fetchTasks: () => Promise<void>;
  setFilter: (partial: Partial<TaskFilterOptions>) => void;
  resetFilter: () => void;
  addTask: (task: Omit<TaskItem, 'id' | 'createdAt' | 'completed'>) => Promise<void>;
  updateTask: (id: string, updates: Partial<TaskItem>) => Promise<void>;
  deleteTask: (id: string) => Promise<void>;
  toggleTaskCompleted: (id: string) => Promise<void>;
  cancelTask: (id: string) => Promise<boolean>;
  clearCompleted: () => void;
}

export const useTaskStore = create<TaskStoreState>((set, get) => {
  const beginMutation = (id: string) =>
    set((state) => ({
      mutatingIds: state.mutatingIds.includes(id)
        ? state.mutatingIds
        : [...state.mutatingIds, id],
      error: null,
    }));

  const finishMutation = (id: string) =>
    set((state) => ({
      mutatingIds: state.mutatingIds.filter((value) => value !== id),
    }));

  const restoreTask = (id: string, previousTask: TaskItem) =>
    set((state) => ({
      tasks: state.tasks.map((task) => (task.id === id ? previousTask : task)),
    }));

  return {
    tasks: [],
    filter: {
      status: 'all',
      priority: 'all',
      category: 'all',
      search: '',
    },
    isLoading: false,
    error: null,
    mutatingIds: [],
    revision: 0,

    fetchTasks: async () => {
      const requestRevision = get().revision;
      set({ isLoading: true, error: null });
      try {
        const res = await tasksApi.list({ limit: 100 });
        const mapped = Array.isArray(res?.tasks)
          ? res.tasks.map(mapBackendTaskToItem)
          : [];
        // A successful empty response is authoritative: clear any previously
        // displayed records instead of falling back to stale or mock data.
        set((state) => ({
          // A confirmed mutation landed during this request; its response is
          // newer than this snapshot, so never overwrite it with stale data.
          tasks:
            state.revision === requestRevision ? mapped : state.tasks,
          isLoading: false,
          error: null,
        }));
      } catch (err: unknown) {
        // Keep the last confirmed task list intact when the request fails.
        set({
          isLoading: false,
          error: getErrorMessage(
            err,
            'Could not load tasks. Showing the last confirmed data.'
          ),
        });
      }
    },

    setFilter: (partial) =>
      set((state) => ({ filter: { ...state.filter, ...partial } })),

    resetFilter: () =>
      set({
        filter: { status: 'all', priority: 'all', category: 'all', search: '' },
      }),

    addTask: async (taskData) => {
      const backendStatus = toBackendTaskStatus(taskData.status);

      if (!backendStatus) {
        set({
          error: 'Unsupported task status. Choose a valid status and try again.',
        });
        return;
      }

      const tempId = `task-${Date.now()}`;
      const newTask: TaskItem = {
        ...taskData,
        id: tempId,
        createdAt: new Date().toISOString(),
        completed: taskData.status === 'completed',
      };

      // Optimistic update limited to this temporary entry.
      set((state) => ({ tasks: [newTask, ...state.tasks], error: null }));

      try {
        const created = await tasksApi.create({
          title: taskData.title,
          description: taskData.description || null,
          category: mapFrontendCategory(taskData.category),
          priority: mapFrontendPriority(taskData.priority),
          status: backendStatus,
          due_date: taskData.dueDate || null,
        });

        set((state) => ({
          tasks: state.tasks.map((task) =>
            task.id === tempId ? mapBackendTaskToItem(created) : task
          ),
          error: null,
          revision: state.revision + 1,
        }));
      } catch (err: unknown) {
        // Remove only the unconfirmed entry; unrelated concurrent changes stay.
        set((state) => ({
          tasks: state.tasks.filter((task) => task.id !== tempId),
          error: getErrorMessage(err, 'Task could not be created. No changes were saved.'),
        }));
      }
    },

    updateTask: async (id, updates) => {
      const target = get().tasks.find((task) => task.id === id);

      if (!target) return;

      if (get().mutatingIds.includes(id)) {
        set({ error: 'A change to this task is already in progress.' });
        return;
      }

      if (updates.status === 'cancelled') {
        set({ error: 'Use Cancel task to cancel a task.' });
        return;
      }

      const backendStatus =
        updates.status !== undefined
          ? toBackendTaskStatus(updates.status)
          : undefined;

      if (updates.status !== undefined && backendStatus === undefined) {
        set({ error: 'Unsupported task status. The task was not changed.' });
        return;
      }

      if (
        backendStatus === 'pending' &&
        confirmedTaskStatus(target) === 'cancelled'
      ) {
        set({ error: 'A cancelled task cannot be reopened here.' });
        return;
      }

      const numId = toBackendTaskId(id);
      if (numId === null) {
        set({
          error: 'This task is not confirmed by the server yet. No changes were saved.',
        });
        return;
      }

      const previousTask = target;
      beginMutation(id);

      set((state) => ({
        tasks: state.tasks.map((task) => {
          if (task.id !== id) return task;
          const updated = { ...task, ...updates };
          if (updates.completed !== undefined) {
            updated.status = updates.completed ? 'completed' : 'pending';
            updated.completedAt = updates.completed
              ? new Date().toISOString()
              : undefined;
          } else if (updates.status !== undefined) {
            updated.completed = updates.status === 'completed';
            updated.completedAt =
              updates.status === 'completed' ? new Date().toISOString() : undefined;
          }
          return updated;
        }),
      }));

      try {
        const saved = await tasksApi.update(numId, {
          title: updates.title,
          description: updates.description,
          category: updates.category ? mapFrontendCategory(updates.category) : undefined,
          priority: updates.priority ? mapFrontendPriority(updates.priority) : undefined,
          status: backendStatus,
          due_date: updates.dueDate,
        });

        set((state) => ({
          tasks: state.tasks.map((task) =>
            task.id === id ? mapBackendTaskToItem(saved) : task
          ),
          error: null,
          revision: state.revision + 1,
        }));
      } catch (err: unknown) {
        restoreTask(id, previousTask);
        set({
          error: getErrorMessage(err, 'Task could not be updated. Previous values were restored.'),
        });
      } finally {
        finishMutation(id);
      }
    },

    deleteTask: async (id) => {
      const target = get().tasks.find((task) => task.id === id);

      if (!target) return;

      if (get().mutatingIds.includes(id)) {
        set({ error: 'A change to this task is already in progress.' });
        return;
      }

      const numId = toBackendTaskId(id);
      if (numId === null) {
        set({ error: 'This task is not confirmed by the server yet. It was not deleted.' });
        return;
      }

      const insertAt = get().tasks.findIndex((task) => task.id === id);
      beginMutation(id);

      set((state) => ({
        tasks: state.tasks.filter((task) => task.id !== id),
      }));

      try {
        await tasksApi.delete(numId);
        set((state) => ({ error: null, revision: state.revision + 1 }));
      } catch (err: unknown) {
        set((state) => {
          if (state.tasks.some((task) => task.id === id)) {
            return {
              error: getErrorMessage(err, 'Task could not be deleted. It was restored.'),
            };
          }
          const restored = [...state.tasks];
          restored.splice(Math.min(insertAt, restored.length), 0, target);
          return {
            tasks: restored,
            error: getErrorMessage(err, 'Task could not be deleted. It was restored.'),
          };
        });
      } finally {
        finishMutation(id);
      }
    },

    toggleTaskCompleted: async (id) => {
      const target = get().tasks.find((task) => task.id === id);
      if (!target) return;

      if (get().mutatingIds.includes(id)) {
        set({ error: 'A change to this task is already in progress.' });
        return;
      }

      const confirmedStatus = confirmedTaskStatus(target);
      if (confirmedStatus === 'cancelled' || confirmedStatus === 'unknown') {
        set({
          error:
            'A cancelled or unrecognised task cannot be reopened here. Refresh tasks first.',
        });
        return;
      }

      const numId = toBackendTaskId(id);
      if (numId === null) {
        set({ error: 'This task is not confirmed by the server yet. It was not changed.' });
        return;
      }

      const previousTask = target;
      const nextCompleted = !target.completed;
      beginMutation(id);

      set((state) => ({
        tasks: state.tasks.map((task) =>
          task.id === id
            ? {
                ...task,
                completed: nextCompleted,
                status: nextCompleted ? 'completed' : 'pending',
                completedAt: nextCompleted ? new Date().toISOString() : undefined,
              }
            : task
        ),
      }));

      try {
        const saved = nextCompleted
          ? await tasksApi.complete(numId)
          : await tasksApi.reopen(numId);

        set((state) => ({
          tasks: state.tasks.map((task) =>
            task.id === id ? mapBackendTaskToItem(saved) : task
          ),
          error: null,
          revision: state.revision + 1,
        }));
      } catch (err: unknown) {
        restoreTask(id, previousTask);
        set({
          error: getErrorMessage(err, 'Task status could not be changed. It was restored.'),
        });
      } finally {
        finishMutation(id);
      }
    },

    cancelTask: async (id) => {
      const target = get().tasks.find((task) => task.id === id);
      if (!target) return false;

      if (get().mutatingIds.includes(id)) {
        set({ error: 'A change to this task is already in progress.' });
        return false;
      }

      if (!isTaskCancellable(target)) {
        set({ error: cancellationBlockReason(target) });
        return false;
      }

      const numId = toBackendTaskId(id);
      if (numId === null) {
        set({ error: 'This task is not confirmed by the server yet. It was not cancelled.' });
        return false;
      }

      // No optimistic cancellation: the display status stays at the last
      // confirmed value until the backend responds.
      beginMutation(id);

      try {
        const saved = await tasksApi.cancel(numId);

        set((state) => ({
          tasks: state.tasks.map((task) =>
            task.id === id ? mapBackendTaskToItem(saved) : task
          ),
          error: null,
          revision: state.revision + 1,
        }));

        // A task cancellation can change how pending reminders should be
        // presented. Refresh them from the backend; never fabricate a local
        // cancellation or deletion, and never invent a backend status.
        try {
          const { useReminderStore } = await import('./reminder.store');
          await useReminderStore.getState().fetchReminders();
        } catch {
          // The reminder store records its own error and preserves confirmed
          // data. Cancellation already succeeded and must not be reported as a
          // failure because a separate refresh failed.
        }

        return true;
      } catch (err: unknown) {
        // Target was never changed locally, so the last confirmed state is
        // already preserved.
        set({
          error: getErrorMessage(
            err,
            'Task could not be cancelled. Previous status was preserved.'
          ),
        });
        return false;
      } finally {
        finishMutation(id);
      }
    },

    clearCompleted: () =>
      set((state) => ({
        tasks: state.tasks.filter((task) => !task.completed),
      })),
  };
});

export default useTaskStore;
