import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  Calendar,
  CalendarClock,
  CheckCircle2,
  Edit2,
  History,
  Loader2,
  Plus,
  Power,
  RefreshCw,
  Repeat,
  X,
} from 'lucide-react';
import DeactivateRecurrenceDialog from '../components/recurrences/DeactivateRecurrenceDialog';
import RecurrenceFormModal from '../components/recurrences/RecurrenceFormModal';
import RecurrenceOccurrencesPanel from '../components/recurrences/RecurrenceOccurrencesPanel';
import { useRecurrenceStore } from '../stores/recurrence.store';
import { useTaskStore } from '../stores/task.store';
import type { RecurrenceCreate, RecurrenceUpdate } from '../types/api.types';
import type { RecurrenceRule } from '../types/recurrence.types';
import {
  formatFrequency,
  formatIntervalUnit,
  formatRecurrenceInstant,
  formatWeekdays,
  getRecurrenceCandidateTasks,
  resolveTaskTitle,
} from '../utils/recurrence';

type RecurrenceTab = 'all' | 'active' | 'inactive';

export const Recurrences: React.FC = () => {
  const rules = useRecurrenceStore((s) => s.rules);
  const isLoading = useRecurrenceStore((s) => s.isLoading);
  const isRefreshing = useRecurrenceStore((s) => s.isRefreshing);
  const error = useRecurrenceStore((s) => s.error);
  const successMessage = useRecurrenceStore((s) => s.successMessage);
  const isCreating = useRecurrenceStore((s) => s.isCreating);
  const createError = useRecurrenceStore((s) => s.createError);
  const mutatingIds = useRecurrenceStore((s) => s.mutatingIds);
  const updateError = useRecurrenceStore((s) => s.updateError);
  const deactivationError = useRecurrenceStore((s) => s.deactivationError);
  const occurrencesByRule = useRecurrenceStore((s) => s.occurrencesByRule);
  const occurrenceLoadingIds = useRecurrenceStore((s) => s.occurrenceLoadingIds);
  const occurrenceErrors = useRecurrenceStore((s) => s.occurrenceErrors);

  const fetchRecurrences = useRecurrenceStore((s) => s.fetchRecurrences);
  const createRecurrence = useRecurrenceStore((s) => s.createRecurrence);
  const updateRecurrence = useRecurrenceStore((s) => s.updateRecurrence);
  const deactivateRecurrence = useRecurrenceStore((s) => s.deactivateRecurrence);
  const fetchOccurrences = useRecurrenceStore((s) => s.fetchOccurrences);
  const clearCreateError = useRecurrenceStore((s) => s.clearCreateError);
  const clearUpdateError = useRecurrenceStore((s) => s.clearUpdateError);
  const clearDeactivationError = useRecurrenceStore(
    (s) => s.clearDeactivationError
  );
  const clearSuccess = useRecurrenceStore((s) => s.clearSuccess);

  const tasks = useTaskStore((s) => s.tasks);

  const [activeTab, setActiveTab] = useState<RecurrenceTab>('all');
  const [formMode, setFormMode] = useState<'create' | 'edit' | null>(null);
  const [editingRule, setEditingRule] = useState<RecurrenceRule | null>(null);
  const [deactivateTarget, setDeactivateTarget] =
    useState<RecurrenceRule | null>(null);
  const [occurrenceTarget, setOccurrenceTarget] =
    useState<RecurrenceRule | null>(null);

  const candidateTasks = useMemo(
    () => getRecurrenceCandidateTasks(tasks, rules),
    [tasks, rules]
  );

  const activeCount = rules.filter((rule) => rule.isActive).length;
  const inactiveCount = rules.length - activeCount;

  const filteredRules = rules.filter((rule) => {
    if (activeTab === 'active') return rule.isActive;
    if (activeTab === 'inactive') return !rule.isActive;
    return true;
  });

  const openCreateModal = () => {
    clearCreateError();
    clearSuccess();
    setEditingRule(null);
    setFormMode('create');
  };

  const openEditModal = (rule: RecurrenceRule) => {
    clearUpdateError();
    clearSuccess();
    setEditingRule(rule);
    setFormMode('edit');
  };

  const closeFormModal = () => {
    setFormMode(null);
    setEditingRule(null);
    clearCreateError();
    clearUpdateError();
  };

  const handleCreate = async (
    taskId: number,
    payload: RecurrenceCreate
  ): Promise<void> => {
    const created = await createRecurrence(taskId, payload);
    if (created) {
      setFormMode(null);
      setEditingRule(null);
    }
  };

  const handleUpdate = async (
    ruleId: number,
    payload: RecurrenceUpdate
  ): Promise<void> => {
    const updated = await updateRecurrence(ruleId, payload);
    if (updated) {
      setFormMode(null);
      setEditingRule(null);
    }
  };

  const openDeactivateDialog = (rule: RecurrenceRule) => {
    clearDeactivationError();
    clearSuccess();
    setDeactivateTarget(rule);
  };

  const handleConfirmDeactivate = async (): Promise<void> => {
    if (!deactivateTarget) return;
    const deactivated = await deactivateRecurrence(deactivateTarget.id);
    if (deactivated) setDeactivateTarget(null);
  };

  const openOccurrences = (rule: RecurrenceRule) => {
    setOccurrenceTarget(rule);
    void fetchOccurrences(rule.id);
  };

  const createDisabled = candidateTasks.length === 0;
  const createDisabledReason =
    tasks.length === 0
      ? 'Create a task with a due date first.'
      : candidateTasks.length === 0
        ? 'Every open task with a due date already has a recurrence rule.'
        : undefined;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
            Recurrence Management
          </h1>
          <p className="text-xs text-slate-400 sm:text-sm">
            Backend-owned recurring task rules and generated occurrence history.
          </p>
        </div>

        <button
          type="button"
          onClick={openCreateModal}
          disabled={createDisabled}
          title={createDisabledReason}
          className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-xs font-semibold text-white shadow-lg shadow-violet-600/25 transition hover:bg-violet-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 sm:text-sm"
        >
          <Plus className="h-4 w-4" />
          <span>New Recurrence</span>
        </button>
      </div>

      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-4">
        <div className="flex items-start gap-3">
          <CalendarClock className="mt-0.5 h-4 w-4 shrink-0 text-violet-400" />
          <div>
            <p className="text-xs font-semibold text-slate-200">
              The backend recurrence worker runs hourly
            </p>
            <p className="mt-0.5 text-[11px] text-slate-400">
              This page shows confirmed rules and generated occurrences only. It
              does not predict or create future tasks. Deactivating a rule stops
              future generation but preserves the task and all history.
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
                Recurrence service error
              </p>
              <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
              <p className="mt-1 text-[10px] text-rose-200/60">
                Last confirmed recurrence data is preserved until a refresh
                succeeds.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              void fetchRecurrences();
            }}
            disabled={isLoading || isRefreshing}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`}
            />
            <span>Retry</span>
          </button>
        </div>
      )}

      {successMessage && (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3.5"
        >
          <div className="flex items-center gap-2.5">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
            <p className="text-xs font-semibold text-emerald-200">
              {successMessage}
            </p>
          </div>
          <button
            type="button"
            onClick={clearSuccess}
            className="rounded-lg p-1 text-emerald-300/70 hover:bg-emerald-500/10 hover:text-emerald-200"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-slate-400">Rules</p>
          <p className="mt-2 text-2xl font-bold text-white">{rules.length}</p>
        </div>
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-emerald-400">Active</p>
          <p className="mt-2 text-2xl font-bold text-white">{activeCount}</p>
        </div>
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-slate-400">Inactive</p>
          <p className="mt-2 text-2xl font-bold text-white">{inactiveCount}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-800 pb-3">
        {(['all', 'active', 'inactive'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={`rounded-xl px-3.5 py-1.5 text-xs font-semibold capitalize transition ${
              activeTab === tab
                ? 'border border-violet-500/30 bg-violet-500/15 text-violet-200'
                : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      <div className="space-y-3" aria-busy={isLoading}>
        {isLoading && rules.length === 0 ? (
          <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-xs text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin text-violet-400" />
            <span>Loading recurrence rules…</span>
          </div>
        ) : filteredRules.length === 0 ? (
          error ? null : (
            <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
              <Repeat className="mx-auto h-12 w-12 text-slate-600" />
              <p className="mt-3 text-sm font-semibold text-slate-300">
                {rules.length === 0
                  ? 'No recurrence rules yet'
                  : 'No rules in this view'}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {rules.length === 0
                  ? 'Create a rule for an open task that has a due date.'
                  : 'Switch tabs to see the other recurrence rules.'}
              </p>
              {rules.length === 0 && (
                <button
                  type="button"
                  onClick={openCreateModal}
                  disabled={createDisabled}
                  title={createDisabledReason}
                  className="mt-4 inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Plus className="h-3.5 w-3.5" />
                  <span>Create Recurrence</span>
                </button>
              )}
              {createDisabled && rules.length === 0 && (
                <p className="mt-2 text-[11px] text-amber-300/80">
                  {createDisabledReason}
                </p>
              )}
            </div>
          )
        ) : (
          filteredRules.map((rule) => {
            const task = resolveTaskTitle(rule.taskId, tasks);
            const isMutating = mutatingIds.includes(rule.id);
            const nextCheck = formatRecurrenceInstant(rule.nextGenerationAt);
            const weekdayText = formatWeekdays(rule.weekdays);

            return (
              <div
                key={rule.id}
                className={`rounded-2xl border p-4 transition ${
                  rule.isActive
                    ? 'border-slate-800/90 bg-slate-900/50 shadow-md hover:border-violet-500/40 hover:bg-slate-900/80'
                    : 'border-slate-800/60 bg-slate-950/40'
                }`}
              >
                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="mt-0.5 rounded-xl border border-violet-500/25 bg-violet-500/10 p-2">
                      <Repeat className="h-4 w-4 text-violet-300" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="truncate text-sm font-bold text-slate-100">
                          {task.title}
                        </h2>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                            rule.isActive
                              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                              : 'border-slate-600/40 bg-slate-700/30 text-slate-400'
                          }`}
                        >
                          {rule.isActive ? 'Active' : 'Inactive'}
                        </span>
                      </div>
                      {task.missing && (
                        <p className="mt-1 text-[10px] text-amber-300/80">
                          This task is not in the current task list. The rule is
                          still confirmed by the backend.
                        </p>
                      )}
                      <p className="mt-1 text-[11px] text-slate-400">
                        {formatFrequency(rule.frequency, rule.interval)}
                        {rule.frequency === 'weekly' && weekdayText
                          ? ` on ${weekdayText}`
                          : ''}
                      </p>
                    </div>
                  </div>

                  <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => openOccurrences(rule)}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/60 px-2.5 py-1.5 text-[11px] font-semibold text-slate-300 transition hover:border-violet-500/40 hover:text-violet-200"
                      title="View generated occurrences"
                    >
                      <History className="h-3.5 w-3.5" />
                      <span>Occurrences</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => openEditModal(rule)}
                      disabled={isMutating}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/60 px-2.5 py-1.5 text-[11px] font-semibold text-slate-300 transition hover:border-violet-500/40 hover:text-violet-200 disabled:cursor-not-allowed disabled:opacity-50"
                      title="Edit recurrence"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                      <span>Edit</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => openDeactivateDialog(rule)}
                      disabled={isMutating || !rule.isActive}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/60 px-2.5 py-1.5 text-[11px] font-semibold text-slate-300 transition hover:border-amber-500/40 hover:text-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
                      title={
                        rule.isActive
                          ? 'Deactivate recurrence (preserves task and history)'
                          : 'Recurrence is already inactive'
                      }
                    >
                      {isMutating ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Power className="h-3.5 w-3.5" />
                      )}
                      <span>Deactivate</span>
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-800/60 pt-3 text-[11px] sm:grid-cols-3 lg:grid-cols-4">
                  <div>
                    <p className="text-slate-500">Frequency</p>
                    <p className="mt-0.5 font-semibold text-slate-200">
                      {rule.frequency.charAt(0).toUpperCase() +
                        rule.frequency.slice(1)}
                    </p>
                  </div>
                  <div>
                    <p className="text-slate-500">Interval</p>
                    <p className="mt-0.5 font-mono font-semibold text-slate-200">
                      {rule.interval} {formatIntervalUnit(rule.frequency)}
                    </p>
                  </div>
                  {rule.frequency === 'weekly' && (
                    <div>
                      <p className="text-slate-500">Weekdays</p>
                      <p className="mt-0.5 font-semibold text-slate-200">
                        {weekdayText || 'Start date weekday'}
                      </p>
                    </div>
                  )}
                  <div>
                    <p className="text-slate-500">Start date</p>
                    <p className="mt-0.5 flex items-center gap-1 font-mono font-semibold text-slate-200">
                      <Calendar className="h-3 w-3 text-slate-400" />
                      {rule.startDate}
                    </p>
                  </div>
                  {rule.endDate && (
                    <div>
                      <p className="text-slate-500">End date</p>
                      <p className="mt-0.5 font-mono font-semibold text-slate-200">
                        {rule.endDate}
                      </p>
                    </div>
                  )}
                  {rule.timezone && (
                    <div>
                      <p className="text-slate-500">Timezone</p>
                      <p className="mt-0.5 font-semibold text-slate-200">
                        {rule.timezone}
                      </p>
                    </div>
                  )}
                  <div>
                    <p className="text-slate-500">Last generated</p>
                    <p className="mt-0.5 font-mono font-semibold text-slate-200">
                      {rule.lastGeneratedDate ?? 'None yet'}
                    </p>
                  </div>
                  <div>
                    <p className="text-slate-500">Next generation check</p>
                    <p className="mt-0.5 font-semibold text-slate-200">
                      {rule.isActive
                        ? nextCheck ?? 'Pending worker check'
                        : 'Stopped (inactive)'}
                    </p>
                  </div>
                </div>

                {!rule.isActive && (
                  <p className="mt-3 rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2 text-[10px] text-slate-400">
                    Inactive — future generation is stopped. The original task,
                    generated tasks, and occurrence history are preserved.
                  </p>
                )}
              </div>
            );
          })
        )}
      </div>

      {formMode && (
        <RecurrenceFormModal
          key={formMode === 'edit' ? `edit-${editingRule?.id ?? 'rule'}` : 'create'}
          mode={formMode}
          rule={editingRule}
          tasks={tasks}
          rules={rules}
          isSubmitting={
            formMode === 'edit' && editingRule
              ? mutatingIds.includes(editingRule.id)
              : isCreating
          }
          error={formMode === 'edit' ? updateError : createError}
          onClose={closeFormModal}
          onCreate={handleCreate}
          onUpdate={handleUpdate}
          onClearError={
            formMode === 'edit' ? clearUpdateError : clearCreateError
          }
        />
      )}

      {deactivateTarget && (
        <DeactivateRecurrenceDialog
          rule={deactivateTarget}
          taskTitle={resolveTaskTitle(deactivateTarget.taskId, tasks).title}
          isSubmitting={mutatingIds.includes(deactivateTarget.id)}
          error={deactivationError}
          onCancel={() => {
            setDeactivateTarget(null);
            clearDeactivationError();
          }}
          onConfirm={handleConfirmDeactivate}
        />
      )}

      {occurrenceTarget && (
        <RecurrenceOccurrencesPanel
          rule={occurrenceTarget}
          taskTitle={resolveTaskTitle(occurrenceTarget.taskId, tasks).title}
          occurrences={occurrencesByRule[occurrenceTarget.id] ?? []}
          tasks={tasks}
          isLoading={occurrenceLoadingIds.includes(occurrenceTarget.id)}
          error={occurrenceErrors[occurrenceTarget.id] ?? null}
          onClose={() => setOccurrenceTarget(null)}
          onRetry={() => {
            void fetchOccurrences(occurrenceTarget.id);
          }}
        />
      )}
    </div>
  );
};

export default Recurrences;
