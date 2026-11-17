export type TaskPriority = 'low' | 'medium' | 'high' | 'critical';
/** `unknown` is explicit: the backend returned a value this client cannot handle. */
export type TaskStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'cancelled'
  | 'unknown';
export type TaskCategory = 'General' | 'Memory' | 'Security' | 'Development' | 'System' | 'Research';
export type TaskBackendStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'cancelled'
  | 'unknown';

export interface TaskItem {
  id: string;
  backendId?: number;
  /** Exact last-confirmed backend status; never an optimistic display value. */
  backendStatus?: TaskBackendStatus;
  title: string;
  description?: string;
  category: TaskCategory;
  priority: TaskPriority;
  status: TaskStatus;
  completed: boolean;
  dueDate?: string; // YYYY-MM-DD
  dueTime?: string; // HH:MM[:SS]
  createdAt: string;
  completedAt?: string;
  tags?: string[];
}

export interface TaskFilterOptions {
  status: 'all' | TaskStatus;
  priority: 'all' | TaskPriority;
  category: 'all' | TaskCategory;
  search: string;
}
