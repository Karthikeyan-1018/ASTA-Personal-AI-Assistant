import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Info,
  Loader2,
  RefreshCw,
  Timer,
  X,
} from 'lucide-react';
import BackgroundTaskDetailPanel from '../components/backgroundTasks/BackgroundTaskDetailPanel';
import BackgroundTaskStatusBadge from '../components/backgroundTasks/BackgroundTaskStatusBadge';
import CancelBackgroundTaskDialog from '../components/backgroundTasks/CancelBackgroundTaskDialog';
import { useBackgroundTaskStore } from '../stores/backgroundTask.store';
import {
  BACKGROUND_TASK_POLL_INTERVAL_MS,
  BACKGROUND_TASK_STATUSES,
  type BackgroundTask,
  type BackgroundTaskFilter,
} from '../types/backgroundTask.types';
import {
  backgroundTaskStatusLabel,
  backgroundTaskTypeLabel,
  countBackgroundTasksByStatus,
  filterBackgroundTasks,
  formatBackgroundTaskInstant,
  isCancellableBackgroundTaskStatus,
  shortBackgroundTaskId,
} from '../utils/backgroundTask';

const STATUS_CARD_STYLES: Record<
  (typeof BACKGROUND_TASK_STATUSES)[number],
  { label: string; valueClass: string }
> = {
  queued: { label: 'Queued', valueClass: 'text-amber-300' },
  running: { label: 'Running', valueClass: 'text-sky-300' },
  completed: { label: 'Completed', valueClass: 'text-emerald-300' },
  failed: { label: 'Failed', valueClass: 'text-rose-300' },
  cancelled: { label: 'Cancelled', valueClass: 'text-slate-400' },
};

export const BackgroundTasks: React.FC = () => {
  const tasks = useBackgroundTaskStore((s) => s.tasks);
  const total = useBackgroundTaskStore((s) => s.total);
  const isLoading = useBackgroundTaskStore((s) => s.isLoading);
  const isRefreshing = useBackgroundTaskStore((s) => s.isRefreshing);
  const error = useBackgroundTaskStore((s) => s.error);
  const lastFetchedAt = useBackgroundTaskStore((s) => s.lastFetchedAt);
  const cancellingIds = useBackgroundTaskStore((s) => s.cancellingIds);
  const cancellationError = useBackgroundTaskStore(
    (s) => s.cancellationError
  );
  const cancellationSuccess = useBackgroundTaskStore(
    (s) => s.cancellationSuccess
  );
  const detail = useBackgroundTaskStore((s) => s.detail);
  const detailTaskId = useBackgroundTaskStore((s) => s.detailTaskId);
  const isDetailLoading = useBackgroundTaskStore((s) => s.isDetailLoading);
  const detailError = useBackgroundTaskStore((s) => s.detailError);

  const refreshTasks = useBackgroundTaskStore((s) => s.refreshTasks);
  const refreshTask = useBackgroundTaskStore((s) => s.refreshTask);
  const fetchTask = useBackgroundTaskStore((s) => s.fetchTask);
  const cancelTask = useBackgroundTaskStore((s) => s.cancelTask);
  const closeDetail = useBackgroundTaskStore((s) => s.closeDetail);
  const clearCancellationError = useBackgroundTaskStore(
    (s) => s.clearCancellationError
  );
  const clearCancellationSuccess = useBackgroundTaskStore(
    (s) => s.clearCancellationSuccess
  );

  const [filter, setFilter] = useState<BackgroundTaskFilter>('all');
  const [cancelTarget, setCancelTarget] = useState<BackgroundTask | null>(null);

  const counts = useMemo(() => countBackgroundTasksByStatus(tasks), [tasks]);
  const visibleTasks = useMemo(
    () => filterBackgroundTasks(tasks, filter),
    [tasks, filter]
  );

  const visibleTotal = counts[filter];

  const detailFallback =
    detailTaskId !== null
      ? tasks.find((task) => task.id === detailTaskId) ?? null
      : null;
  const detailTask =
    detail && detail.id === detailTaskId ? detail : detailFallback;

  const isCancellingTarget =
    cancelTarget !== null && cancellingIds.includes(cancelTarget.id);

  const openDetail = (task: BackgroundTask) => {
    clearCancellationError();
    void fetchTask(task.id);
  };

  const openCancelDialog = (task: BackgroundTask) => {
    clearCancellationError();
    clearCancellationSuccess();
    setCancelTarget(task);
  };

  const confirmCancel = async () => {
    if (!cancelTarget) return;
    const cancelled = await cancelTask(cancelTarget.id);
    if (cancelled) setCancelTarget(null);
  };

  const handleManualRefresh = () => {
    void refreshTasks();
    if (detailTaskId !== null) void refreshTask(detailTaskId);
  };

  const refreshing = isLoading || isRefreshing;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
            Background Task Monitoring
          </h1>
          <p className="text-xs text-slate-400 sm:text-sm">
            Backend-owned task records, execution state, and cancellation
            history.
          </p>
        </div>

        <button
          type="button"
          onClick={handleManualRefresh}
          disabled={refreshing}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-2.5 text-xs font-semibold text-slate-200 transition hover:border-sky-500/40 hover:text-sky-200 disabled:cursor-not-allowed disabled:opacity-50 sm:text-sm"
        >
          <RefreshCw
            className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`}
          />
          <span>{refreshing ? 'Refreshing…' : 'Refresh'}</span>
        </button>
      </div>

      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-4">
        <div className="flex items-start gap-3">
          <Timer className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
          <div>
            <p className="text-xs font-semibold text-slate-200">
              Monitoring only — tasks are queued by their source feature
            </p>
            <p className="mt-0.5 text-[11px] text-slate-400">
              Statuses refresh automatically every{' '}
              {BACKGROUND_TASK_POLL_INTERVAL_MS / 1000} seconds while queued or
              running work exists. Polling pauses once every known task is in a
              terminal state. The backend does not publish progress percentages,
              so none are shown.
            </p>
          </div>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <div>
              <p className="text-xs font-semibold text-rose-200">
                Background task service error
              </p>
              <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
              {tasks.length > 0 && (
                <p className="mt-1 text-[10px] text-rose-200/60">
                  Last confirmed task history is preserved until a refresh
                  succeeds.
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={handleManualRefresh}
            disabled={refreshing}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`}
            />
            <span>Retry</span>
          </button>
        </div>
      )}

      {cancellationSuccess && (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3.5"
        >
          <div className="flex items-center gap-2.5">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
            <p className="text-xs font-semibold text-emerald-200">
              {cancellationSuccess}
            </p>
          </div>
          <button
            type="button"
            onClick={clearCancellationSuccess}
            className="rounded-lg p-1 text-emerald-300/70 hover:bg-emerald-500/10 hover:text-emerald-200"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {cancellationError && !cancelTarget && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-3.5"
        >
          <div className="flex items-center gap-2.5">
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
            <p className="text-xs font-semibold text-rose-200">
              {cancellationError}
            </p>
          </div>
          <button
            type="button"
            onClick={clearCancellationError}
            className="rounded-lg p-1 text-rose-300/70 hover:bg-rose-500/10 hover:text-rose-200"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {BACKGROUND_TASK_STATUSES.map((status) => {
          const style = STATUS_CARD_STYLES[status];
          return (
            <div
              key={status}
              className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4"
            >
              <p className={`text-xs font-medium ${style.valueClass}`}>
                {style.label}
              </p>
              <p className="mt-2 text-2xl font-bold text-white">
                {counts[status]}
              </p>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-800 pb-3">
        {(['all', ...BACKGROUND_TASK_STATUSES] as const).map((value) => {
          const selected = filter === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              aria-pressed={selected}
              className={`rounded-xl px-3.5 py-1.5 text-xs font-semibold capitalize transition ${
                selected
                  ? 'border border-sky-500/30 bg-sky-500/15 text-sky-200'
                  : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
              }`}
            >
              {value === 'all'
                ? 'All tasks'
                : backgroundTaskStatusLabel(value)}
              <span className="ml-1.5 text-[10px] opacity-70">
                {counts[value]}
              </span>
            </button>
          );
        })}
      </div>

      <div className="space-y-3" aria-busy={isLoading}>
        {isLoading && tasks.length === 0 ? (
          <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-xs text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin text-sky-400" />
            <span>Loading background tasks…</span>
          </div>
        ) : error && tasks.length === 0 ? null : visibleTasks.length === 0 ? (
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
            <Timer className="mx-auto h-12 w-12 text-slate-600" />
            <p className="mt-3 text-sm font-semibold text-slate-300">
              {tasks.length === 0
                ? 'No background tasks'
                : `No ${backgroundTaskStatusLabel(
                    filter as Exclude<BackgroundTaskFilter, 'all'>
                  ).toLowerCase()} tasks`}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {tasks.length === 0
                ? 'Tasks appear here after ASTA queues memory embeddings or extraction work. This page monitors existing tasks only.'
                : 'Choose another status filter to view the remaining task history.'}
            </p>
            {tasks.length === 0 && (
              <button
                type="button"
                onClick={handleManualRefresh}
                disabled={refreshing}
                className="mt-4 inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/60 px-3.5 py-2 text-xs font-semibold text-slate-200 transition hover:border-sky-500/40 hover:text-sky-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`}
                />
                <span>Refresh</span>
              </button>
            )}
          </div>
        ) : (
          visibleTasks.map((task) => {
            const isCancelling = cancellingIds.includes(task.id);
            const cancellable = isCancellableBackgroundTaskStatus(task.status);

            return (
              <div
                key={task.id}
                className="rounded-2xl border border-slate-800/90 bg-slate-900/50 p-4 shadow-md transition hover:border-sky-500/30 hover:bg-slate-900/80"
              >
                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="mt-0.5 rounded-xl border border-sky-500/25 bg-sky-500/10 p-2">
                      <Timer className="h-4 w-4 text-sky-300" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm font-bold text-slate-100">
                          {shortBackgroundTaskId(task.id)}
                        </span>
                        <BackgroundTaskStatusBadge status={task.status} />
                      </div>
                      <p className="mt-1 text-sm font-semibold text-slate-200">
                        {backgroundTaskTypeLabel(task.taskType)}
                      </p>
                      <p className="mt-0.5 font-mono text-[10px] text-slate-500">
                        {task.taskType}
                      </p>
                    </div>
                  </div>

                  <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => openDetail(task)}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/60 px-2.5 py-1.5 text-[11px] font-semibold text-slate-300 transition hover:border-sky-500/40 hover:text-sky-200"
                    >
                      <Info className="h-3.5 w-3.5" />
                      <span>Details</span>
                    </button>
                    {cancellable && (
                      <button
                        type="button"
                        onClick={() => openCancelDialog(task)}
                        disabled={isCancelling}
                        className="inline-flex items-center gap-1.5 rounded-xl border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {isCancelling ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <X className="h-3.5 w-3.5" />
                        )}
                        <span>{isCancelling ? 'Cancelling…' : 'Cancel'}</span>
                      </button>
                    )}
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-1 gap-3 border-t border-slate-800/60 pt-3 text-[11px] sm:grid-cols-3">
                  <div>
                    <p className="flex items-center gap-1 text-slate-500">
                      <Clock className="h-3 w-3" />
                      Created
                    </p>
                    <p className="mt-0.5 font-semibold text-slate-200">
                      {formatBackgroundTaskInstant(task.createdAt) ?? '—'}
                    </p>
                  </div>
                  <div>
                    <p className="flex items-center gap-1 text-slate-500">
                      <Clock className="h-3 w-3" />
                      Started
                    </p>
                    <p className="mt-0.5 font-semibold text-slate-200">
                      {formatBackgroundTaskInstant(task.startedAt) ??
                        'Not started'}
                    </p>
                  </div>
                  <div>
                    <p className="flex items-center gap-1 text-slate-500">
                      <Clock className="h-3 w-3" />
                      Completed
                    </p>
                    <p className="mt-0.5 font-semibold text-slate-200">
                      {formatBackgroundTaskInstant(task.completedAt) ??
                        'Not completed'}
                    </p>
                  </div>
                </div>

                {(task.errorCode || task.errorMessage) && (
                  <div className="mt-3 rounded-xl border border-rose-500/25 bg-rose-500/10 px-3 py-2">
                    {task.errorCode && (
                      <p className="font-mono text-[10px] font-semibold text-rose-200">
                        {task.errorCode}
                      </p>
                    )}
                    {task.errorMessage && (
                      <p className="mt-0.5 text-[11px] text-rose-200/80">
                        {task.errorMessage}
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <p className="text-center text-[10px] text-slate-500">
        {filter === 'all'
          ? `Showing ${visibleTotal} of ${total} task${
              total === 1 ? '' : 's'
            }.`
          : `Showing ${visibleTotal} matching task${
              visibleTotal === 1 ? '' : 's'
            } of ${total} total.`}
        {lastFetchedAt
          ? ` Last confirmed update: ${formatBackgroundTaskInstant(
              lastFetchedAt
            )}.`
          : ''}
        {total > tasks.length
          ? ` Only the ${tasks.length} most recent tasks are loaded.`
          : ''}
      </p>

      {detailTaskId !== null && (
        <BackgroundTaskDetailPanel
          task={detailTask}
          isLoading={isDetailLoading}
          error={detailError}
          isCancelling={
            detailTaskId !== null && cancellingIds.includes(detailTaskId)
          }
          onClose={closeDetail}
          onRefresh={() => {
            if (detailTaskId !== null) void refreshTask(detailTaskId);
          }}
          onCancel={openCancelDialog}
        />
      )}

      {cancelTarget && (
        <CancelBackgroundTaskDialog
          task={
            tasks.find((task) => task.id === cancelTarget.id) ?? cancelTarget
          }
          isSubmitting={isCancellingTarget}
          error={cancellationError}
          onCancel={() => {
            if (isCancellingTarget) return;
            setCancelTarget(null);
            clearCancellationError();
          }}
          onConfirm={confirmCancel}
        />
      )}
    </div>
  );
};

export default BackgroundTasks;
