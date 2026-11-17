import React, { useState } from 'react';
import {
  Plus,
  Bell,
  Clock,
  Calendar,
  Trash2,
  Edit2,
  X,
  AlertTriangle,
  RefreshCw,
  Loader2,
  Ban,
} from 'lucide-react';
import { useReminderStore } from '../stores/reminder.store';
import { useTaskStore } from '../stores/task.store';
import {
  isLiveReminder,
  type ReminderItem,
  type ReminderStatus,
} from '../types/reminder.types';
import {
  formatOffsetMinutes,
  localDateString,
  localWallClockToUtcIso,
} from '../utils/reminderTime';
import {
  getTaskReminderEligibility,
  isTaskReminderEligible,
} from '../utils/reminderEligibility';

const OFFSET_UNIT_MINUTES = {
  minutes: 1,
  hours: 60,
  days: 1440,
} as const;

type OffsetUnit = keyof typeof OFFSET_UNIT_MINUTES;

const STATUS_STYLES: Record<ReminderStatus, string> = {
  pending: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  processing: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  delivered: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  cancelled: 'border-slate-600/40 bg-slate-700/30 text-slate-400',
  suppressed: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
  failed: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
};

const STATUS_LABELS: Record<ReminderStatus, string> = {
  pending: 'Pending',
  processing: 'Processing',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
  suppressed: 'Suppressed',
  failed: 'Failed',
};

export const Reminders: React.FC = () => {
  const reminders = useReminderStore((s) => s.reminders);
  const isLoading = useReminderStore((s) => s.isLoading);
  const isCreating = useReminderStore((s) => s.isCreating);
  const mutatingIds = useReminderStore((s) => s.mutatingIds);
  const error = useReminderStore((s) => s.error);
  const fetchReminders = useReminderStore((s) => s.fetchReminders);
  const createReminder = useReminderStore((s) => s.createReminder);
  const rescheduleReminder = useReminderStore((s) => s.rescheduleReminder);
  const cancelReminder = useReminderStore((s) => s.cancelReminder);
  const deleteReminder = useReminderStore((s) => s.deleteReminder);

  const tasks = useTaskStore((s) => s.tasks);

  const [activeTab, setActiveTab] = useState<'today' | 'upcoming' | 'cancelled' | 'all'>('today');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingReminder, setEditingReminder] = useState<ReminderItem | null>(null);

  const [todayStr] = useState(localDateString);

  // Create form state. Titles and descriptions come from the selected task.
  const [formTaskId, setFormTaskId] = useState('');
  const [scheduleMode, setScheduleMode] = useState<'absolute' | 'offset'>('absolute');
  const [formDate, setFormDate] = useState(localDateString);
  const [formTime, setFormTime] = useState('18:00');
  const [offsetValue, setOffsetValue] = useState('30');
  const [offsetUnit, setOffsetUnit] = useState<OffsetUnit>('minutes');

  // Reschedule form state.
  const [rescheduleDate, setRescheduleDate] = useState(localDateString);
  const [rescheduleTime, setRescheduleTime] = useState('18:00');

  const [formError, setFormError] = useState<string | null>(null);

  // The store and the UI share one eligibility rule based on the last
  // backend-confirmed task status.
  const confirmedTasks = tasks.filter((t) => t.backendId !== undefined);
  const reminderTasks = confirmedTasks.filter(isTaskReminderEligible);

  // Resolve from all confirmed tasks so a task that became ineligible while
  // the form was open can be explained instead of silently disappearing.
  const selectedTask = confirmedTasks.find(
    (t) => String(t.backendId) === formTaskId
  );
  const selectedEligibility = getTaskReminderEligibility(selectedTask);

  const editingLinkedTask =
    editingReminder?.taskId === undefined
      ? undefined
      : confirmedTasks.find((t) => t.backendId === editingReminder.taskId);
  const editingEligibility = getTaskReminderEligibility(editingLinkedTask);

  const openCreateModal = () => {
    const firstTask = reminderTasks[0];

    setEditingReminder(null);
    setFormTaskId(firstTask?.backendId !== undefined ? String(firstTask.backendId) : '');
    setScheduleMode('absolute');
    setFormDate(localDateString());
    setFormTime('18:00');
    setOffsetValue('30');
    setOffsetUnit('minutes');
    setFormError(null);
    setModalOpen(true);
  };

  const openEditModal = (r: ReminderItem) => {
    if (r.status !== 'pending') return;

    setEditingReminder(r);
    setRescheduleDate(r.date);
    setRescheduleTime(r.time);
    setFormError(null);
    setModalOpen(true);
  };

  const closeModal = () => {
    if (isCreating) return;
    setModalOpen(false);
    setEditingReminder(null);
    setFormError(null);
  };

  const handleCreateReminder = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const taskId = Number(formTaskId);

    if (!Number.isInteger(taskId) || taskId <= 0) {
      setFormError('Choose a saved task to attach this reminder to.');
      return;
    }

    // Re-check freshly confirmed state at submit time. A task may have been
    // completed or cancelled after the form was opened.
    const currentTask = useTaskStore
      .getState()
      .tasks.find((t) => t.backendId === taskId);
    const currentEligibility = getTaskReminderEligibility(currentTask);

    if (!currentEligibility.eligible) {
      setFormError(
        currentEligibility.message ||
          'Reminders can only be scheduled for pending or in-progress tasks.'
      );
      return;
    }

    if (scheduleMode === 'offset' && !selectedTask?.dueDate) {
      setFormError('The selected task has no deadline. Choose a specific time instead.');
      return;
    }

    try {
      let created = false;

      if (scheduleMode === 'offset') {
        const value = Number(offsetValue);

        if (!Number.isFinite(value) || value < 0) {
          setFormError('Offset must be zero or greater.');
          return;
        }

        const offsetMinutes = Math.round(value * OFFSET_UNIT_MINUTES[offsetUnit]);

        if (offsetMinutes > 525600) {
          setFormError('Offset cannot exceed 365 days.');
          return;
        }

        created = await createReminder({ taskId, offsetMinutes });
      } else {
        // Converts local wall-clock input to a genuine UTC instant.
        const remindAt = localWallClockToUtcIso(formDate, formTime);
        created = await createReminder({ taskId, remindAt });
      }

      if (created) {
        setModalOpen(false);
      } else {
        setFormError(
          useReminderStore.getState().error ||
            'Reminder could not be created. No changes were saved.'
        );
      }
    } catch (err: unknown) {
      setFormError(
        err instanceof Error && err.message
          ? err.message
          : 'Select a valid reminder date and time.'
      );
    }
  };

  const handleReschedule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingReminder) return;

    setFormError(null);

    const linkedTask = useTaskStore
      .getState()
      .tasks.find(
        (t) =>
          editingReminder.taskId !== undefined &&
          (t.backendId === editingReminder.taskId ||
            t.id === String(editingReminder.taskId))
      );
    const eligibility = getTaskReminderEligibility(linkedTask);

    if (!eligibility.eligible) {
      setFormError(
        `This reminder cannot be rescheduled. ${eligibility.message || ''}`.trim()
      );
      return;
    }

    try {
      const remindAt = localWallClockToUtcIso(rescheduleDate, rescheduleTime);
      const updated = await rescheduleReminder(editingReminder.id, { remindAt });

      if (updated) {
        setModalOpen(false);
        setEditingReminder(null);
      } else {
        setFormError(
          useReminderStore.getState().error ||
            'Reminder could not be rescheduled. Previous values were restored.'
        );
      }
    } catch (err: unknown) {
      setFormError(
        err instanceof Error && err.message
          ? err.message
          : 'Select a valid reminder date and time.'
      );
    }
  };

  const filteredReminders = reminders
    .map((r) => {
      // Keep titles live with their linked task, even if the task was renamed
      // after the reminder list was last fetched.
      if (r.taskId === undefined) return r;
      const task = tasks.find(
        (t) => t.backendId === r.taskId || t.id === String(r.taskId)
      );
      return task
        ? { ...r, title: task.title, description: task.description }
        : r;
    })
    .filter((r) => {
      if (activeTab === 'today') {
        return r.date === todayStr && isLiveReminder(r.status);
      }
      if (activeTab === 'upcoming') {
        return r.date > todayStr && isLiveReminder(r.status);
      }
      if (activeTab === 'cancelled') {
        return r.status === 'cancelled';
      }
      return true;
    });

  const isSaving = editingReminder
    ? mutatingIds.includes(editingReminder.id)
    : isCreating;
  const formEligibilityBlocked = editingReminder
    ? !editingEligibility.eligible
    : !selectedEligibility.eligible;

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            Reminders & Schedules
          </h1>
          <p className="text-xs sm:text-sm text-slate-400">
            Backend-scheduled reminders for your saved tasks.
          </p>
        </div>

        <button
          type="button"
          onClick={openCreateModal}
          disabled={reminderTasks.length === 0}
          title={
            reminderTasks.length === 0
              ? 'Create a pending task before scheduling a reminder'
              : undefined
          }
          className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2.5 text-xs sm:text-sm font-semibold text-white shadow-lg shadow-amber-600/25 transition hover:bg-amber-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />
          <span>New Reminder</span>
        </button>
      </div>

      {/* Data reliability banner: never replace confirmed data with mock data */}
      {error && (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <div>
              <p className="text-xs font-semibold text-rose-200">Reminder service error</p>
              <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
              <p className="mt-1 text-[10px] text-rose-200/60">
                Last confirmed reminder data is preserved until a refresh succeeds.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              void fetchReminders();
            }}
            disabled={isLoading}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Retry</span>
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
        {(['today', 'upcoming', 'cancelled', 'all'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={`rounded-xl px-4 py-2 text-xs font-semibold capitalize transition ${
              activeTab === tab
                ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Reminders List */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" aria-busy={isLoading}>
        {isLoading && reminders.length === 0 ? (
          <div className="col-span-full flex items-center justify-center gap-2 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-xs text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
            <span>Loading reminders…</span>
          </div>
        ) : filteredReminders.length === 0 ? (
          error ? null : (
            <div className="col-span-full rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
              <Bell className="mx-auto h-12 w-12 text-slate-600" />
              <p className="mt-3 text-sm font-semibold text-slate-300">
                {reminders.length === 0 ? 'No reminders yet' : 'No reminders in this view'}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {reminders.length === 0
                  ? 'Create a reminder for one of your saved tasks.'
                  : 'Try another tab or create a new reminder.'}
              </p>
            </div>
          )
        ) : (
          filteredReminders.map((r) => {
            const isMutating = mutatingIds.includes(r.id);
            const isTerminal = r.status !== 'pending' && r.status !== 'processing';
            const linkedTask =
              r.taskId === undefined
                ? undefined
                : tasks.find(
                    (t) =>
                      t.backendId === r.taskId || t.id === String(r.taskId)
                  );
            const linkedEligibility = getTaskReminderEligibility(linkedTask);
            const canReschedule =
              r.status === 'pending' && linkedEligibility.eligible;

            return (
              <div
                key={r.id}
                className={`flex flex-col justify-between rounded-2xl border p-4.5 transition ${
                  isTerminal
                    ? 'border-slate-800/60 bg-slate-950/40 opacity-70'
                    : 'border-slate-800/90 bg-slate-900/40 hover:border-amber-500/40 hover:bg-slate-900/70 shadow-lg'
                }`}
              >
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <Bell className="h-4 w-4 shrink-0 text-amber-400" />
                      <h3 className="truncate text-sm font-bold text-slate-100">
                        {r.title}
                      </h3>
                    </div>

                    <span
                      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${STATUS_STYLES[r.status]}`}
                    >
                      {STATUS_LABELS[r.status]}
                    </span>
                  </div>

                  {r.description && (
                    <p className="mt-2 text-xs text-slate-400 leading-relaxed pl-6.5">
                      {r.description}
                    </p>
                  )}
                </div>

                {/* Card Meta & Controls */}
                <div className="mt-4 flex items-center justify-between border-t border-slate-800/60 pt-3 text-[11px] text-slate-500">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="flex items-center gap-1 font-mono text-amber-300 font-semibold">
                      <Clock className="h-3 w-3" />
                      {r.time}
                    </span>
                    <span>·</span>
                    <span className="flex items-center gap-1 font-mono">
                      <Calendar className="h-3 w-3 text-slate-500" />
                      {r.date}
                    </span>
                    <span>·</span>
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                      {typeof r.offsetMinutes === 'number'
                        ? formatOffsetMinutes(r.offsetMinutes)
                        : 'At set time'}
                    </span>
                  </div>

                  <div className="flex shrink-0 items-center gap-1">
                    {canReschedule && (
                      <button
                        type="button"
                        onClick={() => openEditModal(r)}
                        disabled={isMutating}
                        className="rounded p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                        title="Reschedule"
                      >
                        <Edit2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                    {r.status === 'pending' && (
                      <button
                        type="button"
                        onClick={() => {
                          void cancelReminder(r.id);
                        }}
                        disabled={isMutating}
                        className="rounded p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                        title="Cancel reminder"
                      >
                        <Ban className="h-3.5 w-3.5" />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        void deleteReminder(r.id);
                      }}
                      disabled={isMutating}
                      className="rounded p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                      title="Delete reminder"
                    >
                      {isMutating ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {r.status === 'cancelled' && (
                  <p className="mt-2 text-[10px] text-slate-500">
                    Cancelled reminders cannot be reactivated. Create a new reminder instead.
                  </p>
                )}
                {r.status === 'pending' && !linkedEligibility.eligible && (
                  <p className="mt-2 text-[10px] text-amber-300/80">
                    This reminder cannot be rescheduled. {linkedEligibility.message}
                  </p>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Create / Reschedule Reminder Modal */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="reminder-form-title"
        >
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <h2 id="reminder-form-title" className="text-base font-bold text-white">
                {editingReminder ? 'Reschedule Reminder' : 'New Reminder'}
              </h2>
              <button
                type="button"
                onClick={closeModal}
                disabled={isSaving}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
                aria-label="Close reminder form"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form
              onSubmit={
                editingReminder ? handleReschedule : handleCreateReminder
              }
              className="mt-4 space-y-4"
            >
              {editingReminder ? (
                <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    Linked task
                  </p>
                  <p className="mt-1 text-sm font-semibold text-slate-100">
                    {editingReminder.title}
                  </p>
                  {editingReminder.offsetMinutes !== null &&
                    editingReminder.offsetMinutes !== undefined && (
                      <p className="mt-1 text-[11px] text-slate-400">
                        Currently {formatOffsetMinutes(editingReminder.offsetMinutes)}
                      </p>
                    )}
                  {!editingEligibility.eligible && (
                    <p
                      role="alert"
                      className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200"
                    >
                      This reminder cannot be rescheduled.{' '}
                      {editingEligibility.message}
                    </p>
                  )}
                </div>
              ) : (
                <>
                  <div>
                    <label htmlFor="reminder-form-task" className="block text-xs font-medium text-slate-300">
                      Task *
                    </label>
                    <select
                      id="reminder-form-task"
                      required
                      value={formTaskId}
                      onChange={(e) => {
                        setFormTaskId(e.target.value);
                        setFormError(null);
                      }}
                      className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:border-amber-500/60 focus:outline-none"
                    >
                      <option value="">Select a saved task</option>
                      {reminderTasks.map((task) => (
                        <option key={task.backendId} value={task.backendId}>
                          {task.title}
                        </option>
                      ))}
                      {selectedTask && !selectedEligibility.eligible && (
                        <option value={formTaskId} disabled>
                          {selectedTask.title} (no longer eligible)
                        </option>
                      )}
                    </select>
                    {reminderTasks.length === 0 && (
                      <p className="mt-1 text-[11px] text-amber-300/80">
                        Create a pending or in-progress task before scheduling a
                        reminder.
                      </p>
                    )}
                    {selectedTask && !selectedEligibility.eligible && (
                      <p
                        role="alert"
                        className="mt-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200"
                      >
                        {selectedEligibility.message} Choose another pending or
                        in-progress task.
                      </p>
                    )}
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-300">
                      Schedule
                    </label>
                    <div className="mt-1 flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setScheduleMode('absolute')}
                        className={`flex-1 rounded-xl border px-3 py-2 text-xs font-semibold transition ${
                          scheduleMode === 'absolute'
                            ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                            : 'border-slate-800 bg-slate-900 text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        Specific time
                      </button>
                      <button
                        type="button"
                        onClick={() => setScheduleMode('offset')}
                        disabled={!selectedTask?.dueDate}
                        title={
                          !selectedTask?.dueDate
                            ? 'The selected task has no deadline'
                            : undefined
                        }
                        className={`flex-1 rounded-xl border px-3 py-2 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                          scheduleMode === 'offset'
                            ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                            : 'border-slate-800 bg-slate-900 text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        Before deadline
                      </button>
                    </div>
                  </div>
                </>
              )}

              {(!editingReminder && scheduleMode === 'offset') ? (
                <div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-slate-300">
                        Offset
                      </label>
                      <input
                        type="number"
                        required
                        min={0}
                        step={1}
                        value={offsetValue}
                        onChange={(e) => setOffsetValue(e.target.value)}
                        className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white focus:border-amber-500/60 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-slate-300">
                        Unit
                      </label>
                      <select
                        value={offsetUnit}
                        onChange={(e) => setOffsetUnit(e.target.value as OffsetUnit)}
                        className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                      >
                        <option value="minutes">Minutes</option>
                        <option value="hours">Hours</option>
                        <option value="days">Days</option>
                      </select>
                    </div>
                  </div>
                  <p className="mt-1 text-[11px] text-slate-500">
                    Reminder is resolved from the task deadline
                    {selectedTask?.dueDate ? ` (${selectedTask.dueDate})` : ''} in
                    its own timezone.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="reminder-form-date" className="block text-xs font-medium text-slate-300">
                      Date
                    </label>
                    <input
                      id="reminder-form-date"
                      type="date"
                      required
                      value={editingReminder ? rescheduleDate : formDate}
                      onChange={(e) =>
                        editingReminder
                          ? setRescheduleDate(e.target.value)
                          : setFormDate(e.target.value)
                      }
                      className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white focus:outline-none"
                    />
                  </div>

                  <div>
                    <label htmlFor="reminder-form-time" className="block text-xs font-medium text-slate-300">
                      Time
                    </label>
                    <input
                      id="reminder-form-time"
                      type="time"
                      required
                      value={editingReminder ? rescheduleTime : formTime}
                      onChange={(e) =>
                        editingReminder
                          ? setRescheduleTime(e.target.value)
                          : setFormTime(e.target.value)
                      }
                      className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white focus:outline-none"
                    />
                  </div>
                </div>
              )}

              {formError && (
                <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
                  {formError}
                </p>
              )}

              <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
                <button
                  type="button"
                  onClick={closeModal}
                  disabled={isSaving}
                  className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSaving || formEligibilityBlocked}
                  className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {editingReminder
                    ? isSaving
                      ? 'Saving…'
                      : 'Save Time'
                    : isSaving
                      ? 'Creating…'
                      : 'Create Reminder'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Reminders;
