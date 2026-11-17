import type { BackgroundTaskResponse } from '../types/api.types';
import {
  BACKGROUND_TASK_STATUSES,
  BACKGROUND_TASK_STATUS_LABELS,
  BACKGROUND_TASK_TYPE_LABELS,
  CANCELLABLE_BACKGROUND_TASK_STATUSES,
  TERMINAL_BACKGROUND_TASK_STATUSES,
  type BackgroundTask,
  type BackgroundTaskFilter,
  type BackgroundTaskStatus,
} from '../types/backgroundTask.types';
import { parseBackendInstant } from './reminderTime';

export function isBackgroundTaskStatus(
  value: unknown
): value is BackgroundTaskStatus {
  return (
    typeof value === 'string' &&
    (BACKGROUND_TASK_STATUSES as readonly string[]).includes(value)
  );
}

export function isTerminalBackgroundTaskStatus(
  status: BackgroundTaskStatus
): boolean {
  return (TERMINAL_BACKGROUND_TASK_STATUSES as readonly string[]).includes(
    status
  );
}

export function isCancellableBackgroundTaskStatus(
  status: BackgroundTaskStatus
): boolean {
  return (CANCELLABLE_BACKGROUND_TASK_STATUSES as readonly string[]).includes(
    status
  );
}

export function backgroundTaskStatusLabel(
  status: BackgroundTaskStatus
): string {
  return BACKGROUND_TASK_STATUS_LABELS[status];
}

/**
 * Friendly label for allowlisted task types only. Unknown types are shown
 * exactly as returned so the UI never invents a meaning for them.
 */
export function backgroundTaskTypeLabel(taskType: string): string {
  return BACKGROUND_TASK_TYPE_LABELS[taskType] ?? taskType;
}

export function shortBackgroundTaskId(id: number): string {
  return `#${id}`;
}

export function formatBackgroundTaskInstant(value: string | null): string | null {
  if (!value) return null;

  try {
    return parseBackendInstant(value).toLocaleString([], {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch {
    return value;
  }
}

/**
 * Map the exact backend response. Unknown statuses are rejected instead of
 * being coerced into one of the five supported states.
 */
export function mapBackgroundTaskResponse(
  response: BackgroundTaskResponse
): BackgroundTask {
  if (!response || typeof response !== 'object') {
    throw new Error('The background task response could not be read.');
  }

  if (!Number.isFinite(response.id)) {
    throw new Error('The background task response is missing a valid id.');
  }

  if (typeof response.task_type !== 'string' || !response.task_type.trim()) {
    throw new Error('The background task response is missing a task type.');
  }

  if (!isBackgroundTaskStatus(response.status)) {
    throw new Error('The background task response contains an unknown status.');
  }

  return {
    id: response.id,
    taskType: response.task_type,
    status: response.status,
    errorCode: response.error_code ?? null,
    errorMessage: response.error_message ?? null,
    createdAt: response.created_at,
    startedAt: response.started_at ?? null,
    completedAt: response.completed_at ?? null,
  };
}

export interface BackgroundTaskStatusCounts {
  all: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export function countBackgroundTasksByStatus(
  tasks: readonly BackgroundTask[]
): BackgroundTaskStatusCounts {
  const counts: BackgroundTaskStatusCounts = {
    all: tasks.length,
    queued: 0,
    running: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
  };

  for (const task of tasks) counts[task.status] += 1;

  return counts;
}

export function filterBackgroundTasks(
  tasks: readonly BackgroundTask[],
  filter: BackgroundTaskFilter
): BackgroundTask[] {
  if (filter === 'all') return [...tasks];
  return tasks.filter((task) => task.status === filter);
}

export function hasActiveBackgroundTasks(
  tasks: readonly BackgroundTask[]
): boolean {
  return tasks.some((task) => !isTerminalBackgroundTaskStatus(task.status));
}
