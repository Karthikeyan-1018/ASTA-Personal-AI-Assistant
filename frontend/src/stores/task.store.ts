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
    title: t.title,
    description: t.description || undefined,
    category: mapBackendCategory(t.category),
    priority: mapBackendPriority(t.priority),
    status: (t.status === 'cancelled' ? 'pending' : t.status) as TaskStatus,
    completed: t.is_completed,
    dueDate: t.due_date || undefined,
    createdAt: t.created_at,
    completedAt: t.completed_at || undefined,
  };
}

const INITIAL_TASKS: TaskItem[] = [
  {
    id: 'task-1',
    title: 'Audit filesystem tool execution sandbox boundaries',
    description: 'Ensure path traversal validation prevents reading outside c:\\Asta without explicit confirmation.',
    category: 'Security',
    priority: 'critical',
    status: 'in_progress',
    completed: false,
    dueDate: '2026-11-15',
    createdAt: '2026-11-14T09:00:00Z',
    tags: ['sandbox', 'security'],
  },
  {
    id: 'task-2',
    title: 'Index local documentation for semantic hybrid retrieval',
    description: 'Generate 384-dim embeddings using all-MiniLM-L6-v2 for docs and architecture guides.',
    category: 'Memory',
    priority: 'high',
    status: 'pending',
    completed: false,
    dueDate: '2026-11-16',
    createdAt: '2026-11-14T10:15:00Z',
    tags: ['pgvector', 'rag'],
  },
  {
    id: 'task-3',
    title: 'Calibrate LLM hybrid routing latency thresholds',
    description: 'Switch between Groq Llama-3.3-70b (remote) and Ollama DeepSeek (local) based on ping and privacy tag.',
    category: 'System',
    priority: 'medium',
    status: 'pending',
    completed: false,
    dueDate: '2026-11-17',
    createdAt: '2026-11-14T11:30:00Z',
    tags: ['routing', 'groq'],
  },
  {
    id: 'task-4',
    title: 'Validate Phase 14 desktop UI dark mode contrast',
    description: 'Ensure WCAG AA contrast ratio across all Obsidian cards and code syntax panels.',
    category: 'Development',
    priority: 'medium',
    status: 'completed',
    completed: true,
    dueDate: '2026-11-14',
    createdAt: '2026-11-13T14:00:00Z',
    completedAt: '2026-11-14T18:00:00Z',
    tags: ['ui', 'accessibility'],
  },
];

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
  tasks: INITIAL_TASKS,
  filter: {
    status: 'all',
    priority: 'all',
    category: 'all',
    search: '',
  },
  isLoading: false,
  error: null,

  fetchTasks: async () => {
    try {
      set({ isLoading: true, error: null });
      const res = await tasksApi.list({ limit: 100 });
      if (res && res.tasks) {
        const mapped = res.tasks.map(mapBackendTaskToItem);
        if (mapped.length > 0) {
          set({ tasks: mapped, isLoading: false });
          return;
        }
      }
      set({ isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Failed to fetch tasks from backend', isLoading: false });
    }
  },

  setFilter: (partial) =>
    set((state) => ({ filter: { ...state.filter, ...partial } })),

  resetFilter: () =>
    set({
      filter: { status: 'all', priority: 'all', category: 'all', search: '' },
    }),

  addTask: async (taskData) => {
    const tempId = `task-${Date.now()}`;
    const newTask: TaskItem = {
      ...taskData,
      id: tempId,
      createdAt: new Date().toISOString(),
      completed: taskData.status === 'completed',
    };

    // Optimistic update
    set((state) => ({ tasks: [newTask, ...state.tasks] }));

    try {
      const created = await tasksApi.create({
        title: taskData.title,
        description: taskData.description || null,
        category: mapFrontendCategory(taskData.category),
        priority: mapFrontendPriority(taskData.priority),
        status: (taskData.status === 'completed' ? 'completed' : 'pending') as BackendTaskStatus,
        due_date: taskData.dueDate || null,
      });

      // Replace temporary ID with real backend ID
      set((state) => ({
        tasks: state.tasks.map((t) => (t.id === tempId ? mapBackendTaskToItem(created) : t)),
      }));
    } catch {
      // Local optimistic item remains functional
    }
  },

  updateTask: async (id, updates) => {
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

    const numId = Number(id);
    if (!isNaN(numId) && numId > 0) {
      try {
        await tasksApi.update(numId, {
          title: updates.title,
          description: updates.description,
          category: updates.category ? mapFrontendCategory(updates.category) : undefined,
          priority: updates.priority ? mapFrontendPriority(updates.priority) : undefined,
          status: updates.status ? (updates.status as BackendTaskStatus) : undefined,
          due_date: updates.dueDate,
        });
      } catch {
        // Optimistic state kept
      }
    }
  },

  deleteTask: async (id) => {
    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== id),
    }));

    const numId = Number(id);
    if (!isNaN(numId) && numId > 0) {
      try {
        await tasksApi.delete(numId);
      } catch {
        // Optimistic state kept
      }
    }
  },

  toggleTaskCompleted: async (id) => {
    const target = get().tasks.find((t) => t.id === id);
    if (!target) return;

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
    }));

    const numId = Number(id);
    if (!isNaN(numId) && numId > 0) {
      try {
        if (nextCompleted) {
          await tasksApi.complete(numId);
        } else {
          await tasksApi.reopen(numId);
        }
      } catch {
        // Optimistic state kept
      }
    }
  },

  clearCompleted: () =>
    set((state) => ({
      tasks: state.tasks.filter((t) => !t.completed),
    })),
}));

export default useTaskStore;
