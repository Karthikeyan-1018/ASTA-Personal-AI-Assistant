import { create } from 'zustand';
import tasksApi from '../services/api/tasks.api';
import type { TaskItem, TaskFilterOptions, TaskPriority, TaskCategory, TaskStatus } from '../types/task.types';
import type { BackendTaskCategory, BackendTaskPriority, BackendTaskStatus, TaskResponse } from '../types/api.types';

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
  return {
    id: String(t.id),
    backendId: t.id,
    title: t.title,
    description: t.description || undefined,
    category: mapBackendCategory(t.category),
    priority: mapBackendPriority(t.priority),
    status: (t.status === 'cancelled' ? 'pending' : t.status) as TaskStatus,
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

interface TaskStoreState {
  tasks: TaskItem[];
  filter: TaskFilterOptions;
  isLoading: boolean;
  error: string | null;
  fetchTasks: () => Promise<void>;
  setFilter: (partial: Partial<TaskFilterOptions>) => void;
  resetFilter: () => void;
  addTask: (task: Omit<TaskItem, 'id' | 'createdAt' | 'completed'>) => Promise<void>;
  updateTask: (id: string, updates: Partial<TaskItem>) => Promise<void>;
  deleteTask: (id: string) => Promise<void>;
  toggleTaskCompleted: (id: string) => Promise<void>;
  clearCompleted: () => void;
}

export const useTaskStore = create<TaskStoreState>((set, get) => ({
  tasks: [],
  filter: {
    status: 'all',
    priority: 'all',
    category: 'all',
    search: '',
  },
  isLoading: false,
  error: null,

  fetchTasks: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await tasksApi.list({ limit: 100 });
      const mapped = Array.isArray(res?.tasks)
        ? res.tasks.map(mapBackendTaskToItem)
        : [];
      // A successful empty response is authoritative: clear any previously
      // displayed records instead of falling back to stale or mock data.
      set({ tasks: mapped, isLoading: false, error: null });
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
    const previousTasks = get().tasks;
    const tempId = `task-${Date.now()}`;
    const newTask: TaskItem = {
      ...taskData,
      id: tempId,
      createdAt: new Date().toISOString(),
      completed: taskData.status === 'completed',
    };

    // Optimistic update
    set((state) => ({ tasks: [newTask, ...state.tasks], error: null }));

    try {
      const created = await tasksApi.create({
        title: taskData.title,
        description: taskData.description || null,
        category: mapFrontendCategory(taskData.category),
        priority: mapFrontendPriority(taskData.priority),
        status: (taskData.status === 'completed' ? 'completed' : 'pending') as BackendTaskStatus,
        due_date: taskData.dueDate || null,
      });

      // Replace temporary ID with the server-confirmed task.
      set((state) => ({
        tasks: state.tasks.map((t) => (t.id === tempId ? mapBackendTaskToItem(created) : t)),
        error: null,
      }));
    } catch (err: unknown) {
      // The optimistic entry was never confirmed by the server, so remove it
      // and restore the last confirmed list.
      set({
        tasks: previousTasks,
        error: getErrorMessage(err, 'Task could not be created. No changes were saved.'),
      });
    }
  },

  updateTask: async (id, updates) => {
    const previousTasks = get().tasks;

    // Optimistic update
    set((state) => ({
      tasks: state.tasks.map((t) => {
        if (t.id !== id) return t;
        const updated = { ...t, ...updates };
        if (updates.completed !== undefined) {
          updated.status = updates.completed ? 'completed' : 'pending';
          updated.completedAt = updates.completed ? new Date().toISOString() : undefined;
        } else if (updates.status !== undefined) {
          updated.completed = updates.status === 'completed';
          updated.completedAt = updates.status === 'completed' ? new Date().toISOString() : undefined;
        }
        return updated;
      }),
    }));

    const numId = toBackendTaskId(id);
    if (numId === null) {
      set({
        tasks: previousTasks,
        error: 'This task is not confirmed by the server yet. No changes were saved.',
      });
      return;
    }

    try {
      const saved = await tasksApi.update(numId, {
        title: updates.title,
        description: updates.description,
        category: updates.category ? mapFrontendCategory(updates.category) : undefined,
        priority: updates.priority ? mapFrontendPriority(updates.priority) : undefined,
        status: updates.status ? (updates.status as BackendTaskStatus) : undefined,
        due_date: updates.dueDate,
      });

      // Adopt the server response so the UI never shows unconfirmed values.
      set((state) => ({
        tasks: state.tasks.map((t) => (t.id === id ? mapBackendTaskToItem(saved) : t)),
        error: null,
      }));
    } catch (err: unknown) {
      set({
        tasks: previousTasks,
        error: getErrorMessage(err, 'Task could not be updated. Previous values were restored.'),
      });
    }
  },

  deleteTask: async (id) => {
    const previousTasks = get().tasks;
    const numId = toBackendTaskId(id);

    if (numId === null) {
      set({ error: 'This task is not confirmed by the server yet. It was not deleted.' });
      return;
    }

    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== id),
      error: null,
    }));

    try {
      await tasksApi.delete(numId);
    } catch (err: unknown) {
      set({
        tasks: previousTasks,
        error: getErrorMessage(err, 'Task could not be deleted. It was restored.'),
      });
    }
  },

  toggleTaskCompleted: async (id) => {
    const target = get().tasks.find((t) => t.id === id);
    if (!target) return;

    const previousTasks = get().tasks;
    const numId = toBackendTaskId(id);
    if (numId === null) {
      set({ error: 'This task is not confirmed by the server yet. It was not changed.' });
      return;
    }

    const nextCompleted = !target.completed;

    set((state) => ({
      tasks: state.tasks.map((t) => {
        if (t.id !== id) return t;
        return {
          ...t,
          completed: nextCompleted,
          status: (nextCompleted ? 'completed' : 'pending') as TaskStatus,
          completedAt: nextCompleted ? new Date().toISOString() : undefined,
        };
      }),
      error: null,
    }));

    try {
      const saved = nextCompleted
        ? await tasksApi.complete(numId)
        : await tasksApi.reopen(numId);

      set((state) => ({
        tasks: state.tasks.map((t) => (t.id === id ? mapBackendTaskToItem(saved) : t)),
        error: null,
      }));
    } catch (err: unknown) {
      set({
        tasks: previousTasks,
        error: getErrorMessage(err, 'Task status could not be changed. It was restored.'),
      });
    }
  },

  clearCompleted: () =>
    set((state) => ({
      tasks: state.tasks.filter((t) => !t.completed),
    })),
}));

export default useTaskStore;
