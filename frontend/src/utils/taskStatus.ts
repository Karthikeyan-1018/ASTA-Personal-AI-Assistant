/**
 * Central task-status contract between the backend and the UI.
 *
 * The backend enum lives in `app/models/task.py`. Every value is mapped
 * explicitly; an unrecognised value is surfaced as `unknown` instead of being
 * silently coerced into `pending` or any other actionable state.
 */
import type { BackendTaskStatus } from '../types/api.types';
import type { TaskItem, TaskStatus } from '../types/task.types';

export const KNOWN_TASK_STATUSES = [
  'pending',
  'in_progress',
  'completed',
  'cancelled',
] as const;

export type KnownTaskStatus = (typeof KNOWN_TASK_STATUSES)[number];

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
  unknown: 'Unknown',
};

export function isKnownTaskStatus(value: unknown): value is KnownTaskStatus {
  return (
    typeof value === 'string' &&
    (KNOWN_TASK_STATUSES as readonly string[]).includes(value)
  );
}

/** Map an exact backend response value to a display status. */
export function mapBackendTaskStatus(value: unknown): TaskStatus {
  return isKnownTaskStatus(value) ? value : 'unknown';
}

/** Convert a display status to a writable backend status, if one exists. */
export function toBackendTaskStatus(
  value: TaskStatus
): BackendTaskStatus | undefined {
  return isKnownTaskStatus(value) ? value : undefined;
}

/**
 * Return the last status confirmed by the backend. Optimistic display changes
 * must never be treated as confirmed task state.
 */
export function confirmedTaskStatus(task: Pick<TaskItem, 'status' | 'backendStatus'>): TaskStatus {
  return mapBackendTaskStatus(task.backendStatus ?? task.status);
}

export function isOpenTaskStatus(
  value: TaskStatus
): value is 'pending' | 'in_progress' {
  return value === 'pending' || value === 'in_progress';
}

/** Backend cancellation applies to non-terminal, server-confirmed tasks. */
export function isTaskCancellable(
  task: Pick<TaskItem, 'id' | 'backendId' | 'status' | 'backendStatus'>
): boolean {
  return (
    typeof task.backendId === 'number' &&
    Number.isInteger(task.backendId) &&
    task.backendId > 0 &&
    isOpenTaskStatus(confirmedTaskStatus(task))
  );
}
