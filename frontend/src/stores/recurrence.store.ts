import { create } from 'zustand';
import recurrencesApi from '../services/api/recurrences.api';
import type {
  RecurrenceCreate,
  RecurrenceResponse,
  RecurrenceUpdate,
} from '../types/api.types';
import {
  MAX_RECURRENCE_INTERVAL,
  MIN_RECURRENCE_INTERVAL,
  RECURRENCE_PAGE_LIMIT,
  type RecurrenceOccurrence,
  type RecurrenceRule,
} from '../types/recurrence.types';
import {
  isRecurrenceFrequency,
  isValidIsoDate,
  mapRecurrenceOccurrenceResponse,
  mapRecurrenceResponse,
  weekdaysAreValid,
} from '../utils/recurrence';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

let fetchInFlight = false;
let stateVersion = 0;

const ALLOWED_UPDATE_KEYS = new Set([
  'frequency',
  'interval',
  'weekdays',
  'end_date',
  'timezone',
  'is_active',
]);

function validateInterval(value: unknown): string | null {
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value < MIN_RECURRENCE_INTERVAL ||
    value > MAX_RECURRENCE_INTERVAL
  ) {
    return 'Interval must be a whole number from 1 to 365.';
  }

  return null;
}

function validateWeekdayPayload(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (!Array.isArray(value) || !weekdaysAreValid(value)) {
    return 'Weekdays must use 0 (Monday) through 6 (Sunday).';
  }

  return null;
}

function validateCreatePayload(payload: RecurrenceCreate): string | null {
  if (!payload || typeof payload !== 'object') {
    return 'A recurrence payload is required.';
  }

  if (!isRecurrenceFrequency(payload.frequency)) {
    return 'Choose daily, weekly, or monthly.';
  }

  if (payload.interval !== undefined) {
    const error = validateInterval(payload.interval);
    if (error) return error;
  }

  const weekdayError = validateWeekdayPayload(payload.weekdays);
  if (weekdayError) return weekdayError;

  if (
    payload.frequency !== 'weekly' &&
    Array.isArray(payload.weekdays) &&
    payload.weekdays.length > 0
  ) {
    return 'Weekdays can only be supplied for weekly recurrence.';
  }

  if (
    payload.start_date !== undefined &&
    payload.start_date !== null &&
    !isValidIsoDate(payload.start_date)
  ) {
    return 'Start date must be a valid YYYY-MM-DD date.';
  }

  if (
    payload.end_date !== undefined &&
    payload.end_date !== null &&
    !isValidIsoDate(payload.end_date)
  ) {
    return 'End date must be a valid YYYY-MM-DD date.';
  }

  if (
    payload.start_date &&
    payload.end_date &&
    payload.end_date < payload.start_date
  ) {
    return 'End date cannot be before the start date.';
  }

  return null;
}

function validateUpdatePayload(
  payload: RecurrenceUpdate,
  rule: RecurrenceRule
): string | null {
  if (!payload || typeof payload !== 'object') {
    return 'A recurrence update payload is required.';
  }

  const unknown = Object.keys(payload).filter(
    (key) => !ALLOWED_UPDATE_KEYS.has(key)
  );

  if (unknown.length > 0) {
    return `Unsupported recurrence field(s): ${unknown.sort().join(', ')}.`;
  }

  if (
    payload.frequency !== undefined &&
    payload.frequency !== null &&
    !isRecurrenceFrequency(payload.frequency)
  ) {
    return 'Choose daily, weekly, or monthly.';
  }

  if (payload.interval !== undefined && payload.interval !== null) {
    const error = validateInterval(payload.interval);
    if (error) return error;
  }

  const weekdayError = validateWeekdayPayload(payload.weekdays);
  if (weekdayError) return weekdayError;

  const effectiveFrequency = payload.frequency ?? rule.frequency;

  if (
    effectiveFrequency !== 'weekly' &&
    Array.isArray(payload.weekdays) &&
    payload.weekdays.length > 0
  ) {
    return 'Weekdays can only be supplied for weekly recurrence.';
  }

  if (
    payload.end_date !== undefined &&
    payload.end_date !== null &&
    !isValidIsoDate(payload.end_date)
  ) {
    return 'End date must be a valid YYYY-MM-DD date.';
  }

  if (
    payload.end_date !== undefined &&
    payload.end_date !== null &&
    payload.end_date < rule.startDate
  ) {
    return 'End date cannot be before the start date.';
  }

  return null;
}

function sortRules(rules: RecurrenceRule[]): RecurrenceRule[] {
  return [...rules].sort((a, b) => a.id - b.id);
}

async function loadAllRules(): Promise<RecurrenceResponse[]> {
  const collected: RecurrenceResponse[] = [];

  for (let page = 0; page < 10; page += 1) {
    const offset = page * RECURRENCE_PAGE_LIMIT;
    const records = await recurrencesApi.list({
      include_inactive: true,
      limit: RECURRENCE_PAGE_LIMIT,
      offset,
    });

    if (!Array.isArray(records)) {
      throw new Error('The recurrence list response could not be read.');
    }

    collected.push(...records);

    if (records.length < RECURRENCE_PAGE_LIMIT) break;
  }

  return collected;
}

async function loadAllOccurrences(
  recurrenceId: number
): Promise<RecurrenceOccurrence[]> {
  const collected: RecurrenceOccurrence[] = [];

  for (let page = 0; page < 10; page += 1) {
    const offset = page * RECURRENCE_PAGE_LIMIT;
    const records = await recurrencesApi.listOccurrences(recurrenceId, {
      limit: RECURRENCE_PAGE_LIMIT,
      offset,
    });

    if (!Array.isArray(records)) {
      throw new Error('The occurrence list response could not be read.');
    }

    collected.push(...records.map(mapRecurrenceOccurrenceResponse));

    if (records.length < RECURRENCE_PAGE_LIMIT) break;
  }

  return collected;
}

interface RecurrenceStoreState {
  rules: RecurrenceRule[];
  isLoading: boolean;
  isRefreshing: boolean;
  error: string | null;
  successMessage: string | null;

  isCreating: boolean;
  createError: string | null;

  mutatingIds: number[];
  updateError: string | null;
  deactivationError: string | null;

  occurrencesByRule: Record<number, RecurrenceOccurrence[]>;
  occurrenceLoadingIds: number[];
  occurrenceErrors: Record<number, string | null>;

  fetchRecurrences: () => Promise<boolean>;
  createRecurrence: (
    taskId: number,
    payload: RecurrenceCreate
  ) => Promise<RecurrenceRule | null>;
  updateRecurrence: (
    recurrenceId: number,
    payload: RecurrenceUpdate
  ) => Promise<RecurrenceRule | null>;
  deactivateRecurrence: (recurrenceId: number) => Promise<boolean>;
  fetchOccurrences: (recurrenceId: number) => Promise<boolean>;
  clearCreateError: () => void;
  clearUpdateError: () => void;
  clearDeactivationError: () => void;
  clearSuccess: () => void;
}

export const useRecurrenceStore = create<RecurrenceStoreState>((set, get) => ({
  rules: [],
  isLoading: false,
  isRefreshing: false,
  error: null,
  successMessage: null,

  isCreating: false,
  createError: null,

  mutatingIds: [],
  updateError: null,
  deactivationError: null,

  occurrencesByRule: {},
  occurrenceLoadingIds: [],
  occurrenceErrors: {},

  fetchRecurrences: async () => {
    if (fetchInFlight) return false;

    fetchInFlight = true;
    const hasConfirmedRules = get().rules.length > 0;
    const requestVersion = stateVersion;

    set({
      isLoading: !hasConfirmedRules,
      isRefreshing: hasConfirmedRules,
      error: null,
    });

    try {
      const records = await loadAllRules();

      // A create/update/deactivation completed while this request was in
      // flight. Never overwrite newer confirmed state with an older snapshot.
      if (requestVersion !== stateVersion) {
        set({ isLoading: false, isRefreshing: false });
        return false;
      }

      const mapped = sortRules(
        records.map(mapRecurrenceResponse)
      );

      set({
        rules: mapped,
        isLoading: false,
        isRefreshing: false,
        error: null,
      });
      return true;
    } catch (err: unknown) {
      set({
        isLoading: false,
        isRefreshing: false,
        error: getErrorMessage(
          err,
          'Could not load recurrence rules. Showing the last confirmed data.'
        ),
      });
      return false;
    } finally {
      fetchInFlight = false;
    }
  },

  createRecurrence: async (taskId, payload) => {
    if (get().isCreating) {
      set({ createError: 'A recurrence request is already in progress.' });
      return null;
    }

    if (get().rules.some((rule) => rule.taskId === taskId)) {
      set({
        createError:
          'This task already has a recurrence rule. Edit the existing rule instead.',
      });
      return null;
    }

    const validationError = validateCreatePayload(payload);
    if (validationError) {
      set({ createError: validationError });
      return null;
    }

    set({ isCreating: true, createError: null, successMessage: null });

    try {
      const created = await recurrencesApi.createForTask(taskId, payload);
      const mapped = mapRecurrenceResponse(created);

      stateVersion += 1;

      set((state) => ({
        rules: sortRules([
          ...state.rules.filter((rule) => rule.id !== mapped.id),
          mapped,
        ]),
        isCreating: false,
        createError: null,
        successMessage: 'Recurrence created.',
      }));

      return mapped;
    } catch (err: unknown) {
      set({
        isCreating: false,
        createError: getErrorMessage(
          err,
          'Recurrence could not be created. No changes were saved.'
        ),
      });
      return null;
    }
  },

  updateRecurrence: async (recurrenceId, payload) => {
    if (get().mutatingIds.includes(recurrenceId)) {
      set({ updateError: 'This recurrence is already being updated.' });
      return null;
    }

    const target = get().rules.find((rule) => rule.id === recurrenceId);
    if (!target) {
      set({ updateError: 'Recurrence not found. Refresh the list and try again.' });
      return null;
    }

    const validationError = validateUpdatePayload(payload, target);
    if (validationError) {
      set({ updateError: validationError });
      return null;
    }

    if (Object.keys(payload).length === 0) {
      set({ updateError: null, successMessage: 'No changes to save.' });
      return target;
    }

    set((state) => ({
      mutatingIds: [...state.mutatingIds, recurrenceId],
      updateError: null,
      successMessage: null,
    }));

    try {
      const updated = await recurrencesApi.update(recurrenceId, payload);
      const mapped = mapRecurrenceResponse(updated);

      stateVersion += 1;

      set((state) => ({
        rules: state.rules.map((rule) =>
          rule.id === recurrenceId ? mapped : rule
        ),
        mutatingIds: state.mutatingIds.filter((id) => id !== recurrenceId),
        updateError: null,
        successMessage: 'Recurrence updated.',
      }));

      return mapped;
    } catch (err: unknown) {
      set((state) => ({
        mutatingIds: state.mutatingIds.filter((id) => id !== recurrenceId),
        updateError: getErrorMessage(
          err,
          'Recurrence could not be updated. Previous values were preserved.'
        ),
      }));
      return null;
    }
  },

  deactivateRecurrence: async (recurrenceId) => {
    if (get().mutatingIds.includes(recurrenceId)) {
      set({ deactivationError: 'This recurrence is already being changed.' });
      return false;
    }

    const target = get().rules.find((rule) => rule.id === recurrenceId);
    if (!target) {
      set({
        deactivationError:
          'Recurrence not found. Refresh the list and try again.',
      });
      return false;
    }

    set((state) => ({
      mutatingIds: [...state.mutatingIds, recurrenceId],
      deactivationError: null,
      successMessage: null,
    }));

    try {
      await recurrencesApi.deactivate(recurrenceId);

      stateVersion += 1;

      // DELETE returns 204 and the backend deactivation contract sets these
      // two fields. No task, generated task, or history link is removed.
      set((state) => ({
        rules: state.rules.map((rule) =>
          rule.id === recurrenceId
            ? { ...rule, isActive: false, nextGenerationAt: null }
            : rule
        ),
        mutatingIds: state.mutatingIds.filter((id) => id !== recurrenceId),
        deactivationError: null,
        successMessage:
          'Recurrence deactivated. The task, generated tasks, and history were preserved.',
      }));

      return true;
    } catch (err: unknown) {
      set((state) => ({
        mutatingIds: state.mutatingIds.filter((id) => id !== recurrenceId),
        deactivationError: getErrorMessage(
          err,
          'Recurrence could not be deactivated. It remains active.'
        ),
      }));
      return false;
    }
  },

  fetchOccurrences: async (recurrenceId) => {
    if (get().occurrenceLoadingIds.includes(recurrenceId)) return false;

    set((state) => ({
      occurrenceLoadingIds: [...state.occurrenceLoadingIds, recurrenceId],
      occurrenceErrors: {
        ...state.occurrenceErrors,
        [recurrenceId]: null,
      },
    }));

    try {
      const occurrences = await loadAllOccurrences(recurrenceId);

      set((state) => ({
        occurrencesByRule: {
          ...state.occurrencesByRule,
          [recurrenceId]: occurrences,
        },
        occurrenceLoadingIds: state.occurrenceLoadingIds.filter(
          (id) => id !== recurrenceId
        ),
        occurrenceErrors: {
          ...state.occurrenceErrors,
          [recurrenceId]: null,
        },
      }));

      return true;
    } catch (err: unknown) {
      // Keep the last confirmed occurrence list for this rule.
      set((state) => ({
        occurrenceLoadingIds: state.occurrenceLoadingIds.filter(
          (id) => id !== recurrenceId
        ),
        occurrenceErrors: {
          ...state.occurrenceErrors,
          [recurrenceId]: getErrorMessage(
            err,
            'Could not load generated occurrences. Showing the last confirmed data.'
          ),
        },
      }));
      return false;
    }
  },

  clearCreateError: () => set({ createError: null }),
  clearUpdateError: () => set({ updateError: null }),
  clearDeactivationError: () => set({ deactivationError: null }),
  clearSuccess: () => set({ successMessage: null }),
}));

export default useRecurrenceStore;
