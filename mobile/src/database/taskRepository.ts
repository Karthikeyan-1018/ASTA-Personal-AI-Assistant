import { getDatabase } from './db';
import {
  LocalTask,
  CreateTaskInput,
  UpdateTaskInput,
  TaskCategory,
  TaskPriority,
  TaskStatus,
  SyncState,
  SubTask,
  TaskFilter,
  TaskSortOption,
} from '../types/task';
import { enqueueSyncOperation } from '../sync/syncQueue';

interface RawTaskRow {
  local_id: string;
  server_id: number | null;
  title: string;
  description: string | null;
  category: string;
  priority: string;
  status: string;
  is_completed: number;
  due_date: string | null;
  due_time: string | null;
  timezone: string | null;
  tags_json: string | null;
  subtasks_json: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  sync_state: string;
  sync_error: string | null;
}

function parseTaskRow(row: RawTaskRow): LocalTask {
  let tags: string[] = [];
  try {
    tags = row.tags_json ? JSON.parse(row.tags_json) : [];
  } catch {
    tags = [];
  }

  let subtasks: SubTask[] = [];
  try {
    subtasks = row.subtasks_json ? JSON.parse(row.subtasks_json) : [];
  } catch {
    subtasks = [];
  }

  // Normalize legacy sync_state values ('queued' -> 'pending_sync')
  let normalizedSyncState: SyncState = 'pending_sync';
  if (row.sync_state === 'synced') normalizedSyncState = 'synced';
  else if (row.sync_state === 'local_only') normalizedSyncState = 'local_only';
  else if (row.sync_state === 'sync_failed') normalizedSyncState = 'sync_failed';

  return {
    local_id: row.local_id,
    server_id: row.server_id,
    title: row.title,
    description: row.description,
    category: (row.category as TaskCategory) || 'other',
    priority: (row.priority as TaskPriority) || 'medium',
    status: (row.status as TaskStatus) || (row.is_completed ? 'completed' : 'pending'),
    is_completed: Boolean(row.is_completed || row.status === 'completed'),
    due_date: row.due_date,
    due_time: row.due_time,
    timezone: row.timezone,
    tags,
    subtasks,
    notes: row.notes,
    created_at: row.created_at,
    updated_at: row.updated_at || row.created_at,
    completed_at: row.completed_at,
    sync_state: normalizedSyncState,
    sync_error: row.sync_error,
  };
}

export async function getAllTasks(): Promise<LocalTask[]> {
  const db = await getDatabase();
  const rows = await db.getAllAsync<RawTaskRow>(
    'SELECT * FROM mobile_tasks ORDER BY created_at DESC'
  );
  return rows.map(parseTaskRow);
}

export async function getTaskById(localId: string): Promise<LocalTask | null> {
  const db = await getDatabase();
  const row = await db.getFirstAsync<RawTaskRow>(
    'SELECT * FROM mobile_tasks WHERE local_id = ?',
    [localId]
  );
  return row ? parseTaskRow(row) : null;
}

export async function createTask(input: CreateTaskInput): Promise<LocalTask> {
  const db = await getDatabase();
  const now = new Date().toISOString();
  const localId = `asta-task-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

  const category = input.category || 'other';
  const priority = input.priority || 'medium';
  const tagsJson = JSON.stringify(input.tags || []);
  const subtasksJson = JSON.stringify(input.subtasks || []);

  await db.runAsync(
    `INSERT INTO mobile_tasks (
      local_id, server_id, title, description, category, priority, status,
      is_completed, due_date, due_time, timezone, tags_json, subtasks_json,
      notes, created_at, updated_at, completed_at, sync_state, sync_error
    ) VALUES (?, NULL, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending_sync', NULL)`,
    localId,
    input.title.trim(),
    input.description?.trim() || null,
    category,
    priority,
    input.due_date?.trim() || null,
    input.due_time?.trim() || null,
    input.timezone?.trim() || null,
    tagsJson,
    subtasksJson,
    input.notes?.trim() || null,
    now,
    now
  );

  const createdTask: LocalTask = {
    local_id: localId,
    server_id: null,
    title: input.title.trim(),
    description: input.description?.trim() || null,
    category,
    priority,
    status: 'pending',
    is_completed: false,
    due_date: input.due_date?.trim() || null,
    due_time: input.due_time?.trim() || null,
    timezone: input.timezone?.trim() || null,
    tags: input.tags || [],
    subtasks: input.subtasks || [],
    notes: input.notes?.trim() || null,
    created_at: now,
    updated_at: now,
    completed_at: null,
    sync_state: 'pending_sync',
    sync_error: null,
  };

  // Add to synchronization queue
  await enqueueSyncOperation('task', localId, 'CREATE', createdTask);

  return createdTask;
}

export async function updateTask(localId: string, updates: UpdateTaskInput): Promise<LocalTask> {
  const existing = await getTaskById(localId);
  if (!existing) {
    throw new Error(`Task with local_id ${localId} not found`);
  }

  const db = await getDatabase();
  const now = new Date().toISOString();

  const title = updates.title !== undefined ? updates.title.trim() : existing.title;
  const description = updates.description !== undefined ? updates.description?.trim() || null : existing.description;
  const category = updates.category !== undefined ? updates.category : existing.category;
  const priority = updates.priority !== undefined ? updates.priority : existing.priority;
  
  let is_completed = existing.is_completed;
  let status = existing.status;
  let completed_at = existing.completed_at;

  if (updates.is_completed !== undefined) {
    is_completed = updates.is_completed;
    status = is_completed ? 'completed' : 'pending';
    completed_at = is_completed ? now : null;
  } else if (updates.status !== undefined) {
    status = updates.status;
    is_completed = status === 'completed';
    completed_at = is_completed ? now : null;
  }

  const due_date = updates.due_date !== undefined ? updates.due_date?.trim() || null : existing.due_date;
  const due_time = updates.due_time !== undefined ? updates.due_time?.trim() || null : existing.due_time;
  const timezone = updates.timezone !== undefined ? updates.timezone?.trim() || null : existing.timezone;
  const tags = updates.tags !== undefined ? updates.tags : existing.tags;
  const subtasks = updates.subtasks !== undefined ? updates.subtasks : existing.subtasks;
  const notes = updates.notes !== undefined ? updates.notes?.trim() || null : existing.notes;

  await db.runAsync(
    `UPDATE mobile_tasks SET
      title = ?, description = ?, category = ?, priority = ?, status = ?,
      is_completed = ?, due_date = ?, due_time = ?, timezone = ?,
      tags_json = ?, subtasks_json = ?, notes = ?, updated_at = ?,
      completed_at = ?, sync_state = 'pending_sync', sync_error = NULL
    WHERE local_id = ?`,
    title,
    description,
    category,
    priority,
    status,
    is_completed ? 1 : 0,
    due_date,
    due_time,
    timezone,
    JSON.stringify(tags),
    JSON.stringify(subtasks),
    notes,
    now,
    completed_at,
    localId
  );

  const updated: LocalTask = {
    ...existing,
    title,
    description,
    category,
    priority,
    status,
    is_completed,
    due_date,
    due_time,
    timezone,
    tags,
    subtasks,
    notes,
    updated_at: now,
    completed_at,
    sync_state: 'pending_sync',
    sync_error: null,
  };

  await enqueueSyncOperation('task', localId, 'UPDATE', updated);

  return updated;
}

export async function toggleTaskCompletion(localId: string): Promise<LocalTask> {
  const existing = await getTaskById(localId);
  if (!existing) throw new Error('Task not found');
  return updateTask(localId, { is_completed: !existing.is_completed });
}

export async function deleteTask(localId: string): Promise<void> {
  const db = await getDatabase();
  const existing = await getTaskById(localId);
  if (!existing) return;

  await db.runAsync('DELETE FROM mobile_tasks WHERE local_id = ?', [localId]);
  await enqueueSyncOperation('task', localId, 'DELETE', { local_id: localId, server_id: existing.server_id });
}

export async function getFilteredTasks(
  tasks: LocalTask[],
  filter: TaskFilter,
  selectedCategory?: TaskCategory | null,
  searchQuery?: string,
  sortOption: TaskSortOption = 'created_at'
): Promise<LocalTask[]> {
  const todayStr = new Date().toISOString().slice(0, 10);

  let filtered = tasks.filter((task) => {
    // 1. Text search
    if (searchQuery && searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      const matchTitle = task.title.toLowerCase().includes(q);
      const matchDesc = task.description?.toLowerCase().includes(q) || false;
      const matchTags = task.tags.some((t) => t.toLowerCase().includes(q));
      if (!matchTitle && !matchDesc && !matchTags) return false;
    }

    // 2. Category
    if (selectedCategory && task.category !== selectedCategory) {
      return false;
    }

    // 3. Filter views
    switch (filter) {
      case 'today':
        return task.due_date === todayStr && !task.is_completed;
      case 'upcoming':
        return Boolean(task.due_date && task.due_date > todayStr && !task.is_completed);
      case 'overdue':
        return Boolean(task.due_date && task.due_date < todayStr && !task.is_completed);
      case 'completed':
        return task.is_completed;
      case 'important':
        return (task.priority === 'urgent' || task.priority === 'high') && !task.is_completed;
      case 'all':
      default:
        return true;
    }
  });

  // Sort
  filtered.sort((a, b) => {
    switch (sortOption) {
      case 'due_date':
        if (!a.due_date) return 1;
        if (!b.due_date) return -1;
        return a.due_date.localeCompare(b.due_date);
      case 'priority': {
        const pOrder: Record<TaskPriority, number> = { urgent: 4, high: 3, medium: 2, low: 1 };
        return pOrder[b.priority] - pOrder[a.priority];
      }
      case 'title':
        return a.title.localeCompare(b.title);
      case 'created_at':
      default:
        return b.created_at.localeCompare(a.created_at);
    }
  });

  return filtered;
}
