import React, { useMemo, useState } from 'react';
import { Calendar, Loader2, Lock, Repeat, X } from 'lucide-react';
import type { RecurrenceCreate, RecurrenceUpdate } from '../../types/api.types';
import type {
  RecurrenceDraft,
  RecurrenceFrequency,
  RecurrenceRule,
} from '../../types/recurrence.types';
import {
  MAX_RECURRENCE_INTERVAL,
  MIN_RECURRENCE_INTERVAL,
} from '../../types/recurrence.types';
import type { TaskItem } from '../../types/task.types';
import {
  WEEKDAY_OPTIONS,
  buildRecurrenceCreatePayload,
  buildRecurrenceUpdatePayload,
  firstFormError,
  getRecurrenceCandidateTasks,
  getRecurrenceTaskBlockReason,
  isValidIsoDate,
  weekdayFromIsoDate,
} from '../../utils/recurrence';

export interface RecurrenceFormModalProps {
  mode: 'create' | 'edit';
  rule?: RecurrenceRule | null;
  tasks: TaskItem[];
  rules: RecurrenceRule[];
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onCreate: (taskId: number, payload: RecurrenceCreate) => void | Promise<void>;
  onUpdate: (ruleId: number, payload: RecurrenceUpdate) => void | Promise<void>;
  onClearError?: () => void;
}

const FREQUENCY_OPTIONS: Array<{
  value: RecurrenceFrequency;
  label: string;
  hint: string;
}> = [
  { value: 'daily', label: 'Daily', hint: 'Every day or every N days' },
  { value: 'weekly', label: 'Weekly', hint: 'Selected weekdays each week' },
  { value: 'monthly', label: 'Monthly', hint: 'Same day each month' },
];

function applyStartWeekday(
  date: string,
  weekdays: number[],
  setWeekdays: (days: number[]) => void
): void {
  if (weekdays.length > 0) return;
  const weekday = weekdayFromIsoDate(date);
  if (weekday !== null) setWeekdays([weekday]);
}

export const RecurrenceFormModal: React.FC<RecurrenceFormModalProps> = ({
  mode,
  rule,
  tasks,
  rules,
  isSubmitting,
  error,
  onClose,
  onCreate,
  onUpdate,
  onClearError,
}) => {
  const isEdit = mode === 'edit' && Boolean(rule);

  const [taskId, setTaskId] = useState(
    isEdit && rule ? String(rule.taskId) : ''
  );
  const [frequency, setFrequency] = useState<RecurrenceFrequency>(
    rule?.frequency ?? 'daily'
  );
  const [interval, setIntervalValue] = useState(String(rule?.interval ?? 1));
  const [weekdays, setWeekdays] = useState<number[]>(
    rule?.weekdays ? [...rule.weekdays] : []
  );
  const [startDate, setStartDate] = useState(rule?.startDate ?? '');
  const [endDate, setEndDate] = useState(rule?.endDate ?? '');
  const [formError, setFormError] = useState<string | null>(null);
  const [noChanges, setNoChanges] = useState(false);

  const candidateTasks = useMemo(
    () => getRecurrenceCandidateTasks(tasks, rules),
    [tasks, rules]
  );

  const selectedTask = useMemo(
    () => tasks.find((task) => String(task.backendId) === taskId),
    [tasks, taskId]
  );

  const selectedTaskTitle = selectedTask?.title ?? (rule ? `Task #${rule.taskId}` : '');

  const clearFeedback = () => {
    setFormError(null);
    setNoChanges(false);
    onClearError?.();
  };

  const handleTaskChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const value = event.target.value;
    setTaskId(value);
    clearFeedback();

    const nextTask = tasks.find(
      (task) => String(task.backendId) === value
    );

    if (nextTask?.dueDate) {
      setStartDate(nextTask.dueDate);
      if (frequency === 'weekly') {
        applyStartWeekday(nextTask.dueDate, weekdays, setWeekdays);
      }
    }
  };

  const handleFrequencyChange = (
    event: React.ChangeEvent<HTMLSelectElement>
  ) => {
    const nextFrequency = event.target.value as RecurrenceFrequency;
    setFrequency(nextFrequency);
    clearFeedback();

    if (nextFrequency === 'weekly') {
      applyStartWeekday(
        startDate || selectedTask?.dueDate || '',
        weekdays,
        setWeekdays
      );
    } else {
      setWeekdays([]);
    }
  };

  const handleStartDateChange = (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const value = event.target.value;
    setStartDate(value);
    clearFeedback();

    if (frequency === 'weekly' && isValidIsoDate(value)) {
      applyStartWeekday(value, weekdays, setWeekdays);
    }
  };

  const toggleWeekday = (value: number) => {
    clearFeedback();
    setWeekdays((current) =>
      current.includes(value)
        ? current.filter((day) => day !== value)
        : [...current, value].sort((a, b) => a - b)
    );
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (isSubmitting) return;

    setFormError(null);
    setNoChanges(false);

    const draft: RecurrenceDraft = {
      frequency,
      interval,
      weekdays,
      startDate,
      endDate,
    };

    if (isEdit && rule) {
      const result = buildRecurrenceUpdatePayload(rule, draft);

      if (!result.payload) {
        setFormError(firstFormError(result.errors));
        return;
      }

      if (Object.keys(result.payload).length === 0) {
        setNoChanges(true);
        return;
      }

      await onUpdate(rule.id, result.payload);
      return;
    }

    if (!selectedTask) {
      setFormError('Select a task with a due date.');
      return;
    }

    const blockReason = getRecurrenceTaskBlockReason(selectedTask, rules);
    if (blockReason) {
      setFormError(blockReason);
      return;
    }

    if (selectedTask.backendId === undefined || !selectedTask.dueDate) {
      setFormError('Select a task with a due date.');
      return;
    }

    const result = buildRecurrenceCreatePayload(draft, selectedTask.dueDate);

    if (!result.payload) {
      setFormError(firstFormError(result.errors));
      return;
    }

    await onCreate(selectedTask.backendId, result.payload);
  };

  const displayedError = formError || error;
  const startDateFloor = selectedTask?.dueDate;
  const earliestEndDate = startDate || startDateFloor;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="recurrence-form-title"
        className="relative max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-2.5">
            <Repeat className="h-5 w-5 text-violet-400" />
            <h2
              id="recurrence-form-title"
              className="text-base font-bold text-white"
            >
              {isEdit ? 'Edit Recurrence' : 'New Recurrence'}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {isEdit && rule ? (
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    Linked task
                  </label>
                  <input
                    type="text"
                    value={selectedTaskTitle}
                    readOnly
                    disabled
                    aria-label="Linked task (fixed)"
                    className="mt-1 h-9 w-full rounded-lg border border-slate-800 bg-slate-950 px-2.5 text-xs text-slate-300 disabled:opacity-80"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    Start date
                  </label>
                  <input
                    type="date"
                    value={rule.startDate}
                    readOnly
                    disabled
                    aria-label="Start date (fixed)"
                    className="mt-1 h-9 w-full rounded-lg border border-slate-800 bg-slate-950 px-2.5 text-xs text-slate-300 disabled:opacity-80"
                  />
                </div>
              </div>
              <p className="mt-2 flex items-center gap-1.5 text-[10px] text-slate-500">
                <Lock className="h-3 w-3" />
                The linked task and start date cannot be changed after creation.
              </p>
            </div>
          ) : (
            <div>
              <label
                htmlFor="recurrence-task"
                className="block text-xs font-medium text-slate-300"
              >
                Task *
              </label>
              <select
                id="recurrence-task"
                required
                value={taskId}
                onChange={handleTaskChange}
                disabled={isSubmitting}
                className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:border-violet-500/60 focus:outline-none disabled:opacity-60"
              >
                <option value="">Select an open task with a due date</option>
                {candidateTasks.map((task) => (
                  <option key={task.backendId} value={task.backendId}>
                    {task.title} — due {task.dueDate}
                  </option>
                ))}
              </select>
              {candidateTasks.length === 0 && (
                <p className="mt-1 text-[11px] text-amber-300/80">
                  No eligible tasks. A recurrence requires an open task with a
                  due date that does not already have a rule.
                </p>
              )}
              {selectedTask?.dueDate && (
                <p className="mt-1 text-[11px] text-slate-500">
                  The backend uses this task&apos;s deadline as the default start
                  date.
                </p>
              )}
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label
                htmlFor="recurrence-frequency"
                className="block text-xs font-medium text-slate-300"
              >
                Frequency
              </label>
              <select
                id="recurrence-frequency"
                value={frequency}
                onChange={handleFrequencyChange}
                disabled={isSubmitting}
                className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:border-violet-500/60 focus:outline-none disabled:opacity-60"
              >
                {FREQUENCY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label} — {option.hint}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="recurrence-interval"
                className="block text-xs font-medium text-slate-300"
              >
                Interval (1–{MAX_RECURRENCE_INTERVAL})
              </label>
              <input
                id="recurrence-interval"
                type="number"
                required
                min={MIN_RECURRENCE_INTERVAL}
                max={MAX_RECURRENCE_INTERVAL}
                step={1}
                value={interval}
                onChange={(event) => {
                  setIntervalValue(event.target.value);
                  clearFeedback();
                }}
                disabled={isSubmitting}
                className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white focus:border-violet-500/60 focus:outline-none disabled:opacity-60"
              />
              <p className="mt-1 text-[11px] text-slate-500">
                {frequency === 'daily'
                  ? 'Every N days.'
                  : frequency === 'weekly'
                    ? 'Every N weeks.'
                    : 'Every N months.'}
              </p>
            </div>
          </div>

          {frequency === 'weekly' && (
            <fieldset>
              <legend className="block text-xs font-medium text-slate-300">
                Weekdays
              </legend>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {WEEKDAY_OPTIONS.map((option) => {
                  const selected = weekdays.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={selected}
                      aria-label={option.label}
                      onClick={() => toggleWeekday(option.value)}
                      disabled={isSubmitting}
                      className={`rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${
                        selected
                          ? 'border-violet-500/40 bg-violet-500/15 text-violet-200'
                          : 'border-slate-800 bg-slate-900 text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {option.short}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                Monday is 0 and Sunday is 6. If none are selected, the backend
                uses the start date&apos;s weekday.
              </p>
            </fieldset>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label
                htmlFor="recurrence-start-date"
                className="block text-xs font-medium text-slate-300"
              >
                {isEdit ? 'Start date (fixed)' : 'Start date *'}
              </label>
              {isEdit && rule ? (
                <div className="mt-1 flex h-10 items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-3 text-xs text-slate-400">
                  <Lock className="h-3.5 w-3.5" />
                  <span className="font-mono">{rule.startDate}</span>
                </div>
              ) : (
                <input
                  id="recurrence-start-date"
                  type="date"
                  required
                  min={startDateFloor}
                  value={startDate}
                  onChange={handleStartDateChange}
                  disabled={isSubmitting}
                  className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white focus:border-violet-500/60 focus:outline-none disabled:opacity-60"
                />
              )}
              <p className="mt-1 text-[11px] text-slate-500">
                {isEdit
                  ? 'The start date is immutable after creation.'
                  : 'Defaults to the selected task deadline and cannot be before it.'}
              </p>
            </div>

            <div>
              <label
                htmlFor="recurrence-end-date"
                className="block text-xs font-medium text-slate-300"
              >
                End date (optional)
              </label>
              <input
                id="recurrence-end-date"
                type="date"
                min={earliestEndDate}
                value={endDate}
                onChange={(event) => {
                  setEndDate(event.target.value);
                  clearFeedback();
                }}
                disabled={isSubmitting}
                className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white focus:border-violet-500/60 focus:outline-none disabled:opacity-60"
              />
              <p className="mt-1 text-[11px] text-slate-500">
                Leave blank for an open-ended rule. Clear the value to remove an
                existing end date.
              </p>
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-3 text-[11px] text-slate-400">
            <div className="flex items-start gap-2">
              <Calendar className="mt-0.5 h-3.5 w-3.5 shrink-0 text-violet-400" />
              <p>
                Occurrences are generated by the backend recurrence worker,
                which runs hourly. This form does not create future tasks.
              </p>
            </div>
          </div>

          {noChanges && (
            <p
              role="status"
              className="rounded-xl border border-slate-700 bg-slate-800/60 px-3 py-2 text-[11px] text-slate-300"
            >
              No changes to save.
            </p>
          )}

          {displayedError && (
            <p
              role="alert"
              className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200"
            >
              {displayedError}
            </p>
          )}

          <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || (!isEdit && candidateTasks.length === 0)}
              className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              <span>{isEdit ? 'Save changes' : 'Create recurrence'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default RecurrenceFormModal;
