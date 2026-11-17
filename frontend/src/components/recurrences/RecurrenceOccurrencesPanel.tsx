import React from 'react';
import {
  AlertTriangle,
  Calendar,
  Clock3,
  History,
  Loader2,
  RefreshCw,
  X,
} from 'lucide-react';
import type {
  RecurrenceOccurrence,
  RecurrenceRule,
} from '../../types/recurrence.types';
import type { TaskItem } from '../../types/task.types';
import {
  formatRecurrenceInstant,
  resolveTaskTitle,
} from '../../utils/recurrence';

export interface RecurrenceOccurrencesPanelProps {
  rule: RecurrenceRule;
  taskTitle: string;
  occurrences: RecurrenceOccurrence[];
  tasks: TaskItem[];
  isLoading: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: () => void;
}

export const RecurrenceOccurrencesPanel: React.FC<
  RecurrenceOccurrencesPanelProps
> = ({
  rule,
  taskTitle,
  occurrences,
  tasks,
  isLoading,
  error,
  onClose,
  onRetry,
}) => {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="recurrence-occurrences-title"
        className="relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 pb-4">
          <div className="flex items-start gap-3">
            <div className="rounded-xl border border-violet-500/30 bg-violet-500/10 p-2">
              <History className="h-4 w-4 text-violet-300" />
            </div>
            <div>
              <h2
                id="recurrence-occurrences-title"
                className="text-base font-bold text-white"
              >
                Generated occurrences
              </h2>
              <p className="mt-1 text-xs text-slate-400">
                Confirmed occurrence links for{' '}
                <span className="font-semibold text-slate-200">{taskTitle}</span>
                .
              </p>
              <p className="mt-1 text-[10px] text-slate-500">
                Rule #{rule.id} · {rule.frequency} · every {rule.interval}{' '}
                {rule.frequency === 'daily'
                  ? 'day(s)'
                  : rule.frequency === 'weekly'
                    ? 'week(s)'
                    : 'month(s)'}
                {!rule.isActive && ' · inactive'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            title="Close occurrences"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-3 rounded-xl border border-slate-800 bg-slate-900/40 p-3 text-[11px] text-slate-400">
          The backend recurrence worker runs hourly. This view shows only
          occurrences already generated and stored by the backend; it never
          creates future tasks.
        </div>

        {error && (
          <div
            role="alert"
            className="mt-3 flex flex-col gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
              <div>
                <p className="text-[11px] font-semibold text-rose-200">
                  Occurrence service error
                </p>
                <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
                <p className="mt-0.5 text-[10px] text-rose-200/60">
                  Last confirmed occurrences remain visible.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={onRetry}
              disabled={isLoading}
              className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1.5 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw
                className={`h-3 w-3 ${isLoading ? 'animate-spin' : ''}`}
              />
              <span>Retry</span>
            </button>
          </div>
        )}

        <div className="mt-4" aria-busy={isLoading}>
          {isLoading && occurrences.length === 0 ? (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-800 bg-slate-900/30 p-10 text-xs text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin text-violet-400" />
              <span>Loading generated occurrences…</span>
            </div>
          ) : occurrences.length === 0 ? (
            error ? null : (
              <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-10 text-center">
                <History className="mx-auto h-10 w-10 text-slate-600" />
                <p className="mt-3 text-sm font-semibold text-slate-300">
                  No occurrences generated yet
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  The backend worker has not generated any occurrence tasks for
                  this rule yet. Check again after the next hourly run.
                </p>
              </div>
            )
          ) : (
            <ul className="space-y-2">
              {occurrences.map((occurrence) => {
                const linked = resolveTaskTitle(occurrence.taskId, tasks);
                const createdAt = formatRecurrenceInstant(occurrence.createdAt);

                return (
                  <li
                    key={occurrence.id}
                    className="flex flex-col justify-between gap-2 rounded-xl border border-slate-800 bg-slate-900/40 p-3 sm:flex-row sm:items-center"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="flex items-center gap-1.5 font-mono text-xs font-semibold text-violet-200">
                          <Calendar className="h-3.5 w-3.5 text-violet-400" />
                          {occurrence.occurrenceDate}
                        </span>
                        <span className="text-[10px] text-slate-500">
                          Task #{occurrence.taskId}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-slate-200">
                        {linked.title}
                      </p>
                      {linked.missing && (
                        <p className="mt-0.5 text-[10px] text-amber-300/80">
                          Generated task is not in the current task list.
                        </p>
                      )}
                    </div>

                    <div className="shrink-0 text-left text-[10px] text-slate-500 sm:text-right">
                      <span className="inline-flex items-center gap-1">
                        <Clock3 className="h-3 w-3" />
                        Generated {createdAt ?? occurrence.createdAt}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="mt-5 flex justify-end border-t border-slate-800 pt-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default RecurrenceOccurrencesPanel;
