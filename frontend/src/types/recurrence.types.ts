import type { BackendRecurrenceFrequency } from './api.types';

/**
 * Frontend recurrence models.
 *
 * The backend is the source of truth: a rule is identified by its backend id,
 * `nextGenerationAt` is the worker's next scheduling check (not a predicted
 * occurrence date), and occurrence links are stored exactly as returned.
 */
export type RecurrenceFrequency = BackendRecurrenceFrequency;

export interface RecurrenceRule {
  id: number;
  taskId: number;
  frequency: RecurrenceFrequency;
  interval: number;
  weekdays: number[];
  startDate: string;
  endDate: string | null;
  timezone: string | null;
  anchorTime: string | null;
  isActive: boolean;
  lastGeneratedDate: string | null;
  nextGenerationAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface RecurrenceOccurrence {
  id: number;
  recurrenceId: number | null;
  taskId: number;
  occurrenceDate: string;
  createdAt: string;
}

export interface RecurrenceDraft {
  frequency: RecurrenceFrequency;
  interval: number | string;
  weekdays: number[];
  startDate: string;
  endDate: string;
}

export interface RecurrenceFormErrors {
  taskId?: string;
  frequency?: string;
  interval?: string;
  weekdays?: string;
  startDate?: string;
  endDate?: string;
  general?: string;
}

export const MIN_RECURRENCE_INTERVAL = 1;
export const MAX_RECURRENCE_INTERVAL = 365;
export const RECURRENCE_PAGE_LIMIT = 100;
