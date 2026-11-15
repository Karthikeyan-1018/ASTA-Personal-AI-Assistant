import React, { useRef, useState } from 'react';
import {
  Search,
  Bell,
  Menu,
  X,
  Check,
  Trash2,
  Command,
  UserCheck,
  LogOut,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { NavSectionId } from './Sidebar';
import { useUIStore } from '../../stores/ui.store';
import { useAuthStore } from '../../stores/auth.store';

interface SectionMetadata {
  title: string;
  category: string;
  description: string;
}

const SECTION_META: Record<NavSectionId, SectionMetadata> = {
  home: {
    title: 'Dashboard Overview',
    category: 'ASTA Control',
    description: 'Executive briefing, quick input, and daily telemetry',
  },
  chat: {
    title: 'AI Conversation',
    category: 'Reasoning Engine',
    description: 'Multi-turn intelligent co-pilot with simulated streaming',
  },
  tasks: {
    title: 'Task Management',
    category: 'Productivity',
    description: 'Prioritized task queues, objectives, and execution state',
  },
  reminders: {
    title: 'Reminders & Alerts',
    category: 'Scheduling',
    description: 'Time-sensitive triggers and automated routine alerts',
  },
  memory: {
    title: 'Memory & Knowledge',
    category: 'Long-Term Storage',
    description: 'Semantic vector knowledge base and user preferences',
  },
  tools: {
    title: 'Tool Execution Sandbox',
    category: 'System Automation',
    description: 'Audited filesystem, process inspection, and desktop tools',
  },
  'system-monitor': {
    title: 'System Telemetry',
    category: 'Diagnostics',
    description: 'Resource utilization, service health, and process table',
  },
  settings: {
    title: 'Preferences & Config',
    category: 'Settings',
    description: 'Appearance, model routing, privacy, and security controls',
  },
};

export interface TopbarProps {
  activeSection: NavSectionId;
  onOpenMobileMenu: () => void;
  pageAction?: React.ReactNode;
}

export const Topbar: React.FC<TopbarProps> = ({
  activeSection,
  onOpenMobileMenu,
  pageAction,
}) => {
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [profileDropdownOpen, setProfileDropdownOpen] = useState(false);
  const navigate = useNavigate();

  const meta = SECTION_META[activeSection];
  const searchQuery = useUIStore((s) => s.searchQuery);
  const setSearchQuery = useUIStore((s) => s.setSearchQuery);
  const notifications = useUIStore((s) => s.notifications);
  const notificationsOpen = useUIStore((s) => s.notificationsOpen);
  const toggleNotifications = useUIStore((s) => s.toggleNotifications);
  const markNotificationRead = useUIStore((s) => s.markNotificationRead);
  const markAllNotificationsRead = useUIStore((s) => s.markAllNotificationsRead);
  const dismissNotification = useUIStore((s) => s.dismissNotification);

  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <header className="relative z-30 flex h-16 shrink-0 items-center justify-between border-b border-slate-800/80 bg-slate-950/80 px-4 backdrop-blur-md sm:px-6">
      {/* Left: Mobile hamburger + Breadcrumbs & Title */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenMobileMenu}
          className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-slate-200 lg:hidden"
          title="Open Navigation"
        >
          <Menu className="h-5 w-5" />
        </button>

        <div className="flex flex-col">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
            <span>ASTA</span>
            <span className="text-slate-600">/</span>
            <span className="text-indigo-400 font-semibold">{meta.category}</span>
          </div>
          <h1 className="text-base font-bold text-white tracking-tight sm:text-lg">
            {meta.title}
          </h1>
        </div>
      </div>

      {/* Middle & Right: Global Search + Notifications + Custom Page Action + Profile */}
      <div className="flex items-center gap-2.5 sm:gap-3">
        {/* Global Search Bar */}
        <div className="relative hidden md:block w-56 lg:w-72">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            ref={searchInputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={`Search ${meta.title.toLowerCase()}...`}
            className="h-9 w-full rounded-xl border border-slate-800 bg-slate-900/90 pl-9 pr-14 text-xs text-slate-200 placeholder-slate-400 transition-colors focus:border-indigo-500/50 focus:bg-slate-900 focus:outline-none focus:ring-1 focus:ring-indigo-500/30"
          />
          {searchQuery ? (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
            >
              <X className="h-3 w-3" />
            </button>
          ) : (
            <div className="pointer-events-none absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-0.5 rounded border border-slate-700/80 bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-400">
              <Command className="h-2.5 w-2.5" />
              <span>K</span>
            </div>
          )}
        </div>

        {/* Page-Specific Action */}
        {pageAction && <div className="flex items-center">{pageAction}</div>}

        {/* Notifications Popover Toggle */}
        <div className="relative">
          <button
            type="button"
            onClick={toggleNotifications}
            className={`relative rounded-xl border border-slate-800 p-2 text-slate-300 transition hover:bg-slate-800/80 hover:text-white ${
              notificationsOpen ? 'bg-slate-800 text-white border-indigo-500/40' : 'bg-slate-900/60'
            }`}
            title="Notifications"
          >
            <Bell className="h-4 w-4" />
            {unreadCount > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-bold text-white shadow-md shadow-indigo-600/50">
                {unreadCount}
              </span>
            )}
          </button>

          {/* Notifications Dropdown Panel */}
          {notificationsOpen && (
            <div className="absolute right-0 top-12 z-50 w-80 sm:w-96 rounded-2xl border border-slate-800 bg-slate-950 p-4 shadow-2xl shadow-black/80 backdrop-blur-xl">
              <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-white">
                    Notifications
                  </h3>
                  {unreadCount > 0 && (
                    <span className="rounded-full bg-indigo-500/20 px-2 py-0.5 text-[10px] font-semibold text-indigo-300">
                      {unreadCount} new
                    </span>
                  )}
                </div>
                {unreadCount > 0 && (
                  <button
                    type="button"
                    onClick={markAllNotificationsRead}
                    className="text-[11px] font-medium text-indigo-400 hover:text-indigo-300"
                  >
                    Mark all read
                  </button>
                )}
              </div>

              <div className="max-h-72 overflow-y-auto py-2 divide-y divide-slate-800/50">
                {notifications.length === 0 ? (
                  <p className="py-6 text-center text-xs text-slate-400">No notifications.</p>
                ) : (
                  notifications.map((notif) => (
                    <div
                      key={notif.id}
                      className={`group flex items-start justify-between gap-3 p-2.5 transition rounded-xl ${
                        notif.read ? 'opacity-65 hover:opacity-100' : 'bg-indigo-500/5 border border-indigo-500/20'
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="text-xs font-semibold text-slate-200 truncate">
                            {notif.title}
                          </p>
                          {!notif.read && (
                            <span className="h-1.5 w-1.5 rounded-full bg-indigo-400" />
                          )}
                        </div>
                        <p className="mt-0.5 text-[11px] text-slate-400 line-clamp-2">
                          {notif.message}
                        </p>
                        <span className="mt-1 block text-[10px] text-slate-400 font-mono">
                          {notif.timestamp}
                        </span>
                      </div>

                      <div className="flex items-center gap-1 opacity-80 group-hover:opacity-100">
                        {!notif.read && (
                          <button
                            type="button"
                            onClick={() => markNotificationRead(notif.id)}
                            title="Mark as read"
                            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                          >
                            <Check className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => dismissNotification(notif.id)}
                          title="Dismiss"
                          className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-rose-400"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* User Profile Pill & Quick Dropdown */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setProfileDropdownOpen((prev) => !prev)}
            className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-1.5 transition hover:bg-slate-800"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600 text-xs font-bold text-white">
              {user.name.charAt(0)}
            </div>
            <span className="hidden sm:inline text-xs font-medium text-slate-200 pr-1">
              {user.name}
            </span>
          </button>

          {profileDropdownOpen && (
            <div className="absolute right-0 top-12 z-50 w-56 rounded-2xl border border-slate-800 bg-slate-950 p-3 shadow-2xl shadow-black/80">
              <div className="border-b border-slate-800/80 pb-2.5">
                <p className="text-xs font-semibold text-white">{user.name}</p>
                <p className="text-[11px] text-slate-400 font-mono">{user.email}</p>
                <div className="mt-1.5 flex items-center gap-1 text-[10px] text-emerald-400">
                  <UserCheck className="h-3 w-3" />
                  <span>{user.role}</span>
                </div>
              </div>

              <div className="mt-2 space-y-1 text-xs">
                <div className="flex items-center justify-between rounded-lg px-2 py-1.5 text-slate-300">
                  <span>Local Security</span>
                  <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-mono text-indigo-300">
                    Active
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-lg px-2 py-1.5 text-slate-300">
                  <span>Mode</span>
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                    Connected
                  </span>
                </div>
              </div>

              <button
                type="button"
                onClick={async () => {
                  setProfileDropdownOpen(false);
                  await logout();
                  navigate('/');
                }}
                className="mt-2 flex w-full items-center gap-2 rounded-lg border-t border-slate-800/80 px-2 pt-2.5 pb-1 text-xs font-medium text-slate-400 transition hover:text-rose-300"
              >
                <LogOut className="h-3.5 w-3.5" />
                <span>Sign out</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default Topbar;
