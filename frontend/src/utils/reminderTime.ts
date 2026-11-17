/**
 * Local wall-clock and backend instant helpers for reminders.
 *
 * The backend stores `remind_at` as an absolute UTC instant. The UI edits
 * reminders as local wall-clock date/time values, so conversion happens at the
 * boundary and never by appending `Z` to a local value that is not UTC.
 */

const HAS_TIMEZONE_DESIGNATOR = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

export function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

/** Format a Date as a local (not UTC) YYYY-MM-DD calendar date. */
export function localDateString(date: Date = new Date()): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(
    date.getDate()
  )}`;
}

/**
 * Convert a local wall-clock date (`YYYY-MM-DD`) and time (`HH:MM`) into a
 * genuine UTC ISO instant. The returned `Z` suffix is correct here because
 * the Date constructor interprets the supplied components in the browser's
 * local timezone before `toISOString` converts them to UTC.
 */
export function localWallClockToUtcIso(date: string, time: string): string {
  const [year, month, day] = date.split('-').map(Number);
  const [hour, minute] = time.split(':').map(Number);

  const valid =
    Number.isInteger(year) &&
    Number.isInteger(month) &&
    Number.isInteger(day) &&
    Number.isInteger(hour) &&
    Number.isInteger(minute) &&
    year >= 1000 &&
    year <= 9999 &&
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= 31 &&
    hour >= 0 &&
    hour <= 23 &&
    minute >= 0 &&
    minute <= 59;

  if (!valid) {
    throw new Error('Select a valid reminder date and time.');
  }

  const local = new Date(year, month - 1, day, hour, minute, 0, 0);

  if (
    Number.isNaN(local.getTime()) ||
    local.getFullYear() !== year ||
    local.getMonth() !== month - 1 ||
    local.getDate() !== day
  ) {
    throw new Error('Select a valid reminder date and time.');
  }

  return local.toISOString();
}

/**
 * Parse a backend `remind_at` value. The backend stores UTC. PostgreSQL returns
 * an explicit offset, while SQLite can return the same UTC value without one,
 * so a missing designator is interpreted as UTC rather than local time.
 */
export function parseBackendInstant(value: string): Date {
  const trimmed = value.trim();
  const normalised =
    !trimmed.includes('T') && trimmed.includes(' ')
      ? trimmed.replace(' ', 'T')
      : trimmed;
  const withZone = HAS_TIMEZONE_DESIGNATOR.test(normalised)
    ? normalised
    : `${normalised}Z`;
  const parsed = new Date(withZone);

  if (Number.isNaN(parsed.getTime())) {
    throw new Error('Reminder time from the server could not be read.');
  }

  return parsed;
}

/** Split a backend UTC instant into local calendar date and HH:MM time. */
export function backendInstantToLocalParts(value: string): {
  date: string;
  time: string;
} {
  const parsed = parseBackendInstant(value);

  return {
    date: localDateString(parsed),
    time: `${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}`,
  };
}

/** Human-readable description of a task-deadline offset. */
export function formatOffsetMinutes(minutes: number): string {
  if (minutes === 0) return 'at the task deadline';

  if (minutes % 1440 === 0) {
    const days = minutes / 1440;
    return `${days} day${days === 1 ? '' : 's'} before the deadline`;
  }

  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return `${hours} hour${hours === 1 ? '' : 's'} before the deadline`;
  }

  return `${minutes} minute${minutes === 1 ? '' : 's'} before the deadline`;
}
