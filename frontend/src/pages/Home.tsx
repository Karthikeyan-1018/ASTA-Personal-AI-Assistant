import React, { useState } from 'react';
import {
  Sparkles,
  ArrowRight,
  MessageSquare,
  CheckCircle2,
  Circle,
  Bell,
  Brain,
  Wrench,
  Activity,
  Send,
  Clock,
  Plus,
  Shield,
  Layers,
  CheckSquare,
} from 'lucide-react';
import type { NavSectionId } from '../components/layout/Sidebar';
import { useAuthStore } from '../stores/auth.store';
import { useChatStore } from '../stores/chat.store';
import { useTaskStore } from '../stores/task.store';
import { useReminderStore } from '../stores/reminder.store';
import { useMemoryStore } from '../stores/memory.store';
import { useSystemStore } from '../stores/system.store';
import { formatDate } from '../utils/formatDate';

export interface HomeProps {
  onNavigate: (section: NavSectionId) => void;
}

export const Home: React.FC<HomeProps> = ({ onNavigate }) => {
  const user = useAuthStore((s) => s.user);
  const conversations = useChatStore((s) => s.conversations);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const setActiveConversationId = useChatStore((s) => s.setActiveConversationId);
  const createNewConversation = useChatStore((s) => s.createNewConversation);

  const tasks = useTaskStore((s) => s.tasks);
  const toggleTaskCompleted = useTaskStore((s) => s.toggleTaskCompleted);
  const addTask = useTaskStore((s) => s.addTask);

  const reminders = useReminderStore((s) => s.reminders);
  const toggleReminderCompleted = useReminderStore((s) => s.toggleReminderCompleted);

  const memories = useMemoryStore((s) => s.memories);
  const metrics = useSystemStore((s) => s.metrics);

  const [quickPrompt, setQuickPrompt] = useState('');
  const [quickTaskTitle, setQuickTaskTitle] = useState('');
  const [showQuickTaskInput, setShowQuickTaskInput] = useState(false);

  const latestMetric = metrics[metrics.length - 1] || { cpuPercent: 14, memoryPercent: 46 };
  const pendingTasks = tasks.filter((t) => !t.completed);
  const completedTasksCount = tasks.filter((t) => t.completed).length;
  const todayReminders = reminders.filter((r) => !r.completed);

  const handleQuickPromptSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = quickPrompt.trim();
    if (!trimmed) return;
    createNewConversation();
    onNavigate('chat');
    await sendMessage(trimmed);
    setQuickPrompt('');
  };

  const handleCreateQuickTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!quickTaskTitle.trim()) return;
    addTask({
      title: quickTaskTitle.trim(),
      category: 'General',
      priority: 'medium',
      status: 'pending',
    });
    setQuickTaskTitle('');
    setShowQuickTaskInput(false);
  };

  const handleOpenConversation = (convId: string) => {
    setActiveConversationId(convId);
    onNavigate('chat');
  };

  return (
    <div className="space-y-6 pb-6">
      {/* Hero Welcome Banner */}
      <div className="relative overflow-hidden rounded-3xl border border-slate-800/80 bg-gradient-to-br from-slate-900 via-slate-950 to-indigo-950/40 p-6 shadow-2xl sm:p-8">
        <div className="absolute right-0 top-0 -mr-16 -mt-16 h-64 w-64 rounded-full bg-indigo-600/10 blur-3xl" />
        <div className="absolute right-32 bottom-0 -mb-16 h-48 w-48 rounded-full bg-cyan-600/10 blur-3xl" />

        <div className="relative z-10 flex flex-col justify-between gap-6 lg:flex-row lg:items-center">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3 py-1 text-xs font-semibold text-indigo-300">
                <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
                ASTA Executive Co-Pilot
              </span>
              <span className="inline-flex items-center gap-1 rounded-full border border-slate-800 bg-slate-900/80 px-2.5 py-1 text-[11px] font-medium text-slate-400">
                <Clock className="h-3 w-3" />
                {formatDate()}
              </span>
            </div>

            <h1 className="text-2xl font-extrabold tracking-tight text-white sm:text-3xl">
              Good evening, {user.name}.
            </h1>
            <p className="max-w-2xl text-xs sm:text-sm text-slate-400 leading-relaxed">
              Your personal assistant workspace is initialized. Local tool execution sandbox is
              active, semantic memory is synchronized, and background queues are running normally.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => {
                createNewConversation();
                onNavigate('chat');
              }}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-5 py-3 text-xs sm:text-sm font-semibold text-white shadow-lg shadow-indigo-600/25 transition hover:from-indigo-500 hover:to-violet-500 active:scale-[0.98]"
            >
              <MessageSquare className="h-4 w-4" />
              <span>Start New Chat</span>
            </button>
            <button
              type="button"
              onClick={() => onNavigate('tasks')}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-800/80 px-4 py-3 text-xs sm:text-sm font-medium text-slate-200 transition hover:bg-slate-800"
            >
              <CheckSquare className="h-4 w-4 text-indigo-400" />
              <span>View Tasks</span>
            </button>
          </div>
        </div>

        {/* AI Quick Input Bar */}
        <div className="relative z-10 mt-6 pt-6 border-t border-slate-800/80">
          <form onSubmit={handleQuickPromptSubmit} className="relative flex items-center">
            <input
              type="text"
              value={quickPrompt}
              onChange={(e) => setQuickPrompt(e.target.value)}
              placeholder="Ask ASTA anything, inspect local state, or issue a project command..."
              className="h-12 w-full rounded-2xl border border-slate-800 bg-slate-950/80 pl-4 pr-28 text-xs sm:text-sm text-white placeholder-slate-500 shadow-inner focus:border-indigo-500/60 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
            />
            <button
              type="submit"
              disabled={!quickPrompt.trim()}
              className="absolute right-1.5 flex h-9 items-center gap-1.5 rounded-xl bg-indigo-600 px-3.5 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-40"
            >
              <span>Ask ASTA</span>
              <Send className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>
      </div>

      {/* Productivity Overview Cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-2 lg:grid-cols-4 sm:gap-4">
        {/* Pending Tasks Card */}
        <div
          onClick={() => onNavigate('tasks')}
          className="group cursor-pointer rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4 transition hover:border-indigo-500/40 hover:bg-slate-900/70"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Tasks Queue</span>
            <div className="rounded-lg bg-indigo-500/10 p-2 text-indigo-400 group-hover:bg-indigo-500/20">
              <CheckSquare className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-white">{pendingTasks.length}</span>
            <span className="text-xs text-slate-500">pending ({completedTasksCount} done)</span>
          </div>
          <div className="mt-2 flex items-center gap-1 text-[11px] font-medium text-indigo-400">
            <span>Manage tasks</span>
            <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
          </div>
        </div>

        {/* Reminders Card */}
        <div
          onClick={() => onNavigate('reminders')}
          className="group cursor-pointer rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4 transition hover:border-amber-500/40 hover:bg-slate-900/70"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Today's Reminders</span>
            <div className="rounded-lg bg-amber-500/10 p-2 text-amber-400 group-hover:bg-amber-500/20">
              <Bell className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-white">{todayReminders.length}</span>
            <span className="text-xs text-slate-500">active alerts</span>
          </div>
          <div className="mt-2 flex items-center gap-1 text-[11px] font-medium text-amber-400">
            <span>View timeline</span>
            <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
          </div>
        </div>

        {/* Semantic Memory Card */}
        <div
          onClick={() => onNavigate('memory')}
          className="group cursor-pointer rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4 transition hover:border-violet-500/40 hover:bg-slate-900/70"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Semantic Memory</span>
            <div className="rounded-lg bg-violet-500/10 p-2 text-violet-400 group-hover:bg-violet-500/20">
              <Brain className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-white">{memories.length}</span>
            <span className="text-xs text-slate-500">facts & rules</span>
          </div>
          <div className="mt-2 flex items-center gap-1 text-[11px] font-medium text-violet-400">
            <span>Inspect context</span>
            <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
          </div>
        </div>

        {/* System Health Card */}
        <div
          onClick={() => onNavigate('system-monitor')}
          className="group cursor-pointer rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4 transition hover:border-emerald-500/40 hover:bg-slate-900/70"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">System Load</span>
            <div className="rounded-lg bg-emerald-500/10 p-2 text-emerald-400 group-hover:bg-emerald-500/20">
              <Activity className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-white">{latestMetric.cpuPercent}%</span>
            <span className="text-xs text-slate-500">CPU · {latestMetric.memoryPercent}% RAM</span>
          </div>
          <div className="mt-2 flex items-center gap-1 text-[11px] font-medium text-emerald-400">
            <span>Diagnostics</span>
            <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
          </div>
        </div>
      </div>

      {/* Main Grid: Left (Conversations + Tasks) | Right (Quick Actions + Reminders + Activity) */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left Column: Recent Conversations & Tasks */}
        <div className="space-y-6 lg:col-span-2">
          {/* Recent Conversations */}
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
            <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
              <div className="flex items-center gap-2">
                <MessageSquare className="h-4 w-4 text-indigo-400" />
                <h2 className="text-sm font-bold text-white uppercase tracking-wider">
                  Recent Conversations
                </h2>
              </div>
              <button
                type="button"
                onClick={() => onNavigate('chat')}
                className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
              >
                All chats →
              </button>
            </div>

            <div className="mt-3 divide-y divide-slate-800/60">
              {conversations.slice(0, 3).map((conv) => (
                <div
                  key={conv.id}
                  onClick={() => handleOpenConversation(conv.id)}
                  className="group flex cursor-pointer items-start justify-between gap-4 py-3 transition hover:bg-slate-900/50 rounded-xl px-2.5 -mx-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-xs sm:text-sm font-semibold text-slate-200 group-hover:text-indigo-300 truncate">
                        {conv.title}
                      </p>
                      {conv.isPinned && (
                        <span className="rounded bg-indigo-500/20 px-1.5 py-0.2 text-[9px] font-semibold text-indigo-300">
                          PINNED
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-slate-500 line-clamp-1">
                      {conv.previewSnippet}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <span className="text-[10px] font-mono text-slate-500">
                      {conv.messages.length} msgs
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 text-slate-600 transition-transform group-hover:translate-x-1 group-hover:text-indigo-400" />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Upcoming Tasks */}
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
            <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
              <div className="flex items-center gap-2">
                <CheckSquare className="h-4 w-4 text-emerald-400" />
                <h2 className="text-sm font-bold text-white uppercase tracking-wider">
                  Upcoming Tasks
                </h2>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setShowQuickTaskInput((prev) => !prev)}
                  className="inline-flex items-center gap-1 rounded-lg bg-indigo-500/10 px-2 py-1 text-xs font-medium text-indigo-300 hover:bg-indigo-500/20"
                >
                  <Plus className="h-3 w-3" />
                  <span>Quick Add</span>
                </button>
                <button
                  type="button"
                  onClick={() => onNavigate('tasks')}
                  className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
                >
                  Manage →
                </button>
              </div>
            </div>

            {/* Quick Add Form */}
            {showQuickTaskInput && (
              <form onSubmit={handleCreateQuickTask} className="mt-3 flex gap-2">
                <input
                  type="text"
                  value={quickTaskTitle}
                  onChange={(e) => setQuickTaskTitle(e.target.value)}
                  placeholder="Type new task title and press Enter..."
                  autoFocus
                  className="h-9 flex-1 rounded-xl border border-slate-800 bg-slate-950 px-3 text-xs text-slate-200 placeholder-slate-500 focus:border-indigo-500/60 focus:outline-none"
                />
                <button
                  type="submit"
                  className="h-9 rounded-xl bg-indigo-600 px-3 text-xs font-semibold text-white hover:bg-indigo-500"
                >
                  Save
                </button>
              </form>
            )}

            <div className="mt-3 space-y-2">
              {tasks.slice(0, 4).map((task) => (
                <div
                  key={task.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-slate-800/60 bg-slate-900/50 p-3 transition hover:border-slate-700"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <button
                      type="button"
                      onClick={() => toggleTaskCompleted(task.id)}
                      className="text-slate-500 hover:text-emerald-400 transition-colors shrink-0"
                    >
                      {task.completed ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                      ) : (
                        <Circle className="h-4 w-4" />
                      )}
                    </button>
                    <span
                      className={`text-xs sm:text-sm font-medium truncate ${
                        task.completed ? 'text-slate-500 line-through' : 'text-slate-200'
                      }`}
                    >
                      {task.title}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium capitalize ${
                        task.priority === 'critical'
                          ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                          : task.priority === 'high'
                          ? 'bg-amber-500/15 text-amber-300'
                          : 'bg-slate-800 text-slate-400'
                      }`}
                    >
                      {task.priority}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right Column: Quick Actions + Today's Reminders + Audit Trail */}
        <div className="space-y-6">
          {/* Quick Actions Card */}
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
            <h2 className="text-sm font-bold text-white uppercase tracking-wider mb-3">
              Quick Shortcuts
            </h2>

            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => {
                  createNewConversation();
                  onNavigate('chat');
                }}
                className="flex flex-col items-start gap-1.5 rounded-xl border border-slate-800 bg-slate-900/70 p-3 text-left transition hover:border-indigo-500/40 hover:bg-slate-900"
              >
                <MessageSquare className="h-4 w-4 text-indigo-400" />
                <span className="text-xs font-semibold text-slate-200">New Dialogue</span>
                <span className="text-[10px] text-slate-500">AI reasoning</span>
              </button>

              <button
                type="button"
                onClick={() => onNavigate('tasks')}
                className="flex flex-col items-start gap-1.5 rounded-xl border border-slate-800 bg-slate-900/70 p-3 text-left transition hover:border-indigo-500/40 hover:bg-slate-900"
              >
                <CheckSquare className="h-4 w-4 text-emerald-400" />
                <span className="text-xs font-semibold text-slate-200">Create Task</span>
                <span className="text-[10px] text-slate-500">Queue objective</span>
              </button>

              <button
                type="button"
                onClick={() => onNavigate('tools')}
                className="flex flex-col items-start gap-1.5 rounded-xl border border-slate-800 bg-slate-900/70 p-3 text-left transition hover:border-indigo-500/40 hover:bg-slate-900"
              >
                <Wrench className="h-4 w-4 text-cyan-400" />
                <span className="text-xs font-semibold text-slate-200">Run Tool</span>
                <span className="text-[10px] text-slate-500">Sandboxed exec</span>
              </button>

              <button
                type="button"
                onClick={() => onNavigate('memory')}
                className="flex flex-col items-start gap-1.5 rounded-xl border border-slate-800 bg-slate-900/70 p-3 text-left transition hover:border-indigo-500/40 hover:bg-slate-900"
              >
                <Brain className="h-4 w-4 text-violet-400" />
                <span className="text-xs font-semibold text-slate-200">Add Memory</span>
                <span className="text-[10px] text-slate-500">Semantic recall</span>
              </button>
            </div>
          </div>

          {/* Today's Reminders */}
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
              <div className="flex items-center gap-2">
                <Bell className="h-4 w-4 text-amber-400" />
                <h2 className="text-sm font-bold text-white uppercase tracking-wider">
                  Reminders
                </h2>
              </div>
              <button
                type="button"
                onClick={() => onNavigate('reminders')}
                className="text-xs font-medium text-amber-400 hover:text-amber-300"
              >
                All ({reminders.length}) →
              </button>
            </div>

            <div className="mt-3 space-y-2.5">
              {todayReminders.slice(0, 3).map((rem) => (
                <div
                  key={rem.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-slate-800/60 bg-slate-900/50 p-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-slate-200 truncate">{rem.title}</p>
                    <span className="text-[10px] font-mono text-amber-300">{rem.time} · {rem.category}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => toggleReminderCompleted(rem.id)}
                    className="rounded p-1 text-slate-500 hover:text-emerald-400"
                    title="Mark complete"
                  >
                    <CheckCircle2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Assistant Security & State Overview */}
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
            <h2 className="text-sm font-bold text-white uppercase tracking-wider mb-3">
              Runtime Security State
            </h2>
            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between rounded-lg bg-slate-950 p-2.5">
                <span className="flex items-center gap-2 text-slate-400">
                  <Shield className="h-3.5 w-3.5 text-indigo-400" />
                  Execution Boundary
                </span>
                <span className="font-mono text-emerald-400 text-[11px] font-semibold">
                  AUDITED SANDBOX
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg bg-slate-950 p-2.5">
                <span className="flex items-center gap-2 text-slate-400">
                  <Layers className="h-3.5 w-3.5 text-cyan-400" />
                  Model Router
                </span>
                <span className="font-mono text-slate-200 text-[11px]">
                  Hybrid (Groq + Ollama)
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg bg-slate-950 p-2.5">
                <span className="flex items-center gap-2 text-slate-400">
                  <Brain className="h-3.5 w-3.5 text-violet-400" />
                  Vector Retain
                </span>
                <span className="font-mono text-slate-200 text-[11px]">
                  384-dim (Local)
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Home;
