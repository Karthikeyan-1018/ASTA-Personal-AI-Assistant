import React, { useEffect, useRef, useState } from 'react';
import { Check, Loader2, Play, RotateCcw, Volume2 } from 'lucide-react';
import { ttsApi } from '../../services/api/tts.api';
import type { TTSSettingsResponse } from '../../types/api.types';

const TEST_PHRASE = "Hi, I'm ASTA. This is a voice preview using your local speech settings.";

const VoiceSettingsPanel: React.FC = () => {
  const [saved, setSaved] = useState<TTSSettingsResponse | null>(null);
  const [voice, setVoice] = useState('');
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  const releasePreview = () => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = null;
    setPlaying(false);
  };

  useEffect(() => {
    void ttsApi.getSettings().then((settings) => {
      setSaved(settings);
      setVoice(settings.voice);
      setSpeed(settings.speed);
    }).catch(() => setError('Could not load voice settings.'));
    return () => {
      audioRef.current?.pause();
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, []);

  const handleSave = async () => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await ttsApi.updateSettings({ voice, speed });
      setSaved(result);
      setVoice(result.voice);
      setSpeed(result.speed);
      setSuccess('Voice settings saved to your account.');
    } catch {
      setError('Voice settings could not be saved.');
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    releasePreview();
    try {
      const result = await ttsApi.updateSettings({ reset: true });
      setSaved(result);
      setVoice(result.voice);
      setSpeed(result.speed);
      setSuccess('Default voice settings restored.');
    } catch {
      setError('Default voice settings could not be restored.');
    } finally {
      setBusy(false);
    }
  };

  const handlePreview = async () => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    releasePreview();
    try {
      const blob = await ttsApi.synthesize(TEST_PHRASE);
      const url = URL.createObjectURL(blob);
      objectUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = releasePreview;
      audio.onerror = () => {
        releasePreview();
        setError('The voice preview could not be played.');
      };
      await audio.play();
      setPlaying(true);
    } catch {
      setError('Voice preview failed. Check that a local Piper voice is installed.');
    } finally {
      setBusy(false);
    }
  };

  if (!saved) {
    return (
      <section className="rounded-2xl border border-slate-800 bg-slate-950/40 p-5">
        <h2 className="text-base font-bold text-white">Voice Settings</h2>
        <p className="mt-2 text-xs text-slate-400">{error || 'Loading local voice settings…'}</p>
      </section>
    );
  }

  const installedVoices = saved.installed_voices;
  return (
    <section className="space-y-5 rounded-2xl border border-slate-800 bg-slate-950/40 p-5">
      <div>
        <h2 className="flex items-center gap-2 text-base font-bold text-white">
          <Volume2 className="h-4 w-4 text-indigo-400" /> Voice Settings
        </h2>
        <p className="mt-1 text-xs text-slate-400">
          Local Piper speech. These preferences are saved per user and used for message playback and voice conversation.
        </p>
      </div>

      {!saved.enabled ? (
        <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
          Local text-to-speech is disabled by the server configuration.
        </p>
      ) : !saved.available || installedVoices.length === 0 ? (
        <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
          No complete Piper voice model is installed. Add a local ONNX voice and its matching JSON configuration to enable speech.
        </p>
      ) : (
        <div className="grid max-w-xl gap-5">
          <label className="space-y-1.5 text-xs text-slate-300">
            <span className="font-semibold">Installed voice</span>
            <select
              value={voice}
              onChange={(event) => setVoice(event.target.value)}
              disabled={busy}
              className="h-10 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 text-white focus:border-indigo-500/60 focus:outline-none"
            >
              {installedVoices.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <span className="block text-[11px] text-slate-500">Only installed local models are selectable; ASTA won’t download a model in the background.</span>
          </label>

          <label className="space-y-2 text-xs text-slate-300">
            <span className="flex justify-between font-semibold">
              <span>Speaking speed</span><span>{speed.toFixed(2)}×</span>
            </span>
            <input
              type="range"
              min="0.8"
              max="1.2"
              step="0.05"
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value))}
              disabled={busy}
              className="w-full accent-indigo-500"
            />
            <span className="flex justify-between text-[11px] text-slate-500"><span>Slower</span><span>Faster</span></span>
          </label>

          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void handleSave()} disabled={busy || (voice === saved.voice && speed === saved.speed)} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50">
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Save voice settings
            </button>
            <button type="button" onClick={() => void handlePreview()} disabled={busy} className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50">
              <Play className="h-3.5 w-3.5" /> {playing ? 'Playing preview…' : 'Test saved voice'}
            </button>
            <button type="button" onClick={() => void handleReset()} disabled={busy} className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-50">
              <RotateCcw className="h-3.5 w-3.5" /> Restore default
            </button>
          </div>
        </div>
      )}

      {error && <p role="alert" className="text-xs text-rose-300">{error}</p>}
      {success && <p role="status" className="text-xs text-emerald-300">{success}</p>}
    </section>
  );
};

export default VoiceSettingsPanel;
