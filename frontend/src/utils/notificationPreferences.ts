import type {
  NotificationPreferenceResponse,
  NotificationPreferenceUpdate,
} from '../types/api.types';
import type {
  NotificationPreferenceDraft,
  NotificationPreferences,
} from '../types/notification.types';

/**
 * Quiet-hour values are local wall-clock times. They are normalised to HH:MM
 * and must never carry a date, offset, or `Z` suffix.
 */
export function normaliseWallClock(value: string | null | undefined): string {
  const raw = (value ?? '').trim();

  if (!/^\d{2}:\d{2}(?::\d{2})?$/.test(raw)) return '';

  return raw.slice(0, 5);
}

export function isValidWallClock(value: string): boolean {
  if (!/^\d{2}:\d{2}$/.test(value)) return false;

  const [hour, minute] = value.split(':').map(Number);

  return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59;
}

/** Validate a genuine IANA timezone name using the platform tz database. */
export function isValidIanaTimezone(value: string): boolean {
  const candidate = value.trim();

  if (!candidate) return false;

  try {
    new Intl.DateTimeFormat('en-US', { timeZone: candidate });
    return true;
  } catch {
    return false;
  }
}

const FALLBACK_TIMEZONES = [
  'UTC',
  'Africa/Cairo',
  'Africa/Johannesburg',
  'America/Anchorage',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/New_York',
  'America/Sao_Paulo',
  'America/Toronto',
  'Asia/Dubai',
  'Asia/Hong_Kong',
  'Asia/Kolkata',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Perth',
  'Australia/Sydney',
  'Europe/Berlin',
  'Europe/London',
  'Europe/Paris',
  'Pacific/Auckland',
];

/** IANA timezone options available on this runtime, de-duplicated and sorted. */
export function listIanaTimezones(current?: string | null): string[] {
  const zones = new Set<string>(FALLBACK_TIMEZONES);

  try {
    const supported = (
      Intl as typeof Intl & {
        supportedValuesOf?: (key: string) => string[];
      }
    ).supportedValuesOf?.('timeZone');

    if (Array.isArray(supported)) {
      for (const zone of supported) zones.add(zone);
    }
  } catch {
    // Older runtimes fall back to the curated list.
  }

  const browserZone = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      return undefined;
    }
  })();

  if (browserZone) zones.add(browserZone);
  if (current && isValidIanaTimezone(current)) zones.add(current.trim());

  return Array.from(zones).sort((a, b) => a.localeCompare(b));
}

export function mapNotificationPreferences(
  response: NotificationPreferenceResponse
): NotificationPreferences {
  return {
    id: response.id,
    userId: response.user_id,
    inAppEnabled: response.in_app_enabled,
    quietHoursEnabled: response.quiet_hours_enabled,
    quietHoursStart: normaliseWallClock(response.quiet_hours_start) || '00:00',
    quietHoursEnd: normaliseWallClock(response.quiet_hours_end) || '00:00',
    timezone: response.timezone,
    quietHoursActive: response.quiet_hours_active,
    quietHoursEndsAt: response.quiet_hours_ends_at,
  };
}

export function draftFromPreferences(
  preferences: NotificationPreferences
): NotificationPreferenceDraft {
  return {
    inAppEnabled: preferences.inAppEnabled,
    quietHoursEnabled: preferences.quietHoursEnabled,
    quietHoursStart: preferences.quietHoursStart,
    quietHoursEnd: preferences.quietHoursEnd,
    timezone: preferences.timezone,
  };
}

export interface PreferenceDiffResult {
  patch: NotificationPreferenceUpdate;
  error: string | null;
}

/**
 * Build a partial PATCH body containing only fields that actually changed.
 * Quiet-hour times are sent as HH:MM wall-clock values without any offset.
 */
export function buildNotificationPreferencePatch(
  confirmed: NotificationPreferences,
  draft: NotificationPreferenceDraft
): PreferenceDiffResult {
  const patch: NotificationPreferenceUpdate = {};

  const start = normaliseWallClock(draft.quietHoursStart);
  const end = normaliseWallClock(draft.quietHoursEnd);

  if (!isValidWallClock(start)) {
    return { patch, error: 'Quiet-hours start must be a valid HH:MM time.' };
  }

  if (!isValidWallClock(end)) {
    return { patch, error: 'Quiet-hours end must be a valid HH:MM time.' };
  }

  const timezone = draft.timezone.trim();

  if (!isValidIanaTimezone(timezone)) {
    return {
      patch,
      error: 'Choose a valid IANA timezone name, such as Europe/London.',
    };
  }

  if (draft.quietHoursEnabled && start === end) {
    return {
      patch,
      error: 'Quiet-hours start and end must be different times.',
    };
  }

  if (draft.inAppEnabled !== confirmed.inAppEnabled) {
    patch.in_app_enabled = draft.inAppEnabled;
  }

  if (draft.quietHoursEnabled !== confirmed.quietHoursEnabled) {
    patch.quiet_hours_enabled = draft.quietHoursEnabled;
  }

  if (start !== confirmed.quietHoursStart) {
    patch.quiet_hours_start = start;
  }

  if (end !== confirmed.quietHoursEnd) {
    patch.quiet_hours_end = end;
  }

  if (timezone !== confirmed.timezone) {
    patch.timezone = timezone;
  }

  return { patch, error: null };
}
