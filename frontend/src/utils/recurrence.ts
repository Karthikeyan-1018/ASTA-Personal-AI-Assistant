import type {
  RecurrenceCreate,
  RecurrenceOccurrenceResponse,
  RecurrenceResponse,
  RecurrenceUpdate,
} from '../types/api.types';
import type {
  RecurrenceDraft,
  RecurrenceFormErrors,
  RecurrenceFrequency,
  RecurrenceOccurrence,
  RecurrenceRule,
} from '../types/recurrence.types';
import type { TaskItem } from '../types/task.types';
import { parseBackendInstant } from './reminderTime';

export interface WeekdayOption {
  value: number;
  short: string;
  label: string;
}

/** Backend weekday contract: 0 = Monday through 6 = Sunday. */
export const WEEKDAY_OPTIONS: WeekdayOption[] = [
  { value: 0, short: 'Mon', label: 'Monday' },
  { value: 1, short: 'Tue', label: 'Tuesday' },
  { value: 2, short: 'Wed', label: 'Wednesday' },
  { value: 3, short: 'Thu', label: 'Thursday' },
  { value: 4, short: 'Fri', label: 'Friday' },
  { value: 5, short: 'Sat', label: 'Saturday' },
  { value: 6, short: 'Sun', label: 'Sunday' },
];

const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export function isRecurrenceFrequency(
  value: unknown
): value is RecurrenceFrequency {
  return value === 'daily' || value === 'weekly' || value === 'monthly';
}

export function isValidIsoDate(value: string): boolean {
  if (!ISO_DATE_PATTERN.test(value)) return false;

  const [year, month, day] = value.split('-').map(Number);
  if (year < 1000 || year > 9999) return false;
  if (month < 1 || month > 12) return false;
  if (day < 1 || day > 31) return false;

  const parsed = new Date(year, month - 1, day);
  return (
    parsed.getFullYear() === year &&
    parsed.getMonth() === month - 1 &&
    parsed.getDate() === day
  );
}

/** Convert a YYYY-MM-DD calendar date into the backend weekday index. */
export function weekdayFromIsoDate(value: string): number | null {
  if (!isValidIsoDate(value)) return null;

  const [year, month, day] = value.split('-').map(Number);
  // JavaScript uses 0 = Sunday; the backend uses 0 = Monday.
  return (new Date(year, month - 1, day).getDay() + 6) % 7;
}

export function weekdaysAreValid(days: readonly number[]): boolean {
  return days.every(
    (day) => Number.isInteger(day) && day >= 0 && day <= 6
  );
}

export function normaliseWeekdays(days: readonly number[]): number[] {
  return Array.from(new Set(days)).sort((a, b) => a - b);
}

export function weekdayLabel(value: number): string {
  return (
    WEEKDAY_OPTIONS.find((option) => option.value === value)?.short ??
    String(value)
  );
}

export function formatWeekdays(days: readonly number[]): string {
  if (!weekdaysAreValid(days)) return '';
  return normaliseWeekdays(days)
    .map((day) => weekdayLabel(day))
    .join(', ');
}

export function formatFrequency(
  frequency: RecurrenceFrequency,
  interval: number
): string {
  const count = Number.isInteger(interval) && interval > 0 ? interval : 1;

  if (count === 1) {
    if (frequency === 'daily') return 'Daily';
    if (frequency === 'weekly') return 'Weekly';
    return 'Monthly';
  }

  const unit =
    frequency === 'daily'
      ? 'day'
      : frequency === 'weekly'
        ? 'week'
        : 'month';

  return `Every ${count} ${unit}s`;
}

export function formatIntervalUnit(frequency: RecurrenceFrequency): string {
  if (frequency === 'daily') return 'day(s)';
  if (frequency === 'weekly') return 'week(s)';
  return 'month(s)';
}

export function formatRecurrenceInstant(value: string | null): string | null {
  if (!value) return null;

  try {
    return parseBackendInstant(value).toLocaleString([], {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch {
    return value;
  }
}

export function firstFormError(errors: RecurrenceFormErrors): string | null {
  return (
    errors.taskId ||
    errors.frequency ||
    errors.interval ||
    errors.weekdays ||
    errors.startDate ||
    errors.endDate ||
    errors.general ||
    null
  );
}

function parseInterval(value: number | string): number | null {
  const raw = String(value ?? '').trim();
  if (!/^\d+$/.test(raw)) return null;

  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 365) return null;
  return parsed;
}

export interface RecurrenceValidationOptions {
  /** Create requires an explicit start date; edit uses the existing one. */
  requireStartDate?: boolean;
  /** The task due date is the earliest permitted start date. */
  startDateFloor?: string;
  /** The immutable start date used when editing. */
  fixedStartDate?: string;
}

export interface RecurrenceValidationResult {
  errors: RecurrenceFormErrors;
  values: {
    frequency: RecurrenceFrequency;
    interval: number;
    weekdays: number[];
    startDate: string;
    endDate: string | null;
  } | null;
}

/**
 * Validate the user-editable recurrence fields before an API request.
 *
 * Weekly weekdays are optional because the backend defaults an omitted list to
 * the start date's weekday. Non-weekly weekdays are rejected because the
 * backend only accepts them for weekly rules.
 */
export function validateRecurrenceDraft(
  draft: RecurrenceDraft,
  options: RecurrenceValidationOptions = {}
): RecurrenceValidationResult {
  const errors: RecurrenceFormErrors = {};

  if (!isRecurrenceFrequency(draft.frequency)) {
    errors.frequency = 'Choose daily, weekly, or monthly.';
  }

  const interval = parseInterval(draft.interval);
  if (interval === null) {
    errors.interval = 'Interval must be a whole number from 1 to 365.';
  }

  if (!weekdaysAreValid(draft.weekdays)) {
    errors.weekdays = 'Weekdays must use 0 (Monday) through 6 (Sunday).';
  }

  const weekdays = weekdaysAreValid(draft.weekdays)
    ? normaliseWeekdays(draft.weekdays)
    : [];

  if (draft.frequency !== 'weekly' && weekdays.length > 0) {
    errors.weekdays = 'Weekdays can only be selected for weekly recurrence.';
  }

  const startDate =
    options.requireStartDate === false
      ? options.fixedStartDate ?? draft.startDate
      : draft.startDate.trim();

  if (options.requireStartDate !== false) {
    if (!startDate) {
      errors.startDate = 'Choose a start date.';
    } else if (!isValidIsoDate(startDate)) {
      errors.startDate = 'Choose a valid start date.';
    } else if (
      options.startDateFloor &&
      isValidIsoDate(options.startDateFloor) &&
      startDate < options.startDateFloor
    ) {
      errors.startDate = 'Start date cannot be before the task due date.';
    }
  }

  const endDate = draft.endDate.trim();
  if (endDate) {
    if (!isValidIsoDate(endDate)) {
      errors.endDate = 'Choose a valid end date.';
    } else if (
      startDate &&
      isValidIsoDate(startDate) &&
      endDate < startDate
    ) {
      errors.endDate = 'End date cannot be before the start date.';
    }
  }

  const hasErrors = Object.keys(errors).length > 0;

  return {
    errors,
    values: hasErrors
      ? null
      : {
          frequency: draft.frequency,
          interval: interval as number,
          weekdays,
          startDate,
          endDate: endDate || null,
        },
  };
}

export interface RecurrencePayloadResult<TPayload> {
  errors: RecurrenceFormErrors;
  payload: TPayload | null;
}

export function buildRecurrenceCreatePayload(
  draft: RecurrenceDraft,
  taskDueDate: string
): RecurrencePayloadResult<RecurrenceCreate> {
  // Weekdays from a previous weekly selection must not leak into daily or
  // monthly payloads when the user switches frequency in the same form.
  const effectiveWeekdays =
    draft.frequency === 'weekly' ? draft.weekdays : [];

  const validation = validateRecurrenceDraft(
    { ...draft, weekdays: effectiveWeekdays },
    { requireStartDate: true, startDateFloor: taskDueDate }
  );

  if (!validation.values) {
    return { errors: validation.errors, payload: null };
  }

  const { frequency, interval, weekdays, startDate, endDate } =
    validation.values;

  const payload: RecurrenceCreate = {
    frequency,
    interval,
    start_date: startDate,
  };

  // Omit weekdays for non-weekly rules. An omitted weekly list lets the
  // backend apply its documented start-date weekday default.
  if (frequency === 'weekly' && weekdays.length > 0) {
    payload.weekdays = weekdays;
  }

  if (endDate) {
    payload.end_date = endDate;
  }

  return { errors: {}, payload };
}

/**
 * Build a PATCH containing only changed, backend-supported fields.
 *
 * `start_date`, `task_id`, and user ownership are immutable. They are never
 * read from the draft and can never appear in the returned payload.
 */
export function buildRecurrenceUpdatePayload(
  rule: RecurrenceRule,
  draft: RecurrenceDraft
): RecurrencePayloadResult<RecurrenceUpdate> {
  const effectiveWeekdays =
    draft.frequency === 'weekly' ? draft.weekdays : [];

  const validation = validateRecurrenceDraft(
    { ...draft, startDate: rule.startDate, weekdays: effectiveWeekdays },
    {
      requireStartDate: false,
      fixedStartDate: rule.startDate,
    }
  );

  if (!validation.values) {
    return { errors: validation.errors, payload: null };
  }

  const { frequency, interval, weekdays, endDate } = validation.values;
  const payload: RecurrenceUpdate = {};

  if (frequency !== rule.frequency) {
    payload.frequency = frequency;
  }

  if (interval !== rule.interval) {
    payload.interval = interval;
  }

  if (frequency === 'weekly') {
    const current = normaliseWeekdays(rule.weekdays);
    const next = normaliseWeekdays(weekdays);

    if (current.join(',') !== next.join(',')) {
      payload.weekdays = next;
    }
  } else if (rule.weekdays.length > 0) {
    // Explicitly clear stale weekday metadata when leaving weekly cadence.
    payload.weekdays = null;
  }

  const currentEndDate = rule.endDate ?? '';
  const nextEndDate = endDate ?? '';

  if (nextEndDate !== currentEndDate) {
    payload.end_date = nextEndDate || null;
  }

  return { errors: {}, payload };
}

export function mapRecurrenceResponse(rule: RecurrenceResponse): RecurrenceRule {
  return {
    id: rule.id,
    taskId: rule.task_id,
    frequency: rule.frequency,
    interval: rule.interval,
    weekdays: normaliseWeekdays(rule.weekdays ?? []),
    startDate: rule.start_date,
    endDate: rule.end_date,
    timezone: rule.timezone,
    anchorTime: rule.anchor_time,
    isActive: rule.is_active,
    lastGeneratedDate: rule.last_generated_date,
    nextGenerationAt: rule.next_generation_at,
    createdAt: rule.created_at,
    updatedAt: rule.updated_at,
  };
}

export function mapRecurrenceOccurrenceResponse(
  occurrence: RecurrenceOccurrenceResponse
): RecurrenceOccurrence {
  return {
    id: occurrence.id,
    recurrenceId: occurrence.recurrence_id,
    taskId: occurrence.task_id,
    occurrenceDate: occurrence.occurrence_date,
    createdAt: occurrence.created_at,
  };
}

export interface ResolvedTaskTitle {
  title: string;
  missing: boolean;
}

/** Resolve an occurrence's linked task title from the loaded task list. */
export function resolveTaskTitle(
  taskId: number,
  tasks: readonly TaskItem[]
): ResolvedTaskTitle {
  const task = tasks.find(
    (candidate) =>
      candidate.backendId === taskId || candidate.id === String(taskId)
  );

  return task
    ? { title: task.title, missing: false }
    : { title: `Task #${taskId}`, missing: true };
}

export function hasRuleForTask(
  rules: readonly RecurrenceRule[],
  taskId: number
): boolean {
  return rules.some((rule) => rule.taskId === taskId);
}

/**
 * Explain why a task cannot be given a recurrence rule. The backend requires
 * an owned, open task with a due date and allows exactly one rule per task.
 */
export function getRecurrenceTaskBlockReason(
  task: TaskItem,
  rules: readonly RecurrenceRule[]
): string | null {
  if (task.backendId === undefined) {
    return 'This task is not confirmed by the server yet.';
  }

  if (
    task.completed ||
    task.status === 'completed' ||
    task.backendStatus === 'cancelled'
  ) {
    return 'Only pending or in-progress tasks can recur.';
  }

  if (!task.dueDate) {
    return 'The task needs a due date before it can recur.';
  }

  if (hasRuleForTask(rules, task.backendId)) {
    return 'This task already has a recurrence rule.';
  }

  return null;
}

export function getRecurrenceCandidateTasks(
  tasks: readonly TaskItem[],
  rules: readonly RecurrenceRule[]
): TaskItem[] {
  return tasks.filter(
    (task) => getRecurrenceTaskBlockReason(task, rules) === null
  );
}
