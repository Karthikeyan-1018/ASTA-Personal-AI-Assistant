import React, { useState } from 'react';
import {
  Plus,
  Bell,
  Clock,
  Calendar,
  CheckCircle2,
  Circle,
  Trash2,
  Edit2,
  Volume2,
  X,
  Sparkles,
  AlertTriangle,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import { useReminderStore } from '../stores/reminder.store';
import { useUIStore } from '../stores/ui.store';
import type { ReminderItem, ReminderCategory, ReminderFrequency } from '../types/reminder.types';

function getTodayDateString(): string {
  return new Date().toISOString().split('T')[0];
}

export const Reminders: React.FC = () => {
  const reminders = useReminderStore((s) => s.reminders);
  const isLoading = useReminderStore((s) => s.isLoading);
  const error = useReminderStore((s) => s.error);
  const fetchReminders = useReminderStore((s) => s.fetchReminders);
  const addReminder = useReminderStore((s) => s.addReminder);
  const updateReminder = useReminderStore((s) => s.updateReminder);
  const deleteReminder = useReminderStore((s) => s.deleteReminder);
  const toggleReminderActive = useReminderStore((s) => s.toggleReminderActive);
  const toggleReminderCompleted = useReminderStore((s) => s.toggleReminderCompleted);

  const addNotification = useUIStore((s) => s.addNotification);

  const [activeTab, setActiveTab] = useState<'today' | 'upcoming' | 'completed' | 'all'>('today');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingReminder, setEditingReminder] = useState<ReminderItem | null>(null);

  // Notification Preferences Local State
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [desktopPushEnabled, setDesktopPushEnabled] = useState(true);

  const [todayStr] = useState(getTodayDateString);

  // Form State
  const [formTitle, setFormTitle] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formDate, setFormDate] = useState(getTodayDateString);
  const [formTime, setFormTime] = useState('18:00');
  const [formCategory, setFormCategory] = useState<ReminderCategory>('Personal');
  const [formFrequency, setFormFrequency] = useState<ReminderFrequency>('daily');

  const openCreateModal = () => {
    setEditingReminder(null);
    setFormTitle('');
    setFormDesc('');
    setFormDate(todayStr);
    setFormTime('18:00');
    setFormCategory('Personal');
    setFormFrequency('daily');
    setModalOpen(true);
  };

  const openEditModal = (r: ReminderItem) => {
    setEditingReminder(r);
    setFormTitle(r.title);
    setFormDesc(r.description || '');
    setFormDate(r.date);
    setFormTime(r.time);
    setFormCategory(r.category);
    setFormFrequency(r.frequency);
    setModalOpen(true);
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTitle.trim()) return;

    if (editingReminder) {
      updateReminder(editingReminder.id, {
        title: formTitle.trim(),
        description: formDesc.trim() || undefined,
        date: formDate,
        time: formTime,
        category: formCategory,
        frequency: formFrequency,
      });
    } else {
      addReminder({
        title: formTitle.trim(),
        description: formDesc.trim() || undefined,
        date: formDate,
        time: formTime,
        category: formCategory,
        frequency: formFrequency,
        active: true,
      });
    }

    setModalOpen(false);
  };

  const handleSendTestNotification = () => {
    addNotification({
      title: 'ASTA Test Alert',
      message: 'This is a simulated desktop reminder notification delivered to your workspace.',
      category: 'reminder',
    });
  };

  const filteredReminders = reminders.filter((r) => {
    if (activeTab === 'today') return r.date === todayStr && !r.completed;
    if (activeTab === 'upcoming') return (r.date > todayStr || r.frequency !== 'once') && !r.completed;
    if (activeTab === 'completed') return r.completed;
    return true;
  });

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            Reminders & Schedules
          </h1>
          <p className="text-xs sm:text-sm text-slate-400">
            Automated calendar reminders, routine maintenance triggers, and notifications.
          </p>
        </div>

        <button
          type="button"
          onClick={openCreateModal}
          className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2.5 text-xs sm:text-sm font-semibold text-white shadow-lg shadow-amber-600/25 transition hover:bg-amber-500 active:scale-[0.98]"
        >
          <Plus className="h-4 w-4" />
          <span>New Reminder</span>
        </button>
      </div>

      {/* Data reliability banner: never replace confirmed data with mock data */}
      {error && (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <div>
              <p className="text-xs font-semibold text-rose-200">Reminder service error</p>
              <p className="mt-0.5 text-[11px] text-rose-200/80">{error}</p>
              <p className="mt-1 text-[10px] text-rose-200/60">
                Last confirmed reminder data is preserved until a refresh succeeds.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              void fetchReminders();
            }}
            disabled={isLoading}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Retry</span>
          </button>
        </div>
      )}

      {/* Notification Preferences Bar */}
      <div className="flex flex-col gap-4 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/10 text-amber-400">
            <Bell className="h-4 w-4" />
          </div>
          <div>
            <p className="text-xs font-semibold text-slate-200">Workspace Notification Delivery</p>
            <p className="text-[11px] text-slate-500">Configure simulated audio cues and desktop banners</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={desktopPushEnabled}
              onChange={(e) => setDesktopPushEnabled(e.target.checked)}
              className="rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-0"
            />
            <span>Desktop Banners</span>
          </label>

          <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={soundEnabled}
              onChange={(e) => setSoundEnabled(e.target.checked)}
              className="rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-0"
            />
            <Volume2 className="h-3.5 w-3.5 text-slate-400" />
            <span>Audio Chime</span>
          </label>

          <button
            type="button"
            onClick={handleSendTestNotification}
            className="rounded-xl border border-slate-800 bg-slate-800/80 px-3 py-1.5 text-xs font-medium text-amber-300 transition hover:bg-slate-800"
          >
            <span className="flex items-center gap-1.5">
              <Sparkles className="h-3 w-3" />
              Test Toast
            </span>
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
        {(['today', 'upcoming', 'completed', 'all'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={`rounded-xl px-4 py-2 text-xs font-semibold capitalize transition ${
              activeTab === tab
                ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Reminders List */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" aria-busy={isLoading}>
        {isLoading && reminders.length === 0 ? (
          <div className="col-span-full flex items-center justify-center gap-2 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-xs text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
            <span>Loading reminders…</span>
          </div>
        ) : filteredReminders.length === 0 ? (
          error ? null : (
            <div className="col-span-full rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
              <Bell className="mx-auto h-12 w-12 text-slate-600" />
              <p className="mt-3 text-sm font-semibold text-slate-300">
                {reminders.length === 0 ? 'No reminders yet' : 'No reminders in this view'}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {reminders.length === 0
                  ? 'Create a reminder to stay on top of routines and maintenance tasks.'
                  : 'Try another tab or create a new reminder.'}
              </p>
            </div>
          )
        ) : (
          filteredReminders.map((r) => (
            <div
              key={r.id}
              className={`flex flex-col justify-between rounded-2xl border p-4.5 transition ${
                r.completed
                  ? 'border-slate-800/60 bg-slate-950/40 opacity-60'
                  : 'border-slate-800/90 bg-slate-900/40 hover:border-amber-500/40 hover:bg-slate-900/70 shadow-lg'
              }`}
            >
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <button
                      type="button"
                      onClick={() => toggleReminderCompleted(r.id)}
                      className="text-slate-500 hover:text-emerald-400 transition"
                      title={r.completed ? 'Mark pending' : 'Mark completed'}
                    >
                      {r.completed ? (
                        <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                      ) : (
                        <Circle className="h-5 w-5" />
                      )}
                    </button>
                    <h3
                      className={`text-sm font-bold truncate ${
                        r.completed ? 'text-slate-500 line-through' : 'text-slate-100'
                      }`}
                    >
                      {r.title}
                    </h3>
                  </div>

                  {/* Active Switch */}
                  <button
                    type="button"
                    onClick={() => toggleReminderActive(r.id)}
                    className={`h-5 w-9 rounded-full transition-colors relative p-0.5 ${
                      r.active ? 'bg-amber-600' : 'bg-slate-800'
                    }`}
                    title={r.active ? 'Deactivate' : 'Activate'}
                  >
                    <span
                      className={`block h-4 w-4 rounded-full bg-white transition-transform ${
                        r.active ? 'translate-x-4' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>

                {r.description && (
                  <p className="mt-2 text-xs text-slate-400 leading-relaxed pl-7.5">
                    {r.description}
                  </p>
                )}
              </div>

              {/* Card Meta & Controls */}
              <div className="mt-4 flex items-center justify-between border-t border-slate-800/60 pt-3 text-[11px] text-slate-500">
                <div className="flex items-center gap-2">
                  <span className="flex items-center gap-1 font-mono text-amber-300 font-semibold">
                    <Clock className="h-3 w-3" />
                    {r.time}
                  </span>
                  <span>·</span>
                  <span className="flex items-center gap-1 font-mono">
                    <Calendar className="h-3 w-3 text-slate-500" />
                    {r.date}
                  </span>
                  <span>·</span>
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400 capitalize">
                    {r.frequency}
                  </span>
                </div>

                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => openEditModal(r)}
                    className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                    title="Edit"
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteReminder(r.id)}
                    className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-rose-400"
                    title="Delete"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create / Edit Reminder Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <h2 className="text-base font-bold text-white">
                {editingReminder ? 'Edit Reminder' : 'New Reminder'}
              </h2>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleFormSubmit} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300">Title *</label>
                <input
                  type="text"
                  required
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder="e.g. Daily system diagnostic snapshot"
                  className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white placeholder-slate-500 focus:border-amber-500/60 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300">Description</label>
                <textarea
                  rows={2}
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="Optional reminder details..."
                  className="mt-1 w-full rounded-xl border border-slate-800 bg-slate-900 p-3 text-xs text-white placeholder-slate-500 focus:border-amber-500/60 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Date</label>
                  <input
                    type="date"
                    required
                    value={formDate}
                    onChange={(e) => setFormDate(e.target.value)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">Time</label>
                  <input
                    type="time"
                    required
                    value={formTime}
                    onChange={(e) => setFormTime(e.target.value)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white focus:outline-none"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Category</label>
                  <select
                    value={formCategory}
                    onChange={(e) => setFormCategory(e.target.value as ReminderCategory)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                  >
                    <option value="System">System</option>
                    <option value="Maintenance">Maintenance</option>
                    <option value="Personal">Personal</option>
                    <option value="Workflow">Workflow</option>
                    <option value="Work">Work</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">Frequency</label>
                  <select
                    value={formFrequency}
                    onChange={(e) => setFormFrequency(e.target.value as ReminderFrequency)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                  >
                    <option value="once">Once</option>
                    <option value="daily">Daily</option>
                    <option value="weekdays">Weekdays</option>
                    <option value="weekly">Weekly</option>
                  </select>
                </div>
              </div>

              <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="rounded-xl bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500"
                >
                  {editingReminder ? 'Save Changes' : 'Create Reminder'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Reminders;
