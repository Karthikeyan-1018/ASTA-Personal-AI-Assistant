import React from 'react';
import {
  Home,
  MessageSquare,
  CheckSquare,
  Bell,
  Repeat,
  Brain,
  Wrench,
  Timer,
  Activity,
  Settings,
  ChevronLeft,
  ChevronRight,
  Plus,
  Sparkles,
  ShieldCheck,
} from 'lucide-react';
import { useTaskStore } from '../../stores/task.store';
import { useReminderStore } from '../../stores/reminder.store';
import { useRecurrenceStore } from '../../stores/recurrence.store';
import { useBackgroundTaskStore } from '../../stores/backgroundTask.store';
import { isLiveReminder } from '../../types/reminder.types';
import { isCancellableBackgroundTaskStatus } from '../../utils/backgroundTask';
import { useMemoryStore } from '../../stores/memory.store';
import { useAuthStore } from '../../stores/auth.store';
import { useChatStore } from '../../stores/chat.store';

export type NavSectionId =
  | 'home'
  | 'chat'
  | 'tasks'
  | 'reminders'
  | 'recurrences'
  | 'background-tasks'
  | 'memory'
  | 'tools'
  | 'system-monitor'
  | 'settings';

export interface SidebarProps {
  activeSection: NavSectionId;
  onSelectSection: (section: NavSectionId) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeSection,
  onSelectSection,
  collapsed,
  onToggleCollapse,
  mobileOpen,
  onCloseMobile,
}) => {
  const tasks = useTaskStore((s) => s.tasks);
  const reminders = useReminderStore((s) => s.reminders);
  const recurrenceRules = useRecurrenceStore((s) => s.rules);
  const backgroundTasks = useBackgroundTaskStore((s) => s.tasks);
  const memories = useMemoryStore((s) => s.memories);
  const user = useAuthStore((s) => s.user);
  const createNewConversation = useChatStore((s) => s.createNewConversation);

  const pendingTasksCount = tasks.filter((t) => !t.completed).length;
  const activeRemindersCount = reminders.filter((r) => isLiveReminder(r.status)).length;
  const activeRecurrenceCount = recurrenceRules.filter((r) => r.isActive).length;
  const activeBackgroundTaskCount = backgroundTasks.filter((task) =>
    isCancellableBackgroundTaskStatus(task.status)
  ).length;
  const memoryCount = memories.length;

  const navItems = [
    { id: 'home' as NavSectionId, label: 'Home', icon: Home },
    { id: 'chat' as NavSectionId, label: 'Chat', icon: MessageSquare },
    {
      id: 'tasks' as NavSectionId,
      label: 'Tasks',
      icon: CheckSquare,
      badge: pendingTasksCount > 0 ? String(pendingTasksCount) : undefined,
    },
    {
      id: 'reminders' as NavSectionId,
      label: 'Reminders',
      icon: Bell,
      badge: activeRemindersCount > 0 ? String(activeRemindersCount) : undefined,
    },
    {
      id: 'recurrences' as NavSectionId,
      label: 'Recurrences',
      icon: Repeat,
      badge:
        activeRecurrenceCount > 0 ? String(activeRecurrenceCount) : undefined,
    },
    {
      id: 'memory' as NavSectionId,
      label: 'Memory',
      icon: Brain,
      badge: memoryCount > 0 ? String(memoryCount) : undefined,
    },
    { id: 'tools' as NavSectionId, label: 'Tools', icon: Wrench },
    {
      id: 'background-tasks' as NavSectionId,
      label: 'Background Tasks',
      icon: Timer,
      badge:
        activeBackgroundTaskCount > 0
          ? String(activeBackgroundTaskCount)
          : undefined,
    },
    { id: 'system-monitor' as NavSectionId, label: 'System Monitor', icon: Activity },
  ];

  const handleNewChat = () => {
    void createNewConversation();
    onSelectSection('chat');
    if (mobileOpen) onCloseMobile();
  };

  const handleNavClick = (id: NavSectionId) => {
    onSelectSection(id);
    if (mobileOpen) onCloseMobile();
  };

  return (
    <>
      {/* Mobile backdrop overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={onCloseMobile}
        />
      )}

      {/* Sidebar container */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex flex-col border-r border-slate-800/80 bg-slate-950/95 backdrop-blur-xl transition-all duration-300 lg:static ${
          collapsed ? 'w-18' : 'w-64'
        } ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
      >
        {/* App Branding / Logo Header */}
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-slate-800/80 px-4">
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 via-violet-600 to-cyan-400 p-0.5 shadow-lg shadow-indigo-500/25">
              <div className="flex h-full w-full items-center justify-center rounded-[10px] bg-slate-950">
                <Sparkles className="h-5 w-5 text-indigo-400" />
              </div>
            </div>

            {!collapsed && (
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-base font-extrabold tracking-wider text-white">
                    ASTA
                  </span>
                  <span className="rounded bg-indigo-500/20 px-1.5 py-0.2 font-mono text-[9px] font-semibold text-indigo-300">
                    DESKTOP
                  </span>
                </div>
                <p className="truncate text-[10px] font-medium text-slate-400">
                  Personal AI Assistant
                </p>
              </div>
            )}
          </div>

          {!collapsed && (
            <button
              type="button"
              onClick={onToggleCollapse}
              className="hidden rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200 lg:flex"
              title="Collapse sidebar"
              aria-label="Collapse sidebar"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Action: New Chat Button */}
        <div className="p-3">
          <button
            type="button"
            onClick={handleNewChat}
            className={`group relative flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 font-medium text-white shadow-md shadow-indigo-600/25 transition-all hover:from-indigo-500 hover:to-violet-500 hover:shadow-indigo-600/40 active:scale-[0.98] ${
              collapsed ? 'h-11 px-0' : 'h-11 px-4 text-sm'
            }`}
            title="Start new conversation"
            aria-label="Start new conversation"
          >
            <Plus className="h-4 w-4 shrink-0 transition-transform group-hover:rotate-90" />
            {!collapsed && <span>New Chat</span>}
          </button>
        </div>

        {/* Navigation list */}
        <nav className="flex-1 space-y-1 overflow-y-auto px-2.5 py-2">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeSection === item.id;

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => handleNavClick(item.id)}
                title={item.label}
                aria-label={item.label}
                className={`group relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all ${
                  isActive
                    ? 'border border-indigo-500/30 bg-indigo-500/15 text-indigo-300 shadow-sm shadow-indigo-500/10'
                    : 'text-slate-400 hover:border hover:border-slate-800 hover:bg-slate-900/80 hover:text-slate-200'
                } ${collapsed ? 'justify-center px-0' : ''}`}
              >
                {isActive && (
                  <span className="absolute left-0 h-5 w-1 rounded-r-full bg-indigo-500" />
                )}
                <Icon
                  className={`h-5 w-5 shrink-0 transition-colors ${
                    isActive ? 'text-indigo-400' : 'text-slate-400 group-hover:text-slate-200'
                  }`}
                />

                {!collapsed && (
                  <>
                    <span className="flex-1 text-left truncate">{item.label}</span>
                    {item.badge && (
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                          isActive
                            ? 'bg-indigo-500/30 text-indigo-200'
                            : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        {item.badge}
                      </span>
                    )}
                  </>
                )}
              </button>
            );
          })}
        </nav>

        {/* Bottom Section: Settings & User Profile */}
        <div className="shrink-0 border-t border-slate-800/80 p-2.5 space-y-1">
          {/* Settings link */}
          <button
            type="button"
            onClick={() => handleNavClick('settings')}
            title="Settings"
            aria-label="Settings"
            className={`group relative flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-all ${
              activeSection === 'settings'
                ? 'border border-indigo-500/30 bg-indigo-500/15 text-indigo-300'
                : 'text-slate-400 hover:bg-slate-900/80 hover:text-slate-200'
            } ${collapsed ? 'justify-center px-0' : ''}`}
          >
            {activeSection === 'settings' && (
              <span className="absolute left-0 h-5 w-1 rounded-r-full bg-indigo-500" />
            )}
            <Settings className="h-5 w-5 shrink-0" />
            {!collapsed && <span className="flex-1 text-left">Settings</span>}
          </button>

          {/* User Profile Card */}
          <div
            className={`flex items-center gap-3 rounded-xl border border-slate-800/60 bg-slate-900/40 p-2 ${
              collapsed ? 'justify-center p-1.5' : ''
            }`}
          >
            <div className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-xs font-bold text-white shadow-inner">
              {user.name.charAt(0).toUpperCase()}
              <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-slate-950 bg-emerald-400" />
            </div>

            {!collapsed && (
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between">
                  <p className="truncate text-xs font-semibold text-slate-200">{user.name}</p>
                  <span
                    title="Tool sandbox policy is enforced by the backend"
                    aria-label="Tool sandbox policy is enforced by the backend"
                  >
                    <ShieldCheck className="h-3.5 w-3.5 text-indigo-400" />
                  </span>
                </div>
                <p className="truncate text-[10px] text-slate-500">{user.role}</p>
              </div>
            )}
          </div>

          {/* Expand button when collapsed */}
          {collapsed && (
            <button
              type="button"
              onClick={onToggleCollapse}
              className="flex w-full items-center justify-center rounded-lg py-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              title="Expand sidebar"
              aria-label="Expand sidebar"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          )}
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
