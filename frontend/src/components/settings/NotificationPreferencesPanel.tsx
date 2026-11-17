import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Bell,
  Check,
  Clock,
  Globe,
  Loader2,
  Moon,
  RefreshCw,
} from 'lucide-react';
import { useNotificationPreferenceStore } from '../../stores/notificationPreference.store';
import type {
  NotificationPreferenceDraft,
  NotificationPreferences,
} from '../../types/notification.types';
import {
  draftFromPreferences,
  listIanaTimezones,
  normaliseWallClock,
} from '../../utils/notificationPreferences';

const TIMEZONE_LIST_ID = 'asta-notification-timezones';

/**
 * Notification settings backed by `/api/notifications/preferences`.
 *
 * Quiet-hour values are local wall-clock HH:MM values. They are never turned
 * into instants and never receive a `Z` suffix.
 */
export const NotificationPreferencesPanel: React.FC = () => {
  const preferences = useNotificationPreferenceStore((s) => s.preferences);
  const isLoading = useNotificationPreferenceStore((s) => s.isLoading);
  const isSaving = useNotificationPreferenceStore((s) => s.isSaving);
  const error = useNotificationPreferenceStore((s) => s.error);
  const successMessage = useNotificationPreferenceStore(
    (s) => s.successMessage
  );
  const fetchPreferences = useNotificationPreferenceStore(
    (s) => s.fetchPreferences
  );
  const savePreferences = useNotificationPreferenceStore(
    (s) => s.savePreferences
  );
  const clearFeedback = useNotificationPreferenceStore(
    (s) => s.clearFeedback
  );

  const [draftState, setDraftState] = useState<{
    source: NotificationPreferences | null;
    value: NotificationPreferenceDraft | null;
  }>({ source: null, value: null });

  useEffect(() => {
    void fetchPreferences();
  }, [fetchPreferences]);

  // Derive the editable draft from the latest confirmed preferences without a
  // synchronising effect. Local edits stay until the backend confirms new data.
  const draft: NotificationPreferenceDraft | null = preferences
    ? draftState.source === preferences && draftState.value
      ? draftState.value
      : draftFromPreferences(preferences)
    : null;

  const timezones = useMemo(
    () => listIanaTimezones(preferences?.timezone),
    [preferences?.timezone]
  );

  const isDirty =
    preferences !== null &&
    draft !== null &&
    (draft.inAppEnabled !== preferences.inAppEnabled ||
      draft.quietHoursEnabled !== preferences.quietHoursEnabled ||
      normaliseWallClock(draft.quietHoursStart) !==
        preferences.quietHoursStart ||
      normaliseWallClock(draft.quietHoursEnd) !== preferences.quietHoursEnd ||
      draft.timezone.trim() !== preferences.timezone);

  const updateDraft = (partial: Partial<NotificationPreferenceDraft>) => {
    if (!preferences || !draft) return;

    setDraftState({
      source: preferences,
      value: { ...draft, ...partial },
    });
    clearFeedback();
  };

  const handleSave = async () => {
    if (!draft) return;

    await savePreferences({
      ...draft,
      quietHoursStart: normaliseWallClock(draft.quietHoursStart),
      quietHoursEnd: normaliseWallClock(draft.quietHoursEnd),
      timezone: draft.timezone.trim(),
    });
  };

  if (isLoading && !preferences) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-xs text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
        <span>Loading notification preferences…</span>
      </div>
    );
  }

  if (!preferences || !draft) {
    return (
      <div className="space-y-4">
        <h2 className="text-base font-bold text-white">
          Alerts & Notification Delivery
        </h2>

        {error ? (
          <div
            role="alert"
            className="max-w-md rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4"
          >
            <div className="flex items-start gap-2.5">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
              <div>
                <p className="text-xs font-semibold text-rose-200">
                  Notification preferences unavailable
                </p>
                <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => {
                void fetchPreferences();
              }}
              disabled={isLoading}
              className="mt-3 inline-flex items-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
              <span>Retry</span>
            </button>
          </div>
        ) : (
          <p className="text-xs text-slate-400">
            Preferences could not be loaded. Use Retry to try again.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <h2 className="text-base font-bold text-white">
            Alerts & Notification Delivery
          </h2>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Persisted by the ASTA backend and applied to reminder delivery.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {preferences.quietHoursActive && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-violet-500/30 bg-violet-500/10 px-2.5 py-1 text-[10px] font-semibold text-violet-300">
              <Moon className="h-3 w-3" />
              Quiet hours active
              {preferences.quietHoursEndsAt && (
                <span className="font-normal text-violet-200/70">
                  · ends {new Date(preferences.quietHoursEndsAt).toLocaleString()}
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <div>
              <p className="text-xs font-semibold text-rose-200">
                Notification preference error
              </p>
              <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
              <p className="mt-0.5 text-[10px] text-rose-200/60">
                Last confirmed settings are preserved.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              if (isDirty) {
                void handleSave();
              } else {
                void fetchPreferences();
              }
            }}
            disabled={isLoading || isSaving}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isLoading || isSaving ? 'animate-spin' : ''}`}
            />
            <span>Retry</span>
          </button>
        </div>
      )}

      {successMessage && (
        <div
          role="status"
          className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-300"
        >
          <Check className="h-3.5 w-3.5" />
          <span>{successMessage}</span>
        </div>
      )}

      <div className="max-w-md space-y-5">
        <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
          <div className="flex items-start gap-2.5">
            <Bell className="mt-0.5 h-4 w-4 text-indigo-400" />
            <div>
              <p className="text-xs font-semibold text-slate-200">
                In-app notifications
              </p>
              <p className="text-[11px] text-slate-500">
                Allow reminder alerts to be delivered to ASTA
              </p>
            </div>
          </div>
          <input
            type="checkbox"
            checked={draft.inAppEnabled}
            disabled={isSaving}
            onChange={(e) => updateDraft({ inAppEnabled: e.target.checked })}
            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 disabled:opacity-50"
          />
        </div>

        <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
          <div className="flex items-start gap-2.5">
            <Moon className="mt-0.5 h-4 w-4 text-violet-400" />
            <div>
              <p className="text-xs font-semibold text-slate-200">Quiet hours</p>
              <p className="text-[11px] text-slate-500">
                Defer deliveries inside this local time window
              </p>
            </div>
          </div>
          <input
            type="checkbox"
            checked={draft.quietHoursEnabled}
            disabled={isSaving}
            onChange={(e) => updateDraft({ quietHoursEnabled: e.target.checked })}
            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 disabled:opacity-50"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
              <Clock className="h-3.5 w-3.5 text-slate-500" />
              Start
            </label>
            <input
              type="time"
              value={draft.quietHoursStart}
              disabled={isSaving || !draft.quietHoursEnabled}
              onChange={(e) => updateDraft({ quietHoursStart: e.target.value })}
              className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-950 px-2.5 text-xs text-white focus:border-indigo-500/60 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div>
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
              <Clock className="h-3.5 w-3.5 text-slate-500" />
              End
            </label>
            <input
              type="time"
              value={draft.quietHoursEnd}
              disabled={isSaving || !draft.quietHoursEnabled}
              onChange={(e) => updateDraft({ quietHoursEnd: e.target.value })}
              className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-950 px-2.5 text-xs text-white focus:border-indigo-500/60 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
        </div>

        <p className="text-[10px] text-slate-500">
          Times are local wall-clock values. Windows that cross midnight are
          supported. Delivery is deferred, never discarded, when quiet hours are
          active.
        </p>

        <div>
          <label className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
            <Globe className="h-3.5 w-3.5 text-slate-500" />
            Timezone
          </label>
          <input
            type="text"
            list={TIMEZONE_LIST_ID}
            value={draft.timezone}
            disabled={isSaving}
            onChange={(e) => updateDraft({ timezone: e.target.value })}
            placeholder="e.g. Asia/Kolkata"
            className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-950 px-3 font-mono text-xs text-white focus:border-indigo-500/60 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
          <datalist id={TIMEZONE_LIST_ID}>
            {timezones.map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
          <p className="mt-1 text-[10px] text-slate-500">
            Must be a valid IANA timezone name. This zone evaluates quiet hours
            and deadline times.
          </p>
        </div>

        <div className="flex items-center gap-2 border-t border-slate-800/80 pt-4">
          <button
            type="button"
            onClick={() => {
              void handleSave();
            }}
            disabled={isSaving || !isDirty}
            className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>{isSaving ? 'Saving…' : 'Save notification settings'}</span>
          </button>

          <button
            type="button"
            onClick={() => {
              setDraftState({ source: null, value: null });
              clearFeedback();
            }}
            disabled={isSaving || !isDirty}
            className="rounded-xl border border-slate-800 px-3 py-2 text-xs font-medium text-slate-300 transition hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Discard changes
          </button>
        </div>
      </div>
    </div>
  );
};

export default NotificationPreferencesPanel;
