import React from 'react';
import {
  AlertTriangle,
  Ban,
  Clock,
  Loader2,
  RefreshCw,
  Timer,
  X,
} from 'lucide-react';
import BackgroundTaskStatusBadge from './BackgroundTaskStatusBadge';
import type { BackgroundTask } from '../../types/backgroundTask.types';
import {
  backgroundTaskTypeLabel,
  formatBackgroundTaskInstant,
  isCancellableBackgroundTaskStatus,
  isTerminalBackgroundTaskStatus,
  shortBackgroundTaskId,
} from '../../utils/backgroundTask';

export interface BackgroundTaskDetailPanelProps {
  task: BackgroundTask | null;
  isLoading: boolean;
  error: string | null;
  isCancelling: boolean;
  onClose: () => void;
  onRefresh: () => void;
  onCancel: (task: BackgroundTask) => void;
}

export const BackgroundTaskDetailPanel: React.FC<
  BackgroundTaskDetailPanelProps
> = ({ task, isLoading, error, isCancelling, onClose, onRefresh, onCancel }) => {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="background-task-detail-title"
        className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-slate-800 bg-slate-950 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 p-5">
          <div className="flex items-start gap-3">
            <div className="rounded-xl border border-sky-500/25 bg-sky-500/10 p-2">
              <Timer className="h-4 w-4 text-sky-300" />
            </div>
            <div>
              <h2
                id="background-task-detail-title"
                className="text-base font-bold text-white"
              >
                Background Task Detail
              </h2>
              <p className="mt-0.5 text-[11px] text-slate-400">
                Confirmed backend record. No progress is estimated by the UI.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            title="Close details"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {isLoading && !task ? (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-800 bg-slate-900/30 p-10 text-xs text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin text-sky-400" />
              <span>Loading background task…</span>
            </div>
          ) : error && !task ? (
            <div
              role="alert"
              className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4"
            >
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
                <div>
                  <p className="text-xs font-semibold text-rose-200">
                    Background task could not be loaded
                  </p>
                  <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={onRefresh}
                disabled={isLoading}
                className="mt-3 inline-flex items-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`}
                />
                <span>Retry</span>
              </button>
            </div>
          ) : task ? (
            <div className="space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-bold text-white">
                    {shortBackgroundTaskId(task.id)}
                  </span>
                  <BackgroundTaskStatusBadge status={task.status} />
                </div>
                <p className="mt-2 text-sm font-semibold text-slate-100">
                  {backgroundTaskTypeLabel(task.taskType)}
                </p>
                <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                  {task.taskType}
                </p>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Task ID
                  </p>
                  <p className="mt-1 font-mono text-xs font-semibold text-slate-200">
                    {task.id}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Task type
                  </p>
                  <p className="mt-1 text-xs font-semibold text-slate-200">
                    {backgroundTaskTypeLabel(task.taskType)}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Created
                  </p>
                  <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                    <Clock className="h-3.5 w-3.5 text-slate-400" />
                    {formatBackgroundTaskInstant(task.createdAt) ?? '—'}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Started
                  </p>
                  <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                    <Clock className="h-3.5 w-3.5 text-slate-400" />
                    {formatBackgroundTaskInstant(task.startedAt) ?? 'Not started'}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4 sm:col-span-2">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Completed
                  </p>
                  <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                    <Clock className="h-3.5 w-3.5 text-slate-400" />
                    {formatBackgroundTaskInstant(task.completedAt) ?? 'Not completed'}
                  </p>
                </div>
              </div>

              {(task.errorCode || task.errorMessage) && (
                <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4">
                  <div className="flex items-center gap-2 text-xs font-semibold text-rose-200">
                    <AlertTriangle className="h-4 w-4" />
                    Backend error details
                  </div>
                  {task.errorCode && (
                    <p className="mt-2 font-mono text-[11px] text-rose-200/90">
                      Code: {task.errorCode}
                    </p>
                  )}
                  {task.errorMessage && (
                    <p className="mt-1 text-[11px] text-rose-200/80">
                      {task.errorMessage}
                    </p>
                  )}
                </div>
              )}

              {task.status === 'failed' &&
                !task.errorCode &&
                !task.errorMessage && (
                  <p className="rounded-2xl border border-slate-800 bg-slate-900/40 p-3 text-[11px] text-slate-400">
                    The backend reported this task as failed without returning
                    error details.
                  </p>
                )}

              {isTerminalBackgroundTaskStatus(task.status) && (
                <p className="flex items-start gap-2 rounded-2xl border border-slate-800 bg-slate-900/40 p-3 text-[11px] text-slate-400">
                  <Ban className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
                  This task is in a terminal state. Its record and timestamps
                  remain available as history.
                </p>
              )}
            </div>
          ) : null}

          {error && task && (
            <p
              role="alert"
              className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200"
            >
              {error} Last confirmed data is shown above.
            </p>
          )}
        </div>

        {task && (
          <div className="flex flex-wrap justify-end gap-2 border-t border-slate-800 p-4">
            <button
              type="button"
              onClick={onRefresh}
              disabled={isLoading || isCancelling}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs font-semibold text-slate-300 transition hover:border-sky-500/40 hover:text-sky-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`}
              />
              <span>Refresh status</span>
            </button>
            {isCancellableBackgroundTaskStatus(task.status) && (
              <button
                type="button"
                onClick={() => onCancel(task)}
                disabled={isCancelling || isLoading}
                className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isCancelling ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Ban className="h-3.5 w-3.5" />
                )}
                <span>{isCancelling ? 'Cancelling…' : 'Cancel task'}</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default BackgroundTaskDetailPanel;
