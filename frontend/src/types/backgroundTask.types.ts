import type { BackendBackgroundTaskStatus } from './api.types';

/**
 * Frontend background-task models.
 *
 * The backend is the source of truth. The five statuses below are the complete
 * set defined by the backend; they must never be remapped or extended. Task
 * types are preserved exactly as returned and only a small allowlist receives a
 * friendly display label.
 */
export type BackgroundTaskStatus = BackendBackgroundTaskStatus;

export type BackgroundTaskFilter = 'all' | BackgroundTaskStatus;

export interface BackgroundTask {
  id: number;
  taskType: string;
  status: BackgroundTaskStatus;
  errorCode: string | null;
  errorMessage: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
}

export const BACKGROUND_TASK_STATUSES: readonly BackgroundTaskStatus[] = [
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
];

export const TERMINAL_BACKGROUND_TASK_STATUSES: readonly BackgroundTaskStatus[] =
  ['completed', 'failed', 'cancelled'];

export const CANCELLABLE_BACKGROUND_TASK_STATUSES: readonly BackgroundTaskStatus[] =
  ['queued', 'running'];

export const BACKGROUND_TASK_STATUS_LABELS: Record<BackgroundTaskStatus, string> =
  {
    queued: 'Queued',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };

/** Backend task-type allowlist with friendly display labels. */
export const BACKGROUND_TASK_TYPE_LABELS: Record<string, string> = {
  memory_embedding: 'Memory embedding',
  memory_extraction: 'Memory extraction',
};

export const BACKGROUND_TASK_PAGE_LIMIT = 100;
export const BACKGROUND_TASK_POLL_INTERVAL_MS = 10_000;
