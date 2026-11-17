import React from 'react';
import { AlertTriangle, Loader2, ShieldCheck, X } from 'lucide-react';
import type { RecurrenceRule } from '../../types/recurrence.types';

export interface DeactivateRecurrenceDialogProps {
  rule: RecurrenceRule;
  taskTitle: string;
  isSubmitting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
}

export const DeactivateRecurrenceDialog: React.FC<
  DeactivateRecurrenceDialogProps
> = ({ taskTitle, isSubmitting, error, onCancel, onConfirm }) => {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="deactivate-recurrence-title"
        className="relative w-full max-w-lg rounded-3xl border border-amber-500/25 bg-slate-950 p-6 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 pb-4">
          <div className="flex items-start gap-3">
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-2">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
            </div>
            <div>
              <h2
                id="deactivate-recurrence-title"
                className="text-base font-bold text-white"
              >
                Deactivate recurrence?
              </h2>
              <p className="mt-1 text-xs text-slate-400">
                Future generation stops for{' '}
                <span className="font-semibold text-slate-200">
                  {taskTitle}
                </span>
                .
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
            title="Cancel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
            <div className="flex items-center gap-2 text-[11px] font-semibold text-emerald-300">
              <ShieldCheck className="h-3.5 w-3.5" />
              Deactivation preserves your data
            </div>
            <ul className="mt-2 space-y-1 text-[11px] text-slate-300">
              <li>• The original task is kept.</li>
              <li>• Previously generated tasks are kept.</li>
              <li>• Recurrence occurrence history is kept.</li>
              <li>• No future occurrences are generated.</li>
            </ul>
          </div>

          <p className="text-[11px] text-slate-500">
            This action only deactivates the rule. It does not permanently delete
            the recurrence rule, the original task, generated tasks, or history.
          </p>

          {error && (
            <p
              role="alert"
              className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200"
            >
              {error}
            </p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
          >
            Keep active
          </button>
          <button
            type="button"
            onClick={() => {
              void onConfirm();
            }}
            disabled={isSubmitting}
            className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>Deactivate recurrence</span>
          </button>
        </div>
      </div>
    </div>
  );
};

export default DeactivateRecurrenceDialog;
