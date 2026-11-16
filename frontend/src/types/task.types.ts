export type TaskPriority = 'low' | 'medium' | 'high' | 'critical';
export type TaskStatus = 'pending' | 'in_progress' | 'completed';
export type TaskCategory = 'General' | 'Memory' | 'Security' | 'Development' | 'System' | 'Research';

export interface TaskItem {
  id: string;
  backendId?: number;
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
