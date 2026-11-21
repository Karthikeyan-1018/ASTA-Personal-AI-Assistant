export type TaskPriority = 'low' | 'medium' | 'high' | 'urgent';

export type TaskCategory =
  | 'college'
  | 'assignments'
  | 'exams'
  | 'projects'
  | 'personal'
  | 'meetings'
  | 'learning'
  | 'other';

export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';

export type SyncState = 'local_only' | 'pending_sync' | 'synced' | 'sync_failed';

export interface SubTask {
  id: string;
  title: string;
  is_completed: boolean;
}

export interface LocalTask {
  local_id: string;
  server_id: number | null;
  title: string;
  description: string | null;
  category: TaskCategory;
  priority: TaskPriority;
  status: TaskStatus;
  is_completed: boolean;
  due_date: string | null; // YYYY-MM-DD
  due_time: string | null; // HH:MM
  timezone: string | null;
  tags: string[];
  subtasks: SubTask[];
  notes: string | null;
  created_at: string; // ISO
  updated_at: string; // ISO
  completed_at: string | null;
  sync_state: SyncState;
  sync_error: string | null;
}

export interface CreateTaskInput {
  title: string;
  description?: string | null;
  category?: TaskCategory;
  priority?: TaskPriority;
  due_date?: string | null;
  due_time?: string | null;
  timezone?: string | null;
  tags?: string[];
  subtasks?: SubTask[];
  notes?: string | null;
}

export interface UpdateTaskInput {
  title?: string;
  description?: string | null;
  category?: TaskCategory;
  priority?: TaskPriority;
  status?: TaskStatus;
  is_completed?: boolean;
  due_date?: string | null;
  due_time?: string | null;
  timezone?: string | null;
  tags?: string[];
  subtasks?: SubTask[];
  notes?: string | null;
}

export type TaskFilter = 'all' | 'today' | 'upcoming' | 'overdue' | 'completed' | 'important';
export type TaskSortOption = 'due_date' | 'priority' | 'created_at' | 'title';
