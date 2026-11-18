import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  Loader2,
  RefreshCw,
  RotateCcw,
  Sparkles,
} from 'lucide-react';
import { usePersonalityStore } from '../../stores/personality.store';
import {
  PERSONALITY_MODES,
  type PersonalityMode,
  type PersonalitySettings,
  type PersonalitySettingsUpdate,
} from '../../types/personality.types';

type PersonalityDraft = Omit<PersonalitySettings, 'hasOverrides'>;

const INTENSITY_LABELS: Record<number, string> = {
  1: 'Subtle',
  2: 'Light',
  3: 'Balanced',
  4: 'Warm and energetic',
  5: 'Strong but respectful',
};

const MODE_DESCRIPTIONS: Record<PersonalityMode, string> = {
  normal: 'Calm, friendly, balanced help for everyday questions and work.',
  motivational:
    'Warm encouragement and practical next steps when relevant; technical answers stay direct.',
  focus:
    'Direct, structured, technical answers with no jokes or unsolicited motivation.',
  casual:
    'Relaxed, natural conversation with light playfulness; technical answers stay accurate.',
};

function draftFromSettings(settings: PersonalitySettings): PersonalityDraft {
  return {
    assistantName: settings.assistantName,
    mode: settings.mode,
    personalityTraits: [...settings.personalityTraits],
    motivationalPhilosophy: settings.motivationalPhilosophy,
    proactiveMotivationEnabled: settings.proactiveMotivationEnabled,
    motivationIntensity: settings.motivationIntensity,
    responseStyle: settings.responseStyle,
    humorEnabled: settings.humorEnabled,
  };
}

function buildPatch(
  settings: PersonalitySettings,
  draft: PersonalityDraft
): PersonalitySettingsUpdate {
  const patch: PersonalitySettingsUpdate = {};

  if (draft.mode !== settings.mode) patch.mode = draft.mode;
  if (draft.proactiveMotivationEnabled !== settings.proactiveMotivationEnabled) {
    patch.proactiveMotivationEnabled = draft.proactiveMotivationEnabled;
  }
  if (draft.motivationIntensity !== settings.motivationIntensity) {
    patch.motivationIntensity = draft.motivationIntensity;
  }

  return patch;
}

/**
 * Functional personality controls backed by `/api/settings/personality`.
 *
 * Every mode, toggle, intensity, and reset action changes the shared backend
 * persona used by text, streaming, voice, Groq, and Ollama.
 */
export const PersonalityPanel: React.FC = () => {
  const settings = usePersonalityStore((s) => s.settings);
  const isLoading = usePersonalityStore((s) => s.isLoading);
  const isSaving = usePersonalityStore((s) => s.isSaving);
  const error = usePersonalityStore((s) => s.error);
  const successMessage = usePersonalityStore((s) => s.successMessage);
  const fetchSettings = usePersonalityStore((s) => s.fetchSettings);
  const saveSettings = usePersonalityStore((s) => s.saveSettings);
  const resetSettings = usePersonalityStore((s) => s.resetSettings);
  const clearFeedback = usePersonalityStore((s) => s.clearFeedback);

  const [overrides, setOverrides] = useState<Partial<PersonalityDraft>>({});
  const [lastAction, setLastAction] = useState<'save' | 'reset' | null>(null);

  useEffect(() => {
    if (!settings) void fetchSettings();
  }, [settings, fetchSettings]);

  // Derive the draft from the last confirmed backend state plus local edits.
  // This avoids synchronising draft state inside an effect.
  const draft = useMemo(
    () =>
      settings
        ? { ...draftFromSettings(settings), ...overrides }
        : null,
    [settings, overrides]
  );

  const patch = useMemo(
    () => (settings && draft ? buildPatch(settings, draft) : {}),
    [settings, draft]
  );
  const isDirty = Object.keys(patch).length > 0;

  if (!settings || !draft) {
    return (
      <div className="max-w-md rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-white">
              Personality & Motivation
            </h2>
            <p className="mt-0.5 text-[11px] text-slate-500">
              {error
                ? 'Could not load ASTA personality settings.'
                : 'Loading ASTA personality settings…'}
            </p>
          </div>
          {isLoading && (
            <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
          )}
        </div>
        {error && (
          <div className="mt-3 flex flex-col gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3">
            <p className="text-[11px] text-rose-200">{error}</p>
            <button
              type="button"
              onClick={() => {
                void fetchSettings();
              }}
              disabled={isLoading}
              className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-rose-500/40 px-2.5 py-1 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:opacity-60"
            >
              <RefreshCw className="h-3 w-3" />
              Retry
            </button>
          </div>
        )}
      </div>
    );
  }

  const updateDraft = (partial: Partial<PersonalityDraft>) => {
    clearFeedback();
    setOverrides((current) => ({ ...current, ...partial }));
  };

  const handleSave = async () => {
    setLastAction('save');
    if (await saveSettings(patch)) {
      setOverrides({});
      setLastAction(null);
    }
  };

  const handleReset = async () => {
    setLastAction('reset');
    if (await resetSettings()) {
      setOverrides({});
      setLastAction(null);
    }
  };

  return (
    <div className="space-y-5 rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold text-white">
            <Sparkles className="h-4 w-4 text-indigo-400" />
            Personality & Motivation
          </h2>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Saved by ASTA and applied to text, voice, Groq, and Ollama.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {settings.hasOverrides && (
            <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1 text-[10px] font-semibold text-indigo-300">
              Custom
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              void handleReset();
            }}
            disabled={isSaving}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 px-2.5 py-1.5 text-[11px] font-semibold text-slate-300 transition hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Restore defaults
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
            <p className="text-[11px] text-rose-200">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              if (lastAction === 'save') void handleSave();
              else if (lastAction === 'reset') void handleReset();
              else void fetchSettings();
            }}
            disabled={isSaving || isLoading}
            className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-rose-500/40 px-2.5 py-1 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:opacity-60"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      )}

      {successMessage && (
        <div
          role="status"
          className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-[11px] font-semibold text-emerald-300"
        >
          <Check className="h-3.5 w-3.5" />
          {successMessage}
        </div>
      )}

      <div className="grid max-w-md gap-4">
        <div>
          <label
            htmlFor="asta-personality-mode"
            className="block text-xs font-medium text-slate-300"
          >
            Behaviour mode
          </label>
          <select
            id="asta-personality-mode"
            value={draft.mode}
            disabled={isSaving}
            onChange={(e) => {
              updateDraft({ mode: e.target.value as PersonalityMode });
            }}
            className="mt-1.5 h-10 w-full rounded-xl border border-slate-800 bg-slate-950 px-3 text-xs text-white focus:outline-none disabled:opacity-60"
          >
            {PERSONALITY_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode.charAt(0).toUpperCase() + mode.slice(1)}
              </option>
            ))}
          </select>
          <p className="mt-1 text-[10px] text-slate-500">
            {MODE_DESCRIPTIONS[draft.mode]}
          </p>
        </div>

        <div className="flex items-center justify-between border-t border-slate-800/80 pt-4">
          <div>
            <label
              htmlFor="asta-proactive-motivation"
              className="text-xs font-semibold text-slate-200"
            >
              Proactive motivation
            </label>
            <p className="text-[11px] text-slate-500">
              Offer brief, practical encouragement when context clearly calls
              for it.
            </p>
          </div>
          <input
            id="asta-proactive-motivation"
            type="checkbox"
            checked={draft.proactiveMotivationEnabled}
            disabled={isSaving}
            onChange={(e) => {
              updateDraft({
                proactiveMotivationEnabled: e.target.checked,
              });
            }}
            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor="asta-motivation-intensity"
            className="block text-xs font-medium text-slate-300"
          >
            Motivation intensity: {INTENSITY_LABELS[draft.motivationIntensity]}
          </label>
          <input
            id="asta-motivation-intensity"
            type="range"
            min={1}
            max={5}
            step={1}
            value={draft.motivationIntensity}
            disabled={isSaving}
            onChange={(e) => {
              updateDraft({ motivationIntensity: Number(e.target.value) });
            }}
            className="mt-2 w-full accent-indigo-500 disabled:opacity-50"
          />
          <p className="mt-1 text-[10px] text-slate-500">
            Applies only when motivation is appropriate; never forces it into
            neutral or technical replies.
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
            {isSaving ? 'Saving…' : 'Save personality'}
          </button>
          <button
            type="button"
            onClick={() => {
              setOverrides({});
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

export default PersonalityPanel;
