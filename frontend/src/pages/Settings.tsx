import React, { useState } from 'react';
import {
  User,
  Palette,
  MessageSquare,
  Bell,
  Lock,
  Shield,
  Info,
  RotateCcw,
  Check,
  Sparkles,
} from 'lucide-react';
import { useSettingsStore } from '../stores/settings.store';
import { useAuthStore } from '../stores/auth.store';
import type { UserSettings } from '../types/settings.types';

type SettingsTab =
  | 'general'
  | 'appearance'
  | 'chat'
  | 'notifications'
  | 'privacy'
  | 'security'
  | 'about';

export const Settings: React.FC = () => {
  const settings = useSettingsStore((s) => s.settings);
  const updateSettings = useSettingsStore((s) => s.updateSettings);
  const resetToDefaults = useSettingsStore((s) => s.resetToDefaults);

  const user = useAuthStore((s) => s.user);
  const updateName = useAuthStore((s) => s.updateName);

  const [activeTab, setActiveTab] = useState<SettingsTab>('general');
  const [saveToast, setSaveToast] = useState(false);

  const handleUpdate = (partial: Partial<UserSettings>) => {
    updateSettings(partial);
    setSaveToast(true);
    setTimeout(() => setSaveToast(false), 1500);
  };

  const navTabs: { id: SettingsTab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'general', label: 'General', icon: User },
    { id: 'appearance', label: 'Appearance', icon: Palette },
    { id: 'chat', label: 'Chat Preferences', icon: MessageSquare },
    { id: 'notifications', label: 'Notifications', icon: Bell },
    { id: 'privacy', label: 'Privacy', icon: Lock },
    { id: 'security', label: 'Security & Tools', icon: Shield },
    { id: 'about', label: 'About ASTA', icon: Info },
  ];

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            Desktop Preferences & Configuration
          </h1>
          <p className="text-xs sm:text-sm text-slate-400">
            Personalize your assistant persona, UI theme, model routing, and security policies.
          </p>
        </div>

        {saveToast && (
          <div className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-300">
            <Check className="h-3.5 w-3.5 text-emerald-400" />
            <span>Preferences saved locally</span>
          </div>
        )}
      </div>

      {/* Main Settings Grid */}
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Navigation Tabs (Sidebar style) */}
        <div className="w-full lg:w-60 shrink-0 space-y-1">
          {navTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;

            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-xs font-semibold transition ${
                  isActive
                    ? 'border border-indigo-500/30 bg-indigo-500/15 text-white shadow-sm'
                    : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                }`}
              >
                <Icon className={`h-4 w-4 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`} />
                <span>{tab.label}</span>
              </button>
            );
          })}

          <div className="pt-4 border-t border-slate-800/80">
            <button
              type="button"
              onClick={resetToDefaults}
              className="flex w-full items-center gap-2 rounded-xl border border-slate-800 px-3.5 py-2 text-xs font-medium text-slate-400 hover:bg-slate-900 hover:text-rose-400 transition"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              <span>Reset All Defaults</span>
            </button>
          </div>
        </div>

        {/* Content Panel */}
        <div className="flex-1 rounded-3xl border border-slate-800/80 bg-slate-900/30 p-6 shadow-xl backdrop-blur-sm">
          {/* GENERAL TAB */}
          {activeTab === 'general' && (
            <div className="space-y-6">
              <h2 className="text-base font-bold text-white">General Profile & Identity</h2>

              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-slate-300">
                    Assistant Call Sign
                  </label>
                  <p className="text-[11px] text-slate-500">Name of your AI assistant co-pilot</p>
                  <input
                    type="text"
                    value={settings.assistantName}
                    onChange={(e) => handleUpdate({ assistantName: e.target.value })}
                    className="mt-1 h-10 w-full max-w-md rounded-xl border border-slate-800 bg-slate-950 px-3 text-xs text-white focus:border-indigo-500/60 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">
                    Your Operator Name
                  </label>
                  <p className="text-[11px] text-slate-500">Displayed in messages and briefings</p>
                  <input
                    type="text"
                    value={user.name}
                    onChange={(e) => {
                      updateName(e.target.value);
                      handleUpdate({ userDisplayName: e.target.value });
                    }}
                    className="mt-1 h-10 w-full max-w-md rounded-xl border border-slate-800 bg-slate-950 px-3 text-xs text-white focus:border-indigo-500/60 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">
                    Operator Role
                  </label>
                  <p className="text-[11px] text-slate-500">System authorization level</p>
                  <input
                    type="text"
                    disabled
                    value={user.role}
                    className="mt-1 h-10 w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/50 px-3 text-xs text-slate-400 cursor-not-allowed"
                  />
                </div>
              </div>
            </div>
          )}

          {/* APPEARANCE TAB */}
          {activeTab === 'appearance' && (
            <div className="space-y-6">
              <h2 className="text-base font-bold text-white">Theme & Visual Experience</h2>

              <div className="space-y-5">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Color Palette</label>
                  <p className="text-[11px] text-slate-500">Desktop Obsidian darkness level</p>
                  <div className="mt-2 grid grid-cols-3 gap-3 max-w-md">
                    {(['obsidian', 'midnight', 'dark'] as const).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => handleUpdate({ theme: t })}
                        className={`rounded-xl border p-3 text-center text-xs font-bold capitalize transition ${
                          settings.theme === t
                            ? 'border-indigo-500 bg-indigo-500/20 text-white'
                            : 'border-slate-800 bg-slate-950 text-slate-400 hover:text-white'
                        }`}
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">Accent Tone</label>
                  <div className="mt-2 flex items-center gap-3">
                    {[
                      { id: 'indigo', color: 'bg-indigo-500' },
                      { id: 'purple', color: 'bg-violet-500' },
                      { id: 'cyan', color: 'bg-cyan-500' },
                      { id: 'emerald', color: 'bg-emerald-500' },
                    ].map((accent) => (
                      <button
                        key={accent.id}
                        type="button"
                        onClick={() => handleUpdate({ accentColor: accent.id as any })}
                        className={`h-7 w-7 rounded-full ${accent.color} transition-transform ${
                          settings.accentColor === accent.id
                            ? 'ring-2 ring-white ring-offset-2 ring-offset-slate-950 scale-110'
                            : 'opacity-70 hover:opacity-100'
                        }`}
                      />
                    ))}
                  </div>
                </div>

                <div className="flex items-center justify-between max-w-md border-t border-slate-800/80 pt-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Compact Navigation Mode</p>
                    <p className="text-[11px] text-slate-500">Reduce padding across cards and tables</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.compactMode}
                    onChange={(e) => handleUpdate({ compactMode: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>
              </div>
            </div>
          )}

          {/* CHAT PREFERENCES */}
          {activeTab === 'chat' && (
            <div className="space-y-6">
              <h2 className="text-base font-bold text-white">Conversational Co-Pilot Parameters</h2>

              <div className="space-y-5">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Default Model</label>
                  <p className="text-[11px] text-slate-500">Hybrid switchboard configuration</p>
                  <select
                    value={settings.defaultModel}
                    onChange={(e) => handleUpdate({ defaultModel: e.target.value })}
                    className="mt-1.5 h-10 w-full max-w-md rounded-xl border border-slate-800 bg-slate-950 px-3 text-xs text-white focus:outline-none"
                  >
                    <option value="Hybrid Router (Groq + Ollama)">
                      Hybrid Router (Groq Llama 3.3 + Ollama DeepSeek)
                    </option>
                    <option value="Groq Llama-3.3-70b-Versatile">
                      Groq Cloud (Llama 3.3 70B Versatile)
                    </option>
                    <option value="Ollama DeepSeek-R1-Distill-Qwen">
                      Ollama Local (DeepSeek-R1-Distill-Qwen)
                    </option>
                    <option value="Ollama Qwen2.5-Coder-7B">
                      Ollama Local (Qwen 2.5 Coder 7B)
                    </option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">
                    Streaming Render Speed
                  </label>
                  <p className="text-[11px] text-slate-500">Simulated typing animation pacing</p>
                  <div className="mt-2 grid grid-cols-3 gap-3 max-w-md">
                    {(['fast', 'normal', 'relaxed'] as const).map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => handleUpdate({ streamingSpeed: s })}
                        className={`rounded-xl border p-2.5 text-center text-xs font-semibold capitalize transition ${
                          settings.streamingSpeed === s
                            ? 'border-indigo-500 bg-indigo-500/20 text-white'
                            : 'border-slate-800 bg-slate-950 text-slate-400 hover:text-white'
                        }`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex items-center justify-between max-w-md border-t border-slate-800/80 pt-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Send on Enter</p>
                    <p className="text-[11px] text-slate-500">Press Shift + Enter for new lines</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.sendOnEnter}
                    onChange={(e) => handleUpdate({ sendOnEnter: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>

                <div className="flex items-center justify-between max-w-md border-t border-slate-800/80 pt-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Code Syntax Line Wrap</p>
                    <p className="text-[11px] text-slate-500">Wrap long lines in code blocks</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.codeWrap}
                    onChange={(e) => handleUpdate({ codeWrap: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>
              </div>
            </div>
          )}

          {/* NOTIFICATIONS TAB */}
          {activeTab === 'notifications' && (
            <div className="space-y-6">
              <h2 className="text-base font-bold text-white">Alerts & Notification Delivery</h2>

              <div className="space-y-4 max-w-md">
                <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Desktop Push Notifications</p>
                    <p className="text-[11px] text-slate-500">Show task and reminder alert banners</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.desktopPush}
                    onChange={(e) => handleUpdate({ desktopPush: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>

                <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Audible Chime Alert</p>
                    <p className="text-[11px] text-slate-500">Play subtle audio tone when tasks complete</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.soundAlerts}
                    onChange={(e) => handleUpdate({ soundAlerts: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>
              </div>
            </div>
          )}

          {/* PRIVACY TAB */}
          {activeTab === 'privacy' && (
            <div className="space-y-6">
              <h2 className="text-base font-bold text-white">Privacy & Local Data Retention</h2>

              <div className="space-y-4 max-w-md">
                <div>
                  <label className="block text-xs font-medium text-slate-300">
                    Local Memory Vector Retention
                  </label>
                  <p className="text-[11px] text-slate-500">Days to retain automatic memory extractions</p>
                  <select
                    value={settings.retentionDays}
                    onChange={(e) => handleUpdate({ retentionDays: Number(e.target.value) })}
                    className="mt-1.5 h-10 w-full rounded-xl border border-slate-800 bg-slate-950 px-3 text-xs text-white focus:outline-none"
                  >
                    <option value={7}>7 Days</option>
                    <option value={30}>30 Days (Recommended)</option>
                    <option value={90}>90 Days</option>
                    <option value={365}>365 Days (Persistent)</option>
                  </select>
                </div>

                <div className="flex items-center justify-between border-t border-slate-800/80 pt-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Private Chat Mode by Default</p>
                    <p className="text-[11px] text-slate-500">Do not index conversation context into pgvector</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.privateModeDefault}
                    onChange={(e) => handleUpdate({ privateModeDefault: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>
              </div>
            </div>
          )}

          {/* SECURITY & TOOLS TAB */}
          {activeTab === 'security' && (
            <div className="space-y-6">
              <h2 className="text-base font-bold text-white">Execution Security & Sandbox Controls</h2>

              <div className="space-y-4 max-w-md">
                <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">
                      Require Confirmation for Elevated Tools
                    </p>
                    <p className="text-[11px] text-slate-500">
                      Prompts operator before executing write or spawn actions
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.confirmElevatedTools}
                    onChange={(e) => handleUpdate({ confirmElevatedTools: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>

                <div className="flex items-center justify-between border-b border-slate-800/80 pb-4">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">
                      Auto-Approve Read-Only Tools
                    </p>
                    <p className="text-[11px] text-slate-500">
                      Allow safe inspection without operator intervention
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.autoApproveReadOnly}
                    onChange={(e) => handleUpdate({ autoApproveReadOnly: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Tool Execution Audit Log</p>
                    <p className="text-[11px] text-slate-500">Record all simulated commands to audit trail</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.auditLogging}
                    onChange={(e) => handleUpdate({ auditLogging: e.target.checked })}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500"
                  />
                </div>
              </div>
            </div>
          )}

          {/* ABOUT ASTA TAB */}
          {activeTab === 'about' && (
            <div className="space-y-6">
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-tr from-indigo-600 via-violet-600 to-cyan-400 p-0.5 shadow-xl shadow-indigo-600/30">
                  <div className="flex h-full w-full items-center justify-center rounded-[14px] bg-slate-950">
                    <Sparkles className="h-6 w-6 text-indigo-400" />
                  </div>
                </div>
                <div>
                  <h2 className="text-lg font-bold text-white">ASTA – Personal AI Assistant</h2>
                  <p className="font-mono text-xs text-indigo-300">Phase 14 Desktop Release · v1.4.0</p>
                </div>
              </div>

              <p className="text-xs text-slate-400 leading-relaxed max-w-xl">
                ASTA is a personal AI assistant co-pilot built with FastAPI, PostgreSQL 18 with
                pgvector, Groq, Ollama hybrid routing, and a React 19 + TypeScript + Tailwind
                desktop interface.
              </p>

              <div className="rounded-2xl border border-slate-800 bg-slate-950 p-4 space-y-2 text-xs font-mono max-w-xl">
                <div className="flex justify-between text-slate-400">
                  <span>Architecture:</span>
                  <span className="text-slate-200">Decoupled Zustand + React 19 Client</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Styling Engine:</span>
                  <span className="text-slate-200">Tailwind CSS v4 + Obsidian Obsidian Palette</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Backend Target:</span>
                  <span className="text-slate-200">FastAPI Async · SSE Streaming Protocol</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Repository Path:</span>
                  <span className="text-slate-200">c:\Asta</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Settings;
