import React, { useEffect } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  useNavigate,
  useLocation,
  Navigate,
} from 'react-router-dom';
import AppLayout from './components/layout/AppLayout';
import type { NavSectionId } from './components/layout/Sidebar';
import Home from './pages/Home';
import Chat from './pages/Chat';
import Tasks from './pages/Tasks';
import Reminders from './pages/Reminders';
import Recurrences from './pages/Recurrences';
import BackgroundTasks from './pages/BackgroundTasks';
import Memory from './pages/Memory';
import Tools from './pages/Tools';
import SystemMonitor from './pages/SystemMonitor';
import Settings from './pages/Settings';
import Login from './pages/Login';
import { useAuthStore } from './stores/auth.store';
import { useChatStore } from './stores/chat.store';
import { useTaskStore } from './stores/task.store';
import { useReminderStore } from './stores/reminder.store';
import { useRecurrenceStore } from './stores/recurrence.store';
import { useMemoryStore } from './stores/memory.store';
import { useToolStore } from './stores/tool.store';
import { useNotificationStore } from './stores/notification.store';
import { useBackgroundTaskStore } from './stores/backgroundTask.store';

function pathToSection(pathname: string): NavSectionId {
  const clean = pathname.replace(/^\/+/, '').split('/')[0];
  switch (clean) {
    case 'chat':
      return 'chat';
    case 'tasks':
      return 'tasks';
    case 'reminders':
      return 'reminders';
    case 'recurrences':
      return 'recurrences';
    case 'background-tasks':
      return 'background-tasks';
    case 'memory':
      return 'memory';
    case 'tools':
      return 'tools';
    case 'system-monitor':
      return 'system-monitor';
    case 'settings':
      return 'settings';
    default:
      return 'home';
  }
}

function sectionToPath(section: NavSectionId): string {
  if (section === 'home') return '/';
  return `/${section}`;
}

const AppShell: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const activeSection = pathToSection(location.pathname);

  useEffect(() => {
    // Hydrate domain state once an authenticated session is confirmed.
    // Tasks are loaded before reminders because reminder records resolve their
    // display title from the linked task; reversing this order would briefly
    // render fallback "Task #<id>" labels.
    const hydrateTaskDomain = async () => {
      try {
        await useTaskStore.getState().fetchTasks();
      } catch {
        // Store already preserves the last confirmed data and records the error.
      }
      try {
        await useReminderStore.getState().fetchReminders();
      } catch {
        // Store already preserves the last confirmed data and records the error.
      }
      try {
        // Recurrence cards resolve their task titles from the loaded task list.
        await useRecurrenceStore.getState().fetchRecurrences();
      } catch {
        // Store already preserves the last confirmed data and records the error.
      }
    };

    void hydrateTaskDomain();
    useChatStore.getState().loadConversations().catch(() => {});
    useMemoryStore.getState().fetchMemories().catch(() => {});
    useToolStore.getState().fetchTools().catch(() => {});
  }, []);

  useEffect(() => {
    // Controlled polling: the store serialises requests, starts with an
    // immediate fetch, and clears its interval on unmount.
    useNotificationStore.getState().startPolling();
    useBackgroundTaskStore.getState().startPolling();

    return () => {
      useNotificationStore.getState().stopPolling();
      useBackgroundTaskStore.getState().stopPolling();
    };
  }, []);

  const handleNavigate = (section: NavSectionId) => {
    navigate(sectionToPath(section));
  };

  return (
    <AppLayout activeSection={activeSection} onNavigate={handleNavigate}>
      <Routes>
        <Route path="/" element={<Home onNavigate={handleNavigate} />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/reminders" element={<Reminders />} />
        <Route path="/recurrences" element={<Recurrences />} />
        <Route path="/background-tasks" element={<BackgroundTasks />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/system-monitor" element={<SystemMonitor />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
};

const AuthGate: React.FC = () => {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isCheckingAuth = useAuthStore((s) => s.isCheckingAuth);
  const checkAuth = useAuthStore((s) => s.checkAuth);
  const clearSession = useAuthStore((s) => s.clearSession);

  useEffect(() => {
    checkAuth().catch(() => {});

    // Any 401 from a protected endpoint (including the SSE client) returns the
    // user to the login view without a full page reload.
    const handleUnauthorized = () => clearSession();
    window.addEventListener('asta:unauthorized', handleUnauthorized);

    return () => {
      window.removeEventListener('asta:unauthorized', handleUnauthorized);
    };
  }, [checkAuth, clearSession]);

  if (isCheckingAuth) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-slate-950">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500/30 border-t-indigo-500" />
          <p className="text-xs text-slate-400">Connecting to ASTA…</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login />;
  }

  return <AppShell />;
};

export function App() {
  return (
    <BrowserRouter>
      <AuthGate />
    </BrowserRouter>
  );
}

export default App;
