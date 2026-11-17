import { create } from 'zustand';
import backgroundTasksApi from '../services/api/backgroundTasks.api';
import {
  BACKGROUND_TASK_PAGE_LIMIT,
  BACKGROUND_TASK_POLL_INTERVAL_MS,
  type BackgroundTask,
} from '../types/backgroundTask.types';
import {
  backgroundTaskStatusLabel,
  isCancellableBackgroundTaskStatus,
  isTerminalBackgroundTaskStatus,
  mapBackgroundTaskResponse,
} from '../utils/backgroundTask';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

interface FetchBackgroundTaskOptions {
  silent?: boolean;
}

let fetchInFlight = false;
let fetchSequence = 0;
let stateVersion = 0;
let runtimeEpoch = 0;
let detailRequestSeq = 0;
let pollingHandle: ReturnType<typeof setInterval> | null = null;

const detailInFlight = new Set<number>();
const cancelInFlight = new Set<number>();

interface BackgroundTaskStoreState {
  tasks: BackgroundTask[];
  total: number;
  isLoading: boolean;
  isRefreshing: boolean;
  error: string | null;
  isBackendConnected: boolean;
  lastFetchedAt: string | null;

  cancellingIds: number[];
  cancellationError: string | null;
  cancellationSuccess: string | null;

  detail: BackgroundTask | null;
  detailTaskId: number | null;
  isDetailLoading: boolean;
  detailError: string | null;

  fetchTasks: (options?: FetchBackgroundTaskOptions) => Promise<boolean>;
  refreshTasks: () => Promise<boolean>;
  fetchTask: (
    taskId: number,
    options?: FetchBackgroundTaskOptions
  ) => Promise<BackgroundTask | null>;
  refreshTask: (taskId: number) => Promise<BackgroundTask | null>;
  cancelTask: (taskId: number) => Promise<boolean>;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  closeDetail: () => void;
  clearError: () => void;
  clearCancellationError: () => void;
  clearCancellationSuccess: () => void;
}

export const useBackgroundTaskStore = create<BackgroundTaskStoreState>(
  (set, get) => ({
    tasks: [],
    total: 0,
    isLoading: false,
    isRefreshing: false,
    error: null,
    isBackendConnected: false,
    lastFetchedAt: null,

    cancellingIds: [],
    cancellationError: null,
    cancellationSuccess: null,

    detail: null,
    detailTaskId: null,
    isDetailLoading: false,
    detailError: null,

    fetchTasks: async (options) => {
      // Controlled polling: a slow request must never overlap the next tick.
      if (fetchInFlight) return false;

      fetchInFlight = true;
      const requestSequence = ++fetchSequence;
      const requestVersion = stateVersion;
      const requestEpoch = runtimeEpoch;
      const requestDetailSeq = detailRequestSeq;
      const silent = options?.silent === true;

      set((state) => ({
        isLoading: state.tasks.length === 0,
        isRefreshing: state.tasks.length > 0,
        error: silent ? state.error : null,
      }));

      try {
        const response = await backgroundTasksApi.list({
          limit: BACKGROUND_TASK_PAGE_LIMIT,
          offset: 0,
        });

        // A cancellation completed while this request was in flight. Applying
        // the older snapshot would overwrite the confirmed cancellation.
        if (
          requestVersion !== stateVersion ||
          requestEpoch !== runtimeEpoch
        ) {
          if (requestSequence === fetchSequence) {
            set({ isLoading: false, isRefreshing: false });
          }
          return false;
        }

        if (!response || !Array.isArray(response.tasks)) {
          throw new Error('The background task list response could not be read.');
        }

        const mapped = response.tasks.map(mapBackgroundTaskResponse);
        const total = Number.isFinite(response.total)
          ? Math.max(0, response.total)
          : mapped.length;

        set((state) => {
          const detailTask =
            state.detailTaskId !== null
              ? mapped.find((task) => task.id === state.detailTaskId)
              : undefined;

          // Do not let a list snapshot that started before a newer detail
          // request overwrite that request's confirmed detail.
          const canRefreshDetail =
            requestDetailSeq === detailRequestSeq && !state.isDetailLoading;

          return {
            tasks: mapped,
            total,
            isLoading: false,
            isRefreshing: false,
            isBackendConnected: true,
            lastFetchedAt: new Date().toISOString(),
            error: null,
            detail:
              canRefreshDetail && detailTask ? detailTask : state.detail,
          };
        });

        return true;
      } catch (err: unknown) {
        if (requestEpoch !== runtimeEpoch) {
          if (requestSequence === fetchSequence) {
            set({ isLoading: false, isRefreshing: false });
          }
          return false;
        }

        // Preserve the last confirmed task history and details.
        set({
          isLoading: false,
          isRefreshing: false,
          isBackendConnected: false,
          error: getErrorMessage(
            err,
            'Could not load background tasks. Showing the last confirmed data.'
          ),
        });
        return false;
      } finally {
        // An older request from a previous session must not clear the flag for
        // the request that replaced it.
        if (requestSequence === fetchSequence) fetchInFlight = false;
      }
    },

    refreshTasks: async () => get().fetchTasks(),

    fetchTask: async (taskId) => {
      if (!Number.isFinite(taskId) || taskId <= 0) {
        set({ detailError: 'Choose a valid background task.' });
        return null;
      }

      if (detailInFlight.has(taskId)) return null;
      detailInFlight.add(taskId);

      const requestEpoch = runtimeEpoch;
      const requestVersion = stateVersion;
      const requestSeq = ++detailRequestSeq;

      set({
        detailTaskId: taskId,
        isDetailLoading: true,
        detailError: null,
      });

      try {
        const response = await backgroundTasksApi.get(taskId);

        if (requestEpoch !== runtimeEpoch) return null;

        const mapped = mapBackgroundTaskResponse(response);
        const isLatestRequest = requestSeq === detailRequestSeq;

        if (requestVersion !== stateVersion) {
          // A cancellation updated the record while this request was in
          // flight. The confirmed cancellation response is newer.
          if (isLatestRequest) set({ isDetailLoading: false });
          return null;
        }

        set((state) => ({
          tasks: state.tasks.map((task) =>
            task.id === mapped.id ? mapped : task
          ),
          ...(isLatestRequest
            ? {
                detail: mapped,
                detailTaskId: mapped.id,
                isDetailLoading: false,
                detailError: null,
              }
            : {}),
        }));

        return isLatestRequest ? mapped : null;
      } catch (err: unknown) {
        if (requestEpoch !== runtimeEpoch) return null;

        if (requestSeq === detailRequestSeq) {
          set({
            isDetailLoading: false,
            detailError: getErrorMessage(
              err,
              'Could not load this background task. Its last confirmed data is preserved.'
            ),
          });
        }

        return null;
      } finally {
        // A stale request from an ended session must not release a guard that
        // now belongs to the new session's in-flight request.
        if (requestEpoch === runtimeEpoch) detailInFlight.delete(taskId);
      }
    },

    refreshTask: async (taskId) => get().fetchTask(taskId),

    cancelTask: async (taskId) => {
      const state = get();
      const target =
        state.tasks.find((task) => task.id === taskId) ??
        (state.detail?.id === taskId ? state.detail : null);

      if (!target) {
        set({ cancellationError: 'Background task was not found.' });
        return false;
      }

      if (!isCancellableBackgroundTaskStatus(target.status)) {
        set({
          cancellationError:
            'Only queued or running background tasks can be cancelled.',
        });
        return false;
      }

      // Duplicate cancellation requests are rejected synchronously so a double
      // click or repeated confirmation can only produce one API call.
      if (cancelInFlight.has(taskId)) return false;
      cancelInFlight.add(taskId);

      const requestEpoch = runtimeEpoch;

      // Invalidate list/detail requests that started before this mutation.
      stateVersion += 1;

      set((current) => ({
        cancellingIds: [...current.cancellingIds, taskId],
        cancellationError: null,
        cancellationSuccess: null,
      }));

      try {
        const response = await backgroundTasksApi.cancel(taskId);

        if (requestEpoch !== runtimeEpoch) return false;

        const mapped = mapBackgroundTaskResponse(response);

        set((current) => {
          const touchesDetail = current.detailTaskId === mapped.id;
          const message =
            mapped.status === 'cancelled'
              ? `Task #${mapped.id} was cancelled.`
              : `Task #${mapped.id} was already ${backgroundTaskStatusLabel(
                  mapped.status
                ).toLowerCase()}.`;

          return {
            tasks: current.tasks.map((task) =>
              task.id === mapped.id ? mapped : task
            ),
            ...(touchesDetail
              ? {
                  detail: mapped,
                  isDetailLoading: false,
                  detailError: null,
                }
              : {}),
            cancellationSuccess: message,
            cancellationError: null,
            isBackendConnected: true,
          };
        });

        return true;
      } catch (err: unknown) {
        if (requestEpoch !== runtimeEpoch) return false;

        // Cancellation is applied only from the confirmed backend response, so
        // the existing task history remains untouched on failure.
        set({
          cancellationError: getErrorMessage(
            err,
            'Background task could not be cancelled. Last confirmed data was preserved.'
          ),
        });
        return false;
      } finally {
        // A stale request from an ended session must not clear the new
        // session's duplicate-guard or pending-id state.
        if (requestEpoch === runtimeEpoch) {
          cancelInFlight.delete(taskId);
          set((current) => ({
            cancellingIds: current.cancellingIds.filter(
              (value) => value !== taskId
            ),
          }));
        }
      }
    },

    startPolling: (intervalMs = BACKGROUND_TASK_POLL_INTERVAL_MS) => {
      if (pollingHandle !== null) return;

      // Establish the current confirmed state immediately, then keep it fresh
      // without ever overlapping requests.
      void get().fetchTasks();

      pollingHandle = setInterval(() => {
        const tasks = get().tasks;

        // Once every known task is terminal there is nothing left to monitor.
        // An empty list still polls so newly queued tasks are discovered.
        if (
          tasks.length > 0 &&
          tasks.every((task) =>
            isTerminalBackgroundTaskStatus(task.status)
          )
        ) {
          return;
        }

        void get().fetchTasks({ silent: true });
      }, intervalMs);
    },

    stopPolling: () => {
      if (pollingHandle !== null) {
        clearInterval(pollingHandle);
        pollingHandle = null;
      }

      // End the background-task session: invalidate every in-flight response
      // and drop account-scoped data so the next login starts clean.
      runtimeEpoch += 1;
      stateVersion += 1;
      detailRequestSeq += 1;
      fetchSequence += 1;
      fetchInFlight = false;
      detailInFlight.clear();
      cancelInFlight.clear();

      set({
        tasks: [],
        total: 0,
        isLoading: false,
        isRefreshing: false,
        error: null,
        isBackendConnected: false,
        lastFetchedAt: null,
        cancellingIds: [],
        cancellationError: null,
        cancellationSuccess: null,
        detail: null,
        detailTaskId: null,
        isDetailLoading: false,
        detailError: null,
      });
    },

    closeDetail: () => {
      detailRequestSeq += 1;

      set({
        detail: null,
        detailTaskId: null,
        isDetailLoading: false,
        detailError: null,
      });
    },

    clearError: () => set({ error: null }),
    clearCancellationError: () => set({ cancellationError: null }),
    clearCancellationSuccess: () => set({ cancellationSuccess: null }),
  })
);

export default useBackgroundTaskStore;
